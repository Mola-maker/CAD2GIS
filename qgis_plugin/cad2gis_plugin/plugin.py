"""Minimal QGIS UI shell over :mod:`cad2gis_plugin.adapter`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .adapter import convert_and_load, convert_project, load_geopackage


class Cad2GISPlugin:
    """Expose canonical conversion and GeoPackage loading to QGIS."""

    MENU_NAME = "&Cad2GIS"

    def __init__(self, iface: Any) -> None:
        self.iface = iface
        self._actions: list[Any] = []
        self._loaded_layers: tuple[Any, ...] = ()

    def initGui(self) -> None:  # noqa: N802 - QGIS plugin API
        """Install the existing-GeoPackage loader action."""
        from qgis.PyQt.QtWidgets import QAction

        action = QAction("Load Cad2GIS GeoPackage…", self.iface.mainWindow())
        action.triggered.connect(self._choose_geopackage)
        self.iface.addPluginToMenu(self.MENU_NAME, action)
        self.iface.addToolBarIcon(action)
        self._actions.append(action)

    def unload(self) -> None:
        """Remove actions installed by :meth:`initGui`."""
        for action in self._actions:
            self.iface.removePluginMenu(self.MENU_NAME, action)
            self.iface.removeToolBarIcon(action)
        self._actions.clear()

    def run_conversion(
        self,
        project: Mapping[str, Any],
        *,
        load_output: bool = True,
    ) -> Any:
        """Delegate a project conversion and return the canonical result object."""
        if load_output:
            result, self._loaded_layers = convert_and_load(
                project,
                layer_loader=self.iface.addVectorLayer,
            )
            return result
        self._loaded_layers = ()
        return convert_project(project)

    def load_existing_geopackage(self, gpkg_path: str | Path) -> tuple[Any, ...]:
        """Load an already-created GeoPackage into the current QGIS project."""
        self._loaded_layers = load_geopackage(
            gpkg_path,
            layer_loader=self.iface.addVectorLayer,
        )
        return self._loaded_layers

    @property
    def loaded_layers(self) -> tuple[Any, ...]:
        """Layers most recently loaded by the plugin."""
        return self._loaded_layers

    def _choose_geopackage(self) -> None:
        from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "Load Cad2GIS GeoPackage",
            "",
            "GeoPackage (*.gpkg)",
        )
        if not path:
            return
        try:
            self.load_existing_geopackage(path)
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Cad2GIS", str(exc))


__all__ = ["Cad2GISPlugin"]

