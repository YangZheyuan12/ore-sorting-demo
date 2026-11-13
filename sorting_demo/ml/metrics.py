from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping


def binary_metrics(labels: Iterable[int], probabilities: Iterable[float], threshold: float) -> dict[str, float | int]:
    pairs = [(int(label), float(probability)) for label, probability in zip(labels, probabilities, strict=True)]
    if not pairs:
        raise ValueError("无法对空数据计算指标")
    tp = sum(label == 1 and probability >= threshold for label, probability in pairs)
    fn = sum(label == 1 and probability < threshold for label, probability in pairs)
    tn = sum(label == 0 and probability < threshold for label, probability in pairs)
    fp = sum(label == 0 and probability >= threshold for label, probability in pairs)

    def divide(numerator: float, denominator: float) -> float:
        return numerator / denominator if denominator else 0.0

    waste_recall = divide(tp, tp + fn)
    ore_recall = divide(tn, tn + fp)
    precision = divide(tp, tp + fp)
    return {
        "threshold": float(threshold),
        "tp": tp,
        "fn": fn,
        "tn": tn,
        "fp": fp,
        "accuracy": divide(tp + tn, len(pairs)),
        "balanced_accuracy": (waste_recall + ore_recall) / 2.0,
        "waste_precision": precision,
        "waste_recall": waste_recall,
        "ore_recall": ore_recall,
        "ore_misdiscard_rate": divide(fp, fp + tn),
        "f1_waste": divide(2.0 * precision * waste_recall, precision + waste_recall),
    }


def aggregate_by_specimen(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["specimen_id"])].append(row)
    aggregated: list[dict[str, object]] = []
    for specimen_id, items in sorted(grouped.items()):
        labels = {int(item["label"]) for item in items}
        if len(labels) != 1:
            raise ValueError(f"实体 {specimen_id} 出现互相冲突的标签")
        aggregated.append(
            {
                "specimen_id": specimen_id,
                "label": labels.pop(),
                "probability": sum(float(item["probability"]) for item in items) / len(items),
                "views": len(items),
            }
        )
    return aggregated


def choose_risk_threshold(
    labels: Iterable[int],
    probabilities: Iterable[float],
    max_ore_misdiscard_rate: float = 0.0,
) -> tuple[float, dict[str, float | int]]:
    labels_list = [int(value) for value in labels]
    probabilities_list = [float(value) for value in probabilities]
    if len(labels_list) != len(probabilities_list) or not labels_list:
        raise ValueError("标签与概率必须非空且长度一致")
    unique = sorted(set(probabilities_list))
    candidates = [0.0]
    candidates.extend((left + right) / 2.0 for left, right in zip(unique, unique[1:]))
    candidates.extend([unique[-1], min(1.0, unique[-1] + 1e-6), 1.0])

    feasible: list[tuple[tuple[float, float, float], float, dict[str, float | int]]] = []
    for threshold in sorted(set(candidates)):
        metrics = binary_metrics(labels_list, probabilities_list, threshold)
        if float(metrics["ore_misdiscard_rate"]) <= max_ore_misdiscard_rate + 1e-12:
            score = (
                float(metrics["waste_recall"]),
                float(metrics["balanced_accuracy"]),
                -threshold,
            )
            feasible.append((score, threshold, metrics))
    if not feasible:
        raise RuntimeError("没有满足目标矿误抛约束的阈值")
    _, threshold, metrics = max(feasible, key=lambda item: item[0])
    return threshold, metrics
