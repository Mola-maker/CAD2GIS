"""Keep test evidence in Actions summaries even when artifact storage is full."""
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def summarize(path):
    path = Path(path)
    if not path.is_file():
        return f"JUnit report unavailable: {path.name}. Check the test step log.\n"
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failed = [case for case in cases if case.find("failure") is not None or case.find("error") is not None]
    skipped = sum(case.find("skipped") is not None for case in cases)
    lines = [f"Tests: {len(cases)} · failures/errors: {len(failed)} · skipped: {skipped}"]
    lines.extend(f"- `{case.get('classname', '')}.{case.get('name', '')}`" for case in failed)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    result = summarize(sys.argv[1])
    print(result)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(result)
