from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from sorting_demo.ml.manifest import discover_class_images, write_manifest
from sorting_demo.ml.metrics import aggregate_by_specimen, binary_metrics, choose_risk_threshold


class ManifestTests(unittest.TestCase):
    def test_manifest_assigns_every_specimen_to_one_fold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ore = root / "raw" / "ore"
            waste = root / "raw" / "waste"
            ore.mkdir(parents=True)
            waste.mkdir(parents=True)
            for class_index, class_root in enumerate((ore, waste)):
                for specimen in range(1, 5):
                    for view in range(1, 3):
                        Image.new("RGB", (16, 16), (specimen * 20, view * 30, class_index * 80)).save(
                            class_root / f"{specimen}-{view}.jpg"
                        )
            records = discover_class_images(ore, "ORE") + discover_class_images(waste, "WASTE")
            samples = write_manifest(records, root, root / "manifest.csv", n_folds=2, seed=7)
            folds_by_specimen: dict[str, set[int]] = {}
            for sample in samples:
                folds_by_specimen.setdefault(sample.specimen_id, set()).add(sample.fold)
            self.assertTrue(all(len(folds) == 1 for folds in folds_by_specimen.values()))
            self.assertEqual({sample.specimen_id for sample in samples if sample.label == 0}, {
                "ore_001", "ore_002", "ore_003", "ore_004"
            })


class MetricsTests(unittest.TestCase):
    def test_risk_threshold_can_forbid_ore_misdiscard(self) -> None:
        labels = [0, 0, 1, 1]
        probabilities = [0.1, 0.4, 0.6, 0.9]
        threshold, metrics = choose_risk_threshold(labels, probabilities, max_ore_misdiscard_rate=0.0)
        self.assertGreater(threshold, 0.4)
        self.assertEqual(metrics["ore_misdiscard_rate"], 0.0)
        self.assertEqual(metrics["waste_recall"], 1.0)

    def test_specimen_aggregation_uses_mean_probability(self) -> None:
        rows = [
            {"specimen_id": "ore_001", "label": 0, "probability": 0.1},
            {"specimen_id": "ore_001", "label": 0, "probability": 0.3},
            {"specimen_id": "waste_001", "label": 1, "probability": 0.8},
        ]
        aggregated = aggregate_by_specimen(rows)
        self.assertAlmostEqual(float(aggregated[0]["probability"]), 0.2)
        metrics = binary_metrics(
            [int(row["label"]) for row in aggregated],
            [float(row["probability"]) for row in aggregated],
            threshold=0.5,
        )
        self.assertEqual(metrics["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
