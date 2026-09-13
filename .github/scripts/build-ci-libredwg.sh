#!/usr/bin/env bash
# Build CI's reader from the same pinned source and correction as the delivery.
# This validates the C reproducer; private real-DWG acceptance remains separate.
set -euo pipefail

if [[ "${MSYSTEM:-}" != MINGW64 ]]; then
  printf '%s\n' 'This CI build requires the MSYS2 MINGW64 environment.' >&2
  exit 1
fi
: "${GITHUB_WORKSPACE:?Run from the Windows GitHub Actions job}"
: "${RUNNER_TEMP:?A runner-owned temporary directory is required}"
: "${GITHUB_ENV:?The following steps need the qualified reader path}"

source_sha=cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351
patch_sha=e47037ab526f7d5adadcdadda34153764aedcbdc76f970a65137cd4d7f32ad1b
repro_sha=d8041b1352a7fb214584693993882437c775b196c5c269b3dd40c71704891c38
repo=$(cygpath -u "$GITHUB_WORKSPACE")
github_env=$(cygpath -u "$GITHUB_ENV")
stage=$(mktemp -d "$(cygpath -u "$RUNNER_TEMP")/cad2gis-libredwg-ci.XXXXXX")
patch_file="$repo/packaging/patches/libredwg-0.14-cquote-termination.patch"
repro_file="$repo/packaging/patches/cquote_minimal.c"
archive="$stage/libredwg-0.14.tar.gz"
source_dir="$stage/libredwg-0.14"
prefix="$stage/installed"
runtime="$stage/runtime"
mkdir "$runtime"

# Keep build evidence in runner temp and in the job log, without artifact-storage
# dependencies. Failed hashes, builds and checks all stop before GITHUB_ENV.
exec > >(tee "$stage/build.log") 2>&1
printf 'CI native stage: %s\n' "$(cygpath -m "$stage")"
printf '%s  %s\n' "$patch_sha" "$patch_file" "$repro_sha" "$repro_file" | sha256sum --check --strict
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  'https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz' \
  --output "$archive"
printf '%s  %s\n' "$source_sha" "$archive" | sha256sum --check --strict
tar -xzf "$archive" -C "$stage"
cd "$source_dir"
patch --batch --forward -p1 -i "$patch_file"
patched_source_sha=$(sha256sum src/out_dxf.c | cut -d ' ' -f 1)

export CC=gcc
export CFLAGS='-O2 -std=gnu11'
export LDFLAGS='-static'
export PKG_CONFIG_LIBDIR="$stage/no-optional-packages"
gcc -D__USE_MINGW_ANSI_STDIO=1 -O2 -Wall -Wextra -static \
  "$repro_file" -o "$stage/cquote_minimal.exe"
"$stage/cquote_minimal.exe" 2>&1 | tr -d '\r' | tee "$stage/cquote-result.log"
grep -Fx 'fixed termination cases: 8; passed: 1' "$stage/cquote-result.log"

./configure --prefix="$prefix" --host=x86_64-w64-mingw32 \
  --disable-bindings --disable-python --disable-docs --disable-shared
make -j2
# Libtool -static alone still allows shared compiler runtimes. Force the three
# reader/writer links to include them so later PowerShell steps need no MSYS DLLs.
make -C programs LDFLAGS=-all-static \
  -W dwgread.o -W dwg2dxf.o -W dxf2dwg.o \
  dwgread.exe dwg2dxf.exe dxf2dwg.exe
make install

for name in dwg2dxf dwgread dxf2dwg; do
  binary="$runtime/$name.exe"
  cp "$prefix/bin/$name.exe" "$binary"
  strip --strip-unneeded "$binary"
  objdump -p "$binary" > "$stage/$name-pe-imports.txt"
  dlls=$(awk '/DLL Name:/ {print tolower($3)}' "$stage/$name-pe-imports.txt")
  if [[ -z "$dlls" ]]; then
    printf 'No PE imports found for %s\n' "$binary" >&2
    exit 1
  fi
  while IFS= read -r dll; do
    case "$dll" in
      kernel32.dll|msvcrt.dll|user32.dll|advapi32.dll|shell32.dll|api-ms-win-*) ;;
      *) printf 'Unexpected runtime DLL for %s: %s\n' "$name" "$dll" >&2; exit 1 ;;
    esac
  done <<< "$dlls"
  "$binary" --version
done

mkdir "$runtime/source"
cp "$archive" "$patch_file" "$repro_file" "$repo/packaging/patches/README.md" \
  "$repo/packaging/patches/NOTICE.md" "$repo/packaging/patches/COPYING" "$runtime/source/"
cp "$repo/.github/scripts/build-ci-libredwg.sh" "$runtime/source/"
cp COPYING "$runtime/COPYING"
dwg2dxf_sha=$(sha256sum "$runtime/dwg2dxf.exe" | cut -d ' ' -f 1)
dwgread_sha=$(sha256sum "$runtime/dwgread.exe" | cut -d ' ' -f 1)
dxf2dwg_sha=$(sha256sum "$runtime/dxf2dwg.exe" | cut -d ' ' -f 1)
cat > "$runtime/build-receipt.json" <<EOF
{
  "schema": "cad2gis.ci_patched_libredwg.v1",
  "version": "0.14",
  "environment": "MINGW64",
  "compiler_version": "$(gcc -dumpfullversion)",
  "source_sha256": "$source_sha",
  "patch_sha256": "$patch_sha",
  "c_reproducer_sha256": "$repro_sha",
  "patched_out_dxf_sha256": "$patched_source_sha",
  "reproducer_cases": 8,
  "reproducer_execution": "passed",
  "real_dwg_acceptance": "not_run_in_public_ci",
  "native_hashes": {
    "dwg2dxf.exe": "$dwg2dxf_sha",
    "dwgread.exe": "$dwgread_sha",
    "dxf2dwg.exe": "$dxf2dwg_sha"
  }
}
EOF
cat "$runtime/build-receipt.json"
printf 'CAD2GIS_LIBREDWG_CLI=%s\n' "$(cygpath -m "$runtime/dwg2dxf.exe")" >> "$github_env"
