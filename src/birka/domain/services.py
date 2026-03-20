from __future__ import annotations

from typing import Dict, Iterable, List

from birka.domain.model import Diagram, SequenceFlow, TaskView


class DiagramFactory:
    @staticmethod
    def create(diagram_id: str, tasks: Iterable[TaskView], flows: Iterable[SequenceFlow]) -> Diagram:
        tasks_list = list(tasks)
        task_map: Dict[str, TaskView] = {task.task_id: task for task in tasks_list}
        DiagramFactory._ensure_unique_tasks(task_map, tasks_list)
        flows_list = list(flows)
        DiagramFactory._ensure_flow_references(task_map, flows_list)
        return Diagram(diagram_id=diagram_id, tasks=task_map, flows=flows_list)

    @staticmethod
    def _ensure_unique_tasks(task_map: Dict[str, TaskView], tasks: List[TaskView]) -> None:
        if len(task_map) != len(tasks):
            raise ValueError("Duplicate task ids detected in diagram.")

    @staticmethod
    def _ensure_flow_references(task_map: Dict[str, TaskView], flows: List[SequenceFlow]) -> None:
        for flow in flows:
            if flow.head_task_id not in task_map:
                raise ValueError(f"Flow {flow.flow_id} references missing head task {flow.head_task_id}.")
            if flow.tail_task_id not in task_map:
                raise ValueError(f"Flow {flow.flow_id} references missing tail task {flow.tail_task_id}.")
