"""Isolated local full-precision Laya process; stdout is JSON-lines only."""
import contextlib
import json
import os
from pathlib import Path
import sys
import time

os.environ["USE_TF"] = "0"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


try:
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        import laya

        torch.set_num_threads(4)
        started = time.perf_counter()
        device = os.environ.get("LAYA_DEVICE", "auto")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        base = Path(sys.argv[1]).resolve()
        # The official package owns the encoder, decision heads and native readout.
        lock_path = base / "model-lock.json"
        lock = json.loads(lock_path.read_text()) if lock_path.is_file() else {}
        digests = {"model.safetensors": lock["files"]["model.safetensors"]["sha256"]} if lock.get("files") else None
        agent = laya.load(str(base), device=device, backend="eager", expected_sha256=digests)
        # Explicitly disable its device-specific autocast policy: every forward is FP32.
        agent.model.float()
        agent.dtype = torch.float32
        agent.amp_enabled = False
        max_len, head_max_len = 8192, 2048

        def predict(task):
            result = agent.predict(task["state"], task["questions"],
                                   max_len=max_len, head_max_len=head_max_len)
            usage = result["usage"]
            if usage.get("truncated"):
                raise ValueError("输入超过 Laya 8192 token 预算，结果已丢弃，请缩短上下文")
            if usage.get("options"):
                raise ValueError("Laya 编码后无法区分部分候选项，请缩短或改写候选名称和说明")
            result["model"] = os.environ.get("LAYA_MODEL_ID", "convaiinnovations/laya-multilingual")
            result["execution"] = {"device": device, "dtype": "float32", "quantized": False,
                "revision": os.environ.get("LAYA_REVISION", ""), "maxLength": max_len,
                "headMaxLength": head_max_len, "generatedTokens": 0}
            return result

        predict({"state": "用户明确要求退还重复扣款。", "questions": {"route": {
            "type": "choice", "instructions": "选择负责处理的团队。",
            "criteria": {"账务": "支付与退款", "技术": "程序故障"}}}})
        if any(p.is_floating_point() and p.dtype != torch.float32 for p in agent.model.parameters()):
            raise ValueError("模型参数未全部转换为 FP32，拒绝启动")
        info = {"device": device, "dtype": "float32", "quantized": False,
                "model": os.environ.get("LAYA_MODEL_ID", "convaiinnovations/laya-multilingual"),
                "revision": os.environ.get("LAYA_REVISION", ""), "parameters": sum(p.numel() for p in agent.model.parameters()),
                "loadMs": (time.perf_counter()-started)*1000, "layaVersion": laya.__version__,
                "torchVersion": torch.__version__, "maxLength": max_len, "generatedTokens": 0}
    emit({"ready": True, "info": info})
except Exception as exc:
    import traceback
    traceback.print_exc(file=sys.stderr)
    emit({"error": f"Laya 初始化失败：{type(exc).__name__}: {str(exc)[:400]}"})
    sys.exit(1)

for line in sys.stdin:
    try:
        task = json.loads(line)
        started = time.perf_counter()
        with contextlib.redirect_stdout(sys.stderr):
            raw = predict(task)
        emit({"raw": raw, "elapsedMs": (time.perf_counter()-started)*1000})
    except Exception as exc:
        emit({"error": f"Laya 推理失败：{type(exc).__name__}: {str(exc)[:400]}"})
