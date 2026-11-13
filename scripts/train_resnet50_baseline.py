from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TORCH_HOME", str(PROJECT_ROOT / ".torch"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sorting_demo.ml.manifest import SampleRecord, load_manifest  # noqa: E402
from sorting_demo.ml.metrics import (  # noqa: E402
    aggregate_by_specimen,
    binary_metrics,
    choose_risk_threshold,
)


IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def split_by_specimen(
    samples: list[SampleRecord],
    test_fold: int,
    seed: int,
    validation_specimens_per_class: int = 2,
) -> dict[str, list[SampleRecord]]:
    test_groups = {sample.specimen_id for sample in samples if sample.fold == test_fold}
    remaining_groups: dict[int, list[str]] = defaultdict(list)
    specimen_label: dict[str, int] = {}
    for sample in samples:
        specimen_label.setdefault(sample.specimen_id, sample.label)
    for specimen_id, label in specimen_label.items():
        if specimen_id not in test_groups:
            remaining_groups[label].append(specimen_id)

    validation_groups: set[str] = set()
    for label, groups in sorted(remaining_groups.items()):
        ordered = sorted(groups)
        random.Random(seed + test_fold * 101 + label * 1009).shuffle(ordered)
        if len(ordered) <= validation_specimens_per_class:
            raise ValueError("验证实体数过多，训练集将为空")
        validation_groups.update(ordered[:validation_specimens_per_class])

    result = {"train": [], "validation": [], "test": []}
    for sample in samples:
        if sample.specimen_id in test_groups:
            result["test"].append(sample)
        elif sample.specimen_id in validation_groups:
            result["validation"].append(sample)
        else:
            result["train"].append(sample)

    group_sets = {name: {row.specimen_id for row in rows} for name, rows in result.items()}
    if group_sets["train"] & group_sets["validation"] or group_sets["train"] & group_sets["test"] or group_sets["validation"] & group_sets["test"]:
        raise AssertionError("实体分组发生泄漏")
    return result


class FeatureImageDataset(Dataset):
    def __init__(
        self,
        samples: list[SampleRecord],
        transform: transforms.Compose,
        augmented: bool = False,
        augment_copies: int = 0,
    ) -> None:
        self.transform = transform
        self.entries: list[tuple[SampleRecord, int]] = []
        copies = 1 + augment_copies if augmented else 1
        for sample in samples:
            for copy_index in range(copies):
                self.entries.append((sample, copy_index))

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, index: int) -> dict[str, object]:
        sample, copy_index = self.entries[index]
        image_path = PROJECT_ROOT / sample.path
        with Image.open(image_path) as image:
            tensor = self.transform(image.convert("RGB"))
        return {
            "image": tensor,
            "label": float(sample.label),
            "specimen_id": sample.specimen_id,
            "path": sample.path,
            "copy_index": copy_index,
        }


def make_transforms(image_size: int) -> tuple[transforms.Compose, transforms.Compose]:
    training = transforms.Compose(
        [
            transforms.Resize((image_size, image_size), antialias=True),
            transforms.RandomHorizontalFlip(),
            transforms.RandomVerticalFlip(),
            transforms.RandomApply(
                [
                    transforms.RandomAffine(
                        degrees=15,
                        translate=(0.05, 0.05),
                        scale=(0.95, 1.05),
                        fill=0,
                    )
                ],
                p=0.8,
            ),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )
    evaluation = transforms.Compose(
        [
            transforms.Resize((image_size, image_size), antialias=True),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )
    return training, evaluation


def extract_features(
    backbone: nn.Module,
    dataset: Dataset,
    device: torch.device,
    batch_size: int,
) -> dict[str, object]:
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0)
    features: list[torch.Tensor] = []
    labels: list[torch.Tensor] = []
    specimen_ids: list[str] = []
    paths: list[str] = []
    copy_indices: list[int] = []
    backbone.eval()
    with torch.inference_mode():
        for batch in loader:
            output = backbone(batch["image"].to(device)).cpu()
            features.append(output)
            labels.append(batch["label"].float().reshape(-1, 1))
            specimen_ids.extend(batch["specimen_id"])
            paths.extend(batch["path"])
            copy_indices.extend(int(value) for value in batch["copy_index"])
    return {
        "features": torch.cat(features),
        "labels": torch.cat(labels),
        "specimen_ids": specimen_ids,
        "paths": paths,
        "copy_indices": copy_indices,
    }


def balanced_sample_weights(labels: torch.Tensor, specimen_ids: list[str]) -> torch.Tensor:
    entries_per_specimen = Counter(specimen_ids)
    label_by_specimen: dict[str, int] = {}
    for label, specimen_id in zip(labels.flatten().tolist(), specimen_ids, strict=True):
        label_by_specimen.setdefault(specimen_id, int(label))
    specimens_per_label = Counter(label_by_specimen.values())
    weights = [
        1.0 / (specimens_per_label[label_by_specimen[specimen_id]] * entries_per_specimen[specimen_id])
        for specimen_id in specimen_ids
    ]
    tensor = torch.tensor(weights, dtype=torch.float32).reshape(-1, 1)
    return tensor / tensor.mean()


def train_linear_head(
    train_bundle: dict[str, object],
    validation_bundle: dict[str, object],
    epochs: int,
    learning_rate: float,
    weight_decay: float,
    patience: int,
    device: torch.device,
) -> tuple[nn.Linear, dict[str, object]]:
    train_features = train_bundle["features"].to(device)
    train_labels = train_bundle["labels"].to(device)
    validation_features = validation_bundle["features"].to(device)
    validation_labels = validation_bundle["labels"].to(device)
    weights = balanced_sample_weights(train_bundle["labels"], train_bundle["specimen_ids"]).to(device)

    head = nn.Linear(train_features.shape[1], 1).to(device)
    optimizer = torch.optim.AdamW(head.parameters(), lr=learning_rate, weight_decay=weight_decay)
    criterion = nn.BCEWithLogitsLoss(reduction="none")
    best_state = copy.deepcopy(head.state_dict())
    best_validation_loss = math.inf
    best_epoch = 0
    stale_epochs = 0
    history: list[dict[str, float | int]] = []

    for epoch in range(1, epochs + 1):
        head.train()
        optimizer.zero_grad(set_to_none=True)
        train_logits = head(train_features)
        train_loss = (criterion(train_logits, train_labels) * weights).mean()
        train_loss.backward()
        optimizer.step()

        head.eval()
        with torch.inference_mode():
            validation_loss = criterion(head(validation_features), validation_labels).mean().item()
        history.append({"epoch": epoch, "train_loss": float(train_loss.item()), "validation_loss": validation_loss})
        if validation_loss < best_validation_loss - 1e-6:
            best_validation_loss = validation_loss
            best_epoch = epoch
            best_state = copy.deepcopy(head.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                break

    head.load_state_dict(best_state)
    head.eval()
    return head.cpu(), {
        "best_epoch": best_epoch,
        "best_validation_loss": best_validation_loss,
        "epochs_run": len(history),
        "history": history,
    }


def predict_bundle(head: nn.Module, bundle: dict[str, object]) -> list[dict[str, object]]:
    with torch.inference_mode():
        probabilities = torch.sigmoid(head(bundle["features"])).flatten().tolist()
    rows: list[dict[str, object]] = []
    for probability, label, specimen_id, path, copy_index in zip(
        probabilities,
        bundle["labels"].flatten().tolist(),
        bundle["specimen_ids"],
        bundle["paths"],
        bundle["copy_indices"],
        strict=True,
    ):
        if int(copy_index) == 0:
            rows.append(
                {
                    "path": path,
                    "specimen_id": specimen_id,
                    "label": int(label),
                    "probability": float(probability),
                }
            )
    return rows


def brightness_rows(samples: Iterable[SampleRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample in samples:
        with Image.open(PROJECT_ROOT / sample.path) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
        luminance = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
        rows.append(
            {
                "path": sample.path,
                "specimen_id": sample.specimen_id,
                "label": sample.label,
                "probability": 1.0 - float(luminance.mean()) / 255.0,
            }
        )
    return rows


def evaluate_rows(rows: list[dict[str, object]], threshold: float) -> dict[str, object]:
    image_metrics = binary_metrics(
        [int(row["label"]) for row in rows],
        [float(row["probability"]) for row in rows],
        threshold,
    )
    specimen_rows = aggregate_by_specimen(rows)
    specimen_metrics = binary_metrics(
        [int(row["label"]) for row in specimen_rows],
        [float(row["probability"]) for row in specimen_rows],
        threshold,
    )
    return {"image": image_metrics, "specimen": specimen_metrics}


def select_threshold(
    rows: list[dict[str, object]],
    max_ore_misdiscard_rate: float,
    unit: str,
) -> tuple[float, dict[str, float | int]]:
    selected_rows = aggregate_by_specimen(rows) if unit == "specimen" else rows
    return choose_risk_threshold(
        [int(row["label"]) for row in selected_rows],
        [float(row["probability"]) for row in selected_rows],
        max_ore_misdiscard_rate=max_ore_misdiscard_rate,
    )


def write_split_csv(path: Path, splits: dict[str, list[SampleRecord]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["split", "path", "label", "specimen_id", "fold"])
        writer.writeheader()
        for split_name, rows in splits.items():
            for row in rows:
                writer.writerow(
                    {
                        "split": split_name,
                        "path": row.path,
                        "label": row.label,
                        "specimen_id": row.specimen_id,
                        "fold": row.fold,
                    }
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="训练冻结 ResNet50 特征骨干的最小热成像分类基线")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "manifest.csv")
    parser.add_argument("--test-fold", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--augment-copies", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--head-epochs", type=int, default=400)
    parser.add_argument("--head-lr", type=float, default=1e-2)
    parser.add_argument("--weight-decay", type=float, default=1e-3)
    parser.add_argument("--patience", type=int, default=60)
    parser.add_argument("--max-ore-misdiscard-rate", type=float, default=0.0)
    parser.add_argument(
        "--threshold-unit",
        choices=("image", "specimen"),
        default="image",
        help="演示机单帧决策默认按 image 约束误抛；多视角融合系统可改为 specimen",
    )
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "artifacts" / "resnet50_baseline_fold0")
    parser.add_argument("--random-backbone", action="store_true", help="仅用于离线冒烟测试，不下载 ImageNet 权重")
    args = parser.parse_args()

    set_seed(args.seed)
    samples = load_manifest(args.manifest)
    folds = sorted({sample.fold for sample in samples})
    if args.test_fold not in folds:
        raise ValueError(f"test-fold 必须属于 {folds}")
    splits = split_by_specimen(samples, args.test_fold, args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_split_csv(args.output_dir / "split.csv", splits)

    split_summary = {
        name: {
            "images": len(rows),
            "specimens": len({row.specimen_id for row in rows}),
            "specimens_by_label": dict(Counter(row.label_name for row in {item.specimen_id: item for item in rows}.values())),
        }
        for name, rows in splits.items()
    }
    print(json.dumps(split_summary, ensure_ascii=False, indent=2))

    weights = None if args.random_backbone else models.ResNet50_Weights.DEFAULT
    print(f"加载 ResNet50 骨干：{'随机权重' if weights is None else 'ImageNet 预训练权重'}")
    backbone = models.resnet50(weights=weights)
    feature_dimension = backbone.fc.in_features
    backbone.fc = nn.Identity()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    backbone.to(device).eval()
    training_transform, evaluation_transform = make_transforms(args.image_size)

    print(f"提取训练特征，设备：{device}")
    train_bundle = extract_features(
        backbone,
        FeatureImageDataset(splits["train"], training_transform, augmented=True, augment_copies=args.augment_copies),
        device,
        args.batch_size,
    )
    validation_bundle = extract_features(
        backbone,
        FeatureImageDataset(splits["validation"], evaluation_transform),
        device,
        args.batch_size,
    )
    test_bundle = extract_features(
        backbone,
        FeatureImageDataset(splits["test"], evaluation_transform),
        device,
        args.batch_size,
    )

    head, training_info = train_linear_head(
        train_bundle,
        validation_bundle,
        epochs=args.head_epochs,
        learning_rate=args.head_lr,
        weight_decay=args.weight_decay,
        patience=args.patience,
        device=device,
    )
    validation_rows = predict_bundle(head, validation_bundle)
    test_rows = predict_bundle(head, test_bundle)
    threshold, validation_threshold_metrics = select_threshold(
        validation_rows,
        args.max_ore_misdiscard_rate,
        args.threshold_unit,
    )
    model_evaluation = {
        "threshold": threshold,
        "threshold_unit": args.threshold_unit,
        "validation_threshold_metrics": validation_threshold_metrics,
        "validation": evaluate_rows(validation_rows, threshold),
        "test": evaluate_rows(test_rows, threshold),
    }

    brightness_validation = brightness_rows(splits["validation"])
    brightness_test = brightness_rows(splits["test"])
    brightness_threshold, brightness_validation_threshold_metrics = select_threshold(
        brightness_validation,
        args.max_ore_misdiscard_rate,
        args.threshold_unit,
    )
    brightness_evaluation = {
        "score_definition": "1 - mean_luminance / 255",
        "threshold": brightness_threshold,
        "validation_threshold_metrics": brightness_validation_threshold_metrics,
        "validation": evaluate_rows(brightness_validation, brightness_threshold),
        "test": evaluate_rows(brightness_test, brightness_threshold),
    }

    backbone.cpu()
    backbone.fc = nn.Linear(feature_dimension, 1)
    backbone.fc.load_state_dict(head.state_dict())
    checkpoint_path = args.output_dir / "model.pt"
    torch.save(
        {
            "model_name": "resnet50",
            "state_dict": backbone.state_dict(),
            "image_size": args.image_size,
            "mean": IMAGENET_MEAN,
            "std": IMAGENET_STD,
            "waste_threshold": threshold,
            "threshold_unit": args.threshold_unit,
            "labels": {"ORE": 0, "WASTE": 1},
            "test_fold": args.test_fold,
            "seed": args.seed,
            "pretrained_backbone": not args.random_backbone,
        },
        checkpoint_path,
    )

    prediction_path = args.output_dir / "test_predictions.csv"
    with prediction_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["path", "specimen_id", "label", "probability"])
        writer.writeheader()
        writer.writerows(test_rows)

    validation_prediction_path = args.output_dir / "validation_predictions.csv"
    with validation_prediction_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["path", "specimen_id", "label", "probability"])
        writer.writeheader()
        writer.writerows(validation_rows)

    report = {
        "model": "ResNet50 ImageNet frozen feature extractor + linear waste head",
        "prototype_warning": "仅有 24 个独立实体，本结果只用于技术链路验证。",
        "device": str(device),
        "split": split_summary,
        "training": training_info,
        "resnet50": model_evaluation,
        "brightness_baseline": brightness_evaluation,
        "checkpoint": str(checkpoint_path.resolve()),
    }
    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"resnet50": model_evaluation, "brightness": brightness_evaluation}, ensure_ascii=False, indent=2))
    print(f"模型：{checkpoint_path}")
    print(f"报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
