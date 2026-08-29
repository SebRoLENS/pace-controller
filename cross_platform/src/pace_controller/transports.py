"""Interchangeable SCPI transports for Ethernet, RS-232, and offline tests."""

from __future__ import annotations

import re
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from .models import ConnectionConfig, ConnectionKind


PACE_TCP_COMMAND_TERMINATOR = b"\r\n"


class TransportError(RuntimeError):
    pass


class ScpiTransport(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def write(self, command: str) -> None: ...

    @abstractmethod
    def query(self, command: str) -> str: ...


class TcpTransport(ScpiTransport):
    def __init__(
        self,
        host: str,
        port: int = 5025,
        timeout: float = 2.0,
        source_address: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.source_address = source_address
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def connect(self) -> None:
        self.close()
        connection: socket.socket | None = None
        try:
            source = (self.source_address, 0) if self.source_address else None
            connection = socket.create_connection(
                (self.host, self.port), self.timeout, source_address=source
            )
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.settimeout(self.timeout)
            self._socket = connection
        except OSError as exc:
            if connection is not None:
                connection.close()
            via = f" via {self.source_address}" if self.source_address else ""
            raise TransportError(f"TCP {self.host}:{self.port}{via}: {exc}") from exc

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()
        self._socket = None
        self._buffer.clear()

    def write(self, command: str) -> None:
        with self._lock:
            self._send(command)

    def query(self, command: str) -> str:
        with self._lock:
            self._send(command)
            return self._read_line()

    def _send(self, command: str) -> None:
        if self._socket is None:
            raise TransportError("TCP transport is not connected")
        try:
            # K0472 requires LF (ASCII 10) to terminate SCPI commands.  CRLF
            # also preserves the byte sequence used by the validated legacy
            # Windows controller.
            self._socket.sendall(
                command.rstrip("\r\n").encode("ascii")
                + PACE_TCP_COMMAND_TERMINATOR
            )
        except OSError as exc:
            raise TransportError(f"TCP write failed: {exc}") from exc

    def _read_line(self) -> str:
        if self._socket is None:
            raise TransportError("TCP transport is not connected")
        deadline = time.monotonic() + self.timeout
        while True:
            for marker in (b"\r", b"\n"):
                position = self._buffer.find(marker)
                if position >= 0:
                    raw = bytes(self._buffer[:position])
                    del self._buffer[: position + 1]
                    while self._buffer[:1] in (b"\r", b"\n"):
                        del self._buffer[:1]
                    if not raw:
                        # A CRLF reply can be split across TCP packets.  If
                        # CR completed the previous response, ignore the
                        # delayed LF instead of returning an empty response.
                        continue
                    return raw.decode("ascii", errors="replace").strip()
            if time.monotonic() >= deadline:
                raise TransportError("Timed out waiting for the PACE response")
            try:
                chunk = self._socket.recv(4096)
            except socket.timeout as exc:
                raise TransportError("Timed out waiting for the PACE response") from exc
            except OSError as exc:
                raise TransportError(f"TCP read failed: {exc}") from exc
            if not chunk:
                raise TransportError("PACE closed the TCP connection")
            self._buffer.extend(chunk)


class SerialTransport(ScpiTransport):
    def __init__(
        self,
        port: str,
        baud_rate: int = 9600,
        parity: str = "N",
        flow_control: str = "none",
        terminator: str = "\r",
        timeout: float = 2.0,
    ) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.parity = parity
        self.flow_control = flow_control
        self.terminator = terminator
        self.timeout = timeout
        self._serial = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        try:
            import serial

            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                bytesize=serial.EIGHTBITS,
                parity={"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}[self.parity],
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
                write_timeout=self.timeout,
                xonxoff=self.flow_control == "xonxoff",
                rtscts=self.flow_control == "rtscts",
            )
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        except (ImportError, OSError, ValueError) as exc:
            raise TransportError(f"Serial port {self.port}: {exc}") from exc

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except OSError:
                pass
        self._serial = None

    def write(self, command: str) -> None:
        with self._lock:
            self._send(command)

    def query(self, command: str) -> str:
        with self._lock:
            self._send(command)
            return self._read_line()

    def _send(self, command: str) -> None:
        if self._serial is None or not self._serial.is_open:
            raise TransportError("Serial transport is not connected")
        try:
            payload = command.rstrip("\r\n").encode("ascii") + self.terminator.encode("ascii")
            self._serial.write(payload)
            self._serial.flush()
        except OSError as exc:
            raise TransportError(f"Serial write failed: {exc}") from exc

    def _read_line(self) -> str:
        if self._serial is None or not self._serial.is_open:
            raise TransportError("Serial transport is not connected")
        deadline = time.monotonic() + self.timeout
        response = bytearray()
        while time.monotonic() < deadline:
            try:
                value = self._serial.read(1)
            except OSError as exc:
                raise TransportError(f"Serial read failed: {exc}") from exc
            if not value:
                continue
            if value in (b"\r", b"\n"):
                if response:
                    return response.decode("ascii", errors="replace").strip()
                continue
            response.extend(value)
        raise TransportError("Timed out waiting for the PACE serial response")


@dataclass(slots=True)
class SerialPortInfo:
    device: str
    description: str


def list_serial_ports() -> list[SerialPortInfo]:
    try:
        from serial.tools import list_ports

        return [
            SerialPortInfo(port.device, port.description or port.device)
            for port in sorted(list_ports.comports(), key=lambda item: item.device)
        ]
    except ImportError:
        return []


class SimulatorTransport(ScpiTransport):
    """Deterministic PACE-like transport used for tests and offline screenshots."""

    def __init__(self) -> None:
        self.connected = False
        self.current = 25.0
        self.target = 25.0
        self.positive_source = 62.0
        self.negative_source = 0.0
        self.slew = 0.1
        self.control = False
        self.mode = "ACTIVE"
        self.overshoot = False
        self.in_limit_percent = 0.01
        self.in_limit_time = 2
        self.vent_rate = 0.1
        self._updated = time.monotonic()

    def connect(self) -> None:
        self.connected = True
        self._updated = time.monotonic()

    def close(self) -> None:
        self.connected = False

    def write(self, command: str) -> None:
        self._require_connected()
        self._advance()
        normalized = self._normalize(command)
        if normalized.startswith(":OUTP") and ":STAT " in normalized:
            value = normalized.rsplit(" ", 1)[-1]
            self.control = value in {"1", "ON"}
        elif normalized.startswith(":OUTP") and ":MODE " in normalized:
            self.mode = normalized.rsplit(" ", 1)[-1]
        elif ":PRES:SLEW:MODE " in normalized:
            pass
        elif ":PRES:SLEW:OVER " in normalized:
            self.overshoot = normalized.rsplit(" ", 1)[-1] in {"1", "ON"}
        elif ":PRES:SLEW " in normalized and ":SENS" not in normalized:
            self.slew = max(0.000001, float(normalized.rsplit(" ", 1)[-1]))
        elif ":PRES:INL:TIME " in normalized:
            self.in_limit_time = int(float(normalized.rsplit(" ", 1)[-1]))
        elif ":PRES:INL " in normalized and ":SENS" not in normalized:
            self.in_limit_percent = float(normalized.rsplit(" ", 1)[-1])
        elif ":VENT:RATE " in normalized:
            self.vent_rate = float(normalized.rsplit(" ", 1)[-1])
        elif ":VENT " in normalized:
            self.target = 0.0
            self.control = True
        elif re.match(r"^:SOUR\d*:PRES ", normalized):
            self.target = float(normalized.rsplit(" ", 1)[-1])

    def query(self, command: str) -> str:
        self._require_connected()
        self._advance()
        normalized = self._normalize(command)
        if normalized == "*IDN?":
            return "DRUCK,PACE6000,SIMULATOR,1.0"
        if normalized == ":SYST:ERR?":
            return '0,"No error"'
        if re.match(r"^:UNIT\d*:PRES\?$", normalized):
            return "BAR"
        if re.match(r"^:SENS\d*:PRES:CONT\?$", normalized):
            return f"{self.current:.9f}"
        if re.match(r"^:SOUR\d*:PRES\?$", normalized):
            return f"{self.target:.9f}"
        if re.match(r"^:OUTP\d*:STAT\?$", normalized):
            return "1" if self.control else "0"
        if re.match(r"^:OUTP\d*:MODE\?$", normalized):
            return self.mode
        if re.match(r"^:SOUR\d*:PRES:COMP1\?$", normalized):
            return f"{self.positive_source:.9f}"
        if re.match(r"^:SOUR\d*:PRES:COMP2\?$", normalized):
            return f"{self.negative_source:.9f}"
        if re.match(r"^:SENS\d*:PRES:INL\?$", normalized):
            return "1" if abs(self.current - self.target) <= 0.002 else "0"
        if re.match(r"^:SENS\d*:PRES:SLEW\?$", normalized):
            return f"{self.slew if self.control else 0.0:.9f}"
        if re.match(r"^:SOUR\d*:PRES:EFF\?$", normalized):
            effort = min(100.0, abs(self.target - self.current) * 5.0) if self.control else 2.6
            return f"{effort:.9f}"
        if re.match(r"^:SOUR\d*:PRES:SLEW\?$", normalized):
            return f"{self.slew:.9f}"
        if re.match(r"^:SOUR\d*:PRES:SLEW:MODE\?$", normalized):
            return "LIN"
        if re.match(r"^:SOUR\d*:PRES:SLEW:OVER\?$", normalized):
            return "1" if self.overshoot else "0"
        if re.match(r"^:SOUR\d*:PRES:INL\?$", normalized):
            return f"{self.in_limit_percent:.9f}"
        if re.match(r"^:SOUR\d*:PRES:INL:TIME\?$", normalized):
            return str(self.in_limit_time)
        if re.match(r"^:SOUR\d*:PRES:LEV:IMM:AMPL:VENT:RATE\?$", normalized):
            return f"{self.vent_rate:.9f}"
        if re.match(r"^:SOUR\d*:PRES:RANG:LOW\?$", normalized):
            return "0.0"
        if re.match(r"^:SOUR\d*:PRES:RANG\?$", normalized):
            return "200.0"
        return "0"

    def _require_connected(self) -> None:
        if not self.connected:
            raise TransportError("Simulator is not connected")

    def _advance(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        if not self.control:
            return
        difference = self.target - self.current
        increment = min(abs(difference), self.slew * elapsed)
        self.current += increment if difference >= 0 else -increment

    @staticmethod
    def _normalize(command: str) -> str:
        return " ".join(command.strip().upper().split())


def create_transport(
    config: ConnectionConfig, *, source_address: str | None = None
) -> ScpiTransport:
    if config.kind == ConnectionKind.ETHERNET:
        return TcpTransport(
            config.host,
            config.port,
            config.timeout,
            source_address=source_address,
        )
    if config.kind == ConnectionKind.SERIAL:
        if not config.serial_port:
            raise TransportError("No serial port selected")
        return SerialTransport(
            port=config.serial_port,
            baud_rate=config.baud_rate,
            parity=config.parity,
            flow_control=config.flow_control,
            terminator=config.terminator,
            timeout=config.timeout,
        )
    return SimulatorTransport()
