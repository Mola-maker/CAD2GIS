"""Review a color-only reader census migration; never relax conversion gates.

Creates a NEW project only after full record comparison. Numeric differences
are retained in a receipt and limited to 1e-9 absolute source units. No source
snapshot or historical project is rewritten. Different readers/entity sets
require separate semantic review and are rejected here.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import shutil

from cad2gis.cad2gis_v3.project_profile import inventory_sha256

COLOR_FIELDS = {"true_color", "entity_true_color", "layer_true_color"}
DERIVED = {"inventory_sha256", "plan_domain", "inspection_status", "onboarding", "cad_scene_graph"}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def color(value):
    if isinstance(value, str) and re.fullmatch(r'#-?[0-9a-fA-F]{6,8}', value):
        return f'#{int(value[1:], 16) & 0xffffff:06X}'
    return value


def census(value):
    result = {k: copy.deepcopy(v) for k, v in value.items() if k not in DERIVED}
    styles = {}
    for key, count in result.get('style_facts', {}).items():
        key = re.sub(r'(?<=TRUECOLOR:)#-?[0-9A-Fa-f]{6,8}(?=\||$)', lambda m: color(m[0]), key)
        styles[key] = styles.get(key, 0) + count
    result['style_facts'] = styles
    return result


def compare(a, b, path, changes):
    if path.endswith('/source_file') and path.count('/') == 2:
        if a != b:
            changes['relocated_source_paths'] += 1
        return
    if path.rsplit('/', 1)[-1] in COLOR_FIELDS and color(a) == color(b):
        if a != b:
            changes['color_encoding_changes'].append({'path': path, 'old': a, 'new': b})
        return
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            raise ValueError(f'Record fields changed at {path}')
        for key in a:
            compare(a[key], b[key], path + '/' + key, changes)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            raise ValueError(f'Record array length changed at {path}')
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, path + '/' + str(i), changes)
    elif a != b:
        if type(a) is float and type(b) is float and math.isfinite(a) and math.isfinite(b) and abs(a-b) <= 1e-9:
            changes['numeric_changes'].append({'path': path, 'old': a, 'new': b, 'absolute_delta': abs(a-b)})
        else:
            raise ValueError(f'Non-color/non-numeric-epsilon change at {path}: {str(a)[:100]} -> {str(b)[:100]}')


def migrate(project: Path, previous_source: Path, new_source: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    old = read(project / 'review/source_inventory.json')
    new = read(new_source / 'review/source_inventory.json')
    for inventory in (old, new):
        if inventory_sha256(inventory) != inventory['inventory_sha256']:
            raise ValueError('Inventory checksum invalid')
    prior_manifest = read(previous_source / 'source_manifest.json')
    fresh_manifest = read(new_source / 'source_manifest.json')
    for manifest in (prior_manifest, fresh_manifest):
        if manifest['source']['sha256'] != old['source']['sha256']:
            raise ValueError('Source identity changed')
        records = previous_source / 'reader_records.jsonl' if manifest is prior_manifest else new_source / 'reader_records.jsonl'
        artifact = manifest['artifacts']['reader_records']
        if sha(records) != artifact['sha256']:
            raise ValueError('Reader record checksum invalid')
    if census(old) != census(new):
        raise ValueError('Inventory differs beyond RGB encoding; independent review required')
    changes = {'relocated_source_paths': 0, 'color_encoding_changes': [], 'numeric_changes': []}
    count = 0
    with (previous_source / 'reader_records.jsonl').open(encoding='utf-8') as left, (new_source / 'reader_records.jsonl').open(encoding='utf-8') as right:
        for count, pair in enumerate(itertools.zip_longest(left, right), 1):
            if None in pair:
                raise ValueError('Reader record count changed')
            compare(json.loads(pair[0]), json.loads(pair[1]), '/' + str(count), changes)
    new_inventory = census(new)
    new_hash = inventory_sha256(new_inventory)
    new_inventory['inventory_sha256'] = new_hash
    receipt = {'schema_version': 'cad2gis.reader-color-migration.v1', 'source_sha256': old['source']['sha256'],
        'old_inventory_sha256': old['inventory_sha256'], 'observed_inventory_sha256': new['inventory_sha256'],
        'new_inventory_sha256': new_hash, 'record_count': count, 'absolute_numeric_tolerance': 1e-9,
        'prior_records_sha256': sha(previous_source / 'reader_records.jsonl'),
        'observed_records_sha256': sha(new_source / 'reader_records.jsonl'), 'changes': changes,
        'review_scope': 'RGB encoding normalization and bounded numeric replay; no new semantics or GCP approval',
        'original_project_files': {p.relative_to(project).as_posix(): sha(p) for p in sorted(project.rglob('*')) if p.is_file()}}
    updates = {}
    for relative in ('config/source_profile.json', 'config/mapping_registry.json', 'review/unsupported_inventory.json'):
        value = read(project / relative)
        binding = value.get('source_binding', value)
        if binding.get('inventory_sha256') != old['inventory_sha256']:
            raise ValueError(f'Prior binding mismatch: {relative}')
        binding['inventory_sha256'] = new_hash
        updates[relative] = value
    shutil.copytree(project, output)
    for relative, value in updates.items():
        (output / relative).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'review/source_inventory.json').write_text(json.dumps(new_inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'review/reader-color-migration.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    return {k: v for k, v in receipt.items() if k not in {'changes', 'original_project_files'}} | {
        'color_change_count': len(changes['color_encoding_changes']), 'numeric_change_count': len(changes['numeric_changes']),
        'numeric_max_delta': max((x['absolute_delta'] for x in changes['numeric_changes']), default=0)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('project', 'previous-source', 'new-source', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.project, args.previous_source, args.new_source, args.output), ensure_ascii=False))
