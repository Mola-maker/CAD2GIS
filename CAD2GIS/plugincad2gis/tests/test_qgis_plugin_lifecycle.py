from __future__ import annotations

import sys
import types


def test_plugin_adds_dock_widget_with_qgis_iface_method(monkeypatch):
    class FakeDock:
        area = "right"

        def __init__(self, iface):
            self.iface = iface
            self.shown = False

        def show(self):
            self.shown = True

    fake_module = types.ModuleType("qgis_plugin.dockwidget")
    fake_module.Cad2gisDock = FakeDock
    monkeypatch.setitem(sys.modules, "qgis_plugin.dockwidget", fake_module)

    class FakeIface:
        def __init__(self):
            self.calls = []

        def addDockWidget(self, area, dock):
            self.calls.append((area, dock))

    from qgis_plugin.plugin import Cad2gisPlugin

    iface = FakeIface()
    plugin = Cad2gisPlugin(iface)
    plugin.initGui()

    assert iface.calls
    assert iface.calls[0][0] == "right"
    assert plugin.dock.shown
