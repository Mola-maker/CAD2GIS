"""Deterministic APD semantic rules for the experiment converter."""

import re


APD_ANONYMOUS_BLOCKS = {
    "*U7": "SITE",
    "*U11": "BOITE",
    "*U13": "PTECH",
    "*U14": "PTECH",
    "*U15": "PTECH",
    "*U16": "PTECH",
    "*U17": "PTECH",
}

_PTECH = re.compile(
    r"(?i)(pole|poteau|chamber|manhole|handhole|anchor|guy|vault|ptech)"
)
_BOITE = re.compile(r"(?i)(fat|box|closure|cto|nap|splice|splitter|terminal|pbo)")
_SITE = re.compile(r"(?i)(fdt|nro|\bpm\b|exchange|hub|pop|shelter|\bolt\b)")
_DECORATION = re.compile(r"(?i)(info|label|legend|etiket|title|frame|border|table|summary)")
_POLE_LABEL = re.compile(r"(?i)^(?:MR[._-])?DMPH[._-]P\d+[A-Z0-9._/-]*$")
_BOITE_LABEL = re.compile(r"(?i)^DMPH-\d+\.\d+\.[BC]\d+[A-Z0-9._/-]*$")
_SITE_LABEL = re.compile(r"(?i)^(?:FDT[_ -]?ID=)?DMPH-\d+\.\d+$")


def classify_insert_block(block_name):
    """Return an eight-layer feature class or ``None`` for unknown blocks."""
    normalized = (block_name or "").strip()
    if _DECORATION.search(normalized):
        return None
    anonymous = APD_ANONYMOUS_BLOCKS.get(normalized.upper())
    if anonymous is not None:
        return anonymous
    if _SITE.search(normalized):
        return "SITE"
    if _BOITE.search(normalized):
        return "BOITE"
    if _PTECH.search(normalized):
        return "PTECH"
    return None


def is_telecom_block(block_name):
    """Return whether a block has explicit telecom evidence."""
    return classify_insert_block(block_name) is not None


def link_annotations(annotations, features, sigma):
    """Attach each annotation to one nearest feature and return leftovers."""
    linked_annotations = set()
    for annotation_index, annotation in enumerate(annotations):
        ax, ay = annotation["centroid"]
        best_distance = float("inf")
        best_index = None
        for feature_index, feature in enumerate(features):
            fx, fy = feature["centroid"]
            distance = ((ax - fx) ** 2 + (ay - fy) ** 2) ** 0.5
            if distance < best_distance and distance < sigma:
                best_distance = distance
                best_index = feature_index
        if best_index is None:
            continue
        target = features[best_index]
        conflicts = [
            key for key, value in annotation["attrs"].items()
            if target["attrs"].get(key) not in (None, value)
        ]
        if conflicts:
            continue
        for key, value in annotation["attrs"].items():
            if target["attrs"].get(key) is None:
                target["attrs"][key] = value
        if annotation["text"]:
            existing = target.get("annotation_text", "")
            if annotation["text"] not in existing.split("\n"):
                target["annotation_text"] = "\n".join(filter(None, (existing, annotation["text"])))
        linked_annotations.add(annotation_index)
    return [item for index, item in enumerate(annotations) if index not in linked_annotations]


def classify_annotation_target(text):
    """Return the only APD feature family supported by a source label."""
    normalized = (text or "").strip()
    if _POLE_LABEL.fullmatch(normalized):
        return "PTECH"
    if _BOITE_LABEL.fullmatch(normalized):
        return "BOITE"
    if _SITE_LABEL.fullmatch(normalized):
        return "SITE"
    return None


def link_apd_annotations(annotations, features, sigma_native=15.0, tie_tolerance=0.01):
    """Link APD labels by family and native distance, abstaining on ties."""
    linked = set()
    for annotation_index, annotation in enumerate(annotations):
        target_class = classify_annotation_target(annotation.get("text", ""))
        if target_class is None:
            continue
        ax, ay = annotation.get("native_centroid", annotation["centroid"])
        candidates = []
        for feature_index, feature in enumerate(features):
            if feature.get("fc_name") != target_class:
                continue
            fx, fy = feature.get("native_centroid", feature["centroid"])
            distance = ((ax - fx) ** 2 + (ay - fy) ** 2) ** 0.5
            if distance <= sigma_native:
                candidates.append((distance, feature_index))
        candidates.sort()
        if not candidates:
            continue
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] <= tie_tolerance:
            continue
        target = features[candidates[0][1]]
        conflicts = [
            key for key, value in annotation.get("attrs", {}).items()
            if target["attrs"].get(key) not in (None, value)
        ]
        if conflicts:
            continue
        for key, value in annotation.get("attrs", {}).items():
            target["attrs"].setdefault(key, value)
        source_text = annotation.get("text", "")
        target["annotation_text"] = source_text
        target["display_label"] = source_text
        target["label_method"] = f"DWG_DERIVED:apd-family-nearest-{sigma_native:g}m"
        linked.add(annotation_index)
    return [item for index, item in enumerate(annotations) if index not in linked]


def set_traditional_axis_order(spatial_reference, osr_module):
    """Force longitude/easting first while retaining GDAL 2 compatibility."""
    setter = getattr(spatial_reference, "SetAxisMappingStrategy", None)
    strategy = getattr(osr_module, "OAMS_TRADITIONAL_GIS_ORDER", None)
    if setter is not None and strategy is not None:
        setter(strategy)
