from __future__ import annotations

import csv
import hashlib
import random
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
NAME_PATTERN = re.compile(r"^(?P<specimen>\d+)-(?P<view>\d+)$")
LABELS = {"ORE": 0, "WASTE": 1}


@dataclass(frozen=True)
class SampleRecord:
    path: str
    label: int
    label_name: str
    specimen_id: str
    view_id: str
    fold: int
    width: int
    height: int
    sha256: str


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_class_images(class_root: Path, label_name: str) -> list[dict[str, object]]:
    label_name = label_name.upper()
    if label_name not in LABELS:
        raise ValueError(f"未知类别：{label_name}")

    records: list[dict[str, object]] = []
    for path in sorted(p for p in class_root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES):
        match = NAME_PATTERN.match(path.stem)
        if not match:
            raise ValueError(f"图片文件名必须为 实体编号-视角编号：{path.name}")
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            if image.mode != "RGB":
                raise ValueError(f"当前基线要求 RGB 图片，实际为 {image.mode}：{path}")
        specimen = int(match.group("specimen"))
        records.append(
            {
                "absolute_path": path,
                "label": LABELS[label_name],
                "label_name": label_name,
                "specimen_id": f"{label_name.lower()}_{specimen:03d}",
                "view_id": match.group("view"),
                "width": width,
                "height": height,
                "sha256": file_sha256(path),
            }
        )
    if not records:
        raise ValueError(f"没有在 {class_root} 中发现图片")
    return records


def assign_group_folds(
    records: Iterable[dict[str, object]],
    n_folds: int = 4,
    seed: int = 42,
) -> dict[str, int]:
    if n_folds < 2:
        raise ValueError("n_folds 至少为 2")
    groups_by_label: dict[int, set[str]] = defaultdict(set)
    for record in records:
        groups_by_label[int(record["label"])].add(str(record["specimen_id"]))

    assignment: dict[str, int] = {}
    for label, groups in sorted(groups_by_label.items()):
        ordered = sorted(groups)
        random.Random(seed + label * 1009).shuffle(ordered)
        if len(ordered) < n_folds:
            raise ValueError(f"类别 {label} 只有 {len(ordered)} 个实体，无法划分 {n_folds} 折")
        for index, specimen_id in enumerate(ordered):
            assignment[specimen_id] = index % n_folds
    return assignment


def write_manifest(
    records: Iterable[dict[str, object]],
    project_root: Path,
    output_path: Path,
    n_folds: int = 4,
    seed: int = 42,
) -> list[SampleRecord]:
    rows = list(records)
    fold_by_specimen = assign_group_folds(rows, n_folds=n_folds, seed=seed)
    samples: list[SampleRecord] = []
    seen_hashes: dict[str, str] = {}
    for row in rows:
        image_path = Path(row["absolute_path"])
        digest = str(row["sha256"])
        previous = seen_hashes.get(digest)
        if previous is not None:
            raise ValueError(f"发现完全重复图片：{previous} 与 {image_path}")
        seen_hashes[digest] = str(image_path)
        relative = image_path.resolve().relative_to(project_root.resolve()).as_posix()
        specimen_id = str(row["specimen_id"])
        samples.append(
            SampleRecord(
                path=relative,
                label=int(row["label"]),
                label_name=str(row["label_name"]),
                specimen_id=specimen_id,
                view_id=str(row["view_id"]),
                fold=fold_by_specimen[specimen_id],
                width=int(row["width"]),
                height=int(row["height"]),
                sha256=digest,
            )
        )

    samples.sort(key=lambda x: (x.label, x.specimen_id, int(x.view_id)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(samples[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(sample) for sample in samples)
    return samples


def load_manifest(path: Path) -> list[SampleRecord]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        rows = [
            SampleRecord(
                path=row["path"],
                label=int(row["label"]),
                label_name=row["label_name"],
                specimen_id=row["specimen_id"],
                view_id=row["view_id"],
                fold=int(row["fold"]),
                width=int(row["width"]),
                height=int(row["height"]),
                sha256=row["sha256"],
            )
            for row in reader
        ]
    if not rows:
        raise ValueError(f"清单为空：{path}")
    return rows


def manifest_summary(samples: Iterable[SampleRecord]) -> dict[str, object]:
    rows = list(samples)
    return {
        "images": len(rows),
        "images_by_label": dict(Counter(row.label_name for row in rows)),
        "specimens": len({row.specimen_id for row in rows}),
        "specimens_by_label": {
            label: len({row.specimen_id for row in rows if row.label_name == label})
            for label in sorted({row.label_name for row in rows})
        },
        "images_by_fold": dict(Counter(str(row.fold) for row in rows)),
        "specimens_by_fold": {
            str(fold): len({row.specimen_id for row in rows if row.fold == fold})
            for fold in sorted({row.fold for row in rows})
        },
        "dimensions": dict(Counter(f"{row.width}x{row.height}" for row in rows)),
    }
