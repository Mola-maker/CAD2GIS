"""Install/symlink the cad2gis plugin into the active QGIS profile's python/plugins directory.

Usage (from the repo root, with the cad2gis conda env active):
    python qgis_plugin/install.py            # symlink (default; rerunnable)
    python qgis_plugin/install.py --copy     # copy instead (for environments without symlink rights)

Then enable "cad2gis" in QGIS -> Plugins -> Manage and Install Plugins (Installed tab).
"""
import argparse
import os
import shutil
import sys

PLUGIN_NAME = "cad2gis_qgis"
HERE = os.path.abspath(os.path.dirname(__file__))


def _qgis_profile_plugins_dir() -> str:
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "AppData", "Roaming", "QGIS", "QGIS3", "profiles", "default", "python", "plugins"),
        os.path.join(home, ".local", "share", "QGIS", "QGIS3", "profiles", "default", "python", "plugins"),
        os.path.join(home, "Library", "Application Support", "QGIS", "QGIS3", "profiles", "default", "python", "plugins"),
    ]
    for c in candidates:
        if os.path.isdir(os.path.dirname(c)):
            os.makedirs(c, exist_ok=True)
            return c
    c = candidates[0]
    os.makedirs(c, exist_ok=True)
    return c


def _write_repo_path(dest: str) -> None:
    repo = os.path.abspath(os.path.join(HERE, ".."))
    path = os.path.join(dest, "_cad2gis_repo_path.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"REPO_ROOT = {repo!r}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--copy", action="store_true", help="copy files instead of symlinking")
    args = ap.parse_args()

    dest = os.path.join(_qgis_profile_plugins_dir(), PLUGIN_NAME)
    if os.path.islink(dest) or os.path.exists(dest):
        if os.path.islink(dest):
            os.remove(dest)
        else:
            shutil.rmtree(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    if args.copy:
        shutil.copytree(HERE, dest, ignore=shutil.ignore_patterns("__pycache__", "install.py"))
        _write_repo_path(dest)
        print(f"copied -> {dest}")
    else:
        try:
            os.symlink(HERE, dest, target_is_directory=True)
            print(f"symlinked -> {dest}")
        except (OSError, NotImplementedError):
            shutil.copytree(HERE, dest, ignore=shutil.ignore_patterns("__pycache__", "install.py"))
            _write_repo_path(dest)
            print(f"symlink failed; copied -> {dest}")
    print(f"enable '{PLUGIN_NAME}' in QGIS -> Plugins -> Manage and Install Plugins")


if __name__ == "__main__":
    sys.exit(main() or 0)
