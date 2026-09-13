# LibreDWG-derived source notices

The LibreDWG-derived contents of this directory, including the source excerpts
in `cquote_minimal.c` and `libredwg-0.14-cquote-termination.patch`, retain the
upstream GNU General Public License, version 3 or later. The complete GPL
version 3 text is reproduced without changes in [COPYING](COPYING).

This notice and COPYING apply to the upstream-derived material described here.
They do not relicense original CAD2GIS source. Conversely, the CAD2GIS root
PolyForm license does not replace the license of this LibreDWG-derived material.

## Source and retained upstream notice

The reproducer extracts the original and corrected `cquote()` function from
LibreDWG 0.14 `src/out_dxf.c`; the patch changes that same function. The surrounding
stubs and deterministic test cases are described in [README.md](README.md).

Upstream source archive:
<https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz>

Archive SHA256: `cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351`.

The following copyright and license header is retained verbatim from
`libredwg-0.14/src/out_dxf.c`:

```c
/*****************************************************************************/
/*  LibreDWG - free implementation of the DWG file format                    */
/*                                                                           */
/*  Copyright (C) 2018-2026 Free Software Foundation, Inc.                   */
/*                                                                           */
/*  This library is free software, licensed under the terms of the GNU       */
/*  General Public License as published by the Free Software Foundation,     */
/*  either version 3 of the License, or (at your option) any later version.  */
/*  You should have received a copy of the GNU General Public License        */
/*  along with this program.  If not, see <http://www.gnu.org/licenses/>.    */
/*****************************************************************************/
```

Upstream also credits Reini Urban as the writer of `out_dxf.c`. Preserve this
notice and COPYING alongside these source excerpts and patches when distributing
them, including in standalone source archives. Native runtime bundles also retain
the original upstream source archive, patch, reproducer and build instructions.
