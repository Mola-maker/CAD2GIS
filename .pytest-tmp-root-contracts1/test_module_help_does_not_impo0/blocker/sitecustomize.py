import builtins
_original = builtins.__import__
def _blocked(name, *args, **kwargs):
    if name.split('.', 1)[0] in {'osgeo', 'pyproj', 'ezdxf'}:
        raise RuntimeError('blocked heavy import: ' + name)
    return _original(name, *args, **kwargs)
builtins.__import__ = _blocked