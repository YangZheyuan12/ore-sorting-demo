from __future__ import annotations

from dataclasses import dataclass

from .domain import SortItem


HARDWARE_BAUDRATE = 9600
HARDWARE_STOP_COMMAND = "BATCH 0"


@dataclass(frozen=True)
class ProtocolMessage:
    kind: str
    fields: tuple[str, ...]
    raw: str


def batch_commands(items: list[SortItem]) -> list[str]:
    """生成实物固件 sorter_minimal_tail3s_servo_home 使用的批次命令。"""
    commands = [f"BATCH {len(items)}"]
    commands.extend(item.label.value for item in items)
    commands.append("END")
    return commands


def parse_line(line: str) -> ProtocolMessage:
    cleaned = line.strip()
    if not cleaned:
        raise ValueError("空串口消息")
    tokens = tuple(cleaned.split())
    kind = tokens[0].upper().rstrip(":")
    return ProtocolMessage(kind=kind, fields=tokens[1:], raw=cleaned)


def validate_device_message(message: ProtocolMessage) -> bool:
    allowed = {"READY", "OK", "QUEUED", "ARMED", "RUNNING", "IN", "OUT", "DONE", "WARN", "ERROR"}
    return message.kind in allowed
