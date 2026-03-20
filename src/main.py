from __future__ import annotations

import sys
from pathlib import Path

from PyQt6 import QtWidgets

from birka.application.use_cases import LoadDiagram
from birka.infrastructure.json_diagram_source import JsonDiagramSource
from birka.presentation.pyqt_app import MainWindow


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    data_path = project_root / "data" / "bpmn_fragment.json"
    source = JsonDiagramSource(data_path)
    diagram = LoadDiagram(source).execute()

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(diagram)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
