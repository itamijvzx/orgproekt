"""Offline CPU inference. Text never leaves this process; index identity is fixed and checked."""

import hashlib
import json
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MANIFEST = json.loads((Path(__file__).parents[1] / "embedding-model.json").read_text())
INDEX_KEY = MANIFEST["index_key"]
MODEL_PATH = Path("/opt/orgproekt/embeddings")


class EmbeddingUnavailable(Exception):
    pass


class Encoder:
    def __init__(self):
        try:
            for name, spec in MANIFEST["files"].items():
                path = MODEL_PATH / name
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if path.stat().st_size != spec["size"] or digest != spec["sha256"]:
                    raise ValueError("Invalid local model")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            self.session = ort.InferenceSession(
                str(MODEL_PATH / "model.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self.tokenizer = Tokenizer.from_file(str(MODEL_PATH / "tokenizer.json"))
            self.lock = threading.Lock()
        except Exception:
            raise EmbeddingUnavailable("local_model_unavailable") from None

    def chunks(self, text):
        offsets = self.tokenizer.encode(text, add_special_tokens=False).offsets
        if not offsets:
            return []
        result = []
        step = MANIFEST["chunk_tokens"] - MANIFEST["overlap_tokens"]
        for start in range(0, len(offsets), step):
            end = min(start + MANIFEST["chunk_tokens"], len(offsets))
            left, right = offsets[start][0], offsets[end - 1][1]
            result.append((left, right, text[left:right]))
            if end == len(offsets):
                break
        return result

    def encode(self, texts, kind="passage"):
        if kind not in {"query", "passage"}:
            raise ValueError("Unknown prefix")
        rows = []
        with self.lock:
            for first in range(0, len(texts), 8):
                encoded = [self.tokenizer.encode(f"{kind}: {t}") for t in texts[first : first + 8]]
                length = max(len(e.ids) for e in encoded)
                if length > MANIFEST["max_tokens"]:
                    raise ValueError("Text exceeds embedding window; split explicitly")
                ids = np.zeros((len(encoded), length), dtype=np.int64)
                mask = np.zeros_like(ids)
                for i, value in enumerate(encoded):
                    ids[i, : len(value.ids)] = value.ids
                    mask[i, : len(value.ids)] = 1
                inputs = {
                    "input_ids": ids,
                    "attention_mask": mask,
                    "token_type_ids": np.zeros_like(ids),
                }
                hidden = self.session.run(
                    None, {n.name: inputs[n.name] for n in self.session.get_inputs()}
                )[0]
                pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1)[:, None]
                pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
                if pooled.shape[1] != 384 or not np.isfinite(pooled).all():
                    raise EmbeddingUnavailable("invalid_embedding")
                rows.extend(pooled.astype(np.float32).tolist())
        return rows


@lru_cache(maxsize=1)
def encoder():
    return Encoder()
