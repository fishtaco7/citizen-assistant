"""Settings window: capture region, scanning controls and data info."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from .. import updater
from ..version import label as build_label
from . import theme

WIDTH = 384

# Bitcoin donation address shown in the Support section.
BTC_ADDRESS = "bc1qnqpr04750zewr6fwvamjs875ex4he6755ukzkv"


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setFont(theme.font(8, QFont.Weight.Bold, spacing=1.4))
    label.setStyleSheet(theme.qss("color: rgba(136,152,170,0.98); background: transparent;"))
    return label


class _UpdateWorker(QThread):
    """Check or apply on a thread: both touch the network."""

    done = Signal(str, str)      # mode, version ("" when already current)
    failed = Signal(str)

    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self._mode = mode

    def run(self) -> None:  # noqa: D102
        try:
            if self._mode == "check":
                _installed, published, _newer = updater.newest()
                self.done.emit("check", published)
            else:
                self.done.emit("apply", updater.apply())
        except Exception as exc:      # surface it, never take the app down
            self.failed.emit(str(exc))


def _slider() -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setFixedHeight(theme.px(20))
    slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    slider.setCursor(Qt.CursorShape.PointingHandCursor)
    slider.setStyleSheet(theme.qss("""
        QSlider::groove:horizontal {
            height: 4px; background: rgba(79,209,224,0.18);
            border-radius: 2px;
        }
        QSlider::sub-page:horizontal {
            background: rgba(79,209,224,0.75); border-radius: 2px;
        }
        QSlider::handle:horizontal {
            width: 12px; margin: -5px 0; border-radius: 6px;
            background: #4fd1e0;
        }
        QSlider::handle:horizontal:disabled {
            background: rgba(130,148,166,0.5);
        }
        QSlider::sub-page:horizontal:disabled {
            background: rgba(79,209,224,0.20);
        }
    """))
    return slider


def _button(text: str, checkable: bool = False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCheckable(checkable)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setFixedHeight(theme.px(28))
    btn.setStyleSheet(theme.qss("""
        QPushButton {
            color: rgba(168,184,200,0.95); background: rgba(79,209,224,0.06);
            border: 1px solid rgba(79,209,224,0.18); border-radius: 7px;
            font-size: 8px; font-weight: 600;
            letter-spacing: 1.0px; padding: 0 10px;
        }
        QPushButton:hover { background: rgba(79,209,224,0.14); color: #e6f7fa; }
        QPushButton:checked {
            background: rgba(79,209,224,0.22); color: #4fd1e0;
            border-color: rgba(79,209,224,0.55);
        }
        QPushButton:disabled {
            color: rgba(130,148,166,0.55); background: rgba(79,209,224,0.03);
            border-color: rgba(79,209,224,0.10);
        }
    """))
    return btn


class SettingsPanel(QWidget):
    region_requested = Signal()
    pause_toggled = Signal(bool)
    debug_toggled = Signal(bool)
    reload_requested = Signal()
    close_requested = Signal()
    voice_toggled = Signal(bool)
    volume_changed = Signal(int)
    opacity_changed = Signal(int)
    capture_toggled = Signal(bool)
    ui_scale_changed = Signal(int)
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(theme.px(WIDTH))

        self._drag_from = None
        self._pending_version = ""
        self._update_worker: _UpdateWorker | None = None
        self._build()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.px(18), theme.px(14), theme.px(18), theme.px(16))
        root.setSpacing(theme.px(8))

        head = QHBoxLayout()
        head.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        head.setSpacing(theme.px(8))
        title = QLabel("SETTINGS")
        title.setFont(theme.font(9, QFont.Weight.Bold, spacing=1.8))
        title.setStyleSheet(theme.qss("color: rgba(168,184,200,0.95); background: transparent;"))
        head.addWidget(title)
        head.addStretch(1)

        build = QLabel(build_label())
        build.setFont(theme.font(7))
        build.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.85); background: transparent;"))
        build.setToolTip(
            "Build of this folder. If it is older than the copy you just "
            "downloaded, an earlier install is still being launched.")
        head.addWidget(build)

        close = QPushButton("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setFixedSize(theme.px(20), theme.px(20))
        close.setStyleSheet(theme.qss("""
            QPushButton { color: rgba(150,164,180,0.8); background: transparent;
                          border: none; font-size: 12px; }
            QPushButton:hover { color: #ffd9d9; }
        """))
        close.clicked.connect(self.close_requested.emit)
        head.addWidget(close)
        root.addLayout(head)
        root.addSpacing(theme.px(4))

        # Support -----------------------------------------------------
        root.addWidget(_section("Support"))
        coffee = QLabel("Like my overlay? Buy me a coffee (or a ship)!")
        coffee.setFont(theme.font(9, QFont.Weight.DemiBold))
        coffee.setStyleSheet(theme.qss(
            "color: rgba(232,240,248,0.98); background: transparent;"))
        root.addWidget(coffee)

        self.btc_label = QLabel(
            f"BTC: {BTC_ADDRESS}" if BTC_ADDRESS else "Bitcoin address coming soon")
        self.btc_label.setFont(theme.font(8, spacing=0.2))
        self.btc_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btc_label.setStyleSheet(theme.qss(
            "color: rgba(150,166,182,0.95); background: transparent;"))
        self.btc_copy = _button("COPY")
        self.btc_copy.setFixedWidth(theme.px(58))
        self.btc_copy.setToolTip("Copy the Bitcoin donation address")
        self.btc_copy.setEnabled(bool(BTC_ADDRESS))
        self.btc_copy.clicked.connect(self._copy_btc)
        # Keep the full address on its own line; the copy button sits below so
        # it cannot squeeze the address into an ellipsis/clipped label.
        root.addWidget(self.btc_label)
        btc_actions = QHBoxLayout()
        btc_actions.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        btc_actions.addStretch(1)
        btc_actions.addWidget(self.btc_copy)
        root.addLayout(btc_actions)
        root.addSpacing(theme.px(8))

        # Appearance --------------------------------------------------
        root.addWidget(_section("Appearance"))

        ui_scale_row = QHBoxLayout()
        ui_scale_row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        ui_scale_row.setSpacing(theme.px(8))
        ui_scale_name = QLabel("UI SCALE")
        ui_scale_name.setFixedWidth(theme.px(58))
        ui_scale_name.setFont(theme.font(8, QFont.Weight.DemiBold, spacing=0.8))
        ui_scale_name.setStyleSheet(theme.qss(
            "color: rgba(150,166,182,0.95); background: transparent;"))
        self.ui_scale_slider = _slider()
        self.ui_scale_slider.setRange(75, 150)
        self.ui_scale_slider.setSingleStep(5)
        self.ui_scale_slider.setPageStep(10)
        self.ui_scale_slider.setValue(100)
        self.ui_scale_slider.setToolTip(
            "Scale the entire overlay UI; takes effect after restart")
        self.ui_scale_slider.valueChanged.connect(self._on_ui_scale)
        self.ui_scale_label = QLabel("100%")
        self.ui_scale_label.setFixedWidth(theme.px(34))
        self.ui_scale_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ui_scale_label.setFont(theme.font(9))
        self.ui_scale_label.setStyleSheet(theme.qss(
            "color: rgba(150,166,182,0.95); background: transparent;"))
        ui_scale_row.addWidget(ui_scale_name)
        ui_scale_row.addWidget(self.ui_scale_slider, 1)
        ui_scale_row.addWidget(self.ui_scale_label)
        root.addLayout(ui_scale_row)

        ui_scale_hint_row = QHBoxLayout()
        ui_scale_hint_row.setContentsMargins(theme.px(0), theme.px(0),
                                             theme.px(0), theme.px(0))
        ui_scale_hint_row.setSpacing(theme.px(8))
        self.ui_scale_hint = QLabel("Applies after restart")
        self.ui_scale_hint.setFont(theme.font(7))
        self.ui_scale_hint.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: transparent;"))
        self.ui_scale_restart = _button("RESTART")
        self.ui_scale_restart.setFixedHeight(theme.px(20))
        self.ui_scale_restart.setToolTip(
            "Restart the overlay now to apply the new UI scale")
        self.ui_scale_restart.clicked.connect(self.restart_requested.emit)
        ui_scale_hint_row.addWidget(self.ui_scale_hint)
        ui_scale_hint_row.addStretch(1)
        ui_scale_hint_row.addWidget(self.ui_scale_restart)
        root.addLayout(ui_scale_hint_row)
        root.addSpacing(theme.px(4))

        opacity_row = QHBoxLayout()
        opacity_row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        opacity_row.setSpacing(theme.px(8))
        opacity_name = QLabel("OPACITY")
        opacity_name.setFixedWidth(theme.px(58))
        opacity_name.setFont(theme.font(8, QFont.Weight.DemiBold, spacing=0.8))
        opacity_name.setStyleSheet(theme.qss(
            "color: rgba(150,166,182,0.95); background: transparent;"))
        self.opacity_slider = _slider()
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setToolTip(
            "Opacity of the overlay panels (nav bar and settings stay fully visible)")
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        self.opacity_label = QLabel("100%")
        self.opacity_label.setFixedWidth(theme.px(34))
        self.opacity_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opacity_label.setFont(theme.font(9))
        self.opacity_label.setStyleSheet(theme.qss(
            "color: rgba(150,166,182,0.95); background: transparent;"))
        opacity_row.addWidget(opacity_name)
        opacity_row.addWidget(self.opacity_slider, 1)
        opacity_row.addWidget(self.opacity_label)
        root.addLayout(opacity_row)

        capture_row = QHBoxLayout()
        capture_row.setContentsMargins(theme.px(0), theme.px(0),
                                       theme.px(0), theme.px(0))
        capture_row.setSpacing(theme.px(8))
        capture_name = QLabel("SCREENSHOTS")
        capture_name.setFixedWidth(theme.px(58))
        capture_name.setFont(theme.font(8, QFont.Weight.DemiBold, spacing=0.8))
        capture_name.setStyleSheet(theme.qss(
            "color: rgba(150,166,182,0.95); background: transparent;"))
        self.capture_btn = _button("VISIBLE", checkable=True)
        self.capture_btn.setFixedWidth(theme.px(82))
        self.capture_btn.setChecked(True)
        self.capture_btn.setToolTip(
            "Include the overlay in screenshots and screen shares")
        self.capture_btn.toggled.connect(self._on_capture)
        self.capture_hint = QLabel("Shown in screenshots and streams")
        self.capture_hint.setFont(theme.font(7))
        self.capture_hint.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: transparent;"))
        capture_row.addWidget(capture_name)
        capture_row.addWidget(self.capture_btn)
        capture_row.addWidget(self.capture_hint, 1)
        root.addLayout(capture_row)
        root.addSpacing(theme.px(8))

        # Update ------------------------------------------------------
        root.addWidget(_section("Update"))
        update_row = QHBoxLayout()
        update_row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        update_row.setSpacing(theme.px(8))
        self.update_hint = QLabel(f"Installed {build_label()}")
        self.update_hint.setFont(theme.font(7))
        self.update_hint.setWordWrap(True)
        self.update_hint.setStyleSheet(theme.qss(
            "color: rgba(130,148,166,0.9); background: transparent;"))
        self.update_btn = _button("CHECK")
        self.update_btn.setFixedWidth(theme.px(82))
        self.update_btn.setToolTip(
            "Compare this install with GitHub, then copy the newest files")
        self.update_btn.clicked.connect(self._on_update_clicked)
        update_row.addWidget(self.update_hint, 1)
        update_row.addWidget(self.update_btn)
        root.addLayout(update_row)
        root.addSpacing(theme.px(8))

        # Capture region ----------------------------------------------
        root.addWidget(_section("Signature capture region"))
        region_row = QHBoxLayout()
        region_row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        region_row.setSpacing(theme.px(8))
        self.region_label = QLabel("Not calibrated")
        self.region_label.setFont(theme.font(9))
        self.region_label.setStyleSheet(theme.qss("color: rgba(150,166,182,0.95); background: transparent;"))
        self.region_btn = _button("SELECT")
        self.region_btn.clicked.connect(self.region_requested.emit)
        region_row.addWidget(self.region_label, 1)
        region_row.addWidget(self.region_btn)
        root.addLayout(region_row)
        root.addSpacing(theme.px(8))

        # Scanning ----------------------------------------------------
        root.addWidget(_section("Scanning"))
        scan_row = QHBoxLayout()
        scan_row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        scan_row.setSpacing(theme.px(8))
        self.pause_btn = _button("PAUSE", checkable=True)
        self.debug_btn = _button("SHOW PREVIEW", checkable=True)
        self.pause_btn.toggled.connect(self._on_pause)
        self.debug_btn.toggled.connect(self._on_debug)
        scan_row.addWidget(self.pause_btn, 1)
        scan_row.addWidget(self.debug_btn, 1)
        root.addLayout(scan_row)
        root.addSpacing(theme.px(8))

        # Voice -------------------------------------------------------
        root.addWidget(_section("Voice"))
        voice_row = QHBoxLayout()
        voice_row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        voice_row.setSpacing(theme.px(8))

        self.voice_btn = _button("ON", checkable=True)
        self.voice_btn.setFixedWidth(theme.px(52))
        self.voice_btn.setChecked(True)
        self.voice_btn.setToolTip("Speak the ore name when a signature locks")
        self.voice_btn.toggled.connect(self._on_voice)

        self.volume_slider = _slider()
        self.volume_slider.setValue(80)
        self.volume_slider.setToolTip("Announcement volume")
        self.volume_slider.valueChanged.connect(self._on_volume)

        self.volume_label = QLabel("80%")
        self.volume_label.setFixedWidth(theme.px(34))
        self.volume_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.volume_label.setFont(theme.font(9))
        self.volume_label.setStyleSheet(theme.qss(
            "color: rgba(150,166,182,0.95); background: transparent;"))

        voice_row.addWidget(self.voice_btn)
        voice_row.addWidget(self.volume_slider, 1)
        voice_row.addWidget(self.volume_label)
        root.addLayout(voice_row)

        self.preview = QLabel()
        self.preview.setFixedHeight(theme.px(78))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(theme.qss(
            "background: rgba(0,0,0,0.35); border: 1px dashed rgba(79,209,224,0.25);"
            "border-radius: 6px; color: rgba(130,148,166,0.7); font-size: 9px;"))
        self.preview.setText("Drag a box around the signature, then confirm the digits below")
        self.preview.hide()
        root.addWidget(self.preview)
        root.addSpacing(theme.px(8))

        # Signature data ----------------------------------------------
        root.addWidget(_section("Signature data"))
        data_row = QHBoxLayout()
        data_row.setContentsMargins(theme.px(0), theme.px(0), theme.px(0), theme.px(0))
        data_row.setSpacing(theme.px(8))
        self.version_label = QLabel("—")
        self.version_label.setFont(theme.font(9))
        self.version_label.setStyleSheet(theme.qss("color: rgba(150,166,182,0.95); background: transparent;"))
        self.reload_btn = _button("RELOAD")
        self.reload_btn.clicked.connect(self.reload_requested.emit)
        data_row.addWidget(self.version_label, 1)
        data_row.addWidget(self.reload_btn)
        root.addLayout(data_row)

    # ------------------------------------------------------------------
    def _on_pause(self, checked: bool) -> None:
        self.pause_btn.setText("RESUME" if checked else "PAUSE")
        self.pause_toggled.emit(checked)

    def _on_debug(self, checked: bool) -> None:
        self.preview.setVisible(checked)
        self.debug_toggled.emit(checked)
        self.adjustSize()

    def _on_voice(self, checked: bool) -> None:
        self.voice_btn.setText("ON" if checked else "OFF")
        self.volume_slider.setEnabled(checked)
        self.voice_toggled.emit(checked)

    def _on_volume(self, value: int) -> None:
        self.volume_label.setText(f"{value}%")
        self.volume_changed.emit(int(value))

    def _on_ui_scale(self, value: int) -> None:
        value = max(75, min(150, int(value)))
        self.ui_scale_label.setText(f"{value}%")
        self._refresh_scale_hint(value)
        self.ui_scale_changed.emit(value)

    def _refresh_scale_hint(self, value: int) -> None:
        """Highlight the restart hint while the chosen scale is not active."""
        pending = abs(value - round(theme.scale() * 100)) >= 1
        self.ui_scale_hint.setText(
            "Restart to apply" if pending else "Applies after restart")
        self.ui_scale_hint.setStyleSheet(theme.qss(
            "color: rgba(255,176,32,0.95); background: transparent;"
            if pending else
            "color: rgba(130,148,166,0.9); background: transparent;"))

    def _on_opacity(self, value: int) -> None:
        value = max(30, min(100, int(value)))
        self.opacity_label.setText(f"{value}%")
        self.opacity_changed.emit(value)

    def _on_capture(self, checked: bool) -> None:
        self.capture_btn.setText("VISIBLE" if checked else "HIDDEN")
        self.capture_hint.setText(
            "Shown in screenshots and streams" if checked
            else "Hidden from screenshots and streams")
        self.capture_toggled.emit(checked)

    # -- update --------------------------------------------------------
    def _on_update_clicked(self) -> None:
        if self._update_worker is not None and self._update_worker.isRunning():
            return
        if updater.is_frozen():
            self.update_hint.setText(
                "EXE build: download a new copy to update")
            return

        mode = "apply" if self._pending_version else "check"
        self.update_btn.setEnabled(False)
        self.update_btn.setText("…")
        self.update_hint.setText(
            "Copying the newest files…" if mode == "apply" else "Checking GitHub…")

        worker = _UpdateWorker(mode, self)
        worker.done.connect(self._on_update_done)
        worker.failed.connect(self._on_update_failed)
        worker.finished.connect(self._on_update_finished)
        self._update_worker = worker
        worker.start()

    def _on_update_done(self, mode: str, value: str) -> None:
        if mode == "check":
            if value and updater.is_newer(value):
                self._pending_version = value
                self.update_hint.setText(f"v{value} available")
                self.update_btn.setText("UPDATE")
            else:
                # Name both numbers: "nothing happened" usually just means the
                # installed and published versions are the same build.
                self.update_hint.setText(
                    f"Up to date · GitHub has v{value or '?'}")
                self.update_btn.setText("CHECK")
            return

        self._pending_version = ""
        self.update_hint.setText(f"Updated to v{value} · restarting…")
        self.update_btn.setText("CHECK")
        # The new files are already on disk; only a restart loads them.
        self.restart_requested.emit()

    def _on_update_failed(self, message: str) -> None:
        self.update_hint.setText(f"Update failed: {message}")
        self.update_btn.setText("UPDATE" if self._pending_version else "CHECK")

    def _on_update_finished(self) -> None:
        self.update_btn.setEnabled(True)
        self._update_worker = None

    def _copy_btc(self) -> None:
        if not BTC_ADDRESS:
            return
        QGuiApplication.clipboard().setText(BTC_ADDRESS)
        self.btc_copy.setText("COPIED")
        QTimer.singleShot(1400, lambda: self.btc_copy.setText("COPY"))

    # ------------------------------------------------------------------
    def set_region(self, region: dict | None) -> None:
        if region:
            self.region_label.setText(
                f"{region['width']} × {region['height']} px  ·  "
                f"({region['left']}, {region['top']})")
            self.region_btn.setText("CHANGE")
        else:
            self.region_label.setText("Not calibrated")
            self.region_btn.setText("SELECT")

    def set_paused(self, paused: bool) -> None:
        if self.pause_btn.isChecked() != paused:
            self.pause_btn.setChecked(paused)
        self.pause_btn.setText("RESUME" if paused else "PAUSE")

    def set_debug(self, enabled: bool) -> None:
        if self.debug_btn.isChecked() != enabled:
            self.debug_btn.setChecked(enabled)
        self.preview.setVisible(enabled)

    def set_voice(self, enabled: bool) -> None:
        if self.voice_btn.isChecked() != enabled:
            self.voice_btn.setChecked(enabled)
        self.voice_btn.setText("ON" if enabled else "OFF")
        self.volume_slider.setEnabled(enabled)

    def set_volume(self, percent: int) -> None:
        value = max(0, min(100, int(percent)))
        if self.volume_slider.value() != value:
            self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(value)
            self.volume_slider.blockSignals(False)
        self.volume_label.setText(f"{value}%")

    def set_opacity(self, percent: int) -> None:
        value = max(30, min(100, int(percent)))
        if self.opacity_slider.value() != value:
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(value)
            self.opacity_slider.blockSignals(False)
        self.opacity_label.setText(f"{value}%")

    def set_capture_visible(self, visible: bool, locked: bool = False) -> None:
        if self.capture_btn.isChecked() != bool(visible):
            self.capture_btn.blockSignals(True)
            self.capture_btn.setChecked(bool(visible))
            self.capture_btn.blockSignals(False)
        self.capture_btn.setText("VISIBLE" if visible else "HIDDEN")
        self.capture_btn.setEnabled(not locked)
        if locked:
            self.capture_hint.setText("Forced by --capture-visible")
        else:
            self.capture_hint.setText(
                "Shown in screenshots and streams" if visible
                else "Hidden from screenshots and streams")

    def set_ui_scale(self, percent: int) -> None:
        value = max(75, min(150, int(percent)))
        if self.ui_scale_slider.value() != value:
            self.ui_scale_slider.blockSignals(True)
            self.ui_scale_slider.setValue(value)
            self.ui_scale_slider.blockSignals(False)
        self.ui_scale_label.setText(f"{value}%")
        self._refresh_scale_hint(value)

    def set_voice_hint(self, text: str) -> None:
        self.voice_btn.setToolTip(
            text or "Speak the ore name when a signature locks")

    def set_version(self, version: str, count: int) -> None:
        self.version_label.setText(f"Game {version}  ·  {count} ores loaded")

    def set_preview(self, crop) -> None:
        if crop is None or not self.preview.isVisible():
            return
        try:
            rgb = crop[:, :, ::-1].copy()
            h, w = rgb.shape[:2]
            image = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            pix = QPixmap.fromImage(image).scaledToHeight(
                self.preview.height() - 10,
                Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(pix)
        except Exception:
            self.preview.setText("preview unavailable")

    # ------------------------------------------------------------------
    def place_default(self, anchor: QWidget | None = None) -> None:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        if anchor is not None and anchor.isVisible():
            a = anchor.geometry()
            self.move(a.x(), a.bottom() + 12)
        else:
            self.move(geo.center().x() - self.width() // 2, geo.top() + 110)

    def clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) \
            or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(self.x(), geo.left() - 20), geo.right() - self.width() + 20)
        y = min(max(self.y(), geo.top()), geo.bottom() - 60)
        self.move(x, y)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 40:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag_from is not None:
            self._drag_from = None
            self.clamp_to_screen()

    # ------------------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p, w, h = theme.paint(self)
        theme.paint_card(p, QRectF(0, 0, w, h), radius=14,
                         fill=QColor(10, 14, 20, 250))
        p.setPen(QPen(QColor(79, 209, 224, 90), 1.0))
        p.drawLine(18, 1, w - 18, 1)
