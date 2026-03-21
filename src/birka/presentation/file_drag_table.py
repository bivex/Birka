from __future__ import annotations

from typing import Callable, Iterable

from PyQt6 import QtCore, QtGui, QtWidgets


class FileDragTableView(QtWidgets.QTableView):
    def __init__(self, get_paths: Callable[[], Iterable[str]], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_paths = get_paths

    def startDrag(self, supported_actions: QtCore.Qt.DropActions) -> None:  # noqa: N802
        paths = list(self._get_paths())
        if not paths:
            return
        mime = QtCore.QMimeData()
        urls = [QtCore.QUrl.fromLocalFile(path) for path in paths]
        mime.setUrls(urls)

        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.exec(supported_actions)
