from __future__ import annotations

from typing import Iterable, List

from PyQt6 import QtWidgets

from birka.application.rename_batch import BuildRenamePlan, FileRenamer, RenameEntry, RenamePlan, RenameTemplate
from birka.domain.media import MediaItem


class RenamePreviewDialog(QtWidgets.QDialog):
    def __init__(self, items: Iterable[MediaItem], template: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rename Preview")
        self.setMinimumSize(700, 450)
        self._items = list(items)
        self._plan = BuildRenamePlan(RenameTemplate(template)).execute(self._items)

        layout = QtWidgets.QVBoxLayout(self)

        summary = QtWidgets.QLabel(self)
        summary.setText(_summary_text(self._plan))
        layout.addWidget(summary)

        self._table = QtWidgets.QTableWidget(self)
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Current", "New", "Status"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setRowCount(len(self._plan.entries) + len(self._plan.conflicts))

        row = 0
        for entry in self._plan.entries:
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(entry.path.name))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(entry.new_name))
            self._table.setItem(row, 2, QtWidgets.QTableWidgetItem("OK"))
            row += 1
        for conflict in self._plan.conflicts:
            self._table.setItem(row, 0, QtWidgets.QTableWidgetItem(conflict.path.name))
            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(conflict.new_name))
            self._table.setItem(row, 2, QtWidgets.QTableWidgetItem(conflict.reason))
            row += 1
        layout.addWidget(self._table)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
            | QtWidgets.QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def entries(self) -> List[RenameEntry]:
        return list(self._plan.entries)


class RenameCoordinator:
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        self._parent = parent
        self._renamer = FileRenamer()

    def preview_and_apply(self, items: Iterable[MediaItem], template: str) -> None:
        dialog = RenamePreviewDialog(items, template, self._parent)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._renamer.rename(dialog.entries())


def _summary_text(plan: RenamePlan) -> str:
    return (
        f"Planned renames: {len(plan.entries)} | "
        f"Conflicts: {len(plan.conflicts)}"
    )
