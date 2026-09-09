"""Small adb helper for repeatable tests against an explicitly named emulator only."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET


class Emulator:
    def __init__(self, adb: Path, serial: str, evidence: Path):
        if not re.fullmatch(r"emulator-\d+", serial):
            raise ValueError("This test helper never operates on physical Android devices")
        self.adb = adb
        self.serial = serial
        self.evidence = evidence
        evidence.mkdir(parents=True, exist_ok=True)

    def command(self, *arguments: str) -> str:
        process = subprocess.run([str(self.adb), "-s", self.serial, *arguments],
                                 capture_output=True, text=True, encoding="utf-8", check=False, timeout=30)
        dump_completed = arguments[:3] == ("shell", "uiautomator", "dump") and "dumped to:" in process.stdout
        if process.returncode and not dump_completed:
            raise RuntimeError(process.stdout + process.stderr)
        return process.stdout

    def dump(self, name: str = "current") -> ET.Element:
        self.command("shell", "uiautomator", "dump", "/sdcard/cad2gis-smoke.xml")
        path = self.evidence / f"{name}.xml"
        self.command("pull", "/sdcard/cad2gis-smoke.xml", str(path))
        return ET.parse(path).getroot()

    def find(self, *, text: str | None = None, kind: str | None = None, timeout: int = 15,
             scroll: bool = False) -> ET.Element:
        deadline = time.monotonic() + timeout
        while True:
            document = self.dump()
            for node in document.iter("node"):
                bounds = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
                visible = len(bounds) == 4 and bounds[2] > bounds[0] and bounds[3] > bounds[1]
                if visible and (text is None or node.get("text", "").strip() == text) and (kind is None or node.get("class") == kind):
                    return node
            if time.monotonic() >= deadline:
                labels = [node.get("text") for node in document.iter("node") if node.get("text")]
                raise AssertionError(f"UI item not found: text={text!r}, kind={kind!r}. Visible: {labels}")
            if scroll:
                self.command("shell", "input", "swipe", "360", "1050", "360", "350", "350")
            time.sleep(0.5)

    def tap(self, node: ET.Element) -> None:
        left, top, right, bottom = map(int, re.findall(r"\d+", node.attrib["bounds"]))
        self.command("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def text(self, node: ET.Element, value: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9:/._-]+", value):
            raise ValueError("Test input must not contain shell metacharacters")
        self.tap(node)
        previous = node.get("text", "")
        self.command("shell", "input", "keyevent", "123")
        if previous:
            self.command("shell", "input", "keyevent", *(["67"] * len(previous)))
        self.command("shell", "input", "text", value)

    def screenshot(self, name: str) -> None:
        self.command("shell", "screencap", "-p", "/sdcard/cad2gis-smoke.png")
        self.command("pull", "/sdcard/cad2gis-smoke.png", str(self.evidence / f"{name}.png"))

    def connect(self, url: str) -> None:
        document = self.dump()
        if not any(node.get("text") == "连接 CAD2GIS" for node in document.iter("node")):
            self.tap(self.find(text="服务器"))
        self.text(self.find(kind="android.widget.EditText"), url)
        self.tap(self.find(text="连接"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--connect")
    parser.add_argument("--fixture-login", action="store_true")
    args = parser.parse_args()
    emulator = Emulator(args.adb, args.serial, args.evidence)
    if args.connect:
        emulator.connect(args.connect)
    if args.fixture_login:
        emulator.find(text="CAD2GIS test login")
        emulator.text(emulator.find(kind="android.widget.EditText"), "fixture-password")
        emulator.tap(emulator.find(text="Sign in"))
        emulator.find(text="CAD2GIS connected")
        emulator.screenshot("authenticated")
        print("Android WebView HTTPS login verified", flush=True)
    document = emulator.dump("result")
    for node in document.iter("node"):
        if node.get("text"):
            print(node.get("text"))


if __name__ == "__main__":
    main()
