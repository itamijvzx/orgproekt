"""Build-time only: fixed public artifacts, pinned revision/size/hash. No runtime downloads."""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

manifest = json.loads(Path("embedding-model.json").read_text())
destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
for name, spec in manifest["files"].items():
    target = destination / name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == spec["sha256"]:
        continue
    url = f"https://huggingface.co/{manifest['model_id']}/resolve/{manifest['revision']}/{spec['path']}"
    temporary = target.with_suffix(".download")
    digest, size = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > spec["size"]:
                raise RuntimeError("Model artifact exceeds pinned size")
            digest.update(chunk)
            output.write(chunk)
    if size != spec["size"] or digest.hexdigest() != spec["sha256"]:
        raise RuntimeError("Model artifact checksum mismatch")
    temporary.replace(target)
    print(f"Verified {name}: {size} bytes")
(destination / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
