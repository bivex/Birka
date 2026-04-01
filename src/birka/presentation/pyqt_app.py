from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from PyQt6 import QtCore, QtGui, QtWidgets

from birka.domain.model import Diagram, Point, Rect, SequenceFlow, TaskView


@dataclass(frozen=True)
class RenderStyle:
    task_fill: QtGui.QColor = field(default_factory=lambda: QtGui.QColor("#f8f2e8"))
    task_border: QtGui.QColor = field(default_factory=lambda: QtGui.QColor("#4a3f35"))
    task_text: QtGui.QColor = field(default_factory=lambda: QtGui.QColor("#2b251f"))
    flow_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor("#6b5e52"))
    background: QtGui.QColor = field(default_factory=lambda: QtGui.QColor("#f2efe9"))


class DiagramView(QtWidgets.QGraphicsView):
    BORDER_WIDTH = 2
    TEXT_PADDING = 5
    TEXT_WIDTH_OFFSET = 10
    FIT_MARGIN = 40

    def __init__(
        self, diagram: Diagram, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._diagram = diagram
        self._style = RenderStyle()
        self._scene = QtWidgets.QGraphicsScene(self)
        self._scene.setBackgroundBrush(self._style.background)
        self.setScene(self._scene)
        self.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
        )
        self._build_scene()

    def _build_scene(self) -> None:
        self._draw_flows(self._diagram.flows)
        self._draw_tasks(self._diagram.tasks.values())
        self._fit()

    def _draw_tasks(self, tasks: Iterable[TaskView]) -> None:
        pen = QtGui.QPen(self._style.task_border)
        pen.setWidth(self.BORDER_WIDTH)
        brush = QtGui.QBrush(self._style.task_fill)
        for task in tasks:
            rect = QtCore.QRectF(
                task.rect.left, task.rect.top, task.rect.width, task.rect.height
            )
            rect_item = self._scene.addRect(rect, pen, brush)
            rect_item.setZValue(2)
            text_item = self._scene.addText(task.name)
            text_item.setDefaultTextColor(self._style.task_text)
            text_item.setTextWidth(task.rect.width - self.TEXT_WIDTH_OFFSET)
            text_item.setPos(
                task.rect.left + self.TEXT_PADDING, task.rect.top + self.TEXT_PADDING
            )
            text_item.setZValue(3)

    def _draw_flows(self, flows: Iterable[SequenceFlow]) -> None:
        pen = QtGui.QPen(self._style.flow_color)
        pen.setWidth(self.BORDER_WIDTH)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        for flow in flows:
            if len(flow.points) < 2:
                continue
            path = QtGui.QPainterPath()
            start = flow.points[0]
            path.moveTo(start.x, start.y)
            for point in flow.points[1:]:
                path.lineTo(point.x, point.y)
            path_item = self._scene.addPath(path, pen)
            path_item.setZValue(1)

    def _fit(self) -> None:
        m = self.FIT_MARGIN
        self.setSceneRect(self._scene.itemsBoundingRect().adjusted(-m, -m, m, m))
        self.fitInView(self.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()


class MainWindow(QtWidgets.QMainWindow):
    DEFAULT_WIDTH = 900
    DEFAULT_HEIGHT = 600

    def __init__(self, diagram: Diagram) -> None:
        super().__init__()
        self.setWindowTitle("Birka Process View")
        self.setMinimumSize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self._view = DiagramView(diagram, self)
        self.setCentralWidget(self._view)
