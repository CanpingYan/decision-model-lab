"""Behavioral checks for the fifth, offline Chinese decision adapter."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.lab.providers import ModelError, ProviderRegistry
from app.lab.core import run_case
from app.lab.config import PROJECT_ROOT, load_settings

TASK = {"state": "同一订单扣了两次款。", "questions": {"category": {
    "type": "choice", "instructions": "选择负责队列。",
    "criteria": {"账务问题": "支付、扣款和退款", "产品故障": "程序故障"}}}}


class ChineseLocalTest(unittest.TestCase):
    def test_fifth_model_is_independent_local_configuration(self):
        registry = ProviderRegistry({"JEV_API_KEY": "private-key-never-public"})
        statuses = {s["id"]: s for s in registry.statuses()}
        self.assertEqual(set(statuses), {"deepseek", "jev", "alibaba", "laya", "chinese"})
        chinese = statuses["chinese"]
        self.assertTrue(chinese["local"])
        self.assertFalse(chinese["ready"])
        self.assertIn("chinese-decision-0.8b", chinese["model"])
        self.assertNotIn("private-key-never-public", json.dumps(statuses))
        self.assertIsNot(registry.chinese, registry.laya)

    def test_config_resolves_chinese_paths_from_project(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("CHINESE_MODEL_DIR=.runtime/models/chinese-decision-0.8b\nCHINESE_PYTHON=.runtime/chinese-venv/bin/python\n")
            settings = load_settings(path)
            self.assertEqual(settings["CHINESE_MODEL_DIR"], str(PROJECT_ROOT / ".runtime/models/chinese-decision-0.8b"))
            self.assertEqual(settings["CHINESE_PYTHON"], str(PROJECT_ROOT / ".runtime/chinese-venv/bin/python"))

    def test_chinese_result_uses_shared_scoring_and_cannot_receive_gold(self):
        registry = ProviderRegistry({})
        received = []
        def decide(task):
            received.append(task)
            return {"raw": {"model": "quincyqiang/chinese-decision-0.8b", "answers": {
                "category": {"choice": "账务问题", "probabilities": {"账务问题": .8, "产品故障": .2}}}}}
        registry.chinese.decide = decide
        case = {"id": "W01", "scene": "工单分流", "state": TASK["state"],
                "questions": TASK["questions"], "expected": {"category": "账务问题"},
                "rationale": "不能给模型读取的金标依据", "difficulty": "简单"}
        result = run_case(registry, case, "chinese")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["correct"])
        self.assertEqual(set(received[0]), {"state", "questions"})
        self.assertEqual(result["answers"]["category"]["probabilities"]["账务问题"], .8)
        with self.assertRaises(ModelError):
            registry.decide("chinese", {**TASK, "expected": case["expected"]})

    def test_chinese_rejects_quantized_configuration(self):
        registry = ProviderRegistry({"CHINESE_DTYPE": "int8"})
        status = next(s for s in registry.statuses() if s["id"] == "chinese")
        self.assertFalse(status["configured"])
        self.assertIn("CHINESE_DTYPE", " ".join(status["missing"]))


if __name__ == "__main__":
    unittest.main()
