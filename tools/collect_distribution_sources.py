"""Collect pinned corresponding upstream sources beside development installers."""
from pathlib import Path

from build_runtime_bundle import REPOSITORY, download

SOURCES = (
    ("libredwg-0.14.tar.gz", "https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz",
     "cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351"),
    ("pyramids_gis-0.58.1.tar.gz", "https://files.pythonhosted.org/packages/96/d4/14cb2dbce157374954978c68c9141df6177c12617e0701853d0c794b80b1/pyramids_gis-0.58.1.tar.gz",
     "1877ffccad23179982333e888e46aaed978987e3a5f092850fb7f5fbc05f1a48"),
)


def collect(destination: Path) -> None:
    for filename, url, digest in SOURCES:
        print(download(url, digest, destination / filename), flush=True)
    (destination / "SHA256SUMS").write_text(
        "".join(f"{digest}  {filename}\n" for filename, _, digest in SOURCES), encoding="utf-8",
    )


if __name__ == "__main__":
    collect(REPOSITORY / "dist/third-party-sources")
