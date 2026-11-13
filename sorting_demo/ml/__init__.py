"""矿石热成像分类的最小训练与部署组件。"""

from .manifest import SampleRecord, load_manifest
from .metrics import aggregate_by_specimen, binary_metrics, choose_risk_threshold

__all__ = [
    "SampleRecord",
    "aggregate_by_specimen",
    "binary_metrics",
    "choose_risk_threshold",
    "load_manifest",
]
