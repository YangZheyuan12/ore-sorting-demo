from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..classifier_adapter import ClassificationResult
from ..domain import MaterialClass


@dataclass(frozen=True)
class OnnxPrediction:
    waste_probability: float
    label: MaterialClass
    confidence: float
    threshold: float


class OnnxOreClassifier:
    """ONNX Runtime 图像分类适配器；异常或低置信度时保守返回目标矿。"""

    def __init__(self, model_path: Path, metadata_path: Path | None = None) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("请先安装 requirements-ml.txt 中的 onnxruntime") from exc

        self.model_path = Path(model_path)
        metadata_path = metadata_path or self.model_path.with_suffix(".metadata.json")
        self.metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        self.image_size = int(self.metadata["input_shape"][-1])
        self.mean = np.asarray(self.metadata["mean"], dtype=np.float32).reshape(1, 1, 3)
        self.std = np.asarray(self.metadata["std"], dtype=np.float32).reshape(1, 1, 3)
        self.threshold = float(self.metadata["waste_threshold"])
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])

    def _preprocess(self, image_path: str | Path) -> np.ndarray:
        with Image.open(image_path) as image:
            image = image.convert("RGB").resize((self.image_size, self.image_size), Image.Resampling.BILINEAR)
            array = np.asarray(image, dtype=np.float32) / 255.0
        array = (array - self.mean) / self.std
        return np.transpose(array, (2, 0, 1))[None, ...].astype(np.float32)

    def predict(self, image_path: str | Path) -> OnnxPrediction:
        image = self._preprocess(image_path)
        logit = float(self.session.run(["waste_logit"], {"image": image})[0].reshape(-1)[0])
        waste_probability = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, logit))))
        if waste_probability >= self.threshold:
            label = MaterialClass.WASTE
            confidence = waste_probability
        else:
            label = MaterialClass.ORE
            confidence = 1.0 - waste_probability
        return OnnxPrediction(waste_probability, label, confidence, self.threshold)

    def classify(self, image_path: str | Path) -> ClassificationResult:
        prediction = self.predict(image_path)
        return ClassificationResult(
            label=prediction.label,
            confidence=prediction.confidence,
            source="RESNET50_ONNX_BASELINE",
        )
