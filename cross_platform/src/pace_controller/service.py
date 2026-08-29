"""Background PACE control engine shared by Windows and Linux."""

from __future__ import annotations

import math
import queue
import re
import threading
import time
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QObject, Signal

from .models import (
    ConnectionConfig,
    ConnectionKind,
    ControlParameters,
    DeviceCapabilities,
    PressureStep,
    Telemetry,
)
from .network import (
    NetworkLease,
    configure_dedicated_adapter,
    restore_dedicated_adapter,
)
from .storage import DataLogger
from .transports import ScpiTransport, TransportError, create_transport


NUMBER_PATTERN = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")


def scpi_numbers(response: str) -> list[float]:
    values: list[float] = []
    for token in NUMBER_PATTERN.findall(response):
        try:
            values.append(float(token))
        except ValueError:
            pass
    return values


def scpi_number(response: str) -> float:
    values = scpi_numbers(response)
    if not values:
        raise ValueError(f"No numeric value in SCPI response: {response!r}")
    return values[-1]


def scpi_payload(response: str) -> str:
    value = response.strip().strip('"')
    if " " in value and value.startswith(":"):
        value = value.rsplit(" ", 1)[-1]
    return value.strip().strip('"')


def scpi_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("SCPI value must be finite")
    return format(value, ".12g")


class PaceService(QObject):
    """Serializes every device operation in one background thread."""

    connection_changed = Signal(bool, object)
    telemetry_received = Signal(object)
    parameters_received = Signal(object)
    automation_changed = Signal(object)
    log_line = Signal(str)
    alarm = Signal(object)
    busy_changed = Signal(bool)

    def __init__(
        self,
        minimum_source_margin_bar: float = 2.0,
        source_margin_rearm_bar: float = 2.2,
        poll_interval: float = 1.0,
    ) -> None:
        super().__init__()
        self.minimum_source_margin_bar = minimum_source_margin_bar
        self.source_margin_rearm_bar = source_margin_rearm_bar
        self.poll_interval = poll_interval
        self.logger = DataLogger()
        self._commands: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._transport: ScpiTransport | None = None
        self._network_lease: NetworkLease | None = None
        self._connected = False
        self._module = 1
        self._capabilities = DeviceCapabilities()
        self._telemetry = Telemetry()
        self._poll_failures = 0
        self._supply_interlock_latched = False
        self._automation: dict[str, Any] | None = None

    @property
    def telemetry(self) -> Telemetry:
        return self._telemetry

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PACE-service", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 4.0) -> None:
        self._commands.put(("shutdown", ()))
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def connect_device(self, config: ConnectionConfig, module: int) -> None:
        self._commands.put(("connect", (config, module)))

    def disconnect_device(self) -> None:
        self._commands.put(("disconnect", ()))

    def start_manual(
        self,
        step: PressureStep,
        parameters: ControlParameters,
        keep_control: bool,
    ) -> None:
        self._commands.put(("sequence", ("MANUAL", [step], parameters, keep_control)))

    def start_indenting(
        self, target_bar: float, slew_bar_s: float, parameters: ControlParameters
    ) -> None:
        steps = [
            PressureStep(target_bar, slew_bar_s, 120.0),
            PressureStep(0.0, slew_bar_s, 0.0),
        ]
        self._commands.put(("sequence", ("INDENTING", steps, parameters, False)))

    def start_routine(
        self,
        steps: list[PressureStep],
        parameters: ControlParameters,
        keep_control: bool,
    ) -> None:
        self._commands.put(("sequence", ("ROUTINE", steps, parameters, keep_control)))

    def stop_and_measure(self) -> None:
        self._commands.put(("measure", ()))

    def vent(self, parameters: ControlParameters) -> None:
        self._commands.put(("vent", (parameters,)))

    def reload_parameters(self) -> None:
        self._commands.put(("load_parameters", ()))

    def _run(self) -> None:
        next_poll = time.monotonic()
        while not self._stop.is_set():
            wait = max(0.0, next_poll - time.monotonic()) if self._connected else 0.25
            try:
                command, arguments = self._commands.get(timeout=min(wait, 0.25))
                if command == "shutdown":
                    break
                self._dispatch(command, arguments)
            except queue.Empty:
                pass
            except Exception as exc:  # final containment for the worker thread
                self._emit_alarm("device_error", error=str(exc))
                self._write_log(f"Command failed: {exc}")
                self.busy_changed.emit(False)

            now = time.monotonic()
            if self._connected and now >= next_poll:
                self._poll()
                self._process_automation(now)
                next_poll = now + self.poll_interval
        self._close_device(request_measure=True)

    def _dispatch(self, command: str, arguments: tuple[Any, ...]) -> None:
        if command == "connect":
            self._connect(*arguments)
        elif command == "disconnect":
            self._close_device(request_measure=True)
        elif command == "sequence":
            self._start_sequence(*arguments)
        elif command == "measure":
            self._cancel_automation()
            self._set_measure()
            self.busy_changed.emit(False)
            self.automation_changed.emit({"key": "automation_idle"})
        elif command == "vent":
            self._start_vent(*arguments)
        elif command == "load_parameters":
            self._load_parameters()

    def _connect(self, config: ConnectionConfig, module: int) -> None:
        self.busy_changed.emit(True)
        self.connection_changed.emit(False, {"key": "connecting"})
        self._close_device(request_measure=False, emit=False)
        self._module = int(module)
        try:
            self._transport = create_transport(config)
            try:
                self._transport.connect()
            except TransportError:
                if config.kind != ConnectionKind.ETHERNET or not config.auto_configure_network:
                    raise
                self._network_lease = configure_dedicated_adapter()
                self._write_log(
                    f"Prepared dedicated adapter {self._network_lease.interface} "
                    "and route for 192.168.10.1/24."
                )
                self._transport = create_transport(
                    config, source_address=self._network_lease.source_address
                )
                self._transport.connect()

            identity = self._query("*IDN?")
            if "PACE" not in identity.upper() and config.kind != ConnectionKind.SIMULATOR:
                raise TransportError(f"Unexpected instrument identity: {identity}")
            self._write("*CLS")
            self._write(f":UNIT{self._module}:PRES BAR")
            confirmed = scpi_payload(self._query(f":UNIT{self._module}:PRES?"))
            if "BAR" not in confirmed.upper():
                raise TransportError(f"PACE did not confirm bar units: {confirmed}")
            self._capabilities = DeviceCapabilities(
                identity=identity,
                range_min_bar=self._optional_number(
                    f":SOUR{self._module}:PRES:RANG:LOW?"
                ),
                range_max_bar=self._optional_number(f":SOUR{self._module}:PRES:RANG?"),
                module=self._module,
            )
            self._connected = True
            self._poll_failures = 0
            self._write_log(f"Connected through {config.kind.value}: {identity}")
            self.connection_changed.emit(
                True,
                {
                    "key": "connected",
                    "identity": identity,
                    "capabilities": self._capabilities,
                },
            )
            self._load_parameters()
            self._poll()
        except Exception as exc:
            self._close_device(request_measure=False, emit=False)
            self.connection_changed.emit(False, {"key": "connection_error", "error": str(exc)})
            self._write_log(f"Connection failed: {exc}")
        finally:
            self.busy_changed.emit(False)

    def _close_device(self, request_measure: bool, emit: bool = True) -> None:
        self._cancel_automation()
        if request_measure and self._connected:
            try:
                self._set_measure()
            except Exception as exc:
                self._write_log(f"Could not confirm MEASURE during disconnect: {exc}")
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._connected = False
        restore_dedicated_adapter(self._network_lease)
        self._network_lease = None
        if emit:
            self.connection_changed.emit(False, {"key": "disconnected"})
            self.busy_changed.emit(False)

    def _load_parameters(self) -> None:
        self._require_connected()
        values = {
            "target_bar": self._optional_number(f":SOUR{self._module}:PRES?"),
            "slew_bar_s": self._optional_number(f":SOUR{self._module}:PRES:SLEW?"),
            "slew_mode": self._optional_payload(f":SOUR{self._module}:PRES:SLEW:MODE?"),
            "control_mode": self._optional_payload(f":OUTP{self._module}:MODE?"),
            "overshoot": bool(self._optional_number(f":SOUR{self._module}:PRES:SLEW:OVER?", 0.0)),
            "in_limit_percent": self._optional_number(f":SOUR{self._module}:PRES:INL?", 0.01),
            "in_limit_time_seconds": int(self._optional_number(f":SOUR{self._module}:PRES:INL:TIME?", 2.0)),
            "vent_rate_bar_s": self._optional_number(
                f":SOUR{self._module}:PRES:LEV:IMM:AMPL:VENT:RATE?", 0.1
            ),
            "capabilities": self._capabilities,
        }
        self.parameters_received.emit(values)

    def _start_sequence(
        self,
        mode: str,
        steps: list[PressureStep],
        parameters: ControlParameters,
        keep_control: bool,
    ) -> None:
        self._require_connected()
        if not steps:
            raise ValueError("The routine contains no steps")
        self._validate_parameters(parameters)
        self._validate_steps(steps)
        current_margin = (
            self._telemetry.positive_source_bar - self._telemetry.current_pressure_bar
        )
        if not math.isfinite(current_margin) or current_margin < self.minimum_source_margin_bar:
            raise ValueError(
                f"CONTROL blocked: source margin is below {self.minimum_source_margin_bar:.1f} bar"
            )
        if self._supply_interlock_latched and current_margin < self.source_margin_rearm_bar:
            raise ValueError(
                f"Source interlock remains latched until the margin reaches {self.source_margin_rearm_bar:.1f} bar"
            )
        if current_margin >= self.source_margin_rearm_bar:
            self._supply_interlock_latched = False
        self._automation = {
            "mode": mode,
            "steps": steps,
            "parameters": parameters,
            "keep_control": bool(keep_control),
            "index": 0,
            "state": "starting",
            "deadline": 0.0,
            "dwell_end": 0.0,
        }
        self.busy_changed.emit(True)
        self._start_current_step()

    def _validate_parameters(self, parameters: ControlParameters) -> None:
        if not 0.0001 <= parameters.in_limit_percent <= 10.0:
            raise ValueError("In-limits tolerance must be between 0.0001 and 10 %FS")
        if parameters.in_limit_time_seconds < 0:
            raise ValueError("In-limits time cannot be negative")
        if parameters.vent_rate_bar_s <= 0:
            raise ValueError("Vent rate must be positive")
        if parameters.mode not in {"ACTIVE", "PASSIVE", "GAUGE"}:
            raise ValueError("Unsupported control mode")

    def _validate_steps(self, steps: list[PressureStep]) -> None:
        for step in steps:
            if not math.isfinite(step.target_bar) or not math.isfinite(step.slew_bar_s):
                raise ValueError("Pressure step contains a non-finite value")
            if step.slew_bar_s <= 0 and not step.maximum_rate:
                raise ValueError("Slew rate must be positive")
            if step.dwell_seconds < 0:
                raise ValueError("Dwell time cannot be negative")
            low = self._capabilities.range_min_bar
            high = self._capabilities.range_max_bar
            if math.isfinite(low) and step.target_bar < low:
                raise ValueError(f"Target {step.target_bar} bar is below the module range")
            if math.isfinite(high) and step.target_bar > high:
                raise ValueError(f"Target {step.target_bar} bar is above the module range")
            if (
                math.isfinite(self._telemetry.positive_source_bar)
                and self._telemetry.positive_source_bar - step.target_bar
                < self.minimum_source_margin_bar
            ):
                raise ValueError(
                    f"Target {step.target_bar} bar would leave less than {self.minimum_source_margin_bar:.1f} bar positive-source margin"
                )

    def _start_current_step(self) -> None:
        if self._automation is None:
            return
        index = int(self._automation["index"])
        steps: list[PressureStep] = self._automation["steps"]
        step = steps[index]
        parameters: ControlParameters = self._automation["parameters"]
        self._apply_pressure_step(step, parameters)
        distance = (
            abs(step.target_bar - self._telemetry.current_pressure_bar)
            if math.isfinite(self._telemetry.current_pressure_bar)
            else 0.0
        )
        rate = 0.5 if step.maximum_rate else max(step.slew_bar_s, 0.001)
        self._automation["state"] = "waiting"
        self._automation["deadline"] = time.monotonic() + max(180.0, distance / rate * 3.0 + 120.0)
        self.automation_changed.emit(
            {
                "key": "moving",
                "mode": self._automation["mode"],
                "index": index + 1,
                "total": len(steps),
                "target": step.target_bar,
            }
        )

    def _apply_pressure_step(
        self, step: PressureStep, parameters: ControlParameters
    ) -> None:
        module = self._module
        self._write("*CLS")
        self._write(f":UNIT{module}:PRES BAR")
        mode_token = {"ACTIVE": "ACT", "PASSIVE": "PASS", "GAUGE": "GAUG"}[
            parameters.mode
        ]
        self._write(f":OUTP{module}:MODE {mode_token}")
        self._write(f":SOUR{module}:PRES:SLEW:OVER {int(parameters.overshoot)}")
        self._write(f":SOUR{module}:PRES:INL {scpi_float(parameters.in_limit_percent)}")
        self._write(f":SOUR{module}:PRES:INL:TIME {parameters.in_limit_time_seconds}")
        if step.maximum_rate:
            self._write(f":SOUR{module}:PRES:SLEW:MODE MAX")
        else:
            self._write(f":SOUR{module}:PRES:SLEW:MODE LIN")
            self._write(f":SOUR{module}:PRES:SLEW {scpi_float(step.slew_bar_s)}")
        self._write(f":SOUR{module}:PRES {scpi_float(step.target_bar)}")
        self._write(f":OUTP{module}:STAT ON")
        self._assert_no_error()
        self._write_log(
            f"Step: target={step.target_bar} bar, slew={step.slew_bar_s} bar/s, dwell={step.dwell_seconds} s."
        )

    def _process_automation(self, now: float) -> None:
        if self._automation is None:
            return
        try:
            state = self._automation["state"]
            steps: list[PressureStep] = self._automation["steps"]
            index = int(self._automation["index"])
            step = steps[index]
            if state == "waiting":
                if self._telemetry.in_limits:
                    if step.dwell_seconds > 0:
                        self._automation["state"] = "dwelling"
                        self._automation["dwell_end"] = now + step.dwell_seconds
                    else:
                        self._complete_step()
                elif now > float(self._automation["deadline"]):
                    raise TimeoutError("PACE did not reach the target within the safety time")
            elif state == "dwelling":
                remaining = max(0.0, float(self._automation["dwell_end"]) - now)
                self.automation_changed.emit(
                    {
                        "key": "holding",
                        "mode": self._automation["mode"],
                        "seconds": remaining,
                    }
                )
                if remaining <= 0:
                    self._complete_step()
        except Exception as exc:
            self._write_log(f"Automation stopped: {exc}")
            self._cancel_automation()
            try:
                self._set_measure()
            finally:
                self.busy_changed.emit(False)
                self._emit_alarm("device_error", error=f"Automation stopped: {exc}")

    def _complete_step(self) -> None:
        if self._automation is None:
            return
        self._automation["index"] = int(self._automation["index"]) + 1
        steps: list[PressureStep] = self._automation["steps"]
        if int(self._automation["index"]) < len(steps):
            self._start_current_step()
            return
        mode = str(self._automation["mode"])
        keep = bool(self._automation["keep_control"])
        self._automation = None
        if not keep:
            self._set_measure()
            self.automation_changed.emit({"key": "complete", "mode": mode})
        else:
            self.automation_changed.emit({"key": "control_held"})
        self.busy_changed.emit(False)
        self._write_log(f"{mode} complete; keep CONTROL={keep}.")

    def _start_vent(self, parameters: ControlParameters) -> None:
        self._require_connected()
        self._cancel_automation()
        module = self._module
        self._write(f":SOUR{module}:PRES:LEV:IMM:AMPL:VENT:UNIT 0")
        self._write(
            f":SOUR{module}:PRES:LEV:IMM:AMPL:VENT:RATE {scpi_float(parameters.vent_rate_bar_s)}"
        )
        self._write(f":SOUR{module}:PRES:LEV:IMM:AMPL:VENT 1")
        self._assert_no_error()
        self._write_log(f"VENT started at {parameters.vent_rate_bar_s} bar/s.")

    def _set_measure(self) -> None:
        self._require_connected()
        self._write(f":OUTP{self._module}:STAT OFF")
        self._assert_no_error()
        self._write_log("MEASURE requested.")

    def _cancel_automation(self) -> None:
        self._automation = None

    def _poll(self) -> None:
        if not self._connected:
            return
        module = self._module
        try:
            current = scpi_number(self._query(f":SENS{module}:PRES:CONT?"))
            target = scpi_number(self._query(f":SOUR{module}:PRES?"))
            control = bool(scpi_number(self._query(f":OUTP{module}:STAT?")))
            source_positive = self._optional_number(f":SOUR{module}:PRES:COMP1?")
            in_limits = bool(scpi_number(self._query(f":SENS{module}:PRES:INL?")))
            source_negative = self._optional_number(f":SOUR{module}:PRES:COMP2?")
            measured_slew = self._optional_number(f":SENS{module}:PRES:SLEW?")
            effort = self._optional_number(f":SOUR{module}:PRES:EFF?")
            margin = source_positive - current if math.isfinite(source_positive) else math.nan
            self._telemetry = Telemetry(
                timestamp=time.time(),
                current_pressure_bar=current,
                target_pressure_bar=target,
                positive_source_bar=source_positive,
                negative_source_bar=source_negative,
                measured_slew_bar_s=measured_slew,
                valve_effort_percent=effort,
                control=control,
                in_limits=in_limits,
                source_margin_bar=margin,
            )
            self._poll_failures = 0
            self.telemetry_received.emit(self._telemetry)
            self.logger.telemetry(self._telemetry)
            self._check_source_interlock()
        except Exception as exc:
            self._poll_failures += 1
            was_control = self._telemetry.control
            self._write_log(f"Telemetry failure {self._poll_failures}: {exc}")
            if was_control:
                try:
                    self._set_measure()
                except Exception:
                    pass
                self._cancel_automation()
                self.busy_changed.emit(False)
                self._emit_alarm("telemetry_lost")
            if self._poll_failures >= 3:
                self._close_device(request_measure=False)

    def _check_source_interlock(self) -> None:
        margin = self._telemetry.source_margin_bar
        if math.isfinite(margin) and margin >= self.source_margin_rearm_bar:
            self._supply_interlock_latched = False
        if not self._telemetry.control:
            return
        if not math.isfinite(margin):
            try:
                self._set_measure()
            finally:
                self._cancel_automation()
                self.busy_changed.emit(False)
                self._emit_alarm("telemetry_lost")
            return
        if margin < self.minimum_source_margin_bar:
            self._supply_interlock_latched = True
            try:
                self._set_measure()
            finally:
                self._cancel_automation()
                self.busy_changed.emit(False)
                self._write_log(
                    f"Source-margin interlock: {margin:.6f} bar; MEASURE requested."
                )
                self._emit_alarm("interlock")

    def _assert_no_error(self) -> None:
        response = self._query(":SYST:ERR?")
        numbers = scpi_numbers(response)
        if numbers and int(numbers[0]) != 0:
            raise RuntimeError(response)

    def _query(self, command: str) -> str:
        if self._transport is None:
            raise TransportError("No active transport")
        return self._transport.query(command)

    def _write(self, command: str) -> None:
        if self._transport is None:
            raise TransportError("No active transport")
        self._transport.write(command)

    def _optional_number(self, command: str, fallback: float = math.nan) -> float:
        try:
            return scpi_number(self._query(command))
        except Exception:
            return fallback

    def _optional_payload(self, command: str, fallback: str = "") -> str:
        try:
            return scpi_payload(self._query(command))
        except Exception:
            return fallback

    def _require_connected(self) -> None:
        if not self._connected or self._transport is None:
            raise RuntimeError("PACE is not connected")

    def _write_log(self, message: str) -> None:
        self.log_line.emit(self.logger.log(message))

    def _emit_alarm(self, key: str, **values: object) -> None:
        self.alarm.emit({"key": key, **values})
