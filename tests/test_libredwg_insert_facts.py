"""Issue 4 contract tests: authoritative LibreDWG INSERT transform facts.

These tests use fixtures/monkeypatches only.  Real-DWG smoke coverage lives in
``tools/diagnostics/libredwg_insert_probe.py`` and the project regeneration
commands, so CI does not depend on the raw APD files.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

import cad2gis.reader.libredwg as libredwg
from cad2gis.cad2gis_v3.model import SourceEntity
from cad2gis.cad2gis_v3.ports import resolve_insert_affine

TRANSFORM_SCHEMA = "cad2gis.reader-transform-facts.v1"


class _Vec3:
    def __init__(self, x: float, y: float, z: float):
        self.x = x
        self.y = y
        self.z = z


class _Handle:
    def __init__(self, value: int):
        self.value = value


class _HeaderObject:
    def __init__(self, handle: int, name: str, base: tuple[float, float, float]):
        self.type = 49  # DWG_TYPE_BLOCK_HEADER
        self.handle = _Handle(handle)
        self.tio = _Namespace()
        self.tio.object = _Namespace()
        self.tio.object.tio = _Namespace()
        self.tio.object.tio.BLOCK_HEADER = _Namespace()
        self.tio.object.tio.BLOCK_HEADER.this = 12345
        self.tio.object.tio.BLOCK_HEADER.name = name
        self.tio.object.tio.BLOCK_HEADER.base_pt = _Vec3(*base)


class _Namespace:
    pass


class _BlockRef:
    def __init__(self, obj: _HeaderObject):
        self.obj = obj


class _InsertEntity:
    def __init__(self, *, insertion, scale, rotation, extrusion, header: _HeaderObject):
        self.tio = _Namespace()
        struct = _Namespace()
        struct.ins_pt = _Vec3(*insertion)
        struct.scale = _Vec3(*scale)
        struct.rotation = rotation
        struct.extrusion = _Vec3(*extrusion)
        struct.block_header = _BlockRef(header)
        self.tio.INSERT = struct
        self.ownerhandle = None
        self.color = _Namespace()
        self.color.index = 256
        self.color.rgb = "000000"
        self.linewt = -1


class _InsertObject:
    def __init__(self, handle: int, entity: _InsertEntity):
        self.handle = _Handle(handle)
        self.type = 7  # DWG_TYPE_INSERT
        self.tio = _Namespace()
        self.tio.entity = entity


def _build_insert_record(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(libredwg, "_layer_name", lambda _ptr: "POLE")
    monkeypatch.setattr(libredwg, "_parse_dwg_color", lambda _color: (7, None))
    monkeypatch.setattr(
        libredwg, "_resolve_effective_color",
        lambda aci, tc, ltype, layer, styles: (7, "#000000", "style:0"),
    )
    monkeypatch.setattr(
        libredwg, "_entity_utf8_text",
        lambda ptr, object_kind, field: "POLE" if field == "name" else "",
    )
    monkeypatch.setattr(libredwg, "DWG_TYPE_BLOCK_HEADER", 49)
    monkeypatch.setattr(libredwg, "DWG_TYPE_INSERT", 7)

    header = _HeaderObject(0x10, "POLE", (1.0, 2.0, 3.0))
    entity = _InsertEntity(
        insertion=(10.0, 20.0, 4.0),
        scale=(2.0, 3.0, 4.0),
        rotation=0.25,
        extrusion=(0.0, 0.0, 1.0),
        header=header,
    )
    obj = _InsertObject(0xABC, entity)
    return libredwg._build_record(
        source_path=Path("/tmp/fixture.dwg"),
        source_sha256="a" * 64,
        obj=obj,
        entity=entity,
        entity_ptr=777,
        dwg_type_name="INSERT",
        object_name="AcDbBlockReference",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer_styles={"POLE": {"aci": 7, "linetype": "Continuous", "lineweight": -1}},
        reasons=[],
        anon_block_names={},
        block_base_points={0x10: (1.0, 2.0, 3.0)},
        block_base_point_statuses={0x10: "available"},
        owner_attribs={},
    )


def test_reader_emits_six_authoritative_insert_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _build_insert_record(monkeypatch)

    facts = record["raw_properties"]["transform_facts"]
    assert facts["schema_version"] == TRANSFORM_SCHEMA
    assert facts["insertion_point"] == [10.0, 20.0, 4.0]
    assert facts["block_base_point"] == [1.0, 2.0, 3.0]
    assert facts["scale"] == [2.0, 3.0, 4.0]
    assert facts["rotation"] == pytest.approx(0.25)
    assert facts["normal"] == [0.0, 0.0, 1.0]
    assert facts["extrusion"] == [0.0, 0.0, 1.0]
    for field in (
        "insertion_point", "block_base_point", "scale",
        "rotation", "normal", "extrusion",
    ):
        assert facts[f"{field}_status"] == "available"

    provenance = record["raw_properties"]["transform_facts_provenance"]
    assert provenance == {
        "insertion_point": "DWG_DIRECT:LibreDWG:INSERT.ins_pt",
        "block_base_point": "DWG_DIRECT:LibreDWG:BLOCK_HEADER.base_pt",
        "scale": "DWG_DIRECT:LibreDWG:INSERT.scale",
        "rotation": "DWG_DIRECT:LibreDWG:INSERT.rotation",
        "normal": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
        "extrusion": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
    }

    # The legacy top-level compatibility fields and old raw-property slots
    # must agree with the authoritative container, not contradict it.
    assert record["scale_x"] == pytest.approx(2.0)
    assert record["scale_y"] == pytest.approx(3.0)
    assert record["scale_z"] == pytest.approx(4.0)
    assert record["rotation"] == pytest.approx(0.25)
    assert record["raw_properties"]["insertion_point"] == [10.0, 20.0, 4.0]
    assert record["raw_properties"]["block_base_point"] == [1.0, 2.0, 3.0]


def test_reader_marks_unreadable_insert_fact_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _build_insert_record(monkeypatch)
    facts = record["raw_properties"]["transform_facts"]
    facts["rotation"] = None
    facts["rotation_status"] = "unavailable"
    facts["normal"] = None
    facts["normal_status"] = "unavailable"
    entity = SourceEntity.from_record(record)

    affine, diagnostics = resolve_insert_affine(entity)

    assert affine is None
    assert diagnostics[0]["code"] == "missing_block_transform_facts"
    assert set(diagnostics[0]["missing_facts"]) == {"rotation", "normal"}


def test_resolve_insert_affine_applies_translation_rotation_scale_and_block_base() -> None:
    entity = SourceEntity.from_record({
        "entity_key": "insert",
        "source_sha256": "a" * 64,
        "source_file": "fixture.dwg",
        "handle": "A1",
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": "POLE",
        "object_name": "AcDbBlockReference",
        "dwg_type_name": "INSERT",
        "points": ((10.0, 20.0),),
        "centroid": (10.0, 20.0),
        "closed": False,
        "text": "",
        "block_name": "POLE",
        "block_attributes": {},
        "raw_properties": {
            "transform_facts": {
                "schema_version": TRANSFORM_SCHEMA,
                "insertion_point": [10.0, 20.0, 0.0],
                "insertion_point_status": "available",
                "block_base_point": [1.0, 2.0, 0.0],
                "block_base_point_status": "available",
                "scale": [2.0, 3.0, 1.0],
                "scale_status": "available",
                "rotation": math.pi / 2.0,
                "rotation_status": "available",
                "normal": [0.0, 0.0, 1.0],
                "normal_status": "available",
                "extrusion": [0.0, 0.0, 1.0],
                "extrusion_status": "available",
            },
            "transform_facts_provenance": {
                "insertion_point": "DWG_DIRECT:LibreDWG:INSERT.ins_pt",
                "block_base_point": "DWG_DIRECT:LibreDWG:BLOCK_HEADER.base_pt",
                "scale": "DWG_DIRECT:LibreDWG:INSERT.scale",
                "rotation": "DWG_DIRECT:LibreDWG:INSERT.rotation",
                "normal": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
                "extrusion": "DWG_DIRECT:LibreDWG:INSERT.extrusion",
            },
        },
    })

    affine, diagnostics = resolve_insert_affine(entity)

    assert not diagnostics
    assert affine is not None
    # R*S*(1,2) = (-6, 2); insertion - that = (16, 18).
    assert affine.apply((0.0, 0.0)) == pytest.approx((16.0, 18.0))
    # (1,0) in definition space → (10, 20) + R*S*(0, -2) = (16, 20).
    assert affine.apply((1.0, 0.0)) == pytest.approx((16.0, 20.0))


@pytest.mark.parametrize(
    ("insertion", "normal", "extrusion", "code"),
    [
        # Zero normal/extrusion fail closed.
        (
            [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0],
            "invalid_block_orientation_facts",
        ),
        # Oblique (non-planar) INSERTs are never projected for 2-D delivery.
        (
            [0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 1.0, 0.0],
            "non_planar_block_orientation",
        ),
    ],
)
def test_resolve_insert_affine_fails_closed_on_invalid_orientation(
    insertion, normal, extrusion, code,
) -> None:
    base = {
        "transform_facts": {
            "schema_version": TRANSFORM_SCHEMA,
            "insertion_point": insertion,
            "insertion_point_status": "available",
            "block_base_point": [0.0, 0.0, 0.0],
            "block_base_point_status": "available",
            "scale": [1.0, 1.0, 1.0],
            "scale_status": "available",
            "rotation": 0.0,
            "rotation_status": "available",
            "normal": normal,
            "normal_status": "available",
            "extrusion": extrusion,
            "extrusion_status": "available",
        }
    }
    entity = SourceEntity.from_record({
        "entity_key": "insert",
        "source_sha256": "a" * 64,
        "source_file": "fixture.dwg",
        "handle": "A1",
        "layout": "Model",
        "layout_role": "model",
        "cad_role": "model",
        "layer": "POLE",
        "object_name": "AcDbBlockReference",
        "dwg_type_name": "INSERT",
        "points": ((0.0, 0.0),),
        "centroid": (0.0, 0.0),
        "closed": False,
        "text": "",
        "block_name": "POLE",
        "block_attributes": {},
        "raw_properties": base,
    })

    affine, diagnostics = resolve_insert_affine(entity)

    assert affine is None
    assert diagnostics and diagnostics[0]["code"] == code
    assert diagnostics[0]["blocking"] is True


def test_non_insert_entities_do_not_invent_transform_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _build_record is called for TEXT too; the new reader fields must stay
    # unavailable/not_applicable instead of inheriting INSERT-specific locals.
    monkeypatch.setattr(libredwg, "_layer_name", lambda _ptr: "POLE ID")
    monkeypatch.setattr(libredwg, "_parse_dwg_color", lambda _color: (7, None))
    monkeypatch.setattr(
        libredwg, "_resolve_effective_color",
        lambda aci, tc, ltype, layer, styles: (7, "", "style:0"),
    )

    class TextStruct:
        ins_pt = _Vec3(1.0, 2.0, 0.0)
        text_value = "MR.X.P1"

    class TextEntity:
        tio = _Namespace()
        tio.TEXT = TextStruct()
        ownerhandle = None
        color = _Namespace()
        color.index = 256
        color.rgb = "000000"
        linewt = -1

    class TextObject:
        handle = _Handle(0x99)
        type = libredwg.DWG_TYPE_TEXT
        tio = _Namespace()
        tio.entity = TextEntity()

    entity = TextEntity()
    record = libredwg._build_record(
        source_path=Path("/tmp/fixture.dwg"),
        source_sha256="a" * 64,
        obj=TextObject(),
        entity=entity,
        entity_ptr=888,
        dwg_type_name="TEXT",
        object_name="AcDbText",
        layout="Model",
        layout_role="model",
        cad_role="model",
        layer_styles={},
        reasons=[],
    )

    raw = record["raw_properties"]
    assert raw["transform_facts"] == {}
    assert raw["transform_facts_provenance"] == {}
    assert raw["insertion_point_status"] == "not_applicable"
    assert raw["block_base_point_status"] == "not_applicable"
    assert raw["normal_status"] == "not_applicable"
    assert raw["extrusion_status"] == "not_applicable"
