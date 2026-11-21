"""Contract checks at the model-adapter seam without external network calls."""
import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.lab.core import run_case
from app.lab.providers import ModelError, ProviderRegistry

TASK = {"state": "重复扣款尚未退款，旧故障已经修复。", "questions": {"route": {
    "type": "choice", "instructions": "选择当前负责团队。",
    "criteria": {"账务": "支付、退款与账单", "技术": "尚未解决的程序故障"}}}}


class ProvidersTest(unittest.TestCase):
    def test_native_request_has_no_gold_or_other_metadata(self):
        registry = ProviderRegistry({"JEV_API_KEY": "test-private-key"})
        calls = []
        def post(endpoint, key, body):
            calls.append(body)
            return {"model": "jev-1.14.0", "answers": {"route": {
                "choice": "账务", "probabilities": {"账务": 0.8, "技术": 0.2}}}}
        registry._post = post
        result = registry.decide("jev", copy.deepcopy(TASK))
        self.assertEqual(set(calls[0]), {"state", "questions", "model"})
        self.assertEqual(result["answers"]["route"]["choice"], "账务")
        self.assertNotIn("test-private-key", str(result))
        with self.assertRaises(ModelError):
            registry.decide("jev", {**TASK, "expected": {"route": "账务"}})

    def test_generated_labels_do_not_gain_fabricated_probabilities(self):
        registry = ProviderRegistry({"DEEPSEEK_API_KEY": "test-key"})
        registry._post = lambda *args: {"model": "deepseek-flash", "choices": [{"message": {
            "content": '{"answers":{"route":"账务"}}'}}]}
        answer = registry.decide("deepseek", TASK)["answers"]["route"]
        self.assertEqual(answer, {"choice": "账务"})

    def test_echoed_api_key_is_omitted_from_success_and_error_records(self):
        key = "fake-gateway-test-credential"
        registry = ProviderRegistry({"JEV_API_KEY": key})
        case = {**TASK, "id": "W01", "scene": "工单分流", "expected": {"route": "账务"}}
        for choice, status in [("账务", "success"), ("不存在", "error")]:
            with self.subTest(status=status):
                registry._post = lambda *args: {
                    "answers": {"route": {"choice": choice}},
                    "diagnostics": [{"Authorization": "Bearer " + key, key: "echoed"}],
                }
                result = run_case(registry, case, "jev")
                self.assertEqual(result["status"], status)
                self.assertNotIn(key, str(result))
                self.assertEqual(result["raw"]["stages"][0]["diagnostics"], [
                    {"Authorization": "Bearer [redacted]", "[redacted]": "echoed"},
                ])

    def test_invalid_label_and_distribution_refuse_a_valid_result(self):
        registry = ProviderRegistry({"JEV_API_KEY": "test-key"})
        for answer in [{"choice": "invented"}, {"choice": "账务", "probabilities": {"账务": 0.2, "技术": 0.2}}]:
            registry._post = lambda *args: {"answers": {"route": answer}}
            with self.assertRaises(ModelError):
                registry.decide("jev", TASK)

    def test_alibaba_requires_the_documented_workspace_endpoint(self):
        registry = ProviderRegistry({"ALIBABA_API_KEY": "test-key"})
        status = next(s for s in registry.statuses() if s["id"] == "alibaba")
        self.assertFalse(status["configured"])
        registry = ProviderRegistry({"ALIBABA_API_KEY": "test-key", "ALIBABA_WORKSPACE_ID": "workspace123"})
        self.assertEqual(registry._cloud_config("alibaba")[1],
            "https://workspace123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone")

    def test_status_has_no_keys_and_laya_refuses_reduced_precision(self):
        registry = ProviderRegistry({"JEV_API_KEY": "secret-never-return", "LAYA_DTYPE": "int8"})
        self.assertNotIn("secret-never-return", str(registry.statuses()))
        self.assertFalse(next(s for s in registry.statuses() if s["id"] == "laya")["configured"])

    def test_authenticated_request_stops_on_redirect(self):
        received_paths = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                received_paths.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/unexpected")
                self.end_headers()

            def do_GET(self):
                received_paths.append(self.path)
                self.send_response(500)
                self.end_headers()

        try:
            server = HTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("本地回环端口不可用")
        with server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                registry = ProviderRegistry({"API_TIMEOUT": "5"})
                with self.assertRaisesRegex(ModelError, "端点重定向"):
                    registry._post(f"http://127.0.0.1:{server.server_port}/decision",
                                   "synthetic-test-credential", {"state": "示例"})
                self.assertEqual(received_paths, ["/decision"])
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
