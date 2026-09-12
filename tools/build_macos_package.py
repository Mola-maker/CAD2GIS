"""Build an unsigned native macOS PKG containing the offline Python/CAD runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tomllib

from build_runtime_bundle import REPOSITORY, build, download, extract, sha256

PYTHON_HASHES = {
    'arm64': ('aarch64', '81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b'),
    'x86_64': ('x86_64', '65b195c9cedc1fef6767f044f9822069adbd1bd9204d424ece4628776fdc04bb'),
}
SOURCE_SHA = 'cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351'
PATCH_SHA = 'e47037ab526f7d5adadcdadda34153764aedcbdc76f970a65137cd4d7f32ad1b'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    args = parser.parse_args()
    machine = platform.machine()
    if sys.platform != 'darwin' or machine not in PYTHON_HASHES:
        parser.error('Run on native macOS arm64 or x86_64')
    arch, python_sha = PYTHON_HASHES[machine]
    stage = REPOSITORY / 'tmp' / ('packaging-macos-' + machine)
    stage.mkdir(parents=True, exist_ok=False)
    version = tomllib.loads((REPOSITORY / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    python_url = ('https://github.com/astral-sh/python-build-standalone/releases/download/20260901/'
                  f'cpython-3.12.14%2B20260901-{arch}-apple-darwin-install_only_stripped.tar.gz')
    extract(download(python_url, python_sha, stage / 'python.tar.gz'), stage)
    archive = download('https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz',
                       SOURCE_SHA, stage / 'libredwg-0.14.tar.gz')
    extract(archive, stage / 'native-source')
    source = stage / 'native-source/libredwg-0.14'
    patch = REPOSITORY / 'packaging/patches/libredwg-0.14-cquote-termination.patch'
    if sha256(patch) != PATCH_SHA:
        raise ValueError('Native patch hash mismatch')
    prefix = stage / 'native-installed'
    environment = dict(os.environ, CC='clang', CFLAGS='-O2', PKG_CONFIG_LIBDIR=str(stage / 'no-optional-packages'))
    commands = [
        ['patch', '--batch', '-p1', '-i', str(patch)],
        ['./configure', '--prefix=' + str(prefix), '--disable-bindings', '--disable-python',
         '--disable-docs', '--disable-shared'],
        ['make', '-j3'], ['make', 'install'],
    ]
    for command in commands:
        subprocess.run(command, cwd=source, env=environment, check=True)
    native = stage / 'libredwg'
    (native / 'bin').mkdir(parents=True)
    for name in ('dwg2dxf', 'dwgread', 'dxf2dwg'):
        binary = native / 'bin' / name
        shutil.copy2(prefix / 'bin' / name, binary)
        dependencies = subprocess.check_output(['otool', '-L', str(binary)], text=True)
        if any(not line.strip().startswith(('/usr/lib/', '/System/Library/'))
               for line in dependencies.splitlines()[1:]):
            raise ValueError('Native binary depends on unbundled libraries: ' + dependencies)
        subprocess.run([str(binary), '--version'], check=True)
    repro = REPOSITORY / 'packaging/patches/cquote_minimal.c'
    subprocess.run(['clang', '-O2', str(repro), '-o', str(stage / 'cquote-test')], check=True)
    subprocess.run([str(stage / 'cquote-test')], check=True)
    (native / 'source').mkdir()
    for path in (archive, patch, repro):
        shutil.copy2(path, native / 'source' / path.name)
    shutil.copy2(source / 'COPYING', native / 'COPYING')
    (native / 'build-receipt.json').write_text(json.dumps({
        'source_sha256': SOURCE_SHA, 'patch_sha256': PATCH_SHA, 'commands': commands,
        'native_hashes': {p.name: sha256(p) for p in (native / 'bin').iterdir()},
        'reproducer_execution': 'passed', 'real_dwg_acceptance': 'not_run',
    }, indent=2), encoding='utf-8')
    payload = build(stage / 'python', args.wheel, native, stage / 'payload')
    bundle_python = payload / 'runtime/bin/python3'
    env = dict(os.environ, CAD2GIS_LIBREDWG_CLI=str(payload / 'libredwg/bin/dwg2dxf'))
    subprocess.run([str(payload / 'bin/cad2gis'), 'doctor', '--deep', '--strict', '--profile', 'full', '--json'],
                   env=env, check=True)
    subprocess.run([str(bundle_python), '-I', str(REPOSITORY / 'tools/smoke_runtime_bundle.py'),
                    '--bundle', str(payload), '--output', str(stage / 'smoke')], env=env, check=True)
    package_root = stage / 'package-root'
    contents = package_root / 'Applications/CAD2GIS.app/Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Resources').mkdir()
    shutil.move(str(payload), contents / 'Resources/payload')
    launcher = contents / 'MacOS/CAD2GIS'
    launcher.write_text('#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")/../Resources/payload" && pwd)\nexec "$root/bin/cad2gis-desktop" "$@"\n', encoding='utf-8')
    launcher.chmod(0o755)
    with (contents / 'Info.plist').open('wb') as output:
        plistlib.dump({'CFBundleIdentifier': 'org.cad2gis.desktop', 'CFBundleName': 'CAD2GIS',
                      'CFBundleExecutable': 'CAD2GIS', 'CFBundlePackageType': 'APPL',
                      'CFBundleShortVersionString': version, 'CFBundleVersion': version,
                      'NSHighResolutionCapable': True}, output)
    commands_dir = package_root / 'usr/local/bin'
    commands_dir.mkdir(parents=True)
    for launcher in (contents / 'Resources/payload/bin').iterdir():
        (commands_dir / launcher.name).symlink_to('/Applications/CAD2GIS.app/Contents/Resources/payload/bin/' + launcher.name)
    output = REPOSITORY / 'dist/macos'
    output.mkdir(parents=True, exist_ok=True)
    pkg = output / f'CAD2GIS-{version}-macos-{machine}.pkg'
    components = stage / 'components.plist'
    with components.open('wb') as stream:
        plistlib.dump([{'RootRelativeBundlePath': 'Applications/CAD2GIS.app',
                       'BundleIsRelocatable': False}], stream)
    subprocess.run(['pkgbuild', '--root', str(package_root), '--identifier', 'org.cad2gis.desktop',
                    '--component-plist', str(components),
                    '--version', version, '--install-location', '/', str(pkg)], check=True)
    (output / f'macos-{machine}-build.json').write_text(json.dumps({
        'version': version, 'architecture': machine, 'artifact': pkg.name, 'sha256': sha256(pkg),
        'wheel_sha256': sha256(args.wheel), 'python_sha256': python_sha,
        'signing': 'unsigned; not notarized', 'runtime_bundled': True,
        'smoke': 'passed before packaging; installed acceptance runs separately',
    }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
