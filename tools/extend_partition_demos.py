"""Add complete, separately selectable Manado partitions to the nine-drawing release."""
import argparse
import copy
import json
from pathlib import Path
import shutil

from cad2gis.batch import write_json
from cad2gis.native_runtime import ensure_osgeo_runtime
ensure_osgeo_runtime()
from osgeo import ogr, osr  # noqa: E402


def extend(root, audits):
    catalog = json.loads((root / 'assets/catalog.json').read_text(encoding='utf-8'))
    catalog['projects'] = [p for p in catalog['projects'] if not p.get('parent_project_id')]
    parent = next(p for p in catalog['projects'] if p['id'] == 'drawing-03')
    template = json.loads((root / 'assets/drawing-03.json').read_text(encoding='utf-8'))
    report = json.loads((root / 'batch-report.json').read_text(encoding='utf-8'))
    links = next(r for r in report['drawings'] if r['id'] == 'drawing-03')['links']
    for region in ('EMR28560', 'EMR29619'):
        identifier = 'drawing-03-' + region.lower()
        folder = root / 'drawing-03' / region
        dataset = ogr.Open(str(folder / 'delivery.gpkg'))
        local, world = {}, {}
        counts = {}
        for layer in dataset:
            name = layer.GetName()
            if name.startswith(('gpkg_', 'rtree_')) or name == 'layer_styles':
                continue
            if layer.GetSpatialRef() is None:
                continue
            source = layer.GetSpatialRef().Clone()
            source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            target = osr.SpatialReference()
            target.ImportFromEPSG(4326)
            target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            transform = osr.CoordinateTransformation(source, target)
            native, geographic = [], []
            for row in layer:
                geometry = row.GetGeometryRef()
                if geometry is None or geometry.IsEmpty():
                    raise ValueError('Empty partition geometry')
                definition = row.GetDefnRef()
                properties = {definition.GetFieldDefn(i).GetName(): row.GetField(i) for i in range(definition.GetFieldCount())}
                feature = {'type': 'Feature', 'id': f'{name}:{row.GetFID()}', 'properties': properties,
                           'geometry': json.loads(geometry.ExportToJson(options=['COORDINATE_PRECISION=15']))}
                native.append(feature)
                projected = geometry.Clone()
                if projected.Transform(transform) != 0:
                    raise ValueError('Partition projection failed')
                geographic.append({**feature, 'geometry': json.loads(projected.ExportToJson(options=['COORDINATE_PRECISION=15']))})
            local[name] = {'type': 'FeatureCollection', 'features': native}
            world[name] = {'type': 'FeatureCollection', 'features': geographic}
            counts[name] = len(native)
        fixture = copy.deepcopy(template)
        fixture.update(layers=local, geographic_layers=world)
        fixture['provenance'].update(project_id=identifier, project_name=parent['display_name'] + ' / ' + region, parent_project_id='drawing-03')
        fixture['run']['demo'].update(project_id=identifier, project_name=fixture['provenance']['project_name'])
        fixture['run']['delivery_counts'] = counts
        fixture['run']['artifacts'] = {'delivery': 'demo://' + identifier}
        fixture['run']['validation'] = {'partition_id': region, 'independent_audit': f'drawing-03/{region}/process/audit.json',
                                      'absolute_accuracy_verified': False}
        fixture['run']['reasoning'] = {'scope': 'historical_partition_delivery_only'}
        filename = identifier + '.json'
        write_json(root / 'assets' / filename, fixture)
        catalog['projects'].append({**parent, 'id': identifier, 'parent_project_id': 'drawing-03', 'partition_id': region,
            'display_name': fixture['provenance']['project_name'], 'short_name': region, 'fixture': filename,
            'delivery_feature_count': sum(counts.values()), 'description': 'Manado 独立分区：完整交付和独立视觉审查，仍为 CONDITIONAL。'})
        process = folder / 'process'
        process.mkdir(exist_ok=True)
        from tools.publish_onsite_delivery import public
        for path in (audits / region).iterdir():
            if path.suffix in {'.png', '.csv'}:
                shutil.copy2(path, process / path.name)
            elif path.name == 'report.json':
                write_json(process / 'audit.json', public(json.loads(path.read_text(encoding='utf-8'))))
        links[region + ' Web 转化台'] = '../workspace/?demo=1&project=' + identifier
        links[region + ' 源图叠加'] = f'drawing-03/{region}/process/source-delivery-overlay.png'
        links[region + ' 审计报告'] = f'drawing-03/{region}/process/audit.json'
    write_json(root / 'assets/catalog.json', catalog)
    write_json(root / 'batch-report.json', report)
    print('Added two complete partition demos; run refresh_derived_release.py to seal publication')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--audits', type=Path, required=True)
    args = parser.parse_args()
    extend(args.root, args.audits)
