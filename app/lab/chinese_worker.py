"""Offline Chinese Decision worker using the pinned official processor/readout.

Only state/questions arrive here. One forward pass per question, no generation.
The published BF16 checkpoint is loaded as FP32, never quantized.
"""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", USE_TF="0",
                  TOKENIZERS_PARALLELISM="false", HF_HUB_DISABLE_PROGRESS_BARS="1")


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


try:
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        import transformers
        from transformers import AutoModelForCausalLM
        # Official processor, vendored at commit 4708ede2c40e31fb8a851d58413c0e24e75f6326.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "reference/chinese-decision"))
        from decision.processing import DecisionProcessor

        started = time.perf_counter()
        torch.set_num_threads(int(os.environ.get("CHINESE_CPU_THREADS", "4")))
        device = os.environ.get("CHINESE_DEVICE", "auto")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        base = Path(sys.argv[1]).resolve()
        lock_path = base / "model-lock.json"
        if lock_path.is_file():
            lock = json.loads(lock_path.read_text())
            for filename, metadata in lock["files"].items():
                with (base / filename).open("rb") as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != metadata["sha256"]:
                        raise ValueError("模型文件校验失败：" + filename)
        config = json.loads((base / "config.json").read_text())
        if config.get("quantization_config"):
            raise ValueError("仅支持完整非量化权重")
        processor = DecisionProcessor.from_pretrained(base)
        if processor.config.readout != "letters":
            raise ValueError("当前适配器要求官方 A–Z 候选读出模型")
        model = AutoModelForCausalLM.from_pretrained(
            str(base), dtype=torch.float32, attn_implementation="sdpa", local_files_only=True,
            trust_remote_code=False).to(device).eval()
        letters = torch.tensor(processor.letter_ids, device=device)
        if any(p.is_floating_point() and p.dtype != torch.float32 for p in model.parameters()):
            raise ValueError("模型参数未全部转换为 FP32")

        @torch.inference_mode()
        def predict(task):
            answers, lengths = {}, {}
            for qid, question in task["questions"].items():
                # Official encoding rejects inputs over 16384 tokens; never truncate.
                item = processor.encode_question(task["state"], question)
                inputs = processor.pad_decisions([item], device=device)
                output = model(**inputs, logits_to_keep=1, use_cache=False)
                logits = output.logits[0, -1, letters[:len(item["options"])]].float()
                probabilities = (logits / processor.config.temperature).softmax(-1).cpu().tolist()
                if not all(math.isfinite(p) for p in probabilities):
                    raise ValueError("模型返回非有限概率")
                distribution = {key: p for (key, _), p in zip(item["options"], probabilities)}
                answers[qid] = {"choice": max(distribution, key=distribution.get),
                                "probabilities": distribution}
                lengths[qid] = len(item["input_ids"])
            return {"model": os.environ.get("CHINESE_MODEL_ID", "quincyqiang/chinese-decision-0.8b"),
                    "answers": answers, "input_lengths": lengths,
                    "execution": {"device": device, "dtype": "float32", "quantized": False,
                                  "revision": os.environ.get("CHINESE_REVISION", ""),
                                  "temperature": processor.config.temperature,
                                  "maxLength": processor.config.max_length,
                                  "forwardPasses": len(answers), "generatedTokens": 0}}

        # Neutral warm-up; no benchmark answers or labels are used to tune the model.
        predict({"state": "用户申请退还重复扣款。", "questions": {"route": {
            "type": "choice", "instructions": "选择负责团队。",
            "criteria": {"账务": "支付与退款", "技术": "程序故障"}}}})
        info = {"model": os.environ.get("CHINESE_MODEL_ID", "quincyqiang/chinese-decision-0.8b"),
                "revision": os.environ.get("CHINESE_REVISION", ""),
                "device": device, "dtype": "float32", "quantized": False,
                "parameters": sum(p.numel() for p in model.parameters()),
                "loadMs": (time.perf_counter()-started)*1000,
                "torchVersion": torch.__version__, "transformersVersion": transformers.__version__,
                "temperature": processor.config.temperature, "maxLength": processor.config.max_length,
                "generatedTokens": 0}
    emit({"ready": True, "info": info})
except Exception as exc:
    import traceback
    traceback.print_exc(file=sys.stderr)
    emit({"error": f"Chinese Decision 初始化失败：{type(exc).__name__}: {str(exc)[:500]}"})
    sys.exit(1)

for line in sys.stdin:
    try:
        task = json.loads(line)
        started = time.perf_counter()
        with contextlib.redirect_stdout(sys.stderr):
            raw = predict(task)
        emit({"raw": raw, "elapsedMs": (time.perf_counter()-started)*1000})
    except Exception as exc:
        emit({"error": f"Chinese Decision 推理失败：{type(exc).__name__}: {str(exc)[:500]}"})
