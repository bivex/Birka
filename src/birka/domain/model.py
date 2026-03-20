from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height


@dataclass(frozen=True)
class TaskView:
    task_id: str
    name: str
    rect: Rect


@dataclass(frozen=True)
class SequenceFlow:
    flow_id: str
    head_task_id: str
    tail_task_id: str
    points: List[Point]


@dataclass(frozen=True)
class Diagram:
    diagram_id: str
    tasks: Dict[str, TaskView]
    flows: List[SequenceFlow]

    def get_task(self, task_id: str) -> TaskView:
        return self.tasks[task_id]
