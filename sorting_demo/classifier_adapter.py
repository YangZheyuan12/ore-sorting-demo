from __future__ import annotations

from dataclasses import dataclass

from .domain import MaterialClass, SortItem


@dataclass(frozen=True)
class ClassificationResult:
    label: MaterialClass
    confidence: float
    source: str


class ConservativeDecisionPolicy:
    """数据不足阶段的保守决策壳。

    当前演示输入是预设概率；后续模型只需输出 waste_probability，机械控制层无需改动。
    """

    def __init__(self, waste_threshold: float = 0.72) -> None:
        if not 0.5 <= waste_threshold <= 1.0:
            raise ValueError("waste_threshold 应在 [0.5, 1.0] 内")
        self.waste_threshold = waste_threshold

    def decide(self, waste_probability: float, source: str = "DEMO_PROBABILITY") -> ClassificationResult:
        if not 0.0 <= waste_probability <= 1.0:
            raise ValueError("概率必须在 [0, 1] 内")
        if waste_probability >= self.waste_threshold:
            return ClassificationResult(MaterialClass.WASTE, waste_probability, source)
        return ClassificationResult(MaterialClass.ORE, 1.0 - waste_probability, source)


def demo_batch() -> list[SortItem]:
    """固定、可复现的五块矿石验收序列，不声称代表模型精度。"""
    policy = ConservativeDecisionPolicy()
    probabilities = [0.08, 0.91, 0.18, 0.84, 0.12]
    items: list[SortItem] = []
    for index, probability in enumerate(probabilities, start=1):
        result = policy.decide(probability)
        items.append(
            SortItem(
                item_id=f"S{index:02d}",
                label=result.label,
                confidence=result.confidence,
                source=result.source,
            )
        )
    return items
