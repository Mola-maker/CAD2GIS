"""CAD2GIS QGIS plugin main class 鈥?adds a dockwidget, runs the canonical pipeline, loads results."""
from __future__ import annotations

import os


class Cad2gisPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None

    # ---- QGIS plugin lifecycle ----
    def initGui(self):  # noqa: N802 - QGIS-contracted name
        from .dockwidget import Cad2gisDock

        self.dock = Cad2gisDock(self.iface)
        self.iface.addDockWidget(self.dock.area, self.dock)
        self.dock.show()

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    # ---- helpers exposed to the dockwidget ----
    @staticmethod
    def repo_root() -> str:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
