"""Reader-stage RSS probe (MEMORY_OPTIMIZATION_SPEC 6.1B).

Run with the same interpreter/working directory as `cad2gis convert`:
    .venv/bin/python docs/memory_validation/probe_reader.py
"""

from __future__ import annotations

import gc
import resource
import time
from pathlib import Path


def rss_mb() -> int:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) // 1024
    return -1


def main() -> None:
    print(f"before reader RSS MB: {rss_mb()}", flush=True)
    import cad2gis.reader.libredwg as reader

    source = "raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg"
    started = time.time()
    records = reader.extract_dwg_records(source)
    print(f"after reader RSS MB: {rss_mb()}", flush=True)
    print(f"records: {len(records)}", flush=True)
    print(f"elapsed_s: {time.time() - started:.1f}", flush=True)
    print(
        "after reader ru_maxrss MB: "
        f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}",
        flush=True,
    )
    del records
    gc.collect()
    print(f"after del records RSS MB: {rss_mb()}", flush=True)


if __name__ == "__main__":
    main()
