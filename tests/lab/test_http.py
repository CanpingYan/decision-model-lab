from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from serve import LabServer


class FixtureRegistry:
    def __init__(self):
        self.requests = []

    def statuses(self):
        return [{'id': 'jev', 'name': '测试适配器', 'category': '测试', 'configured': True,
                 'ready': True, 'local': False, 'model': 'fixture', 'detail': '仅供接口测试'}]

    def decide(self, model_id, task):
        self.requests.append(task)
        return {'answers': {qid: {'choice': next(iter(q['criteria']))} for qid, q in task['questions'].items()},
                'elapsedMs': 1, 'raw': {'fixture': True}, 'model': 'fixture'}


class HttpIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.registry = FixtureRegistry()
        try:
            cls.server = LabServer(('127.0.0.1', 0), {'LAB_DATA_DIR': cls.temporary.name}, registry=cls.registry)
        except PermissionError:
            cls.temporary.cleanup()
            raise unittest.SkipTest('当前沙箱不允许本地端口；需在允许回环网络的环境运行 HTTP 检查')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)
        cls.temporary.cleanup()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_import_real_workbook_then_decide_through_the_same_interface(self):
        path = ROOT / 'app/web/examples/中文复杂用例200题.xlsx'
        imported = self.request('/api/import', path.read_bytes(), {'X-Filename': 'cases.xlsx'})['dataset']
        self.assertEqual(imported['caseCount'], 200)
        body = json.dumps({'datasetId': imported['id'], 'caseId': 'W01', 'model': 'jev'}).encode()
        result = self.request('/api/decide', body, {'Content-Type': 'application/json'})
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['datasetId'], imported['id'])
        self.assertEqual(set(self.registry.requests[-1]), {'state', 'questions'})
        self.assertNotIn('rationale', json.dumps(self.registry.requests[-1], ensure_ascii=False))
        status = self.request('/api/status')
        self.assertEqual(status['dataset']['id'], imported['id'])

    def test_multi_import_switch_and_reject_stale_demo_and_batch_requests(self):
        full_path = ROOT / 'app/web/examples/中文复杂用例200题.xlsx'
        template_path = ROOT / 'app/web/examples/测试用例模板.xlsx'
        full = self.request('/api/import', full_path.read_bytes(), {'X-Filename': 'full.xlsx'})['dataset']
        second = self.request('/api/import', template_path.read_bytes(), {'X-Filename': 'template.xlsx'})
        template = second['dataset']
        self.assertEqual({dataset['id'] for dataset in second['datasets']}, {full['id'], template['id']})
        self.assertEqual(self.request('/api/status')['dataset']['id'], template['id'])
        before = len(self.registry.requests)
        for endpoint, body in [('/api/decide', {'datasetId': full['id'], 'caseId': 'W01', 'model': 'jev'}),
                               ('/api/jobs', {'datasetId': full['id'], 'models': ['jev'], 'caseIds': ['W01']})]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(endpoint, json.dumps(body).encode(), {'Content-Type': 'application/json'})
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(len(self.registry.requests), before)
        selection = self.request('/api/datasets/select', json.dumps({'datasetId': full['id']}).encode(),
                                 {'Content-Type': 'application/json'})
        self.assertEqual(selection['dataset']['id'], full['id'])
        self.assertEqual(self.request('/api/status')['dataset']['id'], full['id'])
        summaries = self.request('/api/datasets')['datasets']
        self.assertEqual(len(summaries), 2)
        self.assertTrue(all('cases' not in dataset for dataset in summaries))
        repeated = self.request('/api/import', template_path.read_bytes(), {'X-Filename': 'renamed.xlsx'})
        self.assertEqual(len(repeated['datasets']), 2)
        self.assertEqual(repeated['dataset']['name'], 'template.xlsx')

    def test_private_files_and_parent_paths_are_not_served(self):
        for path in ['/.env', '/../.env', '/%2e%2e/.env', '/.runtime/laya-worker.log', '/examples/']:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)

    def test_foreign_origin_and_host_are_rejected(self):
        for headers in [{'Origin': 'https://unrelated.example'}, {'Host': 'unrelated.example'}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/status', headers=headers)
            self.assertEqual(error.exception.code, 403)

    def test_invalid_request_is_not_silently_accepted(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/jobs', b'{"models":[],"caseIds":[]}', {'Content-Type': 'application/json'})
        self.assertIn(error.exception.code, (400, 404))


if __name__ == '__main__':
    unittest.main()
