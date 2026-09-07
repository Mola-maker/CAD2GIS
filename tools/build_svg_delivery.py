"""Compatibility entry point; implementation ships in the installed package."""
from cad2gis.svg_delivery import main, build  # noqa: F401

if __name__ == "__main__":
    main()
