"""Offline contracts for standard OSM placement and revisioned refinement."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pyproj import Geod, Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cad2gis.cad2gis_v3 import osm_matching as matching  # noqa: E402
from cad2gis.cad2gis_v3.units import build_unit_crs_contract  # noqa: E402


SOURCE_SHA = "a" * 64
PARENT_SHA = "b" * 64
ROUTES = [[(0, 0), (100, 0), (140, 40)], [(100, 0), (100, -80), (170, -120)]]
SHIFT = (1000.0, 2000.0)


class Nominal:
    """Real CRS/units with no GDAL dependency for matching-math tests."""

    def __init__(self, scale=1.0, target="EPSG:3857"):
        self.source_crs, self.target_crs = "EPSG:3857", target
        self.unit_contract = build_unit_crs_contract(
            4 if scale == 0.001 else 6, self.source_crs, target,
            source_coordinate_scale_to_m=scale, source_coordinate_scale_reviewed=True,
        )
        self.unit_crs_manifest = self.unit_contract.to_manifest_dict()
        self.forward = Transformer.from_crs(self.source_crs, target, always_xy=True)
        self.reverse = Transformer.from_crs(target, self.source_crs, always_xy=True)
        self.scale = scale

    def point(self, point):
        return self.forward.transform(point[0] * self.scale, point[1] * self.scale)

    def target_to_source_point(self, point):
        x, y = self.reverse.transform(*point)
        return x / self.scale, y / self.scale

    def source_length_to_m(self, value):
        return self.unit_contract.source_length_to_m(value)

    def target_length_to_m(self, value):
        return self.unit_contract.target_length_to_m(value)


def snapshot(tmp_path, routes, shifts=(SHIFT,)):
    geographic = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    elements = []
    for shift in shifts:
        for route in routes:
            values = [geographic.transform(x + shift[0], y + shift[1]) for x, y in route]
            elements.append({"type": "way", "id": len(elements) + 1,
                             "tags": {"highway": "residential", "name": "fixture road"},
                             "geometry": [{"lon": lon, "lat": lat} for lon, lat in values]})
    path = tmp_path / "osm.json"
    path.write_text(json.dumps({"elements": elements}), encoding="utf-8")
    return path


def run_match(tmp_path, *, nominal=None, routes=ROUTES, osm_routes=None, shifts=(SHIFT,), valid=False):
    nominal = nominal or Nominal()
    osm_path = snapshot(tmp_path, ROUTES if osm_routes is None else osm_routes, shifts)
    return matching.match_osm(
        source=tmp_path / "drawing.dwg", source_sha256=SOURCE_SHA,
        features=[SimpleNamespace(feature_class="CABLE", native_points=route) for route in routes],
        nominal=nominal, coordinate_domain={"passed": valid}, osm_data=osm_path,
    )


def test_offline_network_match_applies_all_routes_and_retains_relative_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(matching.osm_anchor, "query_osm_place", lambda *_: pytest.fail("offline must not geocode"))
    monkeypatch.setattr(matching.osm_anchor, "query_osm_roads", lambda *_: pytest.fail("offline must not query roads"))
    original = copy.deepcopy(ROUTES)
    transform, record = run_match(tmp_path)
    assert record["status"] == "matched"
    assert record["quality"]["route_count"] == 2
    assert record["quality"]["candidates"][0]["supported_route_fraction"] == 1.0
    assert record["authority"] == "relative_osm"
    assert record["absolute_accuracy_verified"] is False
    assert record["osm_snapshot"]["input_sha256"] == hashlib.sha256((tmp_path / "osm.json").read_bytes()).hexdigest()
    assert record["attribution"]["license"] == "ODbL-1.0"
    assert transform.point((0.0, 0.0)) == pytest.approx(SHIFT, abs=0.001)
    assert ROUTES == original
    assert "OSM-relative" in transform.coordinate_provenance
    assert "GCP" not in transform.coordinate_provenance
    path = tmp_path / "match.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    loaded, stored = matching.load_match_profile(path, SOURCE_SHA, Nominal())
    assert loaded.points(ROUTES[0]) == transform.points(ROUTES[0])
    assert stored["match_sha256"] == record["match_sha256"]
    assert record['route_selection']['method'] == 'cable_proxy_only'
    assert record['route_selection']['excluded_features'] == []


def test_road_priority_is_source_bound_replayable_and_reported_per_route(tmp_path):
    features = [SimpleNamespace(feature_class='ROAD', native_points=route, source_handle=f'R{index}')
                for index, route in enumerate(ROUTES)]
    features += [SimpleNamespace(feature_class='CABLE', native_points=[(x + 8, y + 6) for x, y in route],
                                 source_handle=f'C{index}') for index, route in enumerate(ROUTES)]
    before = copy.deepcopy([feature.native_points for feature in features])
    transform, record = matching.match_osm(
        source=tmp_path / 'drawing.dwg', source_sha256=SOURCE_SHA, features=features,
        nominal=Nominal(), coordinate_domain={'passed': False}, osm_data=snapshot(tmp_path, ROUTES))
    assert record['status'] == 'matched'
    assert transform.point((0, 0)) == pytest.approx(SHIFT, abs=0.001)
    assert [feature.native_points for feature in features] == before
    selection = record['route_selection']
    assert selection['method'] == 'explicit_road_preferred'
    assert selection['valid_feature_counts'] == {'ROAD': 2, 'CABLE': 2}
    assert selection['selected_unique_route_count'] == 2
    assert [item['source_handle'] for item in selection['excluded_features']] == ['C0', 'C1']
    assert selection['selected_routes'][0]['source_features'][0]['source_handle'] == 'R0'
    assert not selection['road_centerline_semantics_verified']
    diagnostics = record['evidence']['route_diagnostics']
    assert len(diagnostics) == 2
    assert all(item['coverage'] == 1 and item['distance_p95_m_approx'] < 0.001 for item in diagnostics)
    assert all(item['absolute_accuracy_verified'] is False for item in diagnostics)
    from cad2gis.cad2gis_v3.osm_evidence import evidence_geojson
    vector = evidence_geojson(record)['cad_routes']['features'][0]
    assert vector['properties']['osm_coverage'] == 1.0
    refined = matching.refine_match(record, Nominal(), controls_for(transform),
                                   source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    assert refined['route_selection'] == selection
    assert refined['evidence']['route_selection_sha256'] == record['evidence']['route_selection_sha256']
    assert refined['evidence']['route_diagnostics'] != diagnostics
    tampered = copy.deepcopy(record)
    tampered['route_selection']['excluded_features'] = []
    tampered = matching._seal(tampered)
    with pytest.raises(ValueError, match='route selection binding'):
        matching.transformer_from_record(tampered, Nominal())


def test_insufficient_explicit_road_structure_does_not_fallback_to_offset_cable(tmp_path):
    features = [SimpleNamespace(feature_class='ROAD', native_points=[(0, 0), (100, 0)])]
    features += [SimpleNamespace(feature_class='CABLE', native_points=route) for route in ROUTES]
    _, record = matching.match_osm(
        source=tmp_path / 'drawing.dwg', source_sha256=SOURCE_SHA, features=features,
        nominal=Nominal(), coordinate_domain={'passed': False}, osm_data=snapshot(tmp_path, ROUTES))
    assert record['status'] == 'coarse'
    assert record['quality']['reason'] == 'insufficient_route_structure'
    assert record['route_selection']['selected_feature_class'] == 'ROAD'
    assert record['route_selection']['selected_unique_route_count'] == 1
    assert record['absolute_accuracy_verified'] is False


def test_degenerate_road_does_not_suppress_valid_cable_and_dedup_keeps_lineage():
    features = [SimpleNamespace(feature_class='ROAD', native_points=[(0, 0), (0, 0)], source_handle='BAD')]
    features += [SimpleNamespace(feature_class='CABLE', native_points=route, source_handle=str(index))
                 for index, route in enumerate([ROUTES[0], list(reversed(ROUTES[0]))])]
    selection = {}
    assert matching._native_routes(features, selection_evidence=selection) == [ROUTES[0]]
    assert selection['method'] == 'cable_proxy_only'
    assert selection['selected_unique_route_count'] == 1
    assert [item['source_handle'] for item in selection['selected_routes'][0]['source_features']] == ['0', '1']


def test_local_identical_networks_keep_coarse_review_transform_when_matching_abstains(tmp_path):
    transform, record = run_match(tmp_path, shifts=(SHIFT, (4000.0, 5000.0)))
    assert transform is not None
    assert record["status"] == "coarse"
    assert record["needs_review"] is True
    assert record["quality"]["automatic_matching_status"] == "abstained"
    assert record["quality"]["fallback"] == "osm_snapshot_bbox_centre"
    assert record["quality"]["confidence_gap"] < 0.06
    assert record["quality"]["reason"] == "weak_or_ambiguous_network_alignment"
    assert record["absolute_accuracy_verified"] is False
    assert transform.point((85, -40)) == pytest.approx((2585, 3460), abs=1e-6)
    refined = matching.refine_match(record, Nominal(), controls_for(transform),
                                   source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    assert refined["status"] == "matched"
    assert refined["revision"] == 2


def test_longest_route_match_cannot_hide_inconsistent_secondary_route(tmp_path):
    unrelated = [[(0, 0), (100, 0), (140, 40)], [(500, 400), (500, 450), (530, 490)]]
    transform, record = run_match(tmp_path, osm_routes=unrelated)
    assert transform is not None
    assert record["status"] == "coarse"
    assert record["quality"]["automatic_matching_status"] == "abstained"
    assert record["quality"]["reason"] == "weak_or_ambiguous_network_alignment"


def test_single_straight_span_is_insufficient_for_automatic_network_match(tmp_path):
    transform, record = run_match(tmp_path, routes=[[(0, 0), (100, 0)]])
    assert transform is not None
    assert record["status"] == "coarse"
    assert record["needs_review"] is True
    assert record["quality"]["reason"] == "insufficient_route_structure"


def test_local_millimetres_and_mercator_latitude_scale_roundtrip(tmp_path):
    native = [[(x * 1000.0, y * 1000.0) for x, y in route] for route in ROUTES]
    transform, record = run_match(tmp_path, nominal=Nominal(0.001), routes=native)
    assert record["status"] == "matched"
    assert record["base"]["source_unit_to_m"] == 0.001
    latitude = record["base"]["reference_latitude"]
    assert record["base"]["scale_to_3857"] == pytest.approx(0.001 / math.cos(math.radians(latitude)))
    assert transform.point(native[0][0]) == pytest.approx(SHIFT, abs=0.001)
    point = native[1][-1]
    assert transform.target_to_source_point(transform.point(point)) == pytest.approx(point, abs=1e-6)
    assert transform.source_length_to_m(100_000.0) == 100.0
    assert transform.grid_length_m([(0, 0), (100, 0)]) == 100.0
    assert transform.geodesic_length(native[0]) > 140.0


def test_nominal_placement_projects_through_target_crs_and_does_not_double_scale(tmp_path):
    nominal = Nominal(0.001, target="EPSG:32631")
    native = [[((x + SHIFT[0]) * 1000, (y + SHIFT[1]) * 1000) for x, y in route] for route in ROUTES]
    transform, record = run_match(tmp_path, nominal=nominal, routes=native, valid=True)
    assert record["status"] == "matched"
    assert record["base"]["mode"] == "nominal"
    assert record["base"]["scale_to_3857"] == 1.0
    assert record["translation_3857"] == pytest.approx([0.0, 0.0], abs=1e-7)
    point = native[1][-1]
    assert transform.point(point) == pytest.approx(nominal.point(point), abs=1e-7)
    assert transform.target_to_source_point(transform.point(point)) == pytest.approx(point, abs=1e-5)


def test_place_hint_coarse_placement_ignores_legacy_translation(tmp_path, monkeypatch):
    monkeypatch.setattr(matching, "_fetch_roads", lambda *_: ([], "unavailable"))
    anchor = {"target_epsg3857_centre": [1000.0, 2000.0], "translation_dx": 99_999_999,
              "translation_dy": 99_999_999, "display_name": "reviewed locality"}
    transform, record = matching.match_osm(
        source=tmp_path / "drawing.dwg", source_sha256=SOURCE_SHA, features=ROUTES,
        nominal=Nominal(), coordinate_domain={"passed": False}, anchor=anchor,
    )
    assert record["status"] == "coarse"
    assert record["needs_review"] is True
    assert abs(record["translation_3857"][0]) < 10_000
    assert transform.point((85.0, -40.0)) == pytest.approx((1000.0, 2000.0), abs=1e-6)


def test_no_place_or_snapshot_does_not_automatically_geocode_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(matching.osm_anchor, "query_osm_place", lambda *_: pytest.fail("no implicit filename geocoding"))
    transform, record = matching.match_osm(
        source=tmp_path / "city-name.dwg", source_sha256=SOURCE_SHA, features=ROUTES,
        nominal=Nominal(), coordinate_domain={"passed": False},
    )
    assert transform is None
    assert record["status"] == "unavailable"


def controls_for(transform, dx=20.0, dy=-15.0):
    controls = []
    for index, point in enumerate(((0.0, 0.0), (170.0, -120.0), (100.0, 0.0))):
        x, y = transform.base_point(point)
        controls.append({"point_id": f"p{index}", "cad_x": point[0], "cad_y": point[1],
                         "target_easting": x + SHIFT[0] + dx,
                         "target_northing": y + SHIFT[1] + dy + (100.0 if index == 2 else 0.0),
                         "target_crs": "EPSG:3857", "role": "check" if index == 2 else "train",
                         "enabled": True, "weight": 1.0, "longitude": 0.1, "latitude": 0.1})
    return controls


def test_refinement_uses_only_train_and_never_accumulates_translation(tmp_path):
    nominal = Nominal()
    transform, original = run_match(tmp_path, nominal=nominal)
    unchanged = copy.deepcopy(original)
    controls = controls_for(transform)
    refined = matching.refine_match(original, nominal, controls, source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    assert original == unchanged
    assert refined["revision"] == 2
    assert refined["parent_match_sha256"] == original["match_sha256"]
    assert refined["parent_run_sha256"] == PARENT_SHA
    assert refined["translation_3857"] == pytest.approx((1020.0, 1985.0))
    geographic = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    expected = Geod(ellps="WGS84").inv(
        *geographic.transform(1120.0, 1985.0), *geographic.transform(1120.0, 2085.0),
    )[2]
    assert refined["quality"]["check_metrics"]["max_m"] == pytest.approx(expected)
    assert refined["quality"]["train_metrics"]["max_m"] == pytest.approx(0.0)
    assert refined["quality"]["checks_used_for_fit"] is False
    assert refined["controls"][0]["longitude"] == 0.1
    assert refined["needs_review"] is True
    repeated = matching.refine_match(refined, nominal, controls, source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    assert repeated["revision"] == 3
    assert repeated["translation_3857"] == refined["translation_3857"]
    assert repeated["base"] == original["base"]
    assert repeated["absolute_accuracy_verified"] is False


@pytest.mark.parametrize("invalid", ["source", "hash", "units", "checks_only", "duplicate_train"])
def test_refinement_rejects_invalid_bindings_and_training_controls(tmp_path, invalid):
    nominal = Nominal()
    transform, record = run_match(tmp_path, nominal=nominal)
    controls = controls_for(transform)
    source_sha = SOURCE_SHA
    if invalid == "source":
        source_sha = "c" * 64
    elif invalid == "hash":
        record["translation_3857"][0] += 10.0
    elif invalid == "units":
        nominal = Nominal(0.001)
    elif invalid == "checks_only":
        for control in controls:
            control["role"] = "check"
    else:
        controls[1]["cad_x"], controls[1]["cad_y"] = controls[0]["cad_x"], controls[0]["cad_y"]
    with pytest.raises(ValueError):
        matching.refine_match(record, nominal, controls, source_sha256=source_sha, parent_run_sha256=PARENT_SHA)


def test_overpass_node_references_are_supported_and_nonroads_ignored():
    payload = {"elements": [
        {"type": "node", "id": 1, "lon": 0, "lat": 0},
        {"type": "node", "id": 2, "lon": 0.001, "lat": 0},
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "residential"}},
        {"type": "way", "id": 11, "nodes": [1, 2], "tags": {"building": "yes"}},
    ]}
    roads = matching._parse_roads(payload)
    assert len(roads) == 1
    assert roads[0]["osm_way_id"] == 10
    assert roads[0]["points"][1][0] == pytest.approx(111.31949, abs=1e-5)


def test_profile_replay_rejects_changed_geodata_operation_with_same_crs_and_units(tmp_path):
    nominal = Nominal()
    _, record = run_match(tmp_path, nominal=nominal)
    nominal.geodata_registration = {"design_point": [0.0, 0.0], "reference_point": [1000.0, 2000.0]}
    with pytest.raises(ValueError, match="nominal operation binding mismatch"):
        matching.transformer_from_record(record, nominal)


def test_local_reference_has_native_metadata_and_only_registered_geographic_geometry(tmp_path):
    contract = build_unit_crs_contract(
        6, None, "EPSG:3857", local_registration_strategy="osm_relative",
        local_registration_reviewed=True,
    )
    nominal = matching.LocalMatchingReference(None, "EPSG:3857", contract)
    assert nominal.source.IsLocal() == 1
    assert nominal.target.IsProjected() == 1
    assert nominal.operation_metadata()["geographic_operation_applied"] is False
    assert nominal.roundtrip_error([(1, 2), (100, -20)]) == 0.0
    with pytest.raises(ValueError, match="no geodesic location"):
        nominal.geodesic_length([(0, 0), (100, 0)])
    transform, record = run_match(tmp_path, nominal=nominal)
    assert record["source_crs"] is None
    assert record["status"] == "matched"
    assert record["base"]["mode"] == "local"
    assert transform.point((0, 0)) == pytest.approx(SHIFT, abs=0.001)
    assert transform.qgis_rotation((0, 0), math.pi / 2.0) == pytest.approx(270.0)
    assert transform.geodesic_length([(0, 0), (100, 0)]) == pytest.approx(100.0, abs=0.001)


def test_corrupt_optional_road_cache_is_replaced_from_provider(tmp_path, monkeypatch):
    bbox = {"south": 0.0, "north": 0.01, "west": 0.0, "east": 0.01}
    path = tmp_path / f"osm-roads-{matching._digest(bbox)}.json"
    path.write_text("truncated cache", encoding="utf-8")
    roads = [{"osm_way_id": 1, "points": [[0, 0], [100, 0]], "name": "road", "highway": "residential"}]
    monkeypatch.setattr(matching.osm_anchor, "query_osm_roads", lambda _: roads)
    fetched, origin = matching._fetch_roads(bbox, tmp_path)
    assert origin == "overpass"
    assert fetched[0]["osm_way_id"] == 1
    monkeypatch.setattr(matching.osm_anchor, "query_osm_roads", lambda _: pytest.fail("valid snapshot must use cache"))
    cached, origin = matching._fetch_roads(bbox, tmp_path)
    assert origin == "cache"
    assert cached == fetched


def test_osm_provider_endpoint_and_user_agent_are_configurable(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'[{"lat":"0","lon":"0","display_name":"Test place"}]'

    def open_request(request, **kwargs):
        requests.append(request)
        return Response()

    monkeypatch.setenv("CAD2GIS_NOMINATIM_URL", "https://geocoder.example/search?countrycodes=id")
    monkeypatch.setenv("CAD2GIS_OSM_USER_AGENT", "CAD2GIS-project/contact@example.test")
    monkeypatch.setattr(matching.osm_anchor.urllib.request, "urlopen", open_request)
    assert matching.osm_anchor.query_osm_place("Example Place")["display_name"] == "Test place"
    assert requests[0].full_url.startswith("https://geocoder.example/search?countrycodes=id&q=Example+Place")
    assert requests[0].get_header("User-agent") == "CAD2GIS-project/contact@example.test"


def test_valid_nominal_ambiguous_roads_abstain_without_overriding_existing_placement(tmp_path):
    transform, record = run_match(tmp_path, valid=True, shifts=(SHIFT, (4000.0, 5000.0)))
    assert transform is None
    assert record["status"] == "abstained"
    assert record["translation_3857"] == [0.0, 0.0]


def test_region_scope_is_hash_bound_and_refinement_preserves_it(tmp_path):
    osm_path = snapshot(tmp_path, ROUTES)
    nominal = Nominal()
    transform, record = matching.match_osm(
        source=tmp_path / "drawing.dwg", source_sha256=SOURCE_SHA, features=ROUTES,
        nominal=nominal, coordinate_domain={"passed": False}, osm_data=osm_path, region_id="EMR28560",
    )
    assert record["region_id"] == "EMR28560"
    refined = matching.refine_match(record, nominal, controls_for(transform),
                                   source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    assert refined["region_id"] == "EMR28560"
    record["region_id"] = "MAIN"
    with pytest.raises(ValueError, match="hash mismatch"):
        matching.transformer_from_record(record, nominal)
    with pytest.raises(ValueError, match="region_id"):
        matching.refine_match(refined, nominal, controls_for(transform),
                              source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA, region_id="../other")


def test_explicit_place_cache_is_reused_and_provider_changes_invalidate_it(tmp_path, monkeypatch):
    calls = []

    def geocode(name):
        calls.append(name)
        return {"epsg3857_centre": [1000.0, 2000.0], "display_name": "Test place", "lat": 0.018}

    monkeypatch.setattr(matching.osm_anchor, "query_osm_place", geocode)
    monkeypatch.setenv("CAD2GIS_NOMINATIM_URL", "https://provider.example/search")
    first = matching._hint(None, "Test place", cache_dir=tmp_path)
    repeated = matching._hint(None, "Test place", cache_dir=tmp_path)
    assert first == repeated
    assert calls == ["Test place"]
    monkeypatch.setenv("CAD2GIS_NOMINATIM_URL", "https://another.example/search")
    matching._hint(None, "Test place", cache_dir=tmp_path)
    assert calls == ["Test place", "Test place"]


def test_public_nominatim_requests_are_spaced_one_second_apart(monkeypatch):
    clock = [100.0]
    starts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'[{"lat":"0","lon":"0"}]'

    def urlopen(*args, **kwargs):
        starts.append(clock[0])
        return Response()

    def sleep(delay):
        clock[0] += delay

    monkeypatch.setenv("CAD2GIS_NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
    monkeypatch.setattr(matching.osm_anchor, "_NOMINATIM_LAST_REQUEST_AT", None)
    monkeypatch.setattr(matching.osm_anchor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(matching.osm_anchor.time, "sleep", sleep)
    monkeypatch.setattr(matching.osm_anchor.urllib.request, "urlopen", urlopen)
    assert matching.osm_anchor.query_osm_place("A") is not None
    assert matching.osm_anchor.query_osm_place("B") is not None
    assert starts == [100.0, 101.0]


def test_explicit_place_overrides_valid_nominal_online_search_area(tmp_path, monkeypatch):
    area = {"west": 10.0, "east": 10.1, "south": 20.0, "north": 20.1}
    centre = matching._TO_3857.transform(10.05, 20.05)
    queried = []
    monkeypatch.setattr(matching.osm_anchor, "query_osm_place", lambda _: {
        "epsg3857_centre": centre, "bbox": area, "lat": 20.05,
    })

    def fetch(bbox, cache_dir):
        queried.append(bbox)
        return [], "unavailable"

    monkeypatch.setattr(matching, "_fetch_roads", fetch)
    transform, record = matching.match_osm(
        source=tmp_path / "drawing.dwg", source_sha256=SOURCE_SHA, features=ROUTES,
        nominal=Nominal(), coordinate_domain={"passed": True}, place_name="Requested locality",
    )
    assert queried == [area]
    assert record["search_bbox"] == area
    assert record["status"] == "coarse"
    assert record["base"]["mode"] == "nominal"
    assert transform.point((85.0, -40.0)) == pytest.approx(centre)


@pytest.mark.parametrize("reference", ["offline", "place"])
def test_device_only_local_partition_has_coarse_review_without_road_match(tmp_path, monkeypatch, reference):
    centre = (1000.0, 2000.0)
    kwargs = {}
    if reference == "offline":
        kwargs["osm_data"] = snapshot(tmp_path, [[(-100, 0), (100, 0)]])
        monkeypatch.setattr(matching.osm_anchor, "query_osm_place", lambda *_: pytest.fail("offline must not geocode"))
    else:
        kwargs["place_name"] = "Test locality"
        monkeypatch.setattr(matching.osm_anchor, "query_osm_place", lambda _: {"epsg3857_centre": centre, "lat": 0.018})
        monkeypatch.setattr(matching, "_fetch_roads", lambda *_: ([], "unavailable"))
    monkeypatch.setattr(matching, "_match_routes", lambda *_: pytest.fail("device points cannot prove road matching"))
    features = [SimpleNamespace(feature_class="PTECH", native_points=[(20.0, 40.0)]),
                SimpleNamespace(feature_class="SITE", native_points=[(80.0, 100.0)])]
    transform, record = matching.match_osm(
        source=tmp_path / "drawing.dwg", source_sha256=SOURCE_SHA, features=features,
        nominal=Nominal(), coordinate_domain={"passed": False}, region_id="DEVICES", **kwargs,
    )
    assert record["status"] == "coarse"
    assert record["needs_review"] is True
    assert record["region_id"] == "DEVICES"
    assert record["quality"]["reason"] == "no_source_routes"
    assert record["quality"]["automatic_matching_status"] == "not_attempted"
    assert transform.point((50.0, 70.0)) == pytest.approx(centre, abs=1e-6)


def test_empty_partition_is_unavailable_without_any_network_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(matching.osm_anchor, "query_osm_place", lambda *_: pytest.fail("empty partition must not geocode"))
    monkeypatch.setattr(matching, "_fetch_roads", lambda *_: pytest.fail("empty partition must not fetch roads"))
    transform, record = matching.match_osm(
        source=tmp_path / "drawing.dwg", source_sha256=SOURCE_SHA, features=[],
        nominal=Nominal(), coordinate_domain={"passed": False}, place_name="Test locality",
    )
    assert transform is None
    assert record["status"] == "unavailable"
    assert record["quality"]["reason"] == "empty_drawing_partition"


def test_route_samples_do_not_weight_redundant_cad_vertices():
    sparse = [[(0, 0), (100, 0), (100, 100)]]
    dense = [[(x, 0) for x in range(101)] + [(100, 100)]]
    a = matching._samples(sparse, 1.0)
    b = matching._samples(dense, 1.0)
    for expected, actual in zip(a, b):
        assert actual.shape == expected.shape
        assert actual == pytest.approx(expected)


@pytest.mark.parametrize("latitude", [0.0, 60.0, -60.0])
def test_manual_residuals_are_ellipsoidal_ground_metres(tmp_path, latitude):
    nominal = Nominal()
    _, original = run_match(tmp_path, nominal=nominal, valid=True)
    forward = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    reverse = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    x, y = forward.transform(12.0, latitude)
    controls = [
        {"point_id": str(i), "cad_x": p[0], "cad_y": p[1],
         "target_easting": p[0] + x, "target_northing": p[1] + y,
         "target_crs": "EPSG:3857", "role": "train"}
        for i, p in enumerate([(0, 0), (100, 0)])
    ]
    # Exactly 30 ground metres, regardless of map projection scale.
    lon, lat, _ = Geod(ellps="WGS84").fwd(12.0, latitude, 90.0, 30.0)
    check_x, check_y = forward.transform(lon, lat)
    controls.append({"point_id": "check", "cad_x": 0, "cad_y": 0,
                     "target_easting": check_x, "target_northing": check_y,
                     "target_crs": "EPSG:3857", "role": "check"})
    refined = matching.refine_match(original, nominal, controls,
                                    source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    # The nominal base isolates residual measurement from local CAD scale.
    actual = matching.transformer_from_record(refined, nominal).point((0, 0))
    expected = Geod(ellps="WGS84").inv(*reverse.transform(*actual), lon, lat)[2]
    assert refined["quality"]["check_metrics"]["max_m"] == pytest.approx(expected, abs=1e-6)
    assert refined["quality"]["residual_distance_method"] == "WGS84_ellipsoidal_geodesic"
    assert refined["needs_review"] is False


@pytest.mark.parametrize("routes", [
    [[(0, 0), (50, 0), (100, 0)]],
    [[(x, 0) for x in range(101)]],
    [[(0, 0), (100, 0)], [(0, 50), (100, 50)]],
    [[(0, 0), (100, 0)], [(0, 50), (100, 51)]],
])
def test_vertices_and_parallel_spans_do_not_invent_translation_constraints(tmp_path, routes):
    transform, record = run_match(tmp_path, routes=routes, osm_routes=routes)
    assert transform is not None  # The independent coarse area view remains useful.
    assert record["status"] == "coarse"
    assert record["quality"]["reason"] == "source_geometry_unobservable"
    assert record["quality"]["source_observability"]["observable"] is False
    assert record["quality"]["source_observability"]["minimum_eigenvalue"] < 0.04
    assert record["evidence"]["placement_status"] == "coarse"


def test_repeated_network_eight_metres_away_is_not_merged_into_confidence(tmp_path):
    routes = [[(0, 0), (100, 0), (100, 100)]]
    transform, record = run_match(tmp_path, routes=routes, osm_routes=routes,
                                  shifts=(SHIFT, (SHIFT[0] + 8, SHIFT[1])))
    assert record["status"] == "coarse"
    assert record["quality"]["confidence_gap"] == pytest.approx(0.0, abs=1e-7)
    candidates = record["quality"]["candidates"]
    assert len(candidates) >= 2
    assert math.dist(candidates[0]["translation_3857"], candidates[1]["translation_3857"]) == pytest.approx(8.0, abs=0.001)
    assert record["quality"]["search_complete"] is False
    assert transform is not None


def test_point_to_line_fit_retains_minority_direction_translation(tmp_path):
    routes = [[(0, 0), (1000, 0), (1000, 200)]]
    transform, record = run_match(tmp_path, routes=routes, osm_routes=routes)
    assert record["status"] == "matched"
    assert transform.point((0, 0)) == pytest.approx(SHIFT, abs=0.001)
    assert record["quality"]["candidates"][0]["observability"]["rank"] == 2
    assert record["quality"]["sample_weighting"] == "represented_route_length"


def test_evidence_vectors_residuals_and_manual_revision_are_bound_and_rebuilt(tmp_path):
    from cad2gis.cad2gis_v3.osm_evidence import evidence_geojson

    transform, original = run_match(tmp_path)
    evidence = original["evidence"]
    assert evidence["source_sha256"] == SOURCE_SHA
    assert evidence["osm_snapshot_sha256"] == original["osm_snapshot"]["sha256"]
    assert evidence["cad_routes"][0]["native_points"] == ROUTES[0]
    assert evidence["osm_topology"]["geometry_only_way_count"] == 2
    assert evidence["osm_topology"]["nodes"] == []
    layers = evidence_geojson(original)
    assert len(layers["cad_routes"]["features"]) == 2
    assert len(layers["osm_roads"]["features"]) == 2
    assert len(layers["residuals"]["features"]) == original["quality"]["sample_count"]
    coordinates = layers["cad_routes"]["features"][0]["geometry"]["coordinates"][0]
    assert coordinates == pytest.approx(matching._TO_WGS84.transform(*SHIFT), abs=1e-8)
    frozen = copy.deepcopy(original)
    refined = matching.refine_match(original, Nominal(), controls_for(transform),
                                   source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    assert original == frozen
    assert refined["evidence"]["revision"] == 2
    assert refined["evidence"]["translation_3857"] == refined["translation_3857"]
    assert refined["evidence"]["evidence_sha256"] != evidence["evidence_sha256"]
    assert refined["evidence"]["cad_routes"] == evidence["cad_routes"]
    manual = [feature for feature in evidence_geojson(refined)["residuals"]["features"]
              if feature["properties"]["residual_kind"] == "manual_control"]
    assert len(manual) == 3
    check = next(feature for feature in manual if feature["properties"]["role"] == "check")
    assert check["properties"]["error_m"] == refined["quality"]["check_metrics"]["max_m"]
    assert check["properties"]["absolute_accuracy_verified"] is False
    refined["controls"][0]["target_easting"] += 100
    with pytest.raises(ValueError, match="evidence profile hash mismatch"):
        evidence_geojson(refined)


def test_evidence_corruption_is_rejected_even_when_outer_profile_is_resealed(tmp_path):
    from cad2gis.cad2gis_v3.osm_evidence import evidence_geojson

    _, record = run_match(tmp_path)
    record["evidence"]["cad_routes"][0]["native_points"][0] = [999, 999]
    record = matching._seal(record)
    with pytest.raises(ValueError, match="evidence hash mismatch"):
        matching.transformer_from_record(record, Nominal())
    with pytest.raises(ValueError, match="evidence hash mismatch"):
        evidence_geojson(record)


def test_pre_upgrade_profiles_remain_readable_without_fabricated_evidence(tmp_path):
    from cad2gis.cad2gis_v3.osm_evidence import evidence_geojson

    _, record = run_match(tmp_path)
    record.pop("evidence")
    record = matching._seal(record)
    assert matching.transformer_from_record(record, Nominal()).point((0, 0)) == pytest.approx(SHIFT, abs=0.001)
    assert all(not layer["features"] for layer in evidence_geojson(record).values())


def test_manual_pick_history_is_hash_bound_without_affecting_fit(tmp_path):
    transform, record = run_match(tmp_path)
    controls = controls_for(transform)
    controls[0]["editor_state"] = {"schema_version": 1, "initial_target": [12.0, 20.0],
        "undo_targets": [[12.1, 20.0]], "last_action": "nudge", "distance_method": "WGS84_ellipsoidal_geodesic",
        "last_nudge": {"direction": "east", "step_m": 0.01}}
    state = copy.deepcopy(controls[0]["editor_state"])
    refined = matching.refine_match(record, Nominal(), controls, source_sha256=SOURCE_SHA, parent_run_sha256=PARENT_SHA)
    assert refined["controls"][0]["editor_state"] == state
    assert refined["translation_3857"] == pytest.approx([1020, 1985])
    controls[0]["editor_state"]["initial_target"][0] = 0
    assert refined["controls"][0]["editor_state"] == state


@pytest.mark.parametrize("field,value", [
    ("initial_target", [float("nan"), 0]),
    ("undo_targets", [[0, 0]] * 129),
    ("last_nudge", {"direction": "east", "step_m": 100}),
    ("schema_version", True),
])
def test_invalid_manual_pick_history_is_rejected(field, value):
    state = {"schema_version": 1, "initial_target": [12.0, 20.0], "undo_targets": [],
             "last_action": "pick", "distance_method": "WGS84_ellipsoidal_geodesic", field: value}
    with pytest.raises(ValueError, match="editor_state"):
        matching._editor_state(state)
