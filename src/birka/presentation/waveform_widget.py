from __future__ import annotations

from typing import List

from PyQt6 import QtCore, QtGui, QtWidgets


class WaveformWidget(QtWidgets.QWidget):
    position_changed = QtCore.pyqtSignal(int)

    MIN_HEIGHT = 80
    COLOR_BG = "#121214"
    COLOR_PEN = "#4f4f5a"
    COLOR_PLAYHEAD = "#ff007f"
    COLOR_WAVEFORM_PLAYED = "#00f0ff"
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
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        mid_y = h / 2.0

        bg = QtGui.QColor(self.COLOR_BG)
        painter.fillRect(self.rect(), bg)

        playhead_x = int(self._playback_ratio * w)

        # Soft glowing background gradient for the played portion
        if playhead_x > 0:
            bg_gradient = QtGui.QLinearGradient(0, 0, playhead_x, 0)
            bg_gradient.setColorAt(0.0, QtGui.QColor(0, 240, 255, 15))  # 6% opacity neon cyan
            bg_gradient.setColorAt(1.0, QtGui.QColor(127, 0, 255, 5))   # 2% opacity neon purple
            painter.fillRect(0, 0, playhead_x, h, QtGui.QBrush(bg_gradient))

        # Faint center axis line
        center_pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 15))
        center_pen.setWidth(1)
        painter.setPen(center_pen)
        painter.drawLine(0, int(mid_y), w, int(mid_y))

        # Dynamic bar calculation
        bar_width = 3.0
        gap = 1.5
        stride = bar_width + gap
        num_bars = int(w / stride)
        if num_bars < 1:
            num_bars = 1

        if not self._samples:
            resampled = [0.0] * num_bars
        else:
            resampled = _resample(self._samples, num_bars)

        # Set up active (played) waveform brush/pen
        played_pen = QtGui.QPen()
        played_pen.setWidthF(bar_width)
        played_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        played_gradient = QtGui.QLinearGradient(0, 0, 0, h)
        played_gradient.setColorAt(0.0, QtGui.QColor("#00f0ff"))  # Neon Cyan
        played_gradient.setColorAt(0.5, QtGui.QColor("#00aaff"))  # Cyber Blue
        played_gradient.setColorAt(1.0, QtGui.QColor("#7f00ff"))  # Electric Purple
        played_pen.setBrush(QtGui.QBrush(played_gradient))

        # Set up inactive (unplayed) waveform brush/pen
        unplayed_pen = QtGui.QPen()
        unplayed_pen.setWidthF(bar_width)
        unplayed_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        unplayed_gradient = QtGui.QLinearGradient(0, 0, 0, h)
        unplayed_gradient.setColorAt(0.0, QtGui.QColor("#555562"))  # Metallic Grey
        unplayed_gradient.setColorAt(1.0, QtGui.QColor("#2d2d35"))  # Darker Charcoal
        unplayed_pen.setBrush(QtGui.QBrush(unplayed_gradient))

        # Draw rounded waveform bars
        max_bar_h = mid_y - 4.0  # 4px padding from top/bottom
        for i, amp in enumerate(resampled):
            x = int(i * stride + bar_width / 2.0)
            bar_h = max(2.0, amp * max_bar_h)

            if x < playhead_x:
                painter.setPen(played_pen)
            else:
                painter.setPen(unplayed_pen)

            painter.drawLine(x, int(mid_y - bar_h), x, int(mid_y + bar_h))

        # Neon pink playhead with a bright top glow
        playhead_gradient = QtGui.QLinearGradient(playhead_x, 0, playhead_x, h)
        playhead_gradient.setColorAt(0.0, QtGui.QColor("#ffffff"))  # White reflection glow
        playhead_gradient.setColorAt(0.1, QtGui.QColor(self.COLOR_PLAYHEAD))
        playhead_gradient.setColorAt(1.0, QtGui.QColor(self.COLOR_PLAYHEAD))

        playhead_pen = QtGui.QPen()
        playhead_pen.setWidth(self.PLAYHEAD_WIDTH)
        playhead_pen.setBrush(QtGui.QBrush(playhead_gradient))
        painter.setPen(playhead_pen)
        painter.drawLine(playhead_x, 0, playhead_x, h)


def _resample(samples: List[float], target_len: int) -> List[float]:
    if not samples:
        return []
    if len(samples) == target_len:
        return samples
    result = []
    for i in range(target_len):
        pos = i * (len(samples) - 1) / max(1, target_len - 1)
        idx1 = int(pos)
        idx2 = min(idx1 + 1, len(samples) - 1)
        weight = pos - idx1
        val = samples[idx1] * (1.0 - weight) + samples[idx2] * weight
        result.append(val)
    return result
