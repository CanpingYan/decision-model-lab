"""Download the pinned, non-quantized Chinese Decision checkpoint and verify it."""
import hashlib
import json
from pathlib import Path
import sys

MODEL = "quincyqiang/chinese-decision-0.8b"
REVISION = "f40e4bbdc96fda656221d156a5c7fd9c6788090c"
FILES = ["README.md", "config.json", "decision_config.json", "model.safetensors",
         "tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "LICENSE"]


def main():
    from huggingface_hub import HfApi, snapshot_download
    root = Path(__file__).resolve().parents[2]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else root / ".runtime/models/chinese-decision-0.8b"
    info = HfApi().model_info(MODEL, revision=REVISION, files_metadata=True)
    snapshot_download(MODEL, revision=REVISION, local_dir=target, allow_patterns=FILES, max_workers=3)
    lock = {"model": MODEL, "revision": REVISION, "weightsQuantized": False, "files": {}}
    for file in info.siblings:
        if file.rfilename not in FILES:
            continue
        path = target / file.rfilename
        if not path.is_file() or path.stat().st_size != file.size:
            raise RuntimeError("下载文件大小不符：" + file.rfilename)
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        lfs = file.lfs
        if lfs and digest != lfs.sha256:
            raise RuntimeError("下载文件 SHA-256 不符：" + file.rfilename)
        lock["files"][file.rfilename] = {"size": file.size, "sha256": digest}
    if not all((target / name).is_file() for name in FILES):
        raise RuntimeError("完整模型所需文件缺失")
    (target / "model-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n")
    print(f"已下载并校验 {MODEL}@{REVISION} → {target}")


if __name__ == "__main__":
    main()
