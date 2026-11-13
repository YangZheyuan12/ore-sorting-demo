from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sorting_demo.ml.manifest import SampleRecord, load_manifest  # noqa: E402
from sorting_demo.ml.metrics import aggregate_by_specimen, binary_metrics, choose_risk_threshold  # noqa: E402


def score(sample: SampleRecord) -> dict[str, object]:
    with Image.open(PROJECT_ROOT / sample.path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    luminance = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    return {
        "specimen_id": sample.specimen_id,
        "label": sample.label,
        "probability": 1.0 - float(luminance.mean()) / 255.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="四折实体分组热强度基线")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "manifest.csv")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "brightness_baseline" / "report.json",
    )
    parser.add_argument("--max-ore-misdiscard-rate", type=float, default=0.0)
    args = parser.parse_args()

    samples = load_manifest(args.manifest)
    scored = [(sample, score(sample)) for sample in samples]
    fold_reports: list[dict[str, object]] = []
    all_test_specimens: list[dict[str, object]] = []
    for fold in sorted({sample.fold for sample in samples}):
        validation_rows = [row for sample, row in scored if sample.fold != fold]
        test_rows = [row for sample, row in scored if sample.fold == fold]
        validation_specimens = aggregate_by_specimen(validation_rows)
        test_specimens = aggregate_by_specimen(test_rows)
        threshold, validation_metrics = choose_risk_threshold(
            [int(row["label"]) for row in validation_specimens],
            [float(row["probability"]) for row in validation_specimens],
            max_ore_misdiscard_rate=args.max_ore_misdiscard_rate,
        )
        test_metrics = binary_metrics(
            [int(row["label"]) for row in test_specimens],
            [float(row["probability"]) for row in test_specimens],
            threshold,
        )
        for row in test_specimens:
            all_test_specimens.append({**row, "fold": fold, "threshold": threshold})
        fold_reports.append(
            {
                "fold": fold,
                "threshold": threshold,
                "validation": validation_metrics,
                "test": test_metrics,
            }
        )

    predictions = [float(row["probability"]) >= float(row["threshold"]) for row in all_test_specimens]
    labels = [int(row["label"]) for row in all_test_specimens]
    tp = sum(label == 1 and prediction for label, prediction in zip(labels, predictions, strict=True))
    fn = sum(label == 1 and not prediction for label, prediction in zip(labels, predictions, strict=True))
    tn = sum(label == 0 and not prediction for label, prediction in zip(labels, predictions, strict=True))
    fp = sum(label == 0 and prediction for label, prediction in zip(labels, predictions, strict=True))
    pooled = binary_metrics(labels, [1.0 if value else 0.0 for value in predictions], 0.5)
    pooled.update({"tp": tp, "fn": fn, "tn": tn, "fp": fp})
    report = {
        "model": "1 - mean_luminance / 255",
        "unit": "specimen mean across views",
        "folds": fold_reports,
        "pooled_out_of_fold": pooled,
        "warning": "该基线用于检测热强度和采集批次信号，不代表现场泛化性能。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
