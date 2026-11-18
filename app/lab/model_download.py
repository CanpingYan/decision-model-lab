"""Download and verify the pinned, complete multilingual Laya checkpoint."""
import hashlib
import json
from pathlib import Path
import sys

MODEL = "convaiinnovations/laya-multilingual"
REVISION = "92d22143468d8af669dc1a5a0480a2b21b4fe56e"


def main():
    from huggingface_hub import HfApi, snapshot_download

    project = Path(__file__).resolve().parents[2]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else project / ".runtime/models/laya-multilingual"
    info = HfApi().model_info(MODEL, revision=REVISION, files_metadata=True)
    snapshot_download(MODEL, revision=REVISION, local_dir=target, max_workers=3,
        allow_patterns=["README.md", "config.json", "encoder/config.json", "model.safetensors",
                        "rl_agent_config.json", "tokenizer/*"])
    lock = {"model": MODEL, "revision": REVISION, "weightsQuantized": False, "files": {}}
    for file in info.siblings:
        if not file.lfs:
            continue
        path = target / file.rfilename
        if not path.is_file() or path.stat().st_size != file.size:
            raise RuntimeError("下载文件大小不符：" + file.rfilename)
        digest = hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
        if digest != file.lfs.sha256:
            raise RuntimeError("下载文件 SHA-256 不符：" + file.rfilename)
        lock["files"][file.rfilename] = {"size": file.size, "sha256": digest}
    (target / "model-lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2)+"\n")
    print(f"已下载并校验 {MODEL}@{REVISION} → {target}")


if __name__ == "__main__":
    main()
