import unittest

from PyQt6 import QtCore, QtWidgets

if QtCore.QCoreApplication.instance() is None:
    _app = QtWidgets.QApplication([])


class ZarrViewSmokeTests(unittest.TestCase):
    def test_import_and_widget_init(self) -> None:
        try:
            import sys
            from pathlib import Path

            module_path = Path("/Volumes/External/Code/Birka/modules/zarr-view")
            if module_path.exists() and str(module_path) not in sys.path:
                sys.path.append(str(module_path))

            from birka.presentation.zarr_library_view import _apply_qt_compat
            import zarr  # noqa: F401
            _apply_qt_compat()
            from zarrview.ZarrViewer import ZarrViewer  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.fail(f"Zarr view import failed: {exc}")

        root = zarr.group()
        viewer = ZarrViewer(root)
        self.assertIsNotNone(viewer)
