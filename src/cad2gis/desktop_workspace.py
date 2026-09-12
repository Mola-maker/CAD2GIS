"""Bind chat tools to real selected files and fresh conversion destinations."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import secrets

from .pipeline import has_project_configuration, resolve_project_configuration


PROJECT_FILES = (
    "config/source_profile.json", "config/mapping_registry.json",
    "review/source_inventory.json", "review/unsupported_inventory.json",
    "review/cad_scene_graph.json", "ai_onboarding_proposal.json",
)
RUN_FILES = ("run_manifest.json", "delivery.gpkg", "evidence.gpkg", "source_manifest.json")


class SelectedWorkspace:
    def __init__(self, context, roots, admitted):
        self.context, self.roots, self.admitted = context, roots, admitted

    def files(self, path: str = "", page: int = 1, page_size: int = 50):
        if not 1 <= page <= 10000 or not 1 <= page_size <= 100:
            raise ValueError("目录分页无效：page 从 1 开始，page_size 不超过 100")
        if not path:
            project = self.context.get("project")
            source = self.context.get("source")
            path = project if project and self.admitted(project, False).is_dir() else (
                str(Path(source).parent) if source else str(self.roots[0]))
        directory = self.admitted(path, False)
        if not directory.exists():
            return {"path": str(directory), "exists": False, "entries": [], "has_more": False,
                    "next_action": "此目录尚未生成。转换当前图纸请调用 convert_selected_drawing，不要猜测其中的文件。"}
        if not directory.is_dir():
            raise ValueError("请选择目录；文本文件用 read_project_text，数据库文件用 query_project_database")
        entries = []
        for child in directory.iterdir():
            try:
                resolved = self.admitted(str(child))
            except (ValueError, OSError):
                continue
            entries.append({"name": child.name, "path": str(resolved),
                            "kind": "directory" if resolved.is_dir() else "file"})
        entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
        start = (page - 1) * page_size
        return {"path": str(directory), "exists": True, "entries": entries[start:start + page_size],
                "page": page, "total": len(entries), "has_more": start + page_size < len(entries)}

    def snapshot(self):
        paths, known = {}, {}
        for key in ("source", "project", "run"):
            value = self.context.get(key)
            if not value:
                paths[key] = {"path": None, "exists": False, "kind": "unselected"}
                continue
            path = self.admitted(value, False)
            paths[key] = {"path": str(path), "exists": path.exists(),
                          "kind": "directory" if path.is_dir() else "file" if path.is_file() else "planned"}
            found = []
            for relative in PROJECT_FILES if key == "project" else RUN_FILES if key == "run" else ():
                try:
                    candidate = self.admitted(str(path / relative), False)
                    if candidate.is_file():
                        found.append(str(candidate))
                except (ValueError, OSError):
                    continue
            if key == "project" and path.is_dir():
                try:
                    configuration = resolve_project_configuration(project_dir=path, discover_gcp=False)
                    for candidate in (configuration.source_profile, configuration.mapping_registry):
                        admitted = str(self.admitted(str(candidate)))
                        if admitted not in found:
                            found.append(admitted)
                except (ValueError, OSError):
                    pass
            known[key] = found
        return {"paths": paths, "existing_files": known,
                "next_action": "convert_selected_drawing" if paths["source"]["exists"] else "select_source",
                "path_rules": "project 是配置目录，run 是输出目录；planned 表示尚未生成，目录不能作为文本或数据库文件读取。"}

    def conversion_arguments(self):
        if not self.context.get("source"):
            raise ValueError("请先导入或选择一张 DWG 图纸，再发送转换指令")
        source = self.admitted(self.context.get("source", ""))
        if not source.is_file() or source.suffix.lower() != ".dwg":
            raise ValueError("请先导入或选择一张 DWG 图纸，再发送转换指令")
        project = self.admitted(self.context.get("project") or str(source.parent / "project"), False)
        if project.exists() and not project.is_dir():
            raise ValueError("项目配置路径必须是目录")
        run = self.admitted(self.context.get("run") or str(source.parent / "run"), False)
        if run.exists() or run == project:
            run = self.admitted(str(run.with_name(
                f"{run.name}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}")), False)
        return {"source": str(source), "project_dir": str(project), "run_dir": str(run),
                "matching": "osm", "svg_mode": "candidate",
                **({"osm_place": self.context["osm_place"]} if self.context.get("osm_place") else {})}


class SelectedConversion:
    """One bound conversion per turn; retries keep the same mutation identity."""

    def __init__(self, workspace, call):
        self.workspace, self.call = workspace, call
        self.arguments = None
        self.result = None
        self.completed = False
        self.tool = None

    async def execute(self):
        if self.completed:
            return self.result
        if self.arguments is None:
            self.arguments = self.workspace.conversion_arguments()
        arguments = self.arguments
        project = Path(arguments["project_dir"])
        if self.tool is None:
            tool = "auto_onboard_and_convert"
            if has_project_configuration(project):
                configuration = resolve_project_configuration(project_dir=project, discover_gcp=False)
                paths = [self.workspace.admitted(str(path)) for path in (
                    configuration.source_profile, configuration.mapping_registry)]
                if all(path.parent == project / "config" for path in paths):
                    response = await self.call("validate_project", {"project_dir": str(project)})
                    if json.loads(response["result"]).get("conversion_allowed") is True:
                        tool = "run_conversion"
                else:
                    # Alternate configuration layouts are supported by the
                    # canonical converter; its admission gates validate them.
                    # The managed-onboarding validator expects config/.
                    tool = "run_conversion"
            self.tool = tool
        response = await self.call(self.tool, arguments)
        # Cache a completed RPC before checking its artifacts. An incomplete
        # result must be inspected, never replayed as another conversion.
        self.completed = True
        self.result = response.get("conversion") or json.loads(response["result"])
        return self.result

    def verified_result(self):
        if not isinstance(self.result, dict) or not self.arguments:
            raise ValueError("转换尚未返回结果")
        run = self.workspace.admitted(self.arguments["run_dir"], False)
        if not self.result.get("run_manifest") or not self.result.get("delivery"):
            raise ValueError("转换未返回完整产物路径，请检查结果目录；不要重复提交转换")
        manifest = self.workspace.admitted(self.result["run_manifest"], False)
        delivery = self.workspace.admitted(self.result["delivery"], False)
        if manifest != run / "run_manifest.json" or not manifest.is_file() or not delivery.is_file() or run not in delivery.parents:
            raise ValueError("转换返回后未找到完整产物，请检查结果目录；不要重复提交转换")
        return {**self.result, "run_dir": str(run), "project_dir": self.arguments["project_dir"],
                "drawing": self.arguments["source"]}
