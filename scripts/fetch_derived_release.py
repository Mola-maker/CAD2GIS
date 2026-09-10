"""Fetch a hash-pinned derived Pages bundle; reject unsafe paths before extraction."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

_RELEASE_PREFIX = 'https://github.com/Mola-maker/CAD2GIS/releases/download/'
_API_PREFIX = 'https://api.github.com/repos/Mola-maker/CAD2GIS/releases/tags/'


def pinned_asset_endpoint(origin: str) -> tuple[str, str]:
    """Return the release API endpoint and asset name for a pinned URL."""
    if not origin.startswith(_RELEASE_PREFIX):
        raise ValueError('Unexpected release origin')
    tag, separator, asset = origin[len(_RELEASE_PREFIX):].partition('/')
    if not separator or not tag or not asset or '/' in asset:
        raise ValueError('Unexpected release origin')
    return _API_PREFIX + urllib.parse.quote(tag, safe=''), asset


def _request(url: str, accept: str) -> urllib.request.Request:
    headers = {'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28'}
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return urllib.request.Request(url, headers=headers)


def open_pinned_asset(origin: str):
    """Resolve the pinned release asset through the release API.

    Private repositories do not serve ``releases/download`` URLs to token
    clients, so the asset is resolved by name and streamed from the API.
    The manifest SHA-256 still decides what may be unpacked.
    """
    release_endpoint, name = pinned_asset_endpoint(origin)
    with urllib.request.urlopen(_request(release_endpoint, 'application/vnd.github+json'), timeout=60) as response:
        release = json.loads(response.read().decode('utf-8'))
    assets = release.get('assets', ()) if isinstance(release, dict) else ()
    matches = [entry for entry in assets
               if isinstance(entry, dict) and entry.get('name') == name
               and isinstance(entry.get('url'), str)]
    if len(matches) != 1:
        raise ValueError('Release asset is missing or ambiguous')
    return urllib.request.urlopen(_request(matches[0]['url'], 'application/octet-stream'), timeout=120)


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
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'release.zip'
        with open_pinned_asset(spec['url']) as response, archive.open('wb') as stream:
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
