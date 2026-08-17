import importlib, sys
print('python', sys.version.replace('\n', ' '))
for m in ['numpy', 'shapely', 'pyproj', 'osgeo.gdal', 'osgeo.ogr', 'matplotlib', 'scipy', 'PIL']:
    try:
        mod = importlib.import_module(m)
        print(m, getattr(mod, '__version__', 'ok'))
    except Exception as e:
        print(m, 'MISSING', type(e).__name__)
