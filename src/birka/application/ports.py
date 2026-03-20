from __future__ import annotations

from abc import ABC, abstractmethod

from birka.domain.model import Diagram


class DiagramSource(ABC):
    @abstractmethod
    def load(self) -> Diagram:
        raise NotImplementedError
