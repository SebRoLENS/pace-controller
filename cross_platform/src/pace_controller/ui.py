"""Cross-platform Qt graphical interface for PACE Controller."""

from __future__ import annotations

import json
import math
import os
import platform
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .i18n import Translator
from .leak import LeakAssessment, LeakMonitor
from .models import (
    AppSettings,
    ConnectionConfig,
    ConnectionKind,
    ControlParameters,
    DeviceCapabilities,
    LeakThresholds,
    PressureStep,
    Telemetry,
)
from .service import PaceService
from .storage import data_directory, save_settings
from .transports import list_serial_ports


APP_STYLE = """
QMainWindow, QWidget { background: #f4f6f8; color: #202830; font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 10pt; }
QLabel, QCheckBox { background: transparent; }
QGroupBox { background: white; border: 1px solid #cbd3db; border-radius: 6px; margin-top: 12px; padding-top: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #aeb9c4; border-radius: 3px; padding: 5px; min-height: 22px; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { background: #e8ebee; color: #68727c; }
QPushButton, QToolButton { background: #e7edf3; border: 1px solid #9eabb8; border-radius: 4px; padding: 7px 12px; }
QPushButton:hover, QToolButton:hover { background: #d8e5f1; }
QPushButton:disabled { color: #8b949d; background: #edf0f2; }
QPushButton#primary { background: #d7e8f7; border-color: #6088ad; font-weight: 700; }
QPushButton#danger { background: #fde2e2; border-color: #b76b6b; font-weight: 700; }
QTabWidget::pane { border: 1px solid #aeb9c4; background: white; }
QTabBar::tab { background: #dfe5ea; border: 1px solid #aeb9c4; padding: 12px 28px; min-width: 125px; font-size: 11pt; font-weight: 700; }
QTabBar::tab:selected { background: white; color: #0b5c91; border-bottom-color: white; }
QTableWidget { background: white; alternate-background-color: #f3f6f8; gridline-color: #d4dae0; }
QHeaderView::section { background: #e5ebf0; padding: 7px; border: 1px solid #c3ccd4; font-weight: 700; }
QTextEdit { background: #17212b; color: #d7e5ef; font-family: "Cascadia Mono", monospace; }
"""


class LockButton(QToolButton):
    def __init__(self) -> None:
        super().__init__()
        self.unlocked = False
        self.setFixedSize(48, 42)
        self.setToolTip("Unlock protected pressure parameters")

    def set_unlocked(self, unlocked: bool) -> None:
        self.unlocked = unlocked
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#9b2f2f" if self.unlocked else "#245348")
        painter.setPen(QPen(color, 3))
        painter.setBrush(color)
        painter.drawRoundedRect(15, 20, 20, 15, 2, 2)
        painter.setBrush(Qt.NoBrush)
        if self.unlocked:
            painter.drawArc(17, 8, 18, 22, 0, 180 * 16)
            painter.drawLine(17, 19, 17, 13)
        else:
            painter.drawArc(17, 8, 18, 22, 0, 180 * 16)
            painter.drawLine(17, 19, 17, 16)
            painter.drawLine(35, 19, 35, 16)
        painter.end()


class MetricCard(QFrame):
    def __init__(self, title_key: str) -> None:
        super().__init__()
        self.title_key = title_key
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(78)
        self.setStyleSheet("MetricCard { background: #e8e8e8; border: 1px solid #aeb4ba; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        self.title = QLabel(title_key)
        self.title.setStyleSheet("color: #5a6670; font-size: 9pt;")
        self.value = QLabel("--")
        value_font = QFont()
        value_font.setPointSize(11)
        value_font.setBold(True)
        self.value.setFont(value_font)
        layout.addWidget(self.title)
        layout.addStretch(1)
        layout.addWidget(self.value)


class LeakCard(QFrame):
    COLORS = {
        "assessing": ("#f4f5f6", "#59636d"),
        "paused_control": ("#eef1f4", "#59636d"),
        "no_leak": ("#e8f8e8", "#078419"),
        "slight_leak": ("#fffbdc", "#be8500"),
        "pressure_leak": ("#fff0dc", "#d56a00"),
        "significant_leak": ("#ffe1e1", "#c00000"),
    }

    def __init__(self, title_key: str) -> None:
        super().__init__()
        self.title_key = title_key
        self.level = "assessing"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 8)
        self.title = QLabel(title_key)
        self.title.setStyleSheet("font-size: 9pt;")
        self.value = QLabel("ASSESSING")
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setMinimumHeight(56)
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        self.value.setFont(font)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        self.set_level("assessing", "ASSESSING")

    def set_level(self, level: str, text: str) -> None:
        self.level = level
        background, foreground = self.COLORS.get(level, self.COLORS["assessing"])
        self.setStyleSheet(
            f"LeakCard {{ background: {background}; border: 1px solid #cbd3db; }}"
        )
        self.value.setStyleSheet(f"color: {foreground};")
        self.value.setText(text)


class MainWindow(QMainWindow):
    def __init__(
        self,
        service: PaceService,
        settings: AppSettings,
        screenshot_path: str = "",
    ) -> None:
        super().__init__()
        self.service = service
        self.settings = settings
        self.screenshot_path = screenshot_path
        self.t = Translator(settings.language)
        self.connected = False
        self.busy = False
        self.parameters_unlocked = False
        self.current_telemetry = Telemetry()
        self.capabilities = DeviceCapabilities()
        self.sample_monitor = LeakMonitor(settings.leak_thresholds)
        self.inlet_monitor = LeakMonitor(settings.leak_thresholds)
        self._localized: list[tuple[object, str]] = []

        self.setObjectName("mainWindow")
        self.setMinimumSize(1080, 760)
        self.resize(1280, 900)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._connect_signals()
        self.refresh_serial_ports()
        self._load_settings_into_ui()
        self.apply_language()
        self.set_connected(False)
        self.service.start()

        if screenshot_path:
            self.transport_combo.setCurrentIndex(2)
            screenshot_tab = os.environ.get("PACE_CONTROLLER_SCREENSHOT_TAB", "manual").lower()
            self.tabs.setCurrentIndex(
                {"manual": 0, "indenting": 1, "routine": 2, "settings": 3, "log": 4}.get(
                    screenshot_tab, 0
                )
            )
            QTimer.singleShot(100, self.request_connect)
            QTimer.singleShot(2200, self.capture_screenshot)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        self.setCentralWidget(central)

        self.connection_group = QGroupBox()
        self._localized.append((self.connection_group, "connection"))
        connection_layout = QGridLayout(self.connection_group)

        self.transport_label = QLabel()
        self._localized.append((self.transport_label, "transport"))
        self.transport_combo = QComboBox()
        self.transport_combo.addItems(["Ethernet", "RS-232", "Simulator"])
        connection_layout.addWidget(self.transport_label, 0, 0)
        connection_layout.addWidget(self.transport_combo, 0, 1)

        self.transport_stack = QStackedWidget()
        ethernet_page = QWidget()
        ethernet_layout = QHBoxLayout(ethernet_page)
        ethernet_layout.setContentsMargins(0, 0, 0, 0)
        self.host_label = QLabel()
        self._localized.append((self.host_label, "pace_ip"))
        self.host_edit = QLineEdit("192.168.10.2")
        self.host_edit.setMaximumWidth(145)
        self.tcp_port_label = QLabel()
        self._localized.append((self.tcp_port_label, "tcp_port"))
        self.tcp_port_edit = QLineEdit("5025")
        self.tcp_port_edit.setMaximumWidth(80)
        ethernet_layout.addWidget(self.host_label)
        ethernet_layout.addWidget(self.host_edit)
        ethernet_layout.addWidget(self.tcp_port_label)
        ethernet_layout.addWidget(self.tcp_port_edit)
        ethernet_layout.addStretch(1)
        self.transport_stack.addWidget(ethernet_page)

        serial_page = QWidget()
        serial_layout = QHBoxLayout(serial_page)
        serial_layout.setContentsMargins(0, 0, 0, 0)
        self.serial_port_label = QLabel()
        self._localized.append((self.serial_port_label, "serial_port"))
        self.serial_port_combo = QComboBox()
        self.serial_port_combo.setMinimumWidth(170)
        self.refresh_serial_button = QPushButton()
        self._localized.append((self.refresh_serial_button, "refresh"))
        self.baud_label = QLabel()
        self._localized.append((self.baud_label, "baud_rate"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["2400", "4800", "9600", "19200", "38400", "57600", "115200"])
        self.parity_label = QLabel()
        self._localized.append((self.parity_label, "parity"))
        self.parity_combo = QComboBox()
        self.parity_combo.addItems(["None", "Even", "Odd"])
        self.flow_label = QLabel()
        self._localized.append((self.flow_label, "flow_control"))
        self.flow_combo = QComboBox()
        self.flow_combo.addItems(["None", "RTS/CTS", "XON/XOFF"])
        for widget in (
            self.serial_port_label,
            self.serial_port_combo,
            self.refresh_serial_button,
            self.baud_label,
            self.baud_combo,
            self.parity_label,
            self.parity_combo,
            self.flow_label,
            self.flow_combo,
        ):
            serial_layout.addWidget(widget)
        self.transport_stack.addWidget(serial_page)

        simulator_page = QWidget()
        simulator_layout = QHBoxLayout(simulator_page)
        simulator_layout.setContentsMargins(0, 0, 0, 0)
        self.simulator_label = QLabel()
        self._localized.append((self.simulator_label, "offline_preview"))
        self.simulator_label.setStyleSheet("color: #4b3f89; font-weight: 600;")
        simulator_layout.addWidget(self.simulator_label)
        simulator_layout.addStretch(1)
        self.transport_stack.addWidget(simulator_page)
        connection_layout.addWidget(self.transport_stack, 0, 2, 1, 4)

        self.module_label = QLabel()
        self._localized.append((self.module_label, "module"))
        self.module_combo = QComboBox()
        self.module_combo.addItems(["1", "2"])
        self.language_label = QLabel()
        self._localized.append((self.language_label, "language"))
        self.language_combo = QComboBox()
        self.language_combo.addItems(["English", "Italiano"])
        self.auto_network_check = QCheckBox()
        self._localized.append((self.auto_network_check, "auto_network"))
        self.auto_network_check.setChecked(True)
        self.connect_button = QPushButton()
        self.connect_button.setObjectName("primary")
        self._localized.append((self.connect_button, "connect"))
        self.disconnect_button = QPushButton()
        self._localized.append((self.disconnect_button, "disconnect"))
        connection_layout.addWidget(self.module_label, 1, 0)
        connection_layout.addWidget(self.module_combo, 1, 1)
        connection_layout.addWidget(self.language_label, 1, 2)
        connection_layout.addWidget(self.language_combo, 1, 3)
        connection_layout.addWidget(self.auto_network_check, 1, 4)
        connection_layout.addWidget(self.connect_button, 0, 6)
        connection_layout.addWidget(self.disconnect_button, 1, 6)
        connection_layout.setColumnStretch(4, 1)
        root.addWidget(self.connection_group)

        metrics_widget = QWidget()
        metrics_grid = QGridLayout(metrics_widget)
        metrics_grid.setContentsMargins(0, 0, 0, 0)
        metrics_grid.setSpacing(8)
        keys = [
            "current_pressure",
            "target_pressure",
            "positive_source",
            "negative_source",
            "measured_slew",
            "valve_effort",
            "state",
            "target_state",
            "source_margin",
        ]
        self.metrics: dict[str, MetricCard] = {}
        for index, key in enumerate(keys):
            card = MetricCard(key)
            self.metrics[key] = card
            row, column = (0, index) if index < 6 else (1, index - 6)
            column_span = 1
            metrics_grid.addWidget(card, row, column, 1, column_span)
            metrics_grid.setColumnStretch(column, 1)
        root.addWidget(metrics_widget)

        leak_layout = QHBoxLayout()
        leak_layout.setSpacing(8)
        self.sample_leak = LeakCard("sample_leak_title")
        self.inlet_leak = LeakCard("inlet_leak_title")
        leak_layout.addWidget(self.sample_leak, 1)
        leak_layout.addWidget(self.inlet_leak, 1)
        root.addLayout(leak_layout)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.tabs.setMinimumHeight(350)
        self.manual_tab = self._build_manual_tab()
        self.indenting_tab = self._build_indenting_tab()
        self.routine_tab = self._build_routine_tab()
        self.settings_tab = self._build_settings_tab()
        self.log_tab = self._build_log_tab()
        self.tabs.addTab(self.manual_tab, "")
        self.tabs.addTab(self.indenting_tab, "")
        self.tabs.addTab(self.routine_tab, "")
        self.tabs.addTab(self.settings_tab, "")
        self.tabs.addTab(self.log_tab, "")
        root.addWidget(self.tabs, 1)

        self.status = QStatusBar()
        self.connection_status = QLabel()
        self.range_status = QLabel("")
        self.automation_status = QLabel()
        self.automation_status.setStyleSheet("font-weight: 600;")
        self.status.addWidget(self.connection_status)
        self.status.addPermanentWidget(self.range_status)
        self.status.addPermanentWidget(self.automation_status, 1)
        self.setStatusBar(self.status)

    def _build_manual_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(20)

        self.target_group = QGroupBox()
        self._localized.append((self.target_group, "new_target"))
        form = QFormLayout(self.target_group)
        self.target_edit = QLineEdit("0")
        self.slew_edit = QLineEdit("0.1")
        self.slew_mode_combo = QComboBox()
        self.slew_mode_combo.addItems(["Linear", "Maximum"])
        self.keep_control_check = QCheckBox()
        self._localized.append((self.keep_control_check, "keep_control"))
        self.apply_target_button = QPushButton()
        self.apply_target_button.setObjectName("primary")
        self._localized.append((self.apply_target_button, "apply_target"))
        self.measure_button = QPushButton()
        self.measure_button.setObjectName("danger")
        self._localized.append((self.measure_button, "measure_stop"))
        self.target_label = QLabel()
        self._localized.append((self.target_label, "target_bar"))
        self.slew_label = QLabel()
        self._localized.append((self.slew_label, "slew_bar_s"))
        self.slew_mode_label = QLabel()
        self._localized.append((self.slew_mode_label, "slew_mode"))
        form.addRow(self.target_label, self.target_edit)
        form.addRow(self.slew_label, self.slew_edit)
        form.addRow(self.slew_mode_label, self.slew_mode_combo)
        form.addRow(self.keep_control_check)
        buttons = QHBoxLayout()
        buttons.addWidget(self.apply_target_button)
        buttons.addWidget(self.measure_button)
        form.addRow(buttons)
        layout.addWidget(self.target_group, 1)

        self.pressure_group = QGroupBox()
        self._localized.append((self.pressure_group, "pressurization_parameters"))
        advanced_root = QVBoxLayout(self.pressure_group)
        advanced_header = QHBoxLayout()
        advanced_header.addStretch(1)
        self.lock_button = LockButton()
        advanced_header.addWidget(self.lock_button)
        advanced_root.addLayout(advanced_header)
        advanced_form = QFormLayout()
        self.control_mode_combo = QComboBox()
        self.control_mode_combo.addItems(["Active", "Passive", "Gauge"])
        self.overshoot_check = QCheckBox()
        self.in_limit_edit = QLineEdit("0.01")
        self.in_limit_time_spin = QSpinBox()
        self.in_limit_time_spin.setRange(0, 3600)
        self.in_limit_time_spin.setValue(2)
        self.vent_rate_edit = QLineEdit("0.1")
        self.control_mode_label = QLabel()
        self._localized.append((self.control_mode_label, "control_mode"))
        self._localized.append((self.overshoot_check, "allow_overshoot"))
        self.in_limit_label = QLabel()
        self._localized.append((self.in_limit_label, "in_limit_tolerance"))
        self.in_limit_time_label = QLabel()
        self._localized.append((self.in_limit_time_label, "in_limit_time"))
        self.vent_rate_label = QLabel()
        self._localized.append((self.vent_rate_label, "vent_rate"))
        advanced_form.addRow(self.control_mode_label, self.control_mode_combo)
        advanced_form.addRow(self.overshoot_check)
        advanced_form.addRow(self.in_limit_label, self.in_limit_edit)
        advanced_form.addRow(self.in_limit_time_label, self.in_limit_time_spin)
        advanced_form.addRow(self.vent_rate_label, self.vent_rate_edit)
        advanced_root.addLayout(advanced_form)
        advanced_buttons = QHBoxLayout()
        self.reload_parameters_button = QPushButton()
        self._localized.append((self.reload_parameters_button, "reload_parameters"))
        self.vent_button = QPushButton()
        self.vent_button.setObjectName("danger")
        self._localized.append((self.vent_button, "vent"))
        advanced_buttons.addWidget(self.reload_parameters_button)
        advanced_buttons.addWidget(self.vent_button)
        advanced_root.addLayout(advanced_buttons)
        self.lock_hint = QLabel()
        self.lock_hint.setWordWrap(True)
        advanced_root.addWidget(self.lock_hint)
        self.advanced_controls = [
            self.control_mode_combo,
            self.overshoot_check,
            self.in_limit_edit,
            self.in_limit_time_spin,
            self.vent_rate_edit,
            self.vent_button,
        ]
        layout.addWidget(self.pressure_group, 1)
        return tab

    def _build_indenting_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(30, 25, 30, 25)
        self.indent_title = QLabel()
        self._localized.append((self.indent_title, "indenting_title"))
        font = self.indent_title.font()
        font.setPointSize(15)
        font.setBold(True)
        self.indent_title.setFont(font)
        self.indent_description = QLabel()
        self.indent_description.setWordWrap(True)
        self._localized.append((self.indent_description, "indenting_description"))
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.indent_target_edit = QLineEdit("1")
        self.indent_slew_edit = QLineEdit("0.1")
        self.indent_target_label = QLabel()
        self._localized.append((self.indent_target_label, "target_bar"))
        self.indent_slew_label = QLabel()
        self._localized.append((self.indent_slew_label, "slew_bar_s"))
        form.addRow(self.indent_target_label, self.indent_target_edit)
        form.addRow(self.indent_slew_label, self.indent_slew_edit)
        buttons = QHBoxLayout()
        self.start_indent_button = QPushButton()
        self.start_indent_button.setObjectName("primary")
        self._localized.append((self.start_indent_button, "start_indenting"))
        self.stop_indent_button = QPushButton()
        self.stop_indent_button.setObjectName("danger")
        self._localized.append((self.stop_indent_button, "measure_stop"))
        buttons.addWidget(self.start_indent_button)
        buttons.addWidget(self.stop_indent_button)
        buttons.addStretch(1)
        layout.addWidget(self.indent_title)
        layout.addWidget(self.indent_description)
        layout.addWidget(form_widget)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return tab

    def _build_routine_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 12, 15, 12)
        self.routine_description = QLabel()
        self.routine_description.setWordWrap(True)
        self._localized.append((self.routine_description, "routine_description"))
        layout.addWidget(self.routine_description)

        top_buttons = QHBoxLayout()
        self.add_step_button = QPushButton()
        self._localized.append((self.add_step_button, "add_step"))
        self.remove_step_button = QPushButton()
        self._localized.append((self.remove_step_button, "remove_step"))
        self.save_routine_button = QPushButton()
        self._localized.append((self.save_routine_button, "save_routine"))
        self.load_routine_button = QPushButton()
        self._localized.append((self.load_routine_button, "load_routine"))
        for button in (
            self.add_step_button,
            self.remove_step_button,
            self.save_routine_button,
            self.load_routine_button,
        ):
            top_buttons.addWidget(button)
        top_buttons.addStretch(1)
        layout.addLayout(top_buttons)

        self.routine_table = QTableWidget(3, 4)
        self.routine_table.setAlternatingRowColors(True)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.verticalHeader().setVisible(False)
        header = self.routine_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        defaults = [
            ("1", "0.1", "120", "First pressure level"),
            ("2", "0.1", "120", "Second pressure level"),
            ("0", "0.1", "0", "Return to zero"),
        ]
        for row, values in enumerate(defaults):
            for column, value in enumerate(values):
                self.routine_table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(self.routine_table, 1)

        bottom = QHBoxLayout()
        self.keep_final_control_check = QCheckBox()
        self._localized.append((self.keep_final_control_check, "keep_final_control"))
        self.start_routine_button = QPushButton()
        self.start_routine_button.setObjectName("primary")
        self._localized.append((self.start_routine_button, "start_routine"))
        self.stop_routine_button = QPushButton()
        self.stop_routine_button.setObjectName("danger")
        self._localized.append((self.stop_routine_button, "measure_stop"))
        bottom.addWidget(self.keep_final_control_check)
        bottom.addStretch(1)
        bottom.addWidget(self.start_routine_button)
        bottom.addWidget(self.stop_routine_button)
        layout.addLayout(bottom)
        return tab

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(25, 20, 25, 20)
        self.leak_settings_group = QGroupBox()
        self._localized.append((self.leak_settings_group, "leak_settings"))
        form = QFormLayout(self.leak_settings_group)
        self.reference_drop_edit = QLineEdit("0.005")
        self.green_time_edit = QLineEdit("10")
        self.yellow_time_edit = QLineEdit("5")
        self.orange_time_edit = QLineEdit("1")
        labels: list[tuple[QLabel, str, QLineEdit]] = []
        for key, edit in (
            ("reference_drop", self.reference_drop_edit),
            ("green_time", self.green_time_edit),
            ("yellow_time", self.yellow_time_edit),
            ("orange_time", self.orange_time_edit),
        ):
            label = QLabel()
            self._localized.append((label, key))
            labels.append((label, key, edit))
            form.addRow(label, edit)
        self.save_settings_button = QPushButton()
        self.save_settings_button.setObjectName("primary")
        self._localized.append((self.save_settings_button, "save_settings"))
        form.addRow(self.save_settings_button)
        layout.addWidget(self.leak_settings_group)

        data_group = QGroupBox()
        self._localized.append((data_group, "data_folder"))
        data_layout = QHBoxLayout(data_group)
        self.data_path_label = QLabel(str(data_directory()))
        self.data_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_data_button = QPushButton()
        self._localized.append((self.open_data_button, "open_folder"))
        data_layout.addWidget(self.data_path_label, 1)
        data_layout.addWidget(self.open_data_button)
        layout.addWidget(data_group)
        self.safety_info_label = QLabel()
        self.safety_info_label.setWordWrap(True)
        self.safety_info_label.setStyleSheet("color: #a02020; font-weight: 600;")
        self._localized.append((self.safety_info_label, "safety_info"))
        layout.addWidget(self.safety_info_label)
        layout.addStretch(1)
        return tab

    def _build_log_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)
        return tab

    def _connect_signals(self) -> None:
        self.transport_combo.currentIndexChanged.connect(self.transport_stack.setCurrentIndex)
        self.transport_combo.currentIndexChanged.connect(self._transport_changed)
        self.refresh_serial_button.clicked.connect(self.refresh_serial_ports)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        self.connect_button.clicked.connect(self.request_connect)
        self.disconnect_button.clicked.connect(self.service.disconnect_device)
        self.lock_button.clicked.connect(self.toggle_parameter_lock)
        self.reload_parameters_button.clicked.connect(self.service.reload_parameters)
        self.apply_target_button.clicked.connect(self.apply_manual_target)
        self.measure_button.clicked.connect(self.service.stop_and_measure)
        self.start_indent_button.clicked.connect(self.start_indenting)
        self.stop_indent_button.clicked.connect(self.service.stop_and_measure)
        self.add_step_button.clicked.connect(self.add_routine_step)
        self.remove_step_button.clicked.connect(self.remove_routine_step)
        self.save_routine_button.clicked.connect(self.save_routine)
        self.load_routine_button.clicked.connect(self.load_routine)
        self.start_routine_button.clicked.connect(self.start_routine)
        self.stop_routine_button.clicked.connect(self.service.stop_and_measure)
        self.save_settings_button.clicked.connect(self.save_leak_settings)
        self.open_data_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_directory())))
        )
        self.vent_button.clicked.connect(self.start_vent)

        self.service.connection_changed.connect(self.on_connection_changed)
        self.service.telemetry_received.connect(self.on_telemetry)
        self.service.parameters_received.connect(self.on_parameters)
        self.service.automation_changed.connect(self.on_automation)
        self.service.log_line.connect(self.log_view.append)
        self.service.alarm.connect(self.on_alarm)
        self.service.busy_changed.connect(self.set_busy)

    def _load_settings_into_ui(self) -> None:
        config = self.settings.connection
        self.language_combo.setCurrentIndex(1 if self.settings.language == "it" else 0)
        self.transport_combo.setCurrentIndex(
            {ConnectionKind.ETHERNET: 0, ConnectionKind.SERIAL: 1, ConnectionKind.SIMULATOR: 2}[config.kind]
        )
        self.host_edit.setText(config.host)
        self.tcp_port_edit.setText(str(config.port))
        self.auto_network_check.setChecked(config.auto_configure_network)
        self.baud_combo.setCurrentText(str(config.baud_rate))
        self.parity_combo.setCurrentIndex({"N": 0, "E": 1, "O": 2}.get(config.parity, 0))
        self.flow_combo.setCurrentIndex({"none": 0, "rtscts": 1, "xonxoff": 2}.get(config.flow_control, 0))
        if config.serial_port:
            index = self.serial_port_combo.findData(config.serial_port)
            if index >= 0:
                self.serial_port_combo.setCurrentIndex(index)
        thresholds = self.settings.leak_thresholds
        self.reference_drop_edit.setText(f"{thresholds.reference_drop_bar:g}")
        self.green_time_edit.setText(f"{thresholds.green_minutes:g}")
        self.yellow_time_edit.setText(f"{thresholds.yellow_minutes:g}")
        self.orange_time_edit.setText(f"{thresholds.orange_minutes:g}")

    def apply_language(self) -> None:
        self.setWindowTitle(self.t("window_title", version=__version__))
        for widget, key in self._localized:
            text = self.t(key)
            if hasattr(widget, "setTitle"):
                widget.setTitle(text)
            else:
                widget.setText(text)
        for card in self.metrics.values():
            card.title.setText(self.t(card.title_key))
        self.sample_leak.title.setText(self.t(self.sample_leak.title_key))
        self.inlet_leak.title.setText(self.t(self.inlet_leak.title_key))
        self.tabs.setTabText(0, self.t("manual"))
        self.tabs.setTabText(1, self.t("indenting"))
        self.tabs.setTabText(2, self.t("routine"))
        self.tabs.setTabText(3, self.t("settings"))
        self.tabs.setTabText(4, self.t("log"))
        self.slew_mode_combo.setItemText(0, self.t("linear"))
        self.slew_mode_combo.setItemText(1, self.t("maximum"))
        self.control_mode_combo.setItemText(0, self.t("active"))
        self.control_mode_combo.setItemText(1, self.t("passive"))
        self.control_mode_combo.setItemText(2, self.t("gauge"))
        self.parity_combo.setItemText(0, self.t("none"))
        self.parity_combo.setItemText(1, self.t("even"))
        self.parity_combo.setItemText(2, self.t("odd"))
        self.flow_combo.setItemText(0, self.t("none"))
        self.flow_combo.setItemText(1, self.t("rts_cts"))
        self.flow_combo.setItemText(2, self.t("xon_xoff"))
        self.routine_table.setHorizontalHeaderLabels(
            [self.t("target_bar"), self.t("slew_bar_s"), self.t("dwell_s"), self.t("notes")]
        )
        self.connection_status.setText(self.t("disconnected") if not self.connected else self.connection_status.text())
        self.automation_status.setText(self.t("automation_idle") if not self.busy else self.automation_status.text())
        self._set_parameter_lock_ui()
        self._refresh_leak_texts()

    def _language_changed(self, index: int) -> None:
        self.settings.language = "it" if index == 1 else "en"
        self.t.set_language(self.settings.language)
        self.apply_language()
        save_settings(self.settings)

    def _transport_changed(self, index: int) -> None:
        self.auto_network_check.setVisible(index == 0)

    def refresh_serial_ports(self) -> None:
        selected = self.serial_port_combo.currentData()
        self.serial_port_combo.clear()
        for port in list_serial_ports():
            self.serial_port_combo.addItem(f"{port.device} — {port.description}", port.device)
        if selected:
            index = self.serial_port_combo.findData(selected)
            if index >= 0:
                self.serial_port_combo.setCurrentIndex(index)

    def request_connect(self) -> None:
        try:
            kind = [ConnectionKind.ETHERNET, ConnectionKind.SERIAL, ConnectionKind.SIMULATOR][
                self.transport_combo.currentIndex()
            ]
            config = ConnectionConfig(
                kind=kind,
                host=self.host_edit.text().strip(),
                port=int(self.tcp_port_edit.text()),
                serial_port=str(self.serial_port_combo.currentData() or ""),
                baud_rate=int(self.baud_combo.currentText()),
                parity=["N", "E", "O"][self.parity_combo.currentIndex()],
                flow_control=["none", "rtscts", "xonxoff"][self.flow_combo.currentIndex()],
                terminator="\r",
                auto_configure_network=self.auto_network_check.isChecked(),
            )
            if kind == ConnectionKind.SERIAL and not config.serial_port:
                raise ValueError("No serial port selected")
            self.settings.connection = config
            save_settings(self.settings)
            self.service.connect_device(config, int(self.module_combo.currentText()))
        except (ValueError, TypeError) as exc:
            self.show_error(str(exc))

    def on_connection_changed(self, connected: bool, event: object) -> None:
        details = dict(event) if isinstance(event, dict) else {"key": "disconnected"}
        self.connected = connected
        key = str(details.get("key", "disconnected"))
        if key == "connected":
            identity = str(details.get("identity", "PACE"))
            self.connection_status.setText(self.t("connected", identity=identity))
            caps = details.get("capabilities")
            if isinstance(caps, DeviceCapabilities):
                self.capabilities = caps
                if math.isfinite(caps.range_min_bar) and math.isfinite(caps.range_max_bar):
                    self.range_status.setText(
                        self.t("range", minimum=caps.range_min_bar, maximum=caps.range_max_bar)
                    )
        elif key == "connection_error":
            text = self.t("connection_error", error=str(details.get("error", "")))
            self.connection_status.setText(text)
            if not self.screenshot_path:
                self.show_error(text)
        else:
            self.connection_status.setText(self.t(key))
            self.range_status.clear()
        self.set_connected(connected)

    def set_connected(self, connected: bool) -> None:
        self.tabs.setEnabled(connected)
        self.connect_button.setEnabled(not connected and not self.busy)
        self.disconnect_button.setEnabled(connected)
        self.transport_combo.setEnabled(not connected)
        self.transport_stack.setEnabled(not connected)
        self.module_combo.setEnabled(not connected)
        if not connected:
            self.parameters_unlocked = False
            self._set_parameter_lock_ui()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for widget in (
            self.apply_target_button,
            self.start_indent_button,
            self.start_routine_button,
            self.routine_table,
            self.add_step_button,
            self.remove_step_button,
            self.load_routine_button,
        ):
            widget.setEnabled(self.connected and not busy)
        self.measure_button.setEnabled(self.connected)
        self.stop_indent_button.setEnabled(self.connected)
        self.stop_routine_button.setEnabled(self.connected)
        self.connect_button.setEnabled(not self.connected and not busy)

    def toggle_parameter_lock(self) -> None:
        if self.parameters_unlocked:
            self.parameters_unlocked = False
        else:
            answer = QMessageBox.warning(
                self,
                self.t("danger_title"),
                self.t("danger_warning"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            self.parameters_unlocked = True
        self._set_parameter_lock_ui()

    def _set_parameter_lock_ui(self) -> None:
        self.lock_button.set_unlocked(self.parameters_unlocked)
        for widget in self.advanced_controls:
            widget.setEnabled(self.parameters_unlocked and self.connected)
        self.reload_parameters_button.setEnabled(self.connected)
        self.lock_hint.setText(
            self.t("unlocked_hint") if self.parameters_unlocked else self.t("locked_hint")
        )
        self.lock_hint.setStyleSheet(
            "color: #a02020; font-weight: 700;" if self.parameters_unlocked else "color: #59636d;"
        )

    def control_parameters(self) -> ControlParameters:
        return ControlParameters(
            mode=["ACTIVE", "PASSIVE", "GAUGE"][self.control_mode_combo.currentIndex()],
            overshoot=self.overshoot_check.isChecked(),
            in_limit_percent=self.number(self.in_limit_edit.text()),
            in_limit_time_seconds=self.in_limit_time_spin.value(),
            vent_rate_bar_s=self.number(self.vent_rate_edit.text()),
        )

    def apply_manual_target(self) -> None:
        try:
            step = PressureStep(
                target_bar=self.number(self.target_edit.text()),
                slew_bar_s=self.number(self.slew_edit.text()),
                maximum_rate=self.slew_mode_combo.currentIndex() == 1,
            )
            if not self.confirm_steps([step]):
                return
            self.service.start_manual(
                step, self.control_parameters(), self.keep_control_check.isChecked()
            )
        except ValueError as exc:
            self.show_error(str(exc))

    def start_indenting(self) -> None:
        try:
            target = self.number(self.indent_target_edit.text())
            slew = self.number(self.indent_slew_edit.text())
            steps = [PressureStep(target, slew, 120), PressureStep(0, slew, 0)]
            if not self.confirm_steps(steps):
                return
            self.service.start_indenting(target, slew, self.control_parameters())
        except ValueError as exc:
            self.show_error(str(exc))

    def routine_steps(self) -> list[PressureStep]:
        steps: list[PressureStep] = []
        for row in range(self.routine_table.rowCount()):
            values = [
                self.routine_table.item(row, column).text().strip()
                if self.routine_table.item(row, column)
                else ""
                for column in range(4)
            ]
            if not any(values):
                continue
            steps.append(
                PressureStep(
                    self.number(values[0]),
                    self.number(values[1]),
                    self.number(values[2]),
                    note=values[3],
                )
            )
        if not steps:
            raise ValueError("The routine contains no steps")
        return steps

    def start_routine(self) -> None:
        try:
            steps = self.routine_steps()
            if not self.confirm_steps(steps):
                return
            self.service.start_routine(
                steps,
                self.control_parameters(),
                self.keep_final_control_check.isChecked(),
            )
        except ValueError as exc:
            self.show_error(str(exc))

    def confirm_steps(self, steps: list[PressureStep]) -> bool:
        previous = self.current_telemetry.current_pressure_bar
        if not math.isfinite(previous):
            previous = 0.0
        for step in steps:
            if step.slew_bar_s <= 0 and not step.maximum_rate:
                raise ValueError(self.t("invalid_number"))
            if math.isfinite(self.capabilities.range_min_bar) and step.target_bar < self.capabilities.range_min_bar:
                self.show_error(self.t("out_of_range", target=step.target_bar))
                return False
            if math.isfinite(self.capabilities.range_max_bar) and step.target_bar > self.capabilities.range_max_bar:
                self.show_error(self.t("out_of_range", target=step.target_bar))
                return False
            if (
                math.isfinite(self.current_telemetry.positive_source_bar)
                and self.current_telemetry.positive_source_bar - step.target_bar
                < self.settings.minimum_source_margin_bar
            ):
                self.show_error(
                    self.t("source_margin_block", margin=self.settings.minimum_source_margin_bar)
                )
                return False
            if step.target_bar - previous >= 10.0:
                if not self.ask_yes_no("large_target_title", "large_target_warning"):
                    return False
            previous = step.target_bar
        if any(step.maximum_rate or step.slew_bar_s > 0.5 for step in steps):
            if not self.ask_yes_no("high_slew_title", "high_slew_warning"):
                return False
        return True

    def start_vent(self) -> None:
        if self.ask_yes_no("danger_title", "confirm_vent"):
            try:
                self.service.vent(self.control_parameters())
            except ValueError as exc:
                self.show_error(str(exc))

    def on_parameters(self, values: object) -> None:
        data = dict(values) if isinstance(values, dict) else {}
        self._set_if_finite(self.target_edit, data.get("target_bar"))
        self._set_if_finite(self.slew_edit, data.get("slew_bar_s"))
        mode = str(data.get("slew_mode", "LIN")).upper()
        self.slew_mode_combo.setCurrentIndex(1 if "MAX" in mode else 0)
        control = str(data.get("control_mode", "ACT")).upper()
        self.control_mode_combo.setCurrentIndex(2 if "GAUG" in control else 1 if "PASS" in control else 0)
        self.overshoot_check.setChecked(bool(data.get("overshoot", False)))
        self._set_if_finite(self.in_limit_edit, data.get("in_limit_percent"))
        self.in_limit_time_spin.setValue(int(data.get("in_limit_time_seconds", 2)))
        self._set_if_finite(self.vent_rate_edit, data.get("vent_rate_bar_s"))
        caps = data.get("capabilities")
        if isinstance(caps, DeviceCapabilities):
            self.capabilities = caps

    def on_telemetry(self, telemetry: object) -> None:
        if not isinstance(telemetry, Telemetry):
            return
        self.current_telemetry = telemetry
        self.metrics["current_pressure"].value.setText(self.format_metric(telemetry.current_pressure_bar, "bar"))
        self.metrics["target_pressure"].value.setText(self.format_metric(telemetry.target_pressure_bar, "bar"))
        self.metrics["positive_source"].value.setText(self.format_metric(telemetry.positive_source_bar, "bar"))
        self.metrics["negative_source"].value.setText(self.format_metric(telemetry.negative_source_bar, "bar"))
        self.metrics["measured_slew"].value.setText(self.format_metric(telemetry.measured_slew_bar_s, "bar/s"))
        self.metrics["valve_effort"].value.setText(self.format_metric(telemetry.valve_effort_percent, "%"))
        self.metrics["state"].value.setText("CONTROL" if telemetry.control else "MEASURE")
        self.metrics["state"].value.setStyleSheet(
            "color: #b42318;" if telemetry.control else "color: #2459e0;"
        )
        self.metrics["target_state"].value.setText("IN LIMIT" if telemetry.in_limits else "MOVING")
        self.metrics["target_state"].value.setStyleSheet(
            "color: #078419;" if telemetry.in_limits else "color: #d56a00;"
        )
        self.metrics["source_margin"].value.setText(self.format_metric(telemetry.source_margin_bar, "bar"))
        self.metrics["source_margin"].value.setStyleSheet(
            "color: #c00000;"
            if math.isfinite(telemetry.source_margin_bar)
            and telemetry.source_margin_bar < self.settings.minimum_source_margin_bar
            else "color: #078419;"
        )
        sample = self.sample_monitor.add(
            telemetry.timestamp, telemetry.current_pressure_bar, not telemetry.control and not self.busy
        )
        inlet = self.inlet_monitor.add(
            telemetry.timestamp, telemetry.positive_source_bar, not telemetry.control and not self.busy
        )
        self._apply_leak(self.sample_leak, sample)
        self._apply_leak(self.inlet_leak, inlet)

    def on_automation(self, event: object) -> None:
        data = dict(event) if isinstance(event, dict) else {"key": "automation_idle"}
        key = str(data.pop("key", "automation_idle"))
        self.automation_status.setText(self.t(key, **data))

    def on_alarm(self, event: object) -> None:
        data = dict(event) if isinstance(event, dict) else {}
        key = str(data.get("key", "device_error"))
        if self.screenshot_path:
            return
        if key == "interlock":
            QMessageBox.critical(self, self.t("interlock_title"), self.t("interlock_message"))
        elif key == "telemetry_lost":
            QMessageBox.critical(self, self.t("telemetry_lost_title"), self.t("telemetry_lost"))
        else:
            self.show_error(self.t("device_error", error=str(data.get("error", ""))))

    def add_routine_step(self) -> None:
        row = self.routine_table.rowCount()
        self.routine_table.insertRow(row)
        for column, value in enumerate(("0", "0.1", "0", "")):
            self.routine_table.setItem(row, column, QTableWidgetItem(value))

    def remove_routine_step(self) -> None:
        rows = sorted({item.row() for item in self.routine_table.selectedItems()}, reverse=True)
        for row in rows:
            self.routine_table.removeRow(row)

    def save_routine(self) -> None:
        try:
            steps = self.routine_steps()
            path, _ = QFileDialog.getSaveFileName(self, self.t("save_routine"), "routine.json", "JSON (*.json)")
            if not path:
                return
            Path(path).write_text(
                json.dumps([step.to_mapping() for step in steps], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.log_view.append(self.t("routine_saved", path=path))
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))

    def load_routine(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.t("load_routine"), "", "JSON (*.json)")
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            steps = [PressureStep.from_mapping(dict(item)) for item in raw]
            self.routine_table.setRowCount(len(steps))
            for row, step in enumerate(steps):
                values = (
                    f"{step.target_bar:g}",
                    f"{step.slew_bar_s:g}",
                    f"{step.dwell_seconds:g}",
                    step.note,
                )
                for column, value in enumerate(values):
                    self.routine_table.setItem(row, column, QTableWidgetItem(value))
            self.log_view.append(self.t("routine_loaded", path=path))
        except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            self.show_error(str(exc))

    def save_leak_settings(self) -> None:
        try:
            thresholds = LeakThresholds(
                reference_drop_bar=self.number(self.reference_drop_edit.text()),
                green_minutes=self.number(self.green_time_edit.text()),
                yellow_minutes=self.number(self.yellow_time_edit.text()),
                orange_minutes=self.number(self.orange_time_edit.text()),
            )
            thresholds.validate()
            self.settings.leak_thresholds = thresholds
            self.sample_monitor.update_thresholds(thresholds)
            self.inlet_monitor.update_thresholds(thresholds)
            save_settings(self.settings)
            self.log_view.append(self.t("settings_saved"))
        except ValueError as exc:
            self.show_error(str(exc))

    def capture_screenshot(self) -> None:
        if not self.screenshot_path:
            return
        path = Path(self.screenshot_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        image = self.grab()
        if not image.save(str(path), "PNG"):
            self.log_view.append(f"Could not save screenshot: {path}")
            QApplication.exit(2)
            return
        QApplication.exit(0)

    def _apply_leak(self, card: LeakCard, assessment: LeakAssessment) -> None:
        card.set_level(assessment.level, self.t(assessment.level))

    def _refresh_leak_texts(self) -> None:
        self.sample_leak.set_level(self.sample_leak.level, self.t(self.sample_leak.level))
        self.inlet_leak.set_level(self.inlet_leak.level, self.t(self.inlet_leak.level))

    def _set_if_finite(self, edit: QLineEdit, value: object) -> None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        if math.isfinite(numeric):
            edit.setText(f"{numeric:g}")

    def format_metric(self, value: float, unit: str) -> str:
        if not math.isfinite(value):
            return self.t("unavailable")
        return f"{value:.3f} {unit}"

    @staticmethod
    def number(text: str) -> float:
        value = float(text.strip().replace(",", "."))
        if not math.isfinite(value):
            raise ValueError("Numeric value must be finite")
        return value

    def ask_yes_no(self, title_key: str, message_key: str) -> bool:
        answer = QMessageBox.warning(
            self,
            self.t(title_key),
            self.t(message_key),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, self.t("device_error", error=""), message)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.service.shutdown()
        event.accept()
