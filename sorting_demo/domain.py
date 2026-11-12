from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from math import inf
from typing import Any, Deque, Iterable


class MaterialClass(str, Enum):
    ORE = "ORE"
    WASTE = "WASTE"

    @property
    def chinese(self) -> str:
        return "目标矿" if self is MaterialClass.ORE else "废石"


class MachineState(str, Enum):
    IDLE = "IDLE"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    ESTOP = "ESTOP"


class ErrorCode(str, Enum):
    EXTRA_OBJECT = "EXTRA_OBJECT"
    QUEUE_EMPTY = "QUEUE_EMPTY"
    SEQUENCE_ERROR = "SEQUENCE_ERROR"
    ESTOP = "ESTOP"


@dataclass(frozen=True)
class SortItem:
    item_id: str
    label: MaterialClass
    confidence: float = 1.0
    source: str = "DEMO"


@dataclass(frozen=True)
class MachineEvent:
    sequence: int
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SortAction:
    item: SortItem
    servo_target: MaterialClass


@dataclass
class SensorGate:
    """抑制传感器抖动；硬件端仍应独立做同样的去抖与恢复。"""

    recovery_seconds: float = 0.2
    last_accepted_at: float = -inf

    def accept(self, now: float) -> bool:
        if now - self.last_accepted_at < self.recovery_seconds:
            return False
        self.last_accepted_at = now
        return True

    def reset(self) -> None:
        self.last_accepted_at = -inf


class SortingMachine:
    """与机械说明一致的 FIFO 分选状态机。

    入口传感器只计数；出口传感器才弹出 FIFO 队首并决定滑槽方向。
    所有 GUI、仿真和串口实现都应复用这一语义。
    """

    def __init__(self, sensor_recovery_seconds: float = 0.2) -> None:
        self.entry_gate = SensorGate(sensor_recovery_seconds)
        self.exit_gate = SensorGate(sensor_recovery_seconds)
        self.events: list[MachineEvent] = []
        self._event_sequence = 0
        self.queue: Deque[SortItem] = deque()
        self.state = MachineState.IDLE
        self.error: ErrorCode | None = None
        self.expected_batch = 0
        self.in_count = 0
        self.out_count = 0
        self.ore_count = 0
        self.waste_count = 0
        self.servo_target = MaterialClass.ORE

    def _emit(self, kind: str, message: str, **data: Any) -> None:
        self._event_sequence += 1
        self.events.append(MachineEvent(self._event_sequence, kind, message, data))

    def reset(self) -> None:
        self.queue.clear()
        self.state = MachineState.IDLE
        self.error = None
        self.expected_batch = 0
        self.in_count = 0
        self.out_count = 0
        self.ore_count = 0
        self.waste_count = 0
        self.servo_target = MaterialClass.ORE
        self.entry_gate.reset()
        self.exit_gate.reset()
        self._emit("STATE", "系统复位，滑槽回到目标矿默认位", state=self.state.value)

    def load_batch(self, items: Iterable[SortItem]) -> None:
        if self.state is MachineState.RUNNING:
            raise RuntimeError("运行中不能覆盖批次")
        self.queue = deque(items)
        self.expected_batch = len(self.queue)
        self.in_count = self.out_count = 0
        self.ore_count = self.waste_count = 0
        self.error = None
        self.servo_target = MaterialClass.ORE
        self.entry_gate.reset()
        self.exit_gate.reset()
        self.state = MachineState.READY
        self._emit("BATCH", f"已装载 {self.expected_batch} 条演示分类结果", count=self.expected_batch)

    def append_item(self, item: SortItem) -> None:
        if self.state not in {MachineState.IDLE, MachineState.READY}:
            raise RuntimeError("只能在待机阶段增加演示矿石")
        if self.state is MachineState.IDLE:
            self.state = MachineState.READY
        self.queue.append(item)
        self.expected_batch += 1
        self._emit("QUEUE", f"加入 {item.item_id}：{item.label.chinese}", item_id=item.item_id)

    def start(self) -> None:
        if self.state is not MachineState.READY:
            raise RuntimeError(f"当前状态 {self.state.value} 不能启动")
        if not self.queue:
            raise RuntimeError("批次为空；如需测试空队列，请使用诊断模式")
        self.state = MachineState.RUNNING
        self._emit("STATE", "传送带启动", state=self.state.value)

    def start_diagnostic_run(self) -> None:
        """仅供验收演示：启动空 FIFO，以验证出口误触发保护。"""
        self.load_batch([])
        self.state = MachineState.RUNNING
        self._emit("STATE", "诊断模式启动（空 FIFO）", state=self.state.value)

    def stop(self) -> None:
        if self.state is MachineState.RUNNING:
            self.state = MachineState.READY
            self.servo_target = MaterialClass.ORE
            self._emit("STATE", "传送带停止，滑槽回默认位", state=self.state.value)

    def emergency_stop(self) -> None:
        self.state = MachineState.ESTOP
        self.error = ErrorCode.ESTOP
        self.servo_target = MaterialClass.ORE
        self._emit("ERROR", "急停触发：电机停止，滑槽回默认位", code=self.error.value)

    def _fail(self, code: ErrorCode, message: str) -> None:
        self.state = MachineState.ERROR
        self.error = code
        self.servo_target = MaterialClass.ORE
        self._emit("ERROR", message, code=code.value)

    def trigger_entry(self, now: float) -> bool:
        if self.state is not MachineState.RUNNING:
            return False
        if not self.entry_gate.accept(now):
            self._emit("FILTER", "入口传感器抖动已忽略", sensor="ENTRY")
            return False
        self.in_count += 1
        self._emit("ENTRY", f"入口计数 {self.in_count}/{self.expected_batch}", count=self.in_count)
        if self.in_count > self.expected_batch:
            self._fail(ErrorCode.EXTRA_OBJECT, "检测到批次外多放矿石，已停止传送带")
        return True

    def trigger_exit(self, now: float) -> SortAction | None:
        if self.state is not MachineState.RUNNING:
            return None
        if not self.exit_gate.accept(now):
            self._emit("FILTER", "出口传感器抖动已忽略", sensor="EXIT")
            return None
        if not self.queue:
            self._fail(ErrorCode.QUEUE_EMPTY, "出口触发但 FIFO 为空，已停止传送带")
            return None
        if self.out_count >= self.in_count:
            self._fail(ErrorCode.SEQUENCE_ERROR, "出口计数领先于入口计数，已停止传送带")
            return None

        item = self.queue.popleft()
        self.out_count += 1
        if item.label is MaterialClass.ORE:
            self.ore_count += 1
        else:
            self.waste_count += 1
        self._emit(
            "EXIT",
            f"出口弹出 {item.item_id}，分类为{item.label.chinese}",
            item_id=item.item_id,
            label=item.label.value,
            count=self.out_count,
        )
        if self.out_count == self.expected_batch and not self.queue:
            self.state = MachineState.COMPLETE
            self._emit(
                "DONE",
                f"批次完成：目标矿 {self.ore_count}，废石 {self.waste_count}",
                ore=self.ore_count,
                waste=self.waste_count,
            )
        return SortAction(item=item, servo_target=item.label)

    def move_servo(self, target: MaterialClass) -> None:
        if self.state in {MachineState.ERROR, MachineState.ESTOP}:
            return
        self.servo_target = target
        self._emit("SERVO", f"滑槽转向{target.chinese}", target=target.value)

    def return_servo_default(self) -> None:
        if self.servo_target is not MaterialClass.ORE:
            self.servo_target = MaterialClass.ORE
            self._emit("SERVO", "滑槽回到目标矿默认位", target=MaterialClass.ORE.value)

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "error": self.error.value if self.error else None,
            "expected": self.expected_batch,
            "in_count": self.in_count,
            "out_count": self.out_count,
            "ore_count": self.ore_count,
            "waste_count": self.waste_count,
            "servo_target": self.servo_target.value,
            "queue": [item.item_id for item in self.queue],
        }
