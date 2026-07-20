"""QGIS plugin bootstrap for the canonical Cad2GIS runtime."""


def classFactory(iface):  # noqa: N802 - QGIS requires this exact name
    """Return the QGIS plugin instance without importing QGIS at package load."""
    from .plugin import Cad2GISPlugin

    return Cad2GISPlugin(iface)

