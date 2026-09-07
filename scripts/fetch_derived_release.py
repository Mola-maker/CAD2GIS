"""Fetch a hash-pinned derived Pages bundle; reject unsafe paths before extraction."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


def unpack(archive: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    with zipfile.ZipFile(archive) as bundle:
        names = set()
        total = 0
        for item in bundle.infolist():
            path = PurePosixPath(item.filename)
            total += item.file_size
            if (not item.filename or '\\' in item.orig_filename or ':' in item.filename
                    or path.is_absolute() or '..' in path.parts or item.filename in names
                    or path.suffix.lower() in {'.dwg', '.dxf'}
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                    or total > 2_000_000_000):
                raise ValueError('Unsafe or oversized derived release member')
            names.add(item.filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.release-', dir=destination.parent) as temporary:
            staged = Path(temporary) / 'content'
            bundle.extractall(staged)
            try:
                from scripts.verify_derived_release import verify
            except ModuleNotFoundError:
                from verify_derived_release import verify
            verify(staged)
            if os.name == 'nt':
                subprocess.run(['icacls.exe', temporary, '/reset', '/T', '/Q'],
                               capture_output=True, check=True, timeout=60,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            staged.rename(destination)


def fetch(manifest: Path, destination: Path) -> None:
    spec = json.loads(manifest.read_text(encoding='utf-8'))
    if not spec['url'].startswith('https://github.com/Mola-maker/CAD2GIS/releases/download/'):
        raise ValueError('Unexpected release origin')
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'release.zip'
        with urllib.request.urlopen(spec['url'], timeout=120) as response, archive.open('wb') as stream:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > spec['bytes'] or size > 500_000_000:
                    raise ValueError('Release size exceeds manifest')
                stream.write(chunk)
        with archive.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if size != spec['bytes'] or digest != spec['sha256']:
            raise ValueError('Derived release SHA256 or size mismatch')
        unpack(archive, destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('docs/derived-release.json'))
    parser.add_argument('--output', type=Path, default=Path('pages-delivery/nine-drawings'))
    args = parser.parse_args()
    fetch(args.manifest, args.output)
