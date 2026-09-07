"""Compatibility entry point; implementation ships in the installed package."""
from cad2gis.qgis_package import main, package, sha  # noqa: F401

if __name__ == "__main__":
    main()
