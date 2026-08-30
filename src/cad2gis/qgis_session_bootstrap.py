"""QGIS-side half of the typed CAD2GIS desktop session protocol.

This file is executed by QGIS through ``--code``.  It intentionally exposes a
small command dispatcher instead of an eval/exec endpoint.
"""

from __future__ import annotations

import builtins
import json
import os
import secrets
import time
from pathlib import Path

from qgis.PyQt.QtCore import QObject, QSize, QTimer
from qgis.PyQt.QtNetwork import QHostAddress, QTcpServer
from qgis.core import (
    Qgis,
    QgsMapRendererSequentialJob,
    QgsProject,
    QgsProviderRegistry,
    QgsProviderSublayerDetails,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.utils import iface


SESSION_SCHEMA = "cad2gis.qgis_session.v1"
DESCRIPTOR_ENV = "CAD2GIS_QGIS_SESSION_DESCRIPTOR"


class BridgeError(RuntimeError):
    pass


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class Cad2gisQgisBridge(QObject):
    def __init__(self, descriptor_path: Path):
        super().__init__()
        self.descriptor_path = descriptor_path
        self.descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        if self.descriptor.get("schema_version") != SESSION_SCHEMA:
            raise BridgeError("Unsupported QGIS session descriptor schema")
        self.token = str(self.descriptor.get("token", ""))
        if len(self.token) < 32:
            raise BridgeError("QGIS session token is missing")
        self.allowed_roots = tuple(
            Path(value).expanduser().resolve()
            for value in self.descriptor.get("allowed_roots", [])
        )
        if not self.allowed_roots:
            raise BridgeError("QGIS session has no allowed project roots")
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._accept_connections)
        address = QHostAddress("127.0.0.1")
        port = int(self.descriptor["port"])
        if not self.server.listen(address, port):
            raise BridgeError(f"Could not listen on 127.0.0.1:{port}")
        self._buffers = {}
        self._write_state("ready")

    def _write_state(self, status: str, **extra) -> None:
        self.descriptor.update(
            {
                "status": status,
                "pid": os.getpid(),
                "qgis_version": str(Qgis.QGIS_VERSION),
                "updated_at_unix": time.time(),
                **extra,
            }
        )
        _atomic_json(self.descriptor_path, self.descriptor)

    def _path(self, value: str, *, must_exist: bool = True) -> Path:
        path = Path(value).expanduser().resolve()
        if not any(path == root or root in path.parents for root in self.allowed_roots):
            raise BridgeError(f"Path is outside configured CAD2GIS roots: {path}")
        if must_exist and not path.exists():
            raise BridgeError(f"Path does not exist: {path}")
        return path

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            key = id(connection)
            self._buffers[key] = bytearray()
            connection.readyRead.connect(
                lambda connection=connection: self._read_connection(connection)
            )
            connection.disconnected.connect(
                lambda key=key, connection=connection: self._drop_connection(
                    key, connection
                )
            )

    def _drop_connection(self, key: int, connection) -> None:
        self._buffers.pop(key, None)
        connection.deleteLater()

    def _read_connection(self, connection) -> None:
        key = id(connection)
        buffer = self._buffers.setdefault(key, bytearray())
        buffer.extend(bytes(connection.readAll()))
        if len(buffer) > 8 * 1024 * 1024:
            self._reply(connection, False, error="Request exceeded 8 MiB")
            return
        if b"\n" not in buffer:
            return
        line, _, _ = bytes(buffer).partition(b"\n")
        self._buffers.pop(key, None)
        try:
            request = json.loads(line.decode("utf-8"))
            result = self._dispatch(request)
        except Exception as exc:
            self._reply(connection, False, error=str(exc))
            return
        self._reply(connection, True, result=result)

    def _reply(self, connection, ok: bool, *, result=None, error: str = "") -> None:
        payload = {"ok": bool(ok)}
        if ok:
            payload["result"] = result or {}
        else:
            payload["error"] = error or "QGIS session request failed"
        connection.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        connection.flush()
        connection.disconnectFromHost()

    def _dispatch(self, request: dict) -> dict:
        if not isinstance(request, dict) or request.get("schema_version") != SESSION_SCHEMA:
            raise BridgeError("Unsupported QGIS session request schema")
        supplied = str(request.get("token", ""))
        if not secrets.compare_digest(supplied, self.token):
            raise BridgeError("QGIS session token was rejected")
        command = str(request.get("command", ""))
        parameters = request.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise BridgeError("QGIS session parameters must be an object")
        handlers = {
            "status": self.status,
            "open_project": self.open_project,
            "load_layers": self.load_layers,
            "set_layer_visibility": self.set_layer_visibility,
            "zoom_full_extent": self.zoom_full_extent,
            "export_view": self.export_view,
            "shutdown": self.shutdown,
        }
        handler = handlers.get(command)
        if handler is None:
            raise BridgeError(f"Unsupported QGIS session command: {command}")
        return handler(**parameters)

    def _layer_payload(self, layer) -> dict:
        layer_type = "raster" if isinstance(layer, QgsRasterLayer) else "vector"
        tree = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        return {
            "id": layer.id(),
            "name": layer.name(),
            "source": layer.source(),
            "provider": layer.providerType(),
            "type": layer_type,
            "valid": bool(layer.isValid()),
            "visible": bool(tree.isVisible()) if tree is not None else False,
        }

    def status(self) -> dict:
        project = QgsProject.instance()
        canvas = iface.mapCanvas()
        extent = canvas.extent()
        layers = sorted(
            (self._layer_payload(layer) for layer in project.mapLayers().values()),
            key=lambda item: (item["name"].casefold(), item["id"]),
        )
        return {
            "schema_version": "cad2gis.qgis_session_status.v1",
            "qgis_version": str(Qgis.QGIS_VERSION),
            "pid": os.getpid(),
            "project_file": project.fileName(),
            "project_dirty": bool(project.isDirty()),
            "layer_count": len(layers),
            "layers": layers,
            "canvas_extent": [
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum(),
            ],
            "bridge": "typed_loopback",
            "arbitrary_python": False,
        }

    def open_project(self, path: str) -> dict:
        project_path = self._path(path)
        if project_path.suffix.casefold() not in {".qgs", ".qgz"}:
            raise BridgeError("QGIS project path must end in .qgs or .qgz")
        if not QgsProject.instance().read(str(project_path)):
            raise BridgeError(f"QGIS could not open project: {project_path}")
        iface.mapCanvas().refresh()
        return self.status()

    def _style_path(self, styles_dir: Path | None, layer_name: str) -> Path | None:
        if styles_dir is None:
            return None
        direct = styles_dir / f"{layer_name}.qml"
        if direct.is_file():
            return direct
        folded = layer_name.casefold()
        matches = [
            path
            for path in styles_dir.glob("*.qml")
            if path.stem.casefold() == folded
        ]
        return matches[0] if len(matches) == 1 else None

    def _add_layer(self, layer, styles_dir: Path | None) -> dict:
        if layer is None or not layer.isValid():
            raise BridgeError("QGIS provider returned an invalid layer")
        QgsProject.instance().addMapLayer(layer)
        style = self._style_path(styles_dir, layer.name())
        style_loaded = False
        style_message = ""
        if style is not None:
            response = layer.loadNamedStyle(str(style))
            if isinstance(response, tuple):
                style_message = str(response[0])
                style_loaded = bool(response[1])
            else:
                style_loaded = bool(response)
            layer.triggerRepaint()
        payload = self._layer_payload(layer)
        payload.update(
            {
                "style": str(style) if style is not None else None,
                "style_loaded": style_loaded,
                "style_message": style_message,
            }
        )
        return payload

    def _invalid_sublayer_payload(self, detail, layer) -> dict:
        message = "provider returned no layer"
        if layer is not None:
            try:
                message = str(layer.error().message()) or "layer is invalid"
            except Exception:
                message = "layer is invalid"
        return {
            "name": str(detail.name()),
            "source": str(detail.uri()),
            "provider": str(detail.providerKey()),
            "valid": False,
            "error": message,
        }

    def load_layers(
        self,
        path: str,
        styles_dir: str = "",
        clear_existing: bool = False,
    ) -> dict:
        if not isinstance(clear_existing, bool):
            raise BridgeError("clear_existing must be a boolean")
        source = self._path(path)
        style_root = self._path(styles_dir) if styles_dir else None
        if style_root is not None and not style_root.is_dir():
            raise BridgeError("styles_dir must be a directory")
        candidate_layers = []
        skipped = []
        details = QgsProviderRegistry.instance().querySublayers(str(source))
        if details:
            options = QgsProviderSublayerDetails.LayerOptions(
                QgsProject.instance().transformContext()
            )
            for detail in details:
                layer = detail.toLayer(options)
                if layer is None or not layer.isValid():
                    skipped.append(self._invalid_sublayer_payload(detail, layer))
                    continue
                candidate_layers.append(layer)
        elif source.suffix.casefold() in {".tif", ".tiff", ".vrt", ".img"}:
            candidate_layers.append(QgsRasterLayer(str(source), source.stem))
        else:
            candidate_layers.append(QgsVectorLayer(str(source), source.stem, "ogr"))
        valid_layers = [
            layer for layer in candidate_layers if layer is not None and layer.isValid()
        ]
        if not valid_layers:
            names = ", ".join(item["name"] for item in skipped[:8])
            raise BridgeError(f"QGIS provider returned no valid layers: {names}")
        project = QgsProject.instance()
        if clear_existing:
            project.clear()
            project.setDirty(False)
        loaded = [self._add_layer(layer, style_root) for layer in valid_layers]
        iface.mapCanvas().zoomToFullExtent()
        iface.mapCanvas().refresh()
        return {
            "schema_version": "cad2gis.qgis_layers_loaded.v1",
            "source": str(source),
            "loaded_count": len(loaded),
            "loaded_layers": loaded,
            "skipped_count": len(skipped),
            "skipped_layers": skipped,
            "session": self.status(),
        }

    def _find_layer(self, value: str):
        project = QgsProject.instance()
        if value in project.mapLayers():
            return project.mapLayer(value)
        matches = [
            layer
            for layer in project.mapLayers().values()
            if layer.name().casefold() == value.casefold()
        ]
        if len(matches) != 1:
            raise BridgeError(
                f"Layer must match one unique ID or name; found {len(matches)} for {value!r}"
            )
        return matches[0]

    def set_layer_visibility(self, layer: str, visible: bool) -> dict:
        selected = self._find_layer(layer)
        node = QgsProject.instance().layerTreeRoot().findLayer(selected.id())
        if node is None:
            raise BridgeError(f"Layer tree node is missing: {selected.id()}")
        node.setItemVisibilityChecked(bool(visible))
        iface.mapCanvas().refresh()
        return {"layer": self._layer_payload(selected), "session": self.status()}

    def zoom_full_extent(self) -> dict:
        iface.mapCanvas().zoomToFullExtent()
        iface.mapCanvas().refresh()
        return self.status()

    def export_view(self, path: str, width: int = 1600, height: int = 1000) -> dict:
        output = self._path(path, must_exist=False)
        if output.suffix.casefold() != ".png":
            raise BridgeError("QGIS view export currently requires a .png output")
        width = int(width)
        height = int(height)
        if not 320 <= width <= 8192 or not 240 <= height <= 8192:
            raise BridgeError("QGIS view dimensions are outside the supported range")
        output.parent.mkdir(parents=True, exist_ok=True)
        settings = iface.mapCanvas().mapSettings()
        settings.setOutputSize(QSize(width, height))
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if not image.save(str(output), "PNG"):
            raise BridgeError(f"QGIS could not save the rendered map: {output}")
        return {
            "schema_version": "cad2gis.qgis_view_export.v1",
            "path": str(output),
            "width": width,
            "height": height,
            "bytes": output.stat().st_size,
            "png_signature": output.read_bytes()[:8].hex(),
        }

    def shutdown(self) -> dict:
        self._write_state("stopping")
        project = QgsProject.instance()
        project.clear()
        project.setDirty(False)
        QTimer.singleShot(100, iface.mainWindow().close)
        return {"status": "stopping", "pid": os.getpid()}


def _start() -> Cad2gisQgisBridge:
    value = os.environ.get(DESCRIPTOR_ENV, "").strip()
    if not value:
        raise BridgeError(f"{DESCRIPTOR_ENV} is required")
    return Cad2gisQgisBridge(Path(value).expanduser().resolve())


# Keep the QObject alive after the --code script finishes.
builtins._cad2gis_qgis_bridge = _start()
