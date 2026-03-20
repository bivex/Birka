from __future__ import annotations

from birka.application.ports import DiagramSource
from birka.domain.model import Diagram


class LoadDiagram:
    def __init__(self, source: DiagramSource) -> None:
        self._source = source

    def execute(self) -> Diagram:
        return self._source.load()
