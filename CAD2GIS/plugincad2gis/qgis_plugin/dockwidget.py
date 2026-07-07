"""CAD2GIS dockwidget — pick a DWG/DXF, run the canonical pipeline (threaded), load the
GeoPackage layers into QGIS with the shipped QML styles, and show the accuracy report.

The pipeline runs in a QThread so the QGIS UI stays responsive during the (potentially
minutes-long) conversion. Stage events from `pipeline.run(on_stage=...)` are forwarded to the
main thread via a Qt signal and appended to the log.
"""
from __future__ import annotations

import json
import os

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QFileDialog, QPlainTextEdit, QLabel, QCheckBox, QGroupBox, QFormLayout,
    QTabWidget, QTableWidget, QTableWidgetItem,
)
from qgis.core import QgsProject, QgsVectorLayer, QgsLayerTreeGroup


class ConvertWorker(QObject):
    stage = pyqtSignal(str, object)   # (stage_name, detail_dict)
    finished = pyqtSignal(object)     # RunReport.to_dict()
    failed = pyqtSignal(str)

    def __init__(self, path: str, benchmark: str | None, warehouse: str):
        super().__init__()
        self.path = path
        self.benchmark = benchmark
        self.warehouse = warehouse

    def run(self):
        try:
            from cad2gis.pipeline import run

            def on_stage(name, detail):
                self.stage.emit(name, detail or {})

            _coll, rep = run(
                self.path, benchmark=self.benchmark, warehouse=self.warehouse,
                on_stage=on_stage,
            )
            self.finished.emit(rep.to_dict())
        except Exception as ex:  # noqa: BLE001
            self.failed.emit(str(ex))


class Cad2gisDock(QDockWidget):
    area = Qt.RightDockWidgetArea

    def __init__(self, iface):
        super().__init__("CAD2GIS")
        self.iface = iface
        self.repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self._thread = None
        self._worker = None
        self._build_ui()

    # ---- UI ----
    def _build_ui(self):
        root = QWidget()
        v = QVBoxLayout(root)

        # file picker
        g = QGroupBox("Source drawing")
        gf = QFormLayout(g)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("DS-04_comms.dxf ...")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.path_edit); row.addWidget(browse)
        gf.addRow("DWG/DXF:", row)
        v.addWidget(g)

        # options
        self.score_chk = QCheckBox("Score accuracy vs surveyed benchmark")
        self.score_chk.setChecked(True)
        v.addWidget(self.score_chk)

        # actions
        a = QHBoxLayout()
        self.convert_btn = QPushButton("Convert")
        self.convert_btn.clicked.connect(self._convert)
        self.load_btn = QPushButton("Load layers into QGIS")
        self.load_btn.clicked.connect(self._load_layers)
        self.load_btn.setEnabled(False)
        a.addWidget(self.convert_btn); a.addWidget(self.load_btn)
        v.addLayout(a)

        # log
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(2000)
        v.addWidget(self.log, 1)

        # accuracy summary
        self.acc_label = QLabel("accuracy: —")
        v.addWidget(self.acc_label)

        self._build_accuracy_doctor_tabs(v)

        self.setWidget(root)

    def _build_accuracy_doctor_tabs(self, parent):
        tabs = QTabWidget()
        self.issue_table = QTableWidget(0, 5)
        self.issue_table.setHorizontalHeaderLabels(["severity", "type", "class", "handle", "status"])
        self.evidence_view = QPlainTextEdit(); self.evidence_view.setReadOnly(True)
        self.correction_table = QTableWidget(0, 5)
        self.correction_table.setHorizontalHeaderLabels(["status", "patch", "type", "handle", "reason"])
        self.score_view = QPlainTextEdit(); self.score_view.setReadOnly(True)

        issue_tab = QWidget(); issue_layout = QVBoxLayout(issue_tab)
        issue_actions = QHBoxLayout()
        load = QPushButton("Load Review Artifacts")
        load.clicked.connect(self._load_review_artifacts)
        zoom = QPushButton("Zoom to Evidence")
        zoom.clicked.connect(self._zoom_to_selected_issue)
        need_review = QPushButton("Needs Review")
        need_review.clicked.connect(lambda: self._mark_selected_issue("needs_review"))
        reject = QPushButton("Reject")
        reject.clicked.connect(lambda: self._mark_selected_issue("rejected"))
        issue_actions.addWidget(load); issue_actions.addWidget(zoom)
        issue_actions.addWidget(need_review); issue_actions.addWidget(reject)
        issue_layout.addLayout(issue_actions)
        issue_layout.addWidget(self.issue_table)

        evidence_tab = QWidget(); evidence_layout = QVBoxLayout(evidence_tab)
        evidence_layout.addWidget(self.evidence_view)

        corrections_tab = QWidget(); corrections_layout = QVBoxLayout(corrections_tab)
        corrections_layout.addWidget(self.correction_table)

        score_tab = QWidget(); score_layout = QVBoxLayout(score_tab)
        score_layout.addWidget(self.score_view)

        tabs.addTab(issue_tab, "Issues")
        tabs.addTab(evidence_tab, "Evidence")
        tabs.addTab(corrections_tab, "Corrections")
        tabs.addTab(score_tab, "Score")
        parent.addWidget(tabs, 1)

    def _json_or_empty(self, rel_path, empty):
        path = os.path.join(self.repo, rel_path)
        if not os.path.exists(path):
            return empty
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _load_review_artifacts(self):
        diagnostics = self._json_or_empty(os.path.join("build", "diagnostics.json"), {"issues": []})
        proposals = self._json_or_empty(os.path.join("build", "doctor_proposals.json"), {"proposals": []})
        verification = self._json_or_empty(os.path.join("build", "verification_after_corrections.json"), {"status": "not_run"})
        records = []
        corr_dir = os.path.join(self.repo, "build", "corrections")
        if os.path.isdir(corr_dir):
            for name in sorted(os.listdir(corr_dir)):
                if not name.endswith(".jsonl"):
                    continue
                with open(os.path.join(corr_dir, name), "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            records.append(json.loads(line))
        self._populate_issues(diagnostics.get("issues", []))
        self._populate_corrections(records)
        self.evidence_view.setPlainText(json.dumps({
            "diagnostics": diagnostics,
            "doctor_proposals": proposals,
        }, ensure_ascii=False, indent=2))
        self.score_view.setPlainText(json.dumps(verification, ensure_ascii=False, indent=2))
        self.log.appendPlainText(f"loaded review artifacts: {len(diagnostics.get('issues', []))} issues, {len(records)} ledger records")

    def _populate_issues(self, issues):
        self.issue_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            for col, key in enumerate(["severity", "issue_type", "feature_class", "source_handle", "status"]):
                self.issue_table.setItem(row, col, QTableWidgetItem(str(issue.get(key, ""))))

    def _populate_corrections(self, records):
        self.correction_table.setRowCount(len(records))
        for row, record in enumerate(records):
            for col, key in enumerate(["status", "patch_id", "patch_type", "source_handle", "reason"]):
                self.correction_table.setItem(row, col, QTableWidgetItem(str(record.get(key, ""))))

    def _mark_selected_issue(self, status):
        row = self.issue_table.currentRow()
        if row < 0:
            self.log.appendPlainText("choose an issue first")
            return
        self.issue_table.setItem(row, 4, QTableWidgetItem(status))
        self.log.appendPlainText(f"issue marked {status} in dock state only")

    def _zoom_to_selected_issue(self):
        row = self.issue_table.currentRow()
        if row < 0:
            self.log.appendPlainText("choose an issue first")
            return
        item = self.issue_table.item(row, 3)
        handle = item.text() if item else ""
        self.log.appendPlainText(f"zoom requested for source handle {handle}; map-layer mutation is not automatic")

    # ---- actions ----
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a drawing", "",
                                               "CAD drawings (*.dxf *.dwg)")
        if path:
            self.path_edit.setText(path)

    def _convert(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            self.log.appendPlainText("✗ choose an existing file first")
            return
        # DWG -> DXF if needed (the pipeline ingests DWG too, but pre-normalizing gives clearer errors)
        bench = None
        if self.score_chk.isChecked():
            bench = os.path.join(self.repo, "src", "cad2gis", "verify", "benchmark", "ds04_surveyed.json")
        warehouse = os.path.join(self.repo, "build", "qgis_run.gpkg")

        self.convert_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.log.clear()
        self.log.appendPlainText(f"converting {os.path.basename(path)} …")

        self._worker = ConvertWorker(path, bench, warehouse)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.stage.connect(self._on_stage)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_stage(self, name, detail):
        status = detail.get("status", "") if isinstance(detail, dict) else ""
        extra = ""
        if "entities" in (detail or {}):
            extra = f" — {detail['entities']} entities"
        elif "connectivity" in (detail or {}):
            extra = f" — connectivity {detail['connectivity']:.3f}"
        self.log.appendPlainText(f"  {name:12} {status}{extra}")

    def _on_finished(self, rep: dict):
        self.convert_btn.setEnabled(True)
        self.load_btn.setEnabled(True)
        acc = rep.get("accuracy") or {}
        overall = acc.get("overall")
        self.acc_label.setText(f"accuracy: {overall:.4f}" if overall is not None else "accuracy: (not scored)")
        if acc.get("dimensions"):
            for d in acc["dimensions"]:
                self.log.appendPlainText(f"     {d['name']:11} {d['score']:.4f}  {d.get('details','')}")
        self.log.appendPlainText(f"✓ done — GeoPackage: build/qgis_run.gpkg")

    def _on_failed(self, msg: str):
        self.convert_btn.setEnabled(True)
        self.log.appendPlainText(f"✗ failed: {msg}")

    def _load_layers(self):
        gpkg = os.path.join(self.repo, "build", "qgis_run.gpkg")
        if not os.path.exists(gpkg):
            self.log.appendPlainText("✗ no GeoPackage — convert first")
            return
        styles_dir = os.path.join(self.repo, "src", "cad2gis", "warehouse", "styles")
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        group = root.insertGroup(0, "CAD2GIS")

        import fiona
        loaded = 0
        for layer_name in fiona.listlayers(gpkg):
            if layer_name.startswith("cad2gis_"):
                continue  # metadata table, not spatial
            uri = f"{gpkg}|layername={layer_name}"
            vl = QgsVectorLayer(uri, layer_name, "ogr")
            if not vl.isValid():
                self.log.appendPlainText(f"  ✗ {layer_name} invalid")
                continue
            qml = os.path.join(styles_dir, f"{layer_name}.qml")
            if os.path.exists(qml):
                vl.loadNamedStyle(qml)
            proj.addMapLayer(vl, False)
            group.addLayer(vl)
            loaded += 1
        self.log.appendPlainText(f"✓ loaded {loaded} layers into QGIS (CAD2GIS group)")
        self.iface.messageBar().pushInfo("CAD2GIS", f"loaded {loaded} layers")
