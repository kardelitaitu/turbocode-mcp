"""
TurboCode embedding subprocess. Runs as a subprocess of the main MCP server
to keep model memory isolated. Communicates via JSON-line protocol on stdio.

Messages (one per line):
  Request:  {"id": N, "texts": ["..."]}
  Response: {"id": N, "vectors": [[0.1, ...], ...]}
  Error:    {"id": N, "error": "..."}
  Control:  {"type": "shutdown"}
"""

import json
import sys
import numpy as np
from fastembed import TextEmbedding


def _pick_providers() -> list[str] | None:
    """Detect GPU ONNX providers. Returns None to use fastembed defaults (CPU)."""
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    available = ort.get_available_providers()
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider", "MIGraphXExecutionProvider", "ROCMExecutionProvider"]
    matched = [p for p in preferred if p in available]
    if matched:
        return matched + ["CPUExecutionProvider"]
    return None


def main() -> None:
    model_name = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-small-en-v1.5"
    providers = _pick_providers()
    if providers:
        print(f"GPU providers detected: {providers}", file=sys.stderr)
    try:
        kwargs = {"model_name": model_name, "max_length": 512}
        if providers:
            kwargs["providers"] = providers
        model = TextEmbedding(**kwargs)
    except Exception as e:
        sys.stdout.write(json.dumps({"type": "error", "message": str(e)}) + "\n")
        sys.stdout.flush()
        sys.exit(1)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if msg.get("type") == "shutdown":
            break

        req_id = msg["id"]
        texts = msg["texts"]

        try:
            vectors = list(model.embed(texts))
            result = json.dumps(
                {"id": req_id, "vectors": [v.tolist() for v in vectors]},
                default=str,
            )
            sys.stdout.write(result + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(
                json.dumps({"id": req_id, "error": str(e)}, default=str) + "\n"
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
