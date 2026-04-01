from __future__ import annotations

from typing import List

from PyQt6 import QtCore, QtGui, QtWidgets


class WaveformWidget(QtWidgets.QWidget):
    position_changed = QtCore.pyqtSignal(int)

    MIN_HEIGHT = 80
    COLOR_BG = "#f3f1ec"
    COLOR_PLAYED_BG = "#e0dbd2"
    COLOR_PEN = "#6b5e52"
    COLOR_PLAYHEAD = "#e74c3c"
    COLOR_WAVEFORM_PLAYED = "#4a3f35"
    CENTER_LINE_WIDTH = 1
    PLAYHEAD_WIDTH = 2

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: List[float] = []
        self._playback_ratio: float = 0.0
        self._duration_ms: int = 0
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMouseTracking(True)

    def set_samples(self, samples: List[float]) -> None:
        self._samples = samples
        self._playback_ratio = 0.0
        self.update()

    def set_position(self, position_ms: int, duration_ms: int) -> None:
        self._duration_ms = duration_ms
        if duration_ms > 0:
            self._playback_ratio = position_ms / duration_ms
        else:
            self._playback_ratio = 0.0
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._duration_ms > 0:
            ratio = event.position().x() / self.width()
            ratio = max(0.0, min(1.0, ratio))
            self._playback_ratio = ratio
            self.position_changed.emit(int(ratio * self._duration_ms))
            self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: D401, N802
        painter = QtGui.QPainter(self)
        w = self.width()
        h = self.height()
        mid_y = h / 2

        bg = QtGui.QColor(self.COLOR_BG)
        played_bg = QtGui.QColor(self.COLOR_PLAYED_BG)
        pen_color = QtGui.QColor(self.COLOR_PEN)
        playhead_color = QtGui.QColor(self.COLOR_PLAYHEAD)

        painter.fillRect(self.rect(), bg)

        playhead_x = int(self._playback_ratio * w)
        if playhead_x > 0:
            painter.fillRect(0, 0, playhead_x, h, played_bg)

        pen = QtGui.QPen(pen_color)
        pen.setWidth(self.CENTER_LINE_WIDTH)
        painter.setPen(pen)
        painter.drawLine(0, int(mid_y), w, int(mid_y))

        if not self._samples:
            return

        step = w / max(1, len(self._samples))
        for i, amp in enumerate(self._samples):
            x = int(i * step)
            bar_h = amp * mid_y
            if x < playhead_x:
                pen.setColor(QtGui.QColor(self.COLOR_WAVEFORM_PLAYED))
            else:
                pen.setColor(pen_color)
            painter.setPen(pen)
            painter.drawLine(x, int(mid_y - bar_h), x, int(mid_y + bar_h))

        pen.setColor(playhead_color)
        pen.setWidth(self.PLAYHEAD_WIDTH)
        painter.setPen(pen)
        painter.drawLine(playhead_x, 0, playhead_x, h)
