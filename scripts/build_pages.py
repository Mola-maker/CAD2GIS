"""Build both public hosts from the owner-supplied official production website.

The local runtime demo has a separate builder: build_local_demo.py.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import re
import shutil
import tempfile
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "site" / "official"
SOURCE_MANIFEST = PROJECT_ROOT / "site" / "source-manifest.json"
WIKI_ROOT = PROJECT_ROOT / "docs" / "wiki"
NOTICE_ROOT = PROJECT_ROOT / "site" / "notices"
CANONICAL_BASE = "https://cad2gis.molamaker.cn/"
OWNER = "cad2gis-official-static-v1"


def _inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _markdown(text: str) -> str:
    """Small renderer for the deliberately limited author-owned Wiki format."""
    parts, paragraph, table, listing, code = [], [], [], [], None

    def flush():
        if paragraph:
            parts.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()
        if table:
            header, *rows = table
            parts.append(
                '<div class="wiki-table"><table><thead><tr>'
                + "".join("<th>" + _inline(c) + "</th>" for c in header)
                + "</tr></thead><tbody>"
            )
            parts.extend(
                "<tr>" + "".join("<td>" + _inline(c) + "</td>" for c in row) + "</tr>"
                for row in rows
            )
            parts.append("</tbody></table></div>")
            table.clear()
        if listing:
            parts.append(
                "<ol>"
                + "".join("<li>" + _inline(c) + "</li>" for c in listing)
                + "</ol>"
            )
            listing.clear()

    for line in text.splitlines():
        if line.startswith("```"):
            if code is None:
                flush()
                code = []
            else:
                parts.append(
                    '<pre class="codeblk"><code>'
                    + escape("\n".join(code))
                    + "</code></pre>"
                )
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        if not line.strip():
            flush()
            continue
        if line.startswith("|"):
            if re.fullmatch(r"[| :\-]+", line):
                continue
            table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        match = re.match(r"^(#{1,3}) (.+)", line)
        if match:
            flush()
            level = len(match[1])
            parts.append(f"<h{level}>" + _inline(match[2]) + f"</h{level}>")
            continue
        match = re.match(r"^\d+\. (.+)", line)
        if match:
            listing.append(match[1])
            continue
        paragraph.append(line)
    flush()
    return "\n".join(parts)


def _wiki_page(language: str, markdown: str) -> str:
    english = language == "en"
    counterpart = "index.html" if english else "en.html"
    title = "CAD2GIS · MCP & Plugin Guide" if english else "CAD2GIS · MCP 与插件指南"
    content = (
        _markdown(markdown)
        .replace('href="Home-EN"', 'href="en.html"')
        .replace('href="Home"', 'href="index.html"')
    )
    return f'''<!doctype html>
<html lang="{"en" if english else "zh-CN"}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><meta name="description" content="CAD2GIS 0.8.0: DWG/DXF to GIS, source-preserving CAD tools, MCP and plugins with validation evidence.">
<link rel="canonical" href="{CANONICAL_BASE}wiki/{"en.html" if english else "index.html"}">
<link rel="alternate" hreflang="en" href="{CANONICAL_BASE}wiki/en.html">
<link rel="alternate" hreflang="zh-CN" href="{CANONICAL_BASE}wiki/index.html">
<link rel="stylesheet" href="../docs/assets/fonts.css">
<link rel="stylesheet" href="../docs/assets/theme.css">
<link rel="stylesheet" href="../docs/assets/docs.css">
<link rel="stylesheet" href="wiki.css"></head><body>
<header id="docs-topbar"><a class="tb-back" href="../">CAD2GIS</a><span class="tb-brand">WIKI / 0.8.0</span><nav aria-label="Wiki navigation"><a href="{counterpart}" lang="{"zh-CN" if english else "en"}">{"中文" if english else "English"}</a><a href="../docs/index.html">{"Historical manual" if english else "历史手册"}</a><a href="https://github.com/Mola-maker/CAD2GIS/wiki">GitHub Wiki ↗</a></nav></header>
<main class="doc-main"><article class="doc-wrap">{content}</article></main>
<footer class="doc-wrap"><p>Apache-2.0 · Original code / 原创代码 · CONDITIONAL demo evidence</p></footer></body></html>'''


def build(
    output: Path, *, public_base_url: str = "https://mola-maker.github.io/CAD2GIS/"
) -> None:
    parsed = urlsplit(public_base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("public_base_url must be an absolute HTTP(S) site URL")
    base = public_base_url.rstrip("/") + "/"
    root, source, output = (
        PROJECT_ROOT.resolve(),
        SOURCE_ROOT.resolve(),
        output.absolute(),
    )
    if output.is_symlink():
        raise ValueError("Refusing symlink output")
    output = output.resolve()
    if (
        output == root
        or not output.is_relative_to(root)
        or source.is_relative_to(output)
        or output.is_relative_to(source)
    ):
        raise ValueError("Output must be a project child separate from source assets")
    marker = output / ".cad2gis-site-owner"
    if (
        output.exists()
        and any(output.iterdir())
        and (not marker.is_file() or marker.read_text() != OWNER)
    ):
        raise ValueError("Refusing to replace an unowned output directory")
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf8"))
    entries = manifest["files"]
    expected = set()
    for item in entries:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe source manifest path")
        path = source / relative
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("Unsafe source asset")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Source asset hash mismatch: {relative}")
        expected.add(relative.as_posix())
    actual = {
        p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file()
    }
    if actual != expected:
        raise ValueError("Unmanifested or missing official site assets")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".official-build-", dir=output.parent))
    try:
        shutil.copytree(source, stage, dirs_exist_ok=True)
        shutil.copytree(NOTICE_ROOT, stage / "notices")
        for name in ("index.html", "cad2gis-report.html"):
            path = stage / name
            page = path.read_text(encoding="utf8")
            banner = '<aside class="cad2gis-current"><a href="wiki/index.html">MCP / 插件 0.8.0 · 中文指南</a> · <a href="wiki/en.html">English</a> · <a href="https://github.com/Mola-maker/CAD2GIS/releases/tag/v0.8.0">下载 / Download</a></aside>'
            style = '<style>.cad2gis-current{position:relative;z-index:100;padding:12px 20px;border-bottom:1px solid #d7d7ce;background:#fffff8;color:#142a33;font:14px "et-book","Songti SC",serif;line-height:1.6}.cad2gis-current a{color:inherit;text-decoration:underline}</style>'
            page = page.replace("</head>", style + "</head>", 1)
            page = re.sub(r"(<body\b[^>]*>)", r"\1" + banner, page, count=1)
            path.write_text(page, encoding="utf8")
        wiki = stage / "wiki"
        wiki.mkdir()
        for lang, filename, md in [
            ("zh", "index.html", "Home.md"),
            ("en", "en.html", "Home-EN.md"),
        ]:
            (wiki / filename).write_text(
                _wiki_page(lang, (WIKI_ROOT / md).read_text(encoding="utf8")),
                encoding="utf8",
            )
        (wiki / "wiki.css").write_text(
            """/* Extend the official documentation design; fonts and palette are inherited. */
.doc-wrap h1{font-size:clamp(30px,5vw,48px);line-height:1.2}.doc-wrap h2{margin-top:2.4em}.doc-wrap p,.doc-wrap li{line-height:1.85}.wiki-table{overflow-x:auto;margin:24px 0}.wiki-table table{width:100%;border-collapse:collapse;min-width:560px}.wiki-table th,.wiki-table td{padding:12px;text-align:left;border-bottom:1px solid var(--line,#ddd);vertical-align:top}.wiki-table th{font-family:var(--mono);font-size:12px}.codeblk{overflow-x:auto;white-space:pre;font-size:13px}.codeblk code{border:0;background:transparent}#docs-topbar{box-sizing:border-box}#docs-topbar nav{min-width:0}footer{padding-bottom:40px}
""",
            encoding="utf8",
        )
        manual = stage / "docs/index.html"
        page = manual.read_text(encoding="utf8").replace(
            '<main class="doc-main">',
            '<main class="doc-main"><div class="doc-wrap"><p class="doc-kicker">CURRENT AGENT / 0.8.0</p><p>以下手册保留历史快照。当前 MCP 与插件能力、Apache-2.0 许可和安装说明：<a href="../wiki/index.html">中文 Wiki</a> · <a href="../wiki/en.html">English Wiki</a>。</p></div>',
            1,
        )
        manual.write_text(page, encoding="utf8")
        pages = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*.html"))
        (stage / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(
                "<url><loc>"
                + CANONICAL_BASE
                + (
                    "wiki/"
                    if n == "wiki/index.html"
                    else ""
                    if n == "index.html"
                    else n
                )
                + "</loc></url>"
                for n in pages
            )
            + "</urlset>\n",
            encoding="utf8",
        )
        (stage / "robots.txt").write_text(
            "User-agent: *\nAllow: /\nSitemap: " + CANONICAL_BASE + "sitemap.xml\n",
            encoding="utf8",
        )
        (stage / ".nojekyll").write_text("", encoding="utf8")
        (stage / ".cad2gis-site-owner").write_text(OWNER, encoding="utf8")
        (stage / "build-manifest.json").write_text(
            json.dumps(
                {
                    "mode": "official-production-archive",
                    "version": "0.8.0",
                    "public_base_url": base,
                    "canonical_base_url": CANONICAL_BASE,
                    "source_archive_sha256": manifest["archive_sha256"],
                    "source_prefix": manifest["prefix"],
                    "source_files": len(entries),
                    "pages": pages,
                    "wiki_languages": ["zh-CN", "en"],
                    "contains_source_cad_binaries": False,
                    "historical_demo_status": "CONDITIONAL",
                    "absolute_accuracy_verified": False,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf8",
        )
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "_site")
    parser.add_argument(
        "--public-base-url", default="https://mola-maker.github.io/CAD2GIS/"
    )
    args = parser.parse_args()
    build(args.output, public_base_url=args.public_base_url)
    print(f"Built official-standard site: {args.output.resolve()}")


if __name__ == "__main__":
    main()
