"""Coordinate-runtime compatibility helpers without drawing-specific rules.

Native modules are supplied by the caller; importing this module does not load
GDAL or any other optional GIS runtime.
"""


def set_traditional_axis_order(spatial_reference, osr_module):
    """Force longitude/easting first while retaining GDAL 2 compatibility."""
    setter = getattr(spatial_reference, "SetAxisMappingStrategy", None)
    strategy = getattr(osr_module, "OAMS_TRADITIONAL_GIS_ORDER", None)
    if setter is not None and strategy is not None:
        setter(strategy)
