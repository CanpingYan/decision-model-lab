"""Collections with overlapping case IDs stay independent across import/switch/restart."""
from copy import deepcopy
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from lab.core import LabStore


def collection(identifier, label='是'):
    return {'id': identifier, 'name': identifier + '.xlsx', 'caseCount': 1,
            'scenes': [{'id': '同一场景', 'name': '同一场景', 'count': 1}],
            'cases': [{'id': 'SAME01', 'scene': '同一场景', 'title': '同名用例',
                       'state': '属于题集 ' + identifier,
                       'questions': {'answer': {'type': 'choice', 'instructions': '选择',
                                                'criteria': {label: '第一项', '其他': '第二项'}}},
                       'expected': {'answer': label}, 'rationale': '只应归属于当前题集'}]}


class Registry:
    def __init__(self):
        self.calls = []

    def statuses(self):
        return [{'id': 'jev', 'configured': True, 'ready': True, 'local': False}]

    def decide(self, model_id, task):
        self.calls.append(deepcopy(task))
        return {'answers': {qid: {'choice': next(iter(q['criteria']))} for qid, q in task['questions'].items()},
                'raw': {'fixture': True}, 'elapsedMs': 1, 'model': 'fixture'}


def finish(store, job_id):
    deadline = time.monotonic() + 3
    while store.get_job(job_id)['state'] == 'running' and time.monotonic() < deadline:
        threading.Event().wait(.01)
    return store.get_job(job_id)


class MultipleDatasetTest(unittest.TestCase):
    def test_multiple_imports_deduplicate_and_selection_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(directory)
            first = store.save_dataset(collection('first'))
            store.save_dataset(collection('second', '否'))
            duplicate = collection('first')
            duplicate['name'] = '另一个文件名.xlsx'
            reused = store.save_dataset(duplicate)
            self.assertEqual(len(store.list_datasets()), 2)
            self.assertEqual(reused['name'], first['name'])
            self.assertEqual(reused['importedAt'], first['importedAt'])
            store.select_dataset('second')
            restored = LabStore(directory)
            self.assertEqual(restored.get_current_dataset()['id'], 'second')
            self.assertEqual({row['id'] for row in restored.list_datasets()}, {'first', 'second'})
            self.assertNotIn('cases', restored.list_datasets()[0])
            self.assertNotIn('expected', json.dumps(restored.list_datasets(), ensure_ascii=False))

    def test_same_case_id_has_separate_outputs_and_saved_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(directory)
            registry = Registry()
            jobs = {}
            for identifier, label in [('first', '是'), ('second', '否')]:
                store.save_dataset(collection(identifier, label))
                started = store.start_job(registry, identifier, ['jev'], ['SAME01'])
                job = finish(store, started['id'])
                jobs[identifier] = job
                self.assertEqual(job['state'], 'completed')
                self.assertEqual(job['results'][0]['datasetId'], identifier)
                self.assertEqual(job['results'][0]['labels']['answer'], label)
            restored = LabStore(directory)
            restored.select_dataset('first')
            self.assertEqual(restored.selection()['jobs'][0]['id'], jobs['first']['id'])
            restored.select_dataset('second')
            self.assertEqual(restored.selection()['jobs'][0]['id'], jobs['second']['id'])
            self.assertEqual([task['state'] for task in registry.calls], ['属于题集 first', '属于题集 second'])
            self.assertTrue(all(set(task) == {'state', 'questions'} for task in registry.calls))

    def test_stale_collection_cannot_start_a_test_after_switching(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(directory)
            store.save_dataset(collection('first'))
            store.save_dataset(collection('second'))
            registry = Registry()
            with self.assertRaisesRegex(ValueError, '已切换'):
                store.get_current_dataset('first')
            with self.assertRaisesRegex(ValueError, '已切换'):
                store.start_job(registry, 'first', ['jev'], ['SAME01'])
            self.assertEqual(registry.calls, [])

    def test_running_batch_rejects_switch_without_changing_current(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingRegistry(Registry):
            def decide(self, model_id, task):
                entered.set()
                release.wait(3)
                return super().decide(model_id, task)
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(directory)
            store.save_dataset(collection('first'))
            store.save_dataset(collection('second'))
            registry = BlockingRegistry()
            started = store.start_job(registry, 'second', ['jev'], ['SAME01'])
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(ValueError, '仍在运行'):
                    store.select_dataset('first')
                self.assertEqual(store.current_id, 'second')
            finally:
                release.set()
                finish(store, started['id'])
            self.assertEqual(registry.calls[0]['state'], '属于题集 second')

    def test_legacy_collection_is_cataloged_without_reimport(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'datasets'
            folder.mkdir()
            legacy = collection('legacy')
            legacy.pop('caseCount')
            legacy.pop('scenes')
            (folder / 'legacy.json').write_text(json.dumps(legacy))
            (Path(directory) / 'current.json').write_text('{"datasetId":"legacy"}')
            store = LabStore(directory)
            summary = store.list_datasets()[0]
            self.assertEqual(summary['caseCount'], 1)
            self.assertEqual(summary['scenes'][0]['name'], '同一场景')
            self.assertEqual(store.selection()['dataset']['id'], 'legacy')

    def test_older_collection_latest_job_is_restored_beyond_initial_twenty(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(directory)
            store.save_dataset(collection('old'))
            store.save_dataset(collection('new'))
            base = time.time() - 100
            for index in range(30):
                identifier = f'job{index:02d}'
                path = Path(directory) / 'runs' / (identifier + '.json')
                job = {'id': identifier, 'datasetId': 'old' if index == 0 else 'new',
                       'startedAt': f'2026-01-01T00:00:{index:02d}+00:00',
                       'state': 'running' if index == 0 else 'completed',
                       'results': [], 'total': 1, 'completed': 1}
                path.write_text(json.dumps(job))
                os.utime(path, (base + index, base + index))
            restored = LabStore(directory)
            self.assertNotIn('job00', restored.jobs)
            restored.select_dataset('old')
            self.assertEqual(restored.selection()['jobs'][0]['id'], 'job00')
            self.assertEqual(restored.selection()['jobs'][0]['state'], 'stopped')
            self.assertEqual(json.loads((Path(directory) / 'runs/job00.json').read_text())['state'], 'stopped')
            restored.select_dataset('new')


if __name__ == '__main__':
    unittest.main()
