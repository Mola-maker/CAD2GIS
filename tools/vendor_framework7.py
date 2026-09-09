"""Vendor the reviewed Framework7 distribution with an integrity-pinned archive."""
from pathlib import Path
import hashlib
import io
import tarfile
import urllib.request

URL = 'https://registry.npmjs.org/framework7/-/framework7-9.1.3.tgz'
SHA256 = 'cb3b88398e70fac25b6aac71a873ca18894e33dcf47e365901fe61c8174461be'


def main():
    root = Path(__file__).resolve().parents[1]
    data = urllib.request.urlopen(URL, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Framework7 archive checksum mismatch')
    destination = root / 'src/cad2gis/desktop/vendor'
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for name in ('framework7-bundle.min.css', 'framework7-bundle.min.js', 'LICENSE'):
            members = [m for m in archive.getmembers() if m.isfile() and Path(m.name).name == name]
            if len(members) != 1:
                raise ValueError(f'Ambiguous asset: {name}')
            (destination / name).write_bytes(archive.extractfile(members[0]).read())
    print(destination)


if __name__ == '__main__':
    main()
