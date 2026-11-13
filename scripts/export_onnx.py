from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
from torch import nn
from torchvision import models


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="把 ResNet50 热成像基线导出为 ONNX 并验证数值一致性")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "resnet50_baseline_fold0" / "model.pt",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--opset", type=int, default=18)
    parser.add_argument("--tolerance", type=float, default=1e-4)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    model = models.resnet50(weights=None)
    model.fc = nn.Linear(model.fc.in_features, 1)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()

    output_path = args.output or args.checkpoint.with_suffix(".onnx")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_size = int(checkpoint["image_size"])
    example = torch.randn(1, 3, image_size, image_size, dtype=torch.float32)
    with torch.inference_mode():
        expected = model(example).numpy()

    torch.onnx.export(
        model,
        (example,),
        output_path,
        input_names=["image"],
        output_names=["waste_logit"],
        opset_version=args.opset,
        dynamo=True,
    )
    onnx_model = onnx.load(output_path)
    onnx.checker.check_model(onnx_model)
    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    actual = session.run(["waste_logit"], {"image": example.numpy()})[0]
    max_absolute_error = float(np.max(np.abs(expected - actual)))
    if max_absolute_error > args.tolerance:
        raise RuntimeError(f"PyTorch/ONNX 最大误差 {max_absolute_error} 超过容限 {args.tolerance}")

    external_data_files = sorted(
        path.name
        for path in output_path.parent.glob(f"{output_path.name}.*")
        if path.is_file() and path != output_path
    )
    metadata = {
        "model": "resnet50",
        "input_name": "image",
        "input_shape": [1, 3, image_size, image_size],
        "output_name": "waste_logit",
        "output_semantics": "sigmoid(waste_logit) = p_waste",
        "mean": list(checkpoint["mean"]),
        "std": list(checkpoint["std"]),
        "waste_threshold": float(checkpoint["waste_threshold"]),
        "threshold_unit": checkpoint.get("threshold_unit", "image"),
        "labels": checkpoint["labels"],
        "test_fold": int(checkpoint["test_fold"]),
        "pretrained_backbone": bool(checkpoint["pretrained_backbone"]),
        "onnx_opset": args.opset,
        "onnxruntime_providers_tested": session.get_providers(),
        "pytorch_onnx_max_absolute_error": max_absolute_error,
        "external_data_files": external_data_files,
    }
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(f"ONNX：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
