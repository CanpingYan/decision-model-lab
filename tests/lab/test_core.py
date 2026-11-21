import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from lab.core import LabStore, run_case
from lab.workbook import validate_case


def sample():
    return {'id': 'M1', 'scene': '记忆更新', 'title': '提问与事实',
            'state': '旧记忆：主库为 PostgreSQL。新消息：是否已经迁移到 MySQL？未提供执行记录。',
            'questions': {
                'kind': {'type': 'choice', 'instructions': '新消息是什么行为？',
                         'criteria': {'提问': '寻求确认', '事实报告': '陈述已发生的事实'}},
                'relation': {'type': 'choice', 'instructions': '事实与记忆有什么关系？',
                             'criteria': {'替代': '已生效的新值', '确认': '旧值仍成立'}}},
            'conditions': {'relation': {'question': 'kind', 'equals': '事实报告'}},
            'expected': {'kind': '提问'}, 'rationale': '疑问句不构成已迁移证据。',
            'difficulty': '复杂', 'tags': ['事实与提问', '证据缺失', '时间线']}


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def statuses(self):
        return [{'id': 'jev', 'configured': True, 'ready': True, 'local': False}]

    def decide(self, model_id, task):
        self.calls.append(task)
        answers = {qid: {'choice': next(iter(q['criteria']))} for qid, q in task['questions'].items()}
        return {'answers': answers, 'raw': {'actual': 'fixture'}, 'elapsedMs': 1, 'model': 'fixture'}


class CoreTest(unittest.TestCase):
    def test_conditional_question_skips_from_actual_prediction_without_gold_leak(self):
        registry = FakeRegistry()
        case = sample()
        result = run_case(registry, case, 'jev')
        self.assertTrue(result['correct'])
        self.assertEqual(len(registry.calls), 1)
        self.assertEqual(set(registry.calls[0]), {'state', 'questions'})
        self.assertEqual(set(registry.calls[0]['questions']), {'kind'})
        self.assertEqual(result['trace'][1]['status'], 'skipped')
        case['expected']['kind'] = '事实报告'
        wrong_gold = run_case(registry, case, 'jev')
        self.assertFalse(wrong_gold['correct'])
        self.assertEqual(registry.calls[0], registry.calls[1])

    def test_actual_parent_enables_second_stage_with_only_business_input(self):
        case = sample()
        case['questions']['kind']['criteria'] = {'事实报告': '事实', '提问': '疑问'}
        case['expected'] = {'kind': '事实报告', 'relation': '替代'}
        registry = FakeRegistry()
        result = run_case(registry, case, 'jev')
        self.assertTrue(result['correct'])
        self.assertEqual(len(registry.calls), 2)
        self.assertEqual(set(registry.calls[1]['questions']), {'relation'})
        self.assertEqual(registry.calls[1]['state'], case['state'])
        self.assertNotIn('expected', json.dumps(registry.calls, ensure_ascii=False))

    def test_skipped_parent_propagates_to_earlier_question_in_dependency_chain(self):
        case = sample()
        case['questions'] = {'confirm': case['questions']['relation'].copy(), **case['questions']}
        case['conditions']['confirm'] = {'question': 'relation', 'equals': '替代'}
        validate_case(case)
        registry = FakeRegistry()

        result = run_case(registry, case, 'jev')

        self.assertTrue(result['correct'])
        self.assertEqual(len(registry.calls), 1)
        self.assertEqual(
            [(step['questionIds'], step['status']) for step in result['trace']],
            [(['kind'], 'success'), (['relation'], 'skipped'), (['confirm'], 'skipped')],
        )

    def test_invalid_labels_are_errors_and_raw_output_is_preserved(self):
        class Invalid(FakeRegistry):
            def decide(self, model_id, task):
                error = RuntimeError('非法选项')
                error.raw = {'text': '并不存在的类别'}
                raise error
        result = run_case(Invalid(), sample(), 'jev')
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['correct'])
        self.assertEqual(result['raw']['stages'], [{'text': '并不存在的类别'}])

    def test_no_standard_answer_has_no_accuracy_score(self):
        case = sample()
        case['expected'] = {}
        result = run_case(FakeRegistry(), case, 'jev')
        self.assertEqual(result['status'], 'success')
        self.assertIsNone(result['correct'])
        self.assertNotIn('probabilities', result['answers']['kind'])

    def test_stopping_during_first_stage_does_not_start_a_conditional_request(self):
        stopped = threading.Event()
        class StopAfterForward(FakeRegistry):
            def decide(self, model_id, task):
                response = super().decide(model_id, task)
                stopped.set()
                return response
        case = sample()
        case['questions']['kind']['criteria'] = {'事实报告': '事实', '提问': '疑问'}
        case['expected'] = {'kind': '事实报告', 'relation': '替代'}
        registry = StopAfterForward()
        result = run_case(registry, case, 'jev', cancelled=stopped.is_set)
        self.assertEqual(len(registry.calls), 1)
        self.assertEqual(result['status'], 'stopped')
        self.assertIsNone(result['correct'])
        self.assertEqual(result['labels'], {'kind': '事实报告'})

    def test_stop_preserves_inflight_result_and_stops_next_request(self):
        entered, release = threading.Event(), threading.Event()
        class Blocking(FakeRegistry):
            def decide(self, model_id, task):
                entered.set()
                release.wait(3)
                return super().decide(model_id, task)
        with tempfile.TemporaryDirectory() as directory:
            store = LabStore(directory)
            first, second = sample(), sample()
            second['id'] = 'M2'
            dataset = {'id': 'testdataset', 'name': '测试题集', 'cases': [first, second]}
            store.save_dataset(dataset)
            registry = Blocking()
            job = store.start_job(registry, dataset['id'], ['jev'], ['M1', 'M2'])
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ValueError):
                store.save_dataset(dataset)
            store.stop_job(job['id'])
            release.set()
            deadline = time.monotonic() + 3
            while store.get_job(job['id'])['state'] == 'running' and time.monotonic() < deadline:
                threading.Event().wait(.01)
            final = store.get_job(job['id'])
            self.assertEqual(final['state'], 'stopped')
            self.assertEqual(final['completed'], 1)
            self.assertEqual(len(registry.calls), 1)
            restored = LabStore(directory)
            self.assertEqual(restored.get_job(job['id'])['results'], final['results'])
            self.assertEqual(restored.get_dataset()['id'], dataset['id'])

    def test_all_chinese_cases_validate_and_preserve_original_ids(self):
        new_path = ROOT / 'app/web/examples/中文复杂用例200题.json'
        if not new_path.exists():
            self.skipTest('中文题集尚未生成')
        new = json.loads(new_path.read_text())['cases']
        old = json.loads((ROOT / 'tests/fixtures/benchmark-v3-cases.json').read_text())['cases']
        self.assertEqual({c['id'] for c in new}, {c['id'] for c in old})
        self.assertEqual(len(new), 200)
        self.assertEqual(len({c['scene'] for c in new}), 4)
        for case in new:
            validate_case(case)
            self.assertTrue(case['expected'])
            self.assertGreaterEqual(len(case['tags']), 3)


if __name__ == '__main__':
    unittest.main()
