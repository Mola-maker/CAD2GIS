from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_terminal_replay_is_progressive_and_motion_safe() -> None:
    motion = (ROOT / "src" / "cad2gis" / "webdemo" / "hero-motion.js").read_text(
        encoding="utf-8"
    )
    styles = (ROOT / "src" / "cad2gis" / "webdemo" / "styles.css").read_text(
        encoding="utf-8"
    )

    assert 'querySelector("[data-process-terminal]")' in motion
    assert 'terminal.classList.add("is-enhanced")' in motion
    assert 'new IntersectionObserver' in motion
    assert 'document.addEventListener("visibilitychange"' in motion
    assert "if (reduceMotion)" in motion
    assert '--terminal-progress", "100%"' in motion
    assert ".process-terminal-shell.is-enhanced" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles

