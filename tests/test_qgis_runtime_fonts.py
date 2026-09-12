from types import SimpleNamespace

import pytest

from cad2gis import qgis_runtime_fonts as fonts


class Font:
    def __init__(self, family="Arial", size=10):
        self.name, self.size = family, size

    def family(self):
        return self.name

    def toString(self):
        return f"{self.name},{self.size}"


@pytest.fixture
def qt(monkeypatch):
    state = SimpleNamespace(families=[], loaded=[], load_success=True, missing=set())

    class Database:
        @staticmethod
        def families():
            return state.families

        @staticmethod
        def addApplicationFont(path):
            state.loaded.append(path)
            if state.load_success:
                state.families = ["Arial"]
                return 0
            return -1

        @staticmethod
        def applicationFontFamilies(identifier):
            return state.families if identifier == 0 else []

    class Info:
        def __init__(self, font):
            pass

        def family(self):
            return next(iter(state.families), "")

    class Metrics:
        def __init__(self, font):
            pass

        def inFontUcs4(self, point):
            return point not in state.missing

    monkeypatch.setattr(fonts, "_qt", lambda: (Font, Database, Info, Metrics))
    monkeypatch.delenv("CAD2GIS_QGIS_FONT", raising=False)
    return state


def test_empty_qt_database_cannot_pass_snapshot_check(qt, monkeypatch):
    monkeypatch.setattr(fonts, "_fallback_paths", lambda: [])
    with pytest.raises(ValueError, match="no usable runtime font"):
        fonts.ensure_runtime_fonts()
    with pytest.raises(ValueError, match="no resolved font"):
        fonts.verify_rendered_text([], [(Font(), "CAD2GIS")])


def test_empty_qt_database_loads_existing_font_only_into_application(qt, tmp_path, monkeypatch):
    path = tmp_path / "Arial.ttf"
    path.write_bytes(b"font bytes")
    monkeypatch.setattr(fonts, "_fallback_paths", lambda: [path])
    result = fonts.ensure_runtime_fonts()
    assert result["available_family_count"] == 1
    assert result["resolved_family"] == "Arial"
    assert result["loaded_application_fonts"][0]["families"] == ["Arial"]
    assert len(result["loaded_application_fonts"][0]["sha256"]) == 64
    assert path.read_bytes() == b"font bytes"


def test_available_database_never_loads_fallback_font(qt, monkeypatch):
    qt.families = ["Arial"]
    monkeypatch.setattr(fonts, "_fallback_paths", lambda: pytest.fail("must not load fallback"))
    assert fonts.ensure_runtime_fonts()["loaded_application_fonts"] == []
    assert qt.loaded == []


def test_explicit_missing_or_unreadable_font_fails_without_silent_fallback(qt, tmp_path):
    path = tmp_path / "broken.ttf"
    with pytest.raises(ValueError, match="does not exist"):
        fonts.ensure_runtime_fonts(path)
    path.write_bytes(b"broken")
    qt.load_success = False
    with pytest.raises(ValueError, match="Cannot load requested"):
        fonts.ensure_runtime_fonts(path)


def test_explicit_font_precedes_environment_font(qt, tmp_path, monkeypatch):
    path = tmp_path / "requested.ttf"
    path.write_bytes(b"font")
    monkeypatch.setenv("CAD2GIS_QGIS_FONT", str(tmp_path / "missing.ttf"))
    fonts.ensure_runtime_fonts(path)
    assert qt.loaded == [str(path.resolve())]


def test_environment_font_is_loaded(qt, tmp_path, monkeypatch):
    path = tmp_path / "configured.ttf"
    path.write_bytes(b"font")
    monkeypatch.setenv("CAD2GIS_QGIS_FONT", str(path))
    fonts.ensure_runtime_fonts()
    assert qt.loaded == [str(path.resolve())]


def test_actual_rendered_glyphs_are_required_even_when_font_resolves(qt):
    qt.families = ["Arial"]
    qt.missing = {ord("中")}
    label = SimpleNamespace(isDiagram=False, isUnplaced=False, labelFont=Font(), labelText="中")
    with pytest.raises(ValueError, match=r"U\+4E2D"):
        fonts.verify_rendered_text([label], [(Font(), "CAD2GIS")])


def test_font_evidence_only_claims_placed_text(qt):
    qt.families = ["Arial"]
    labels = [SimpleNamespace(isDiagram=False, isUnplaced=False, labelFont=Font(), labelText="FAT.01"),
              SimpleNamespace(isDiagram=False, isUnplaced=True, labelFont=Font(), labelText="not placed")]
    result = fonts.verify_rendered_text(labels, [(Font(), "CAD2GIS")])
    assert result["rendered_labels_checked"] == 1
    assert result["annotations_checked"] == 1
    assert result["all_rendered_glyphs_available"] is True
    assert result["fonts"][0]["missing_codepoints"] == []


def test_enabled_layers_cannot_claim_label_glyph_verification_without_placed_labels(qt):
    qt.families = ["Arial"]
    with pytest.raises(ValueError, match="no placed labels"):
        fonts.verify_rendered_text([], [(Font(), "CAD2GIS")], expected_label_layers=["PTECH"])
    result = fonts.verify_rendered_text([], [(Font(), "CAD2GIS")])
    assert result["rendered_labels_checked"] == 0
    assert result["enabled_label_layers"] == []
