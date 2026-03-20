from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from birka.application.ports import DiagramSource
from birka.domain.model import Diagram, Point, Rect, SequenceFlow, TaskView
from birka.domain.services import DiagramFactory


@dataclass(frozen=True)
class _RawTask:
    task_id: str
    name: str
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class _RawFlow:
    flow_id: str
    head_id: str
    tail_id: str
    points: List[Point]


class JsonDiagramSource(DiagramSource):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Diagram:
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        diagram_id = payload.get("context", {}).get("id", "diagram")
        raw_tasks = list(self._extract_tasks(payload.get("data", [])))
        raw_flows = list(self._extract_flows(payload.get("data", [])))
        tasks = [TaskView(task.task_id, task.name, Rect(task.left, task.top, task.width, task.height)) for task in raw_tasks]
        flows = [SequenceFlow(flow.flow_id, flow.head_id, flow.tail_id, flow.points) for flow in raw_flows]
        return DiagramFactory.create(diagram_id=diagram_id, tasks=tasks, flows=flows)

    def _extract_tasks(self, items: Iterable[dict]) -> Iterable[_RawTask]:
        for item in items:
            if item.get("_type") != "BPMNTaskView":
                continue
            label = _find_label(item.get("subViews", []))
            name = label or item.get("nameLabel", {}).get("text", "") or "Unnamed Task"
            yield _RawTask(
                task_id=item.get("_id", ""),
                name=name,
                left=float(item.get("left", 0)),
                top=float(item.get("top", 0)),
                width=float(item.get("width", 0)),
                height=float(item.get("height", 0)),
            )

    def _extract_flows(self, items: Iterable[dict]) -> Iterable[_RawFlow]:
        for item in items:
            if item.get("_type") != "BPMNSequenceFlowView":
                continue
            head_ref = item.get("head", {}).get("$ref")
            tail_ref = item.get("tail", {}).get("$ref")
            points = _parse_points(item.get("points", ""))
            if not head_ref or not tail_ref:
                continue
            yield _RawFlow(flow_id=item.get("_id", ""), head_id=head_ref, tail_id=tail_ref, points=points)


def _parse_points(raw: str) -> List[Point]:
    points: List[Point] = []
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        x_str, y_str = pair.split(":")
        points.append(Point(float(x_str), float(y_str)))
    return points


def _find_label(subviews: Iterable[dict]) -> Optional[str]:
    for view in subviews:
        if view.get("_type") == "LabelView" and view.get("text"):
            return str(view.get("text"))
    return None
