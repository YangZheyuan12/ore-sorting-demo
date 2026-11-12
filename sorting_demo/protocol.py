from __future__ import annotations

from dataclasses import dataclass

from .domain import SortItem


@dataclass(frozen=True)
class ProtocolMessage:
    kind: str
    fields: tuple[str, ...]
    raw: str


def batch_commands(items: list[SortItem]) -> list[str]:
    commands = ["RESET", f"BATCH {len(items)}"]
    commands.extend(f"PUSH {item.item_id} {item.label.value}" for item in items)
    return commands


def parse_line(line: str) -> ProtocolMessage:
    cleaned = line.strip()
    if not cleaned:
        raise ValueError("空串口消息")
    tokens = tuple(cleaned.split())
    return ProtocolMessage(kind=tokens[0].upper(), fields=tokens[1:], raw=cleaned)


def validate_device_message(message: ProtocolMessage) -> bool:
    allowed = {"READY", "ACK", "EVENT", "STATE", "ERROR", "DONE", "PONG", "INFO"}
    return message.kind in allowed
