# LibreDWG 0.14 local termination correction

These files accompany the original LibreDWG 0.14 source archive and its GPLv3
`COPYING` license in each patched native runtime. The patch changes only the
final NUL placement in `src/out_dxf.c::cquote()`: it terminates at the actual
output cursor when space remains. Allocation, escaping, pointer ownership and
the return value are unchanged. Unwritten heap bytes must not become MTEXT.

Original upstream source:
https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz

| Input | SHA256 |
| --- | --- |
| Original `libredwg-0.14.tar.gz` | `cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351` |
| `libredwg-0.14-cquote-termination.patch` | `e47037ab526f7d5adadcdadda34153764aedcbdc76f970a65137cd4d7f32ad1b` |
| `cquote_minimal.c` | `d8041b1352a7fb214584693993882437c775b196c5c269b3dd40c71704891c38` |

The reproducer retains the unmodified and fixed functions derived from that
GPLv3 source. It fills an allocation with known bytes to demonstrate the old
append error deterministically, then checks eight empty/ASCII/LF/CR/UTF8
cases. Its unrelated MIF lookup is stubbed and intentionally not exercised;
real native roundtrip tests cover actual reader/writer behavior separately.
Preserve the accompanying upstream license when redistributing these sources.

Apply with `git apply --check` followed by `git apply` from a fresh extracted
source directory. Build only in new isolated native directories and retain the
previous binaries and evidence. Linux and Windows receipts record the exact
compiler, flags, input hashes, commands, output hashes and validation results.
No existing Python runtime or locked Python dependency needs recompilation.

Investigation evidence is recorded in
`tmp/reader-investigation/READER_MTEXT_FINDINGS_20260911.md`. Raw evidence is
local and contains real drawing text; it is not copied into distributable
packages. Acceptance requires five independent processes for each of the real
Darat and Manado inputs, stable complete MTEXT facts against `dwgread` JSON,
unchanged input hashes and the ATTRIB/SVG application bundle checks. The NUL
correction does not claim to fix separate encoding or DXF boundary issues.
