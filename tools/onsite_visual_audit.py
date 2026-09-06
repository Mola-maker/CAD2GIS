"""Compatibility entry point; implementation ships in the installed package."""
from cad2gis.visual_audit import audit, main, write_index

__all__ = ["audit", "main", "write_index"]

if __name__ == "__main__":
    main()