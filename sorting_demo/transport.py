from __future__ import annotations

from typing import Any


try:
    import serial  # type: ignore[import-not-found]
    from serial.tools import list_ports  # type: ignore[import-not-found]
except ImportError:  # 软件仿真不需要 pyserial
    serial = None
    list_ports = None


class SerialTransport:
    def __init__(self) -> None:
        self._port: Any = None
        self._buffer = ""

    @property
    def dependency_available(self) -> bool:
        return serial is not None

    @property
    def connected(self) -> bool:
        return bool(self._port and self._port.is_open)

    def ports(self) -> list[str]:
        if list_ports is None:
            return []
        return [port.device for port in list_ports.comports()]

    def connect(self, port_name: str, baudrate: int = 115200) -> None:
        if serial is None:
            raise RuntimeError("未安装 pyserial；纯软件演示不受影响")
        self.close()
        self._port = serial.Serial(port_name, baudrate=baudrate, timeout=0)

    def close(self) -> None:
        if self._port is not None:
            self._port.close()
        self._port = None
        self._buffer = ""

    def send(self, line: str) -> None:
        if not self.connected:
            raise RuntimeError("串口未连接")
        self._port.write((line.strip() + "\n").encode("ascii"))

    def poll(self) -> list[str]:
        if not self.connected:
            return []
        waiting = self._port.in_waiting
        if waiting:
            self._buffer += self._port.read(waiting).decode("utf-8", errors="replace")
        lines: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                lines.append(line)
        return lines
