"""Package current-worktree source and the agent plugin without private runtime data.

Only the Python standard library and Git are required. ``build --dry-run`` is
read only unless an explicit new report path is supplied. ``verify`` needs no Git.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "cad2gis.source_delivery.v1"
MANIFEST = "DELIVERY-MANIFEST.json"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
PLUGIN = "plugins/cad2gis-agent/"
DEFAULT_FILE_LIMIT = 25 * 1024 * 1024
DEFAULT_TOTAL_LIMIT = 250 * 1024 * 1024
NEW_ROOTS = {"src", "tests", "docs", "tools", "packaging", "apps", "plugins", ".github", ".agents", ".claude-plugin"}
NEW_ROOT_FILES = {"Dockerfile", "pyproject.toml", "package.json", "package-lock.json", "pyrightconfig.json", ".gitignore", ".gitattributes", ".dockerignore"}
EXCLUDED_PARTS = {".git", ".worktrees", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cad2gis-app", ".claude", ".codex", ".omc", ".idea", ".vscode", "tmp", "temp", "output", "dist", "build", "raw", "validation", "pages-delivery", "_site", "errorreports"}
PRIVATE_SUFFIXES = {".keystore", ".jks", ".p12", ".pfx", ".pem", ".key"}
GENERATED_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak", ".whl", ".apk", ".aab", ".msi", ".exe", ".dll", ".so", ".dylib", ".zip", ".7z", ".tar", ".gz"}
RAW_SUFFIXES = {".dwg", ".dxf", ".dgn"}


class DeliveryError(ValueError):
    pass


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise DeliveryError("Git inspection failed: " + result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def relative_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(p in {"..", "."} for p in path.parts) or "\\" in name or ":" in name or "\x00" in name:
        raise DeliveryError(f"Unsafe archive path: {name!r}")
    return path


def exclusion(name: str, tracked: bool) -> str | None:
    path = relative_name(name)
    parts = tuple(p.lower() for p in path.parts)
    basename, suffix = parts[-1], path.suffix.lower()
    if any(p in EXCLUDED_PARTS or p.startswith((".venv", ".pytest-")) or p.endswith(".egg-info") for p in parts):
        return "runtime/build/private directory"
    if basename == ".env" or (basename.startswith(".env.") and not basename.endswith((".example", ".sample", ".template"))):
        return "machine environment secrets"
    if suffix in PRIVATE_SUFFIXES or basename in {"local.properties", "key.properties", "id_rsa", "id_ed25519"} or re.match(r"(?:credentials?|secrets?|tokens?)(?:[._-]|$)", basename):
        return "credentials or signing material"
    if name == ".mcp.json":
        return "machine MCP configuration"
    if suffix in RAW_SUFFIXES:
        return "original CAD input excluded from source delivery"
    if suffix in GENERATED_SUFFIXES or basename.endswith(("~", ":zone.identifier")):
        return "compiled/generated/installable artifact"
    if parts[0] in {"baseline", "baselines"}:
        if not tracked:
            return "baseline additions require explicit Git review"
        if len(parts) >= 4 and parts[2] == "config":
            return None
        if len(parts) >= 4 and parts[2] == "delivery" and basename == "view-with-source-svg.qgz":
            return None
        return "baseline derived output"
    if parts[0] == "plugins" and not name.startswith(PLUGIN):
        return "unsupported/local plugin"
    if not tracked and parts[0] not in NEW_ROOTS and not (len(parts) == 1 and (name in NEW_ROOT_FILES or re.match(r"(?:README|LICENSE|NOTICE)(?:[.-]|$)", name))):
        return "untracked file outside reviewed source roots"
    return None


def state(root: Path) -> dict:
    entries, tracked = [], {}
    for row in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not row:
            continue
        fields, raw_name = row.split(b"\t", 1)
        mode, _, stage = fields.decode("ascii").split()
        name = raw_name.decode("utf-8")
        if stage != "0":
            raise DeliveryError(f"Resolve the Git conflict before delivery: {name}")
        tracked[name] = mode
    untracked = [p.decode("utf-8") for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p]
    changes = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0")
    index = 0
    while index < len(changes) and changes[index]:
        row = changes[index].decode("utf-8")
        item = {"status": row[:2], "path": row[3:]}
        if "R" in row[:2] or "C" in row[:2]:
            index += 1
            item["original_path"] = changes[index].decode("utf-8")
        entries.append(item)
        index += 1
    return {"base_head": git(root, "rev-parse", "HEAD").decode().strip(),
            "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD").decode().strip(),
            "tracked": tracked, "untracked": sorted(untracked),
            "workspace_dirty": bool(entries), "changes": sorted(entries, key=lambda item: (item["path"], item["status"]))}


def read_file(root: Path, name: str, limit: int) -> bytes:
    relative_name(name)
    path = root / name
    if not path.is_file() or path.is_symlink() or path.resolve() != root.joinpath(*PurePosixPath(name).parts):
        raise DeliveryError(f"Not an ordinary source file, missing, or crosses a link: {name}")
    before = path.stat()
    if before.st_size > limit:
        raise DeliveryError(f"File exceeds {limit} byte limit (not silently omitted): {name} ({before.st_size})")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    after = path.stat()
    if len(data) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
        raise DeliveryError(f"Source changed while being read: {name}")
    if re.search(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{64,}", data):
        raise DeliveryError(f"Private key content found; remove it before delivery: {name}")
    if re.search(rb"\bsk-(?:proj-|live-|svcacct-)?[A-Za-z0-9_-]{32,}", data):
        raise DeliveryError(f"Possible literal API credential found; review before delivery: {name}")
    return data


def tree_hash(files: list[dict]) -> str:
    return digest(encoded([{key: item[key] for key in ("path", "sha256", "size", "mode")} for item in files]))


def inventory(root: Path, *, max_file_bytes: int, max_total_bytes: int) -> dict:
    snapshot = state(root)
    files, excluded, folded = [], [], set()
    total = 0
    for name in sorted(set(snapshot["tracked"]) | set(snapshot["untracked"])):
        tracked = name in snapshot["tracked"]
        reason = exclusion(name, tracked)
        if not (root / name).exists() and any(change["path"] == name and "D" in change["status"] for change in snapshot["changes"]):
            reason = "deleted from current worktree"
        if reason:
            excluded.append({"path": name, "tracked": tracked, "reason": reason})
            continue
        if name.casefold() in folded:
            raise DeliveryError(f"Case-colliding archive path: {name}")
        folded.add(name.casefold())
        if name == MANIFEST:
            raise DeliveryError("Reserved delivery manifest already exists in source")
        mode = snapshot["tracked"].get(name, "100755" if name.endswith(".sh") else "100644")
        if mode not in {"100644", "100755"}:
            raise DeliveryError(f"Symlinks and submodules require explicit materialization: {name}")
        data = read_file(root, name, max_file_bytes)
        total += len(data)
        if total > max_total_bytes:
            raise DeliveryError(f"Source total exceeds {max_total_bytes} byte limit at {name}; nothing was silently omitted")
        files.append({"path": name, "size": len(data), "sha256": digest(data), "mode": mode, "tracked": tracked})
    if not any(item["path"] == "pyproject.toml" for item in files):
        raise DeliveryError("Source delivery requires pyproject.toml")
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", version):
        raise DeliveryError("Unsafe project version")
    for filename in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        plugin_version = json.loads((root / PLUGIN / filename).read_text(encoding="utf-8"))["version"]
        if plugin_version != version:
            raise DeliveryError(f"Plugin version differs from source: {filename}={plugin_version}, source={version}")
    common = {"schema_version": SCHEMA, "version": version, "base_head": snapshot["base_head"], "branch": snapshot["branch"],
              "workspace_dirty": snapshot["workspace_dirty"], "changes": snapshot["changes"], "zip_timestamp": list(ZIP_TIME),
              "selection": "Git tracked current bytes plus non-ignored additions in reviewed source roots; explicit exclusions recorded",
              "excluded": excluded}
    source = {**common, "kind": "source", "files": files, "tree_sha256": tree_hash(files), "total_bytes": total}
    plugin_files = [{**item, "path": item["path"][len(PLUGIN):]} for item in files if item["path"].startswith(PLUGIN)]
    if not plugin_files or any(item["path"] == MANIFEST for item in plugin_files):
        raise DeliveryError("Plugin source is missing or contains a reserved manifest")
    plugin = {**common, "kind": "agent-plugin", "files": plugin_files, "tree_sha256": tree_hash(plugin_files), "total_bytes": sum(item["size"] for item in plugin_files)}
    return {"source": source, "plugin": plugin, "snapshot": snapshot}


def write_zip(root: Path, target: Path, manifest: dict, *, max_file_bytes: int) -> None:
    prefix = f"CAD2GIS-{manifest['version']}/" if manifest["kind"] == "source" else "cad2gis-agent/"
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in manifest["files"]:
            source_name = item["path"] if manifest["kind"] == "source" else PLUGIN + item["path"]
            data = read_file(root, source_name, max_file_bytes)
            if digest(data) != item["sha256"] or len(data) != item["size"]:
                raise DeliveryError(f"Source changed after inventory: {source_name}")
            entry = zipfile.ZipInfo(prefix + item["path"], date_time=ZIP_TIME)
            entry.create_system, entry.external_attr = 3, (stat.S_IFREG | int(item["mode"][-3:], 8)) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data, compresslevel=9)
        entry = zipfile.ZipInfo(prefix + MANIFEST, date_time=ZIP_TIME)
        entry.create_system, entry.external_attr = 3, (stat.S_IFREG | 0o644) << 16
        entry.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(entry, encoded(manifest), compresslevel=9)


def verify(path: Path, *, max_file_bytes: int = DEFAULT_FILE_LIMIT, max_total_bytes: int = DEFAULT_TOTAL_LIMIT) -> dict:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names}):
            raise DeliveryError("Duplicate archive member")
        for name in names:
            relative_name(name)
        manifests = [name for name in names if len(PurePosixPath(name).parts) == 2 and name.endswith("/" + MANIFEST)]
        if len(manifests) != 1 or archive.getinfo(manifests[0]).file_size > 8 * 1024 * 1024:
            raise DeliveryError("Missing, ambiguous, or oversized delivery manifest")
        manifest = json.loads(archive.read(manifests[0]))
        if manifest.get("schema_version") != SCHEMA or manifest.get("kind") not in {"source", "agent-plugin"}:
            raise DeliveryError("Unsupported delivery manifest")
        prefix = manifests[0].removesuffix(MANIFEST)
        canonical_prefix = f"CAD2GIS-{manifest['version']}/" if manifest["kind"] == "source" else "cad2gis-agent/"
        if prefix != canonical_prefix:
            raise DeliveryError("Noncanonical archive root")
        manifest_info = archive.getinfo(manifests[0])
        if manifest_info.date_time != ZIP_TIME or manifest_info.create_system != 3 or (manifest_info.external_attr >> 16) != (stat.S_IFREG | 0o644):
            raise DeliveryError("Noncanonical manifest metadata")
        files = manifest["files"]
        expected = {prefix + item["path"] for item in files} | {manifests[0]}
        if set(names) != expected or len(files) != len(expected) - 1 or tree_hash(files) != manifest["tree_sha256"]:
            raise DeliveryError("Archive member list or tree hash mismatch")
        total = 0
        for item in files:
            relative_name(item["path"])
            if item["mode"] not in {"100644", "100755"}:
                raise DeliveryError("Unsupported archive member mode")
            check_name = item["path"] if manifest["kind"] == "source" else PLUGIN + item["path"]
            if exclusion(check_name, item["tracked"]):
                raise DeliveryError(f"Forbidden delivery member: {check_name}")
            info = archive.getinfo(prefix + item["path"])
            total += info.file_size
            if info.file_size > max_file_bytes or total > max_total_bytes:
                raise DeliveryError("Archive exceeds verification size budget")
            if info.date_time != ZIP_TIME or info.create_system != 3 or (info.external_attr >> 16) != (stat.S_IFREG | int(item["mode"][-3:], 8)):
                raise DeliveryError(f"Noncanonical ZIP metadata: {item['path']}")
            data = archive.read(info)
            if len(data) != item["size"] or digest(data) != item["sha256"]:
                raise DeliveryError(f"Archive content digest mismatch: {item['path']}")
        if total != manifest["total_bytes"]:
            raise DeliveryError("Archive total size mismatch")
    return {"path": str(path.resolve()), "kind": manifest["kind"], "version": manifest["version"], "file_count": len(files),
            "total_bytes": total, "tree_sha256": manifest["tree_sha256"], "sha256": digest(path.read_bytes())}


def publish_new(staged: Path, destination: Path) -> None:
    """Exclusive create; never replace an existing delivery or delete an old one."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("xb") as output, staged.open("rb") as source:
            created = True
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                output.write(chunk)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)  # Only this invocation's incomplete new file.
        raise


def build(root: Path, source_dir: Path, plugin_dir: Path, *, max_file_bytes: int = DEFAULT_FILE_LIMIT, max_total_bytes: int = DEFAULT_TOTAL_LIMIT, repeat: bool = True) -> dict:
    root = root.resolve()
    plan = inventory(root, max_file_bytes=max_file_bytes, max_total_bytes=max_total_bytes)
    version = plan["source"]["version"]
    targets = {"source": source_dir.resolve() / f"CAD2GIS-{version}-source.zip", "plugin": plugin_dir.resolve() / f"CAD2GIS-{version}-agent-plugin.zip"}
    sidecars = [p.with_suffix(".manifest.json") for p in targets.values()] + [p.with_suffix(".sha256") for p in targets.values()]
    if any(path.exists() for path in [*targets.values(), *sidecars]):
        raise DeliveryError("Delivery already exists; choose new output directories. No files were overwritten.")
    results = []
    with tempfile.TemporaryDirectory(prefix="cad2gis-source-delivery-") as temporary:
        staged = Path(temporary)
        for kind, target in targets.items():
            archive = staged / target.name
            write_zip(root, archive, plan[kind], max_file_bytes=max_file_bytes)
            result = verify(archive, max_file_bytes=max_file_bytes, max_total_bytes=max_total_bytes)
            if repeat:
                second = staged / ("repeat-" + target.name)
                write_zip(root, second, plan[kind], max_file_bytes=max_file_bytes)
                if digest(second.read_bytes()) != result["sha256"]:
                    raise DeliveryError("Repeated archive build differs")
            result.update(path=str(target), repeated_hash_verified=repeat)
            results.append(result)
        if state(root) != plan["snapshot"]:
            raise DeliveryError("Git/source inventory changed during packaging; freeze changes and retry")
        for item in plan["source"]["files"]:
            if digest(read_file(root, item["path"], max_file_bytes)) != item["sha256"]:
                raise DeliveryError(f"Source changed before publication: {item['path']}")
        # Every archive has passed verification before any delivery is published.
        for kind, target in targets.items():
            publish_new(staged / target.name, target)
            manifest_file = staged / (target.stem + ".manifest.json")
            manifest_file.write_bytes(encoded(plan[kind]))
            publish_new(manifest_file, target.with_suffix(".manifest.json"))
            checksum = staged / (target.stem + ".sha256")
            checksum.write_text(f"{digest(target.read_bytes())}  {target.name}\n", encoding="ascii")
            publish_new(checksum, target.with_suffix(".sha256"))
    return {"status": "verified", "base_head": plan["source"]["base_head"], "workspace_dirty": plan["source"]["workspace_dirty"], "archives": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    package = sub.add_parser("build")
    package.add_argument("--repo", type=Path, default=ROOT)
    package.add_argument("--source-dir", type=Path)
    package.add_argument("--plugin-dir", type=Path)
    package.add_argument("--dry-run", action="store_true")
    package.add_argument("--report", type=Path, help="Exclusive-create a dry-run inventory JSON outside reviewed source roots")
    check = sub.add_parser("verify")
    check.add_argument("archive", type=Path)
    for command in (package, check):
        command.add_argument("--max-file-bytes", type=int, default=DEFAULT_FILE_LIMIT)
        command.add_argument("--max-total-bytes", type=int, default=DEFAULT_TOTAL_LIMIT)
    args = parser.parse_args(argv)
    try:
        if args.max_file_bytes <= 0 or args.max_total_bytes <= 0:
            raise DeliveryError("Size limits must be positive")
        limits = {"max_file_bytes": args.max_file_bytes, "max_total_bytes": args.max_total_bytes}
        if args.command == "verify":
            result = verify(args.archive, **limits)
        elif args.dry_run:
            plan = inventory(args.repo.resolve(), **limits)
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                with args.report.open("xb") as stream:
                    stream.write(encoded({k: plan[k] for k in ("source", "plugin")}))
            result = {"status": "dry_run", "base_head": plan["source"]["base_head"], "workspace_dirty": plan["source"]["workspace_dirty"],
                      "source_files": len(plan["source"]["files"]), "plugin_files": len(plan["plugin"]["files"]), "source_bytes": plan["source"]["total_bytes"],
                      "excluded_counts": dict(Counter(item["reason"] for item in plan["source"]["excluded"])), "report": str(args.report) if args.report else None}
        else:
            if args.report:
                raise DeliveryError("--report is for --dry-run; build writes manifests beside both archives")
            result = build(args.repo, args.source_dir or args.repo / "dist/source", args.plugin_dir or args.repo / "dist/plugin", **limits)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (DeliveryError, OSError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
