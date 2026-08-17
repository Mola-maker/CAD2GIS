"""L1/L2 annotation-family validation and the structural repair loop.

L1 (cross-layer isolation): a family's regex must match its declared layer's
samples and reject samples from every other layer.

L2 (intra-layer grouping): when a layer has multiple families, each sample
must be assignable to exactly one family via token coverage + Hungarian
assignment; ambiguous assignments indicate overlapping families.

Structural failures trigger a repair loop (max 2 attempts) that feeds the
failure evidence back to the model.  Literal separator/case variants are
tolerated — they never fail validation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


def _tokenize(text: str) -> list[str]:
    return [tok for tok in re.split(r"[^A-Za-z0-9]+", text.strip()) if tok]


_ASSET_CLASS_BY_LAYER_HINT = (
    (re.compile(r"(?i)(fat|closure|splice|otb|odp|splitter)"), "BOITE"),
    # POLE/PTECH is a stronger device semantic than an FDT area qualifier:
    # ``POLE ID FDT 2 73`` is a pole label layer, not a site layer.
    (re.compile(r"(?i)(pole|ptech)"), "PTECH"),
    (re.compile(r"(?i)(fdt|olt|hub)"), "SITE"),
)


def infer_target_class(layer: str) -> str:
    """Map a label layer name to the most likely feature class."""
    for pattern, feature_class in _ASSET_CLASS_BY_LAYER_HINT:
        if pattern.search(layer):
            return feature_class
    return "PTECH"


def derive_family_from_samples(
    layer: str,
    samples: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically derive regexes from aligned text samples.

    Samples are grouped by token width (a layer like POLE ID often holds
    several label families: ``EXT.MR.IJY.KLDYA.P001`` alongside
    ``MR.DSBR.P060``).  Each group is aligned positionally: numeric columns
    become ``\\d+``, varying letter columns ``[A-Za-z]+``, stable letter
    tokens stay literal.  Every derived pattern full-matches its samples by
    construction — robust where the LLM repair loop fails.
    """
    texts = [
        str(sample.get("text", "")).strip()
        for sample in samples
        if str(sample.get("text", "")).strip()
    ]
    if len(texts) < 2:
        return []
    by_width: dict[int, list[str]] = {}
    for text in texts:
        by_width.setdefault(len(_tokenize(text)), []).append(text)
    result: list[dict[str, Any]] = []
    for width, group in sorted(by_width.items()):
        tokenized = [_tokenize(text) for text in group]
        parts: list[str] = []
        alignable = True
        for position in range(width):
            column = [tokens[position] for tokens in tokenized]
            all_digits = all(token.isdigit() for token in column)
            all_alpha = all(token.isalpha() for token in column)
            if all_digits:
                parts.append(r"\d+")
            elif all_alpha:
                if len(set(column)) == 1:
                    parts.append(re.escape(column[0]))
                else:
                    parts.append("[A-Za-z]+")
            else:
                # Mixed token (e.g. P137, A14): split alpha prefix + digits.
                alpha_prefixes: list[str] = []
                digit_suffixes: list[str] = []
                for token in column:
                    alpha_match = re.match(r"[A-Za-z]+", token)
                    if alpha_match is None:
                        alpha_prefixes.append("")
                        digit_suffixes.append(token)
                    else:
                        alpha_prefixes.append(alpha_match.group(0))
                        digit_suffixes.append(token[len(alpha_match.group(0)):])
                if (
                    all(re.fullmatch(r"[A-Za-z]+", a) for a in alpha_prefixes)
                    and all(d.isdigit() for d in digit_suffixes)
                ):
                    if len(set(alpha_prefixes)) == 1:
                        parts.append(re.escape(alpha_prefixes[0]) + r"\d+")
                    else:
                        parts.append(r"[A-Za-z]+\d+")
                else:
                    alignable = False
                    break
        if not alignable:
            continue
        family_id = "auto_" + re.sub(
            r"[^a-z0-9]+", "_", layer.casefold()
        ).strip("_")
        if len(result) > 0:
            family_id = f"{family_id}_{len(result) + 1}"
        result.append({
            "family_id": family_id[:48] or "auto_family",
            "text_pattern": "^" + r"\.".join(parts) + "$",
            "target_class": infer_target_class(layer),
            "max_distance_native_m": 15.0,
            "source_layer": layer,
            "auto_derived": True,
        })
    return result


def _cleaned_pattern(pattern: str) -> str:
    """Normalize a regex to token-level structure, preserving field boundaries.

    ``\\d`` classes become ``0`` (digit placeholder), letter char classes
    (``[A-C]``) become ``A`` (letter placeholder), escaped separators become
    ``.`` (token boundary), quantifiers and anchors are removed.  ``.*``
    collapses become ``.`` too — the collapsed region no longer contributes
    tokens.
    """
    body = re.sub(r"\\[dDwWsS]", "0", pattern)
    body = re.sub(r"\\[A-Za-z]", "", body)

    def _class_placeholder(match: re.Match) -> str:
        cls = match.group(0)
        if re.search(r"[A-Za-z]", cls):
            return "A"
        if re.search(r"[0-9]", cls):
            return "0"
        return "."

    body = re.sub(r"\[[^\]]*\]", _class_placeholder, body)
    body = re.sub(r"\{[^}]*\}", "", body)
    body = body.replace(".*", ".").replace("*", "").replace("+", "").replace("?", "")
    body = body.replace("^", "").replace("$", "")
    return body


def _literal_tokens(pattern: str) -> list[str]:
    """Tokens a regex asserts structurally (digit classes as ``0``)."""
    return [tok for tok in _tokenize(_cleaned_pattern(pattern)) if tok]


def _separator_normalized(pattern: str) -> str:
    """Map separator variants to a canonical dot so they compare equal."""
    normalized = re.sub(r"[^A-Za-z0-9\\A-Za-z\\d]", ".", pattern)
    normalized = re.sub(r"\\.", ".", normalized)
    return normalized


def _field_tokens(pattern: str) -> list[str]:
    """Extract the field-level tokens a pattern actually asserts."""
    normalized = _separator_normalized(pattern)
    fields: list[str] = []
    for field in normalized.split("."):
        if not field:
            continue
        if field == "*" or field == "+":
            continue
        fields.append(field)
    return fields


def _fullmatch(pattern: str, text: str) -> bool:
    try:
        return re.compile(pattern).fullmatch(text) is not None
    except re.error:
        return False


def l1_validate_family(
    family: Mapping[str, Any],
    layer_samples: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    min_match_fraction: float = 0.8,
    min_structure_fraction: float = 1.0,
) -> dict[str, Any]:
    """Cross-layer isolation + structural field coverage.

    Isolation: own-layer samples match; foreign samples don't.  Structure:
    alphabetic field tokens of the own-layer samples must appear literally in
    the pattern (``.*`` collapse of middle fields fails this check).
    """
    pattern = str(family.get("text_pattern", ""))
    source_layer = str(family.get("source_layer", "")).strip()
    # Case-insensitive layer matching: the model may return "Pole ID" while
    # the inventory key is "POLE ID".  A miss here would let a family pass
    # L1 vacuously (own samples empty), so compare casefolded.
    source_layer_key = source_layer.casefold()
    own = next(
        (samples for layer, samples in layer_samples.items()
         if layer.strip().casefold() == source_layer_key),
        (),
    )
    own_matched = sum(
        1 for sample in own if _fullmatch(pattern, str(sample.get("text", "")))
    )
    foreign_matched = 0
    foreign_total = 0
    # Auto-derived families carry an exact source_layer bound, so at runtime
    # the layer filter already isolates them (semantics.py matches both text
    # and layer).  Cross-layer rejection is only meaningful for unbounded
    # families, and a wide [A-Za-z]+ pattern would otherwise fail vacuously.
    skip_foreign = bool(family.get("auto_derived"))
    for layer, samples in layer_samples.items():
        if layer.strip().casefold() == source_layer_key:
            continue
        for sample in samples:
            foreign_total += 1
            if not skip_foreign and _fullmatch(pattern, str(sample.get("text", ""))):
                foreign_matched += 1

    own_fraction = own_matched / len(own) if own else 0.0
    structure_fraction = (
        sum(
            _token_coverage(pattern, str(sample.get("text", "")))
            for sample in own
        )
        / len(own)
        if own
        else 1.0
    )
    # A family without a resolvable source layer is vacuous: it could match
    # any text anywhere.  Fail closed instead of passing with no own samples.
    layer_resolved = bool(source_layer and own)
    # Auto-derived families cover one width-group of a multi-family layer
    # (e.g. POLE ID holds EXT.MR.* and MR.DSBR.* families), so requiring a
    # high whole-layer match fraction would reject every group.  Only at
    # least one own-sample match is required; the width-group guarantee is
    # structural (the pattern was derived from that group).
    own_requirement = (
        1.0 / len(own)
        if family.get("auto_derived") and own
        else (min_match_fraction if own else False)
    )
    passed = (
        layer_resolved
        and (own_fraction >= own_requirement if own else False)
        and foreign_matched == 0
        and structure_fraction >= min_structure_fraction
    )
    return {
        "family_id": family.get("family_id", ""),
        "source_layer": source_layer,
        "own_samples": len(own),
        "own_matched": own_matched,
        "own_fraction": round(own_fraction, 3),
        "structure_fraction": round(structure_fraction, 3),
        "foreign_matched": foreign_matched,
        "foreign_total": foreign_total,
        "passed": bool(passed),
    }


def _token_covered(token: str, pattern_tokens: list[str]) -> bool:
    """One text token is structurally asserted when some pattern token covers
    its alphabetic field (case-insensitive) and its numeric part is covered
    either literally or by a digit placeholder (``0``)."""
    alpha_match = re.match(r"[A-Za-z]+", token)
    alpha_part = alpha_match.group(0).casefold() if alpha_match else ""
    numeric_part = token[len(alpha_match.group(0)):] if alpha_match else token
    for pattern_token in pattern_tokens:
        p_token = pattern_token.casefold()
        if not p_token:
            continue
        if p_token == "a":
            # Letter placeholder ([A-Za-z]+): covers any alphabetic field.
            return True
        if p_token.startswith("a") and set(p_token[1:]) <= {"0"}:
            # Letter+digit placeholder (e.g. "a0" from [A-Za-z]+\d+):
            # covers any mixed alphabetic-numeric field.
            return True
        if alpha_part:
            if not p_token.startswith(alpha_part):
                continue
            remainder = p_token[len(alpha_part):]
            if numeric_part == "":
                return True
            if remainder == "" or remainder.startswith("0") or remainder == numeric_part.casefold():
                return True
        else:
            # Pure numeric token: literal match or digit placeholder.
            if p_token == numeric_part.casefold() or p_token.startswith("0"):
                return True
    return False


def _token_coverage(pattern: str, text: str) -> float:
    """Structural coverage: every field token of the text must be asserted.

    The cleaned pattern keeps field boundaries (digit classes → ``0``,
    separators → ``.``).  A ``.*`` collapse removes whole fields from the
    cleaned token stream, so coverage drops below 1.0 for those fields.
    Literal separator and case variants are tolerated by construction.
    """
    tokens = _tokenize(text)
    if not tokens:
        return 0.0
    pattern_tokens = _literal_tokens(pattern)
    covered = sum(
        1 for token in tokens if _token_covered(token, pattern_tokens)
    )
    return covered / len(tokens)


def _fullmatch_count(text: str, families: Sequence[Mapping[str, Any]]) -> int:
    """Number of families whose regex full-matches the sample text."""
    count = 0
    for family in families:
        pattern = str(family.get("text_pattern", ""))
        if _fullmatch(pattern, text):
            count += 1
    return count


def l2_validate_family_group(
    families: Sequence[Mapping[str, Any]],
    samples: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Intra-layer grouping: every sample full-matches exactly one family.

    A sample matching two or more families indicates overlapping patterns
    (ambiguous); a sample matching none indicates the families miss part of
    the layer's label structure (unassigned).  Fullmatch is the hard grouping
    test; token coverage remains the runtime cost signal for assignment.
    """
    records: list[dict[str, Any]] = []
    for sample in samples:
        text = str(sample.get("text", ""))
        matched = _fullmatch_count(text, families)
        records.append({
            "text": text,
            "matched_families": matched,
            "ambiguous": matched > 1,
            "unassigned": matched == 0,
        })
    ambiguous = [r for r in records if r["ambiguous"]]
    unassigned = [r for r in records if r["unassigned"]]
    return {
        "sample_count": len(records),
        "ambiguous_count": len(ambiguous),
        "unassigned_count": len(unassigned),
        "passed": not ambiguous and not unassigned,
        "ambiguous_samples": ambiguous[:10],
        "unassigned_samples": unassigned[:10],
    }


def structural_failure_evidence(
    family: Mapping[str, Any],
    layer_samples: Sequence[Mapping[str, Any]],
    l1_result: Mapping[str, Any],
    l2_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the evidence payload sent back to the model for a repair attempt."""
    pattern = str(family.get("text_pattern", ""))
    matched = [
        str(sample.get("text", ""))
        for sample in layer_samples
        if _fullmatch(pattern, str(sample.get("text", "")))
    ]
    unmatched = [
        str(sample.get("text", ""))
        for sample in layer_samples
        if not _fullmatch(pattern, str(sample.get("text", "")))
    ]
    return {
        "family_id": family.get("family_id", ""),
        "current_text_pattern": pattern,
        "target_class": family.get("target_class", ""),
        "l1_own_fraction": l1_result.get("own_fraction"),
        "l1_foreign_matched": l1_result.get("foreign_matched"),
        "l2_ambiguous_count": l2_result.get("ambiguous_count"),
        "matched_samples": matched[:20],
        "unmatched_samples": unmatched[:20],
        "instruction": (
            "Fix the text_pattern so that it preserves every observed field "
            "token of this layer's label structure (no .* collapsing middle "
            "fields, no invented placeholders). Separator and case variants "
            "are tolerated. Keep the family_id and target_class unchanged."
        ),
    }
