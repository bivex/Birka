from __future__ import annotations

from typing import List

from PyQt6 import QtGui, QtWidgets


class WaveformWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: List[float] = []
        self.setMinimumHeight(80)

    def set_samples(self, samples: List[float]) -> None:
        self._samples = samples
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: D401
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#f3f1ec"))
        pen = QtGui.QPen(QtGui.QColor("#6b5e52"))
        pen.setWidth(1)
        painter.setPen(pen)

        mid_y = self.height() / 2
        painter.drawLine(0, int(mid_y), self.width(), int(mid_y))

        if not self._samples:
            return
        width = self.width()
        step = width / max(1, len(self._samples))
        for i, amp in enumerate(self._samples):
            x = int(i * step)
            h = amp * (self.height() / 2)
            painter.drawLine(x, int(mid_y - h), x, int(mid_y + h))
