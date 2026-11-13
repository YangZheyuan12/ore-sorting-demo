from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sorting_demo.ml.onnx_classifier import OnnxOreClassifier  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 ONNX Runtime 对单张矿石热成像图分类")
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "resnet50_baseline_fold0" / "model.onnx",
    )
    args = parser.parse_args()
    prediction = OnnxOreClassifier(args.model).predict(args.image)
    print(
        json.dumps(
            {
                "image": str(args.image),
                "label": prediction.label.value,
                "confidence": prediction.confidence,
                "waste_probability": prediction.waste_probability,
                "waste_threshold": prediction.threshold,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
