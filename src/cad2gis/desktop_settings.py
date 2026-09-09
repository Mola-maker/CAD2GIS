"""Persistent desktop AI settings, shared by the harness and canonical CLI jobs."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from .cad2gis_v3.curation_providers.config import ProviderConfig, _validate_base_url
from .cad2gis_v3.curation_providers.base import ProviderError


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    """Windows DPAPI binds saved credentials to the current OS user."""
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        success = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        success = function(ctypes.byref(source), "CAD2GIS AI", None, None, None, 1, ctypes.byref(output))
    if not success:
        raise ValueError("无法解密此用户的 AI 配置，请重新输入密钥")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class AISettings:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.file = self.directory / "ai-config.json"
        self.lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if not self.file.exists():
            return {"active": os.environ.get("CAD2GIS_LLM_PROVIDER", "deepseek").replace("_", "-"), "profiles": {}}
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            for profile in raw.get("profiles", {}).values():
                if "key_dpapi" in profile:
                    encrypted = profile.pop("key_dpapi")
                    if os.name != "nt":
                        raise ValueError("Windows 加密密钥不可在另一系统中读取，请重新配置")
                    profile["api_key"] = _crypt(base64.b64decode(encrypted), decrypt=True).decode("utf-8") if encrypted else ""
            return raw
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("AI 配置文件不可读，请检查当前用户的配置目录") from exc

    def profile(self, provider: str | None = None) -> dict[str, Any]:
        with self.lock:
            saved = self._load()
            provider = provider or saved.get("active", "deepseek")
            if not isinstance(provider, str) or provider.replace("_", "-") not in {"deepseek", "new-api"}:
                raise ValueError("请选择 DeepSeek 或 OpenAI 兼容服务")
            provider = provider.replace("_", "-")
            prefix = "DEEPSEEK" if provider == "deepseek" else "NEW_API"
            result = {
                "provider": provider,
                "base_url": os.environ.get(prefix + "_BASE_URL", "https://api.deepseek.com" if provider == "deepseek" else "https://api.openai.com/v1"),
                "model": os.environ.get(prefix + "_MODEL", "deepseek-v4-flash" if provider == "deepseek" else ""),
                "api_key": os.environ.get(prefix + "_API_KEY", ""),
                "capability": os.environ.get(prefix + "_CAPABILITY", "json_object"),
                "timeout_s": 90, "max_tokens": 4096, "max_requests": 32,
                "token_field": "max_tokens" if provider == "deepseek" else "max_completion_tokens",
            }
            result.update(saved.get("profiles", {}).get(provider, {}))
            return result

    def environment(self, provider: str | None = None) -> dict[str, str]:
        profile = self.profile(provider)
        prefix = "DEEPSEEK" if profile["provider"] == "deepseek" else "NEW_API"
        return {
            "CAD2GIS_LLM_PROVIDER": profile["provider"].replace("-", "_"),
            prefix + "_BASE_URL": profile["base_url"], prefix + "_MODEL": profile["model"],
            prefix + "_API_KEY": profile["api_key"], prefix + "_CAPABILITY": profile["capability"],
            "CAD2GIS_LLM_TIMEOUT_S": str(profile["timeout_s"]),
            "CAD2GIS_LLM_MAX_COMPLETION_TOKENS": str(profile["max_tokens"]),
            "CAD2GIS_LLM_TOKEN_FIELD": profile["token_field"],
        }

    def require(self, provider: str | None = None) -> dict[str, Any]:
        profile = self.profile(provider)
        if not profile["api_key"]:
            raise ValueError("AI 尚未配置密钥。请打开底部「AI 配置」，保存并测试连接。")
        if not profile["model"]:
            raise ValueError("AI 尚未配置模型名称，请在「AI 配置」填写服务实际支持的模型。")
        try:
            ProviderConfig.from_env(self.environment(profile["provider"]), provider=profile["provider"].replace("-", "_"))
        except ProviderError as exc:
            raise ValueError(str(exc)) from None
        return profile

    def public(self) -> dict[str, Any]:
        result = {"active": self._load().get("active", "deepseek"), "profiles": {},
                  "storage": "Windows DPAPI 用户加密" if os.name == "nt" else "当前用户私有配置文件（0600）"}
        for provider in ("deepseek", "new-api"):
            profile = self.profile(provider)
            profile["has_key"] = bool(profile.pop("api_key"))
            try:
                self.require(provider)
                profile["configured"], profile["issue"] = True, ""
            except ValueError as exc:
                profile["configured"], profile["issue"] = False, str(exc)
            result["profiles"][provider] = profile
        return result

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = payload.get("provider", "deepseek")
        with self.lock:
            profile = self.profile(provider)
            provider = profile["provider"]
            for field in ("base_url", "model", "capability", "token_field"):
                if field in payload:
                    value = payload[field]
                    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 for c in value):
                        raise ValueError("AI 配置字段无效")
                    profile[field] = value.strip()
            url = profile["base_url"].rstrip("/")
            if url.endswith("/chat/completions"):
                url = url[:-len("/chat/completions")]
            profile["base_url"] = _validate_base_url(url, "服务地址")
            if not profile["model"]:
                raise ValueError("请填写模型名称")
            if profile["capability"] not in {"json_object", "json_schema"}:
                raise ValueError("请选择有效的结构化输出方式")
            if profile["token_field"] not in {"max_tokens", "max_completion_tokens"}:
                raise ValueError("请选择有效的输出长度参数")
            key = payload.get("api_key", "")
            if not isinstance(key, str) or len(key) > 8192 or any(ord(c) < 32 for c in key):
                raise ValueError("API 密钥格式无效")
            if key.strip():
                profile["api_key"] = key.strip()
            if payload.get("clear_key") is True:
                profile["api_key"] = ""
            for field, lower, upper in (("timeout_s", 10, 300), ("max_tokens", 128, 32768), ("max_requests", 1, 80)):
                value = payload.get(field, profile[field])
                if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                    raise ValueError(f"{field} 必须在 {lower} 到 {upper} 之间")
                profile[field] = value
            state = self._load()
            state["active"] = provider
            state.setdefault("profiles", {})[provider] = profile
            if os.name == "nt":
                for item in state["profiles"].values():
                    secret = item.pop("api_key", "")
                    item["key_dpapi"] = base64.b64encode(_crypt(secret.encode("utf-8"))).decode("ascii") if secret else ""
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                self.directory.chmod(0o700)
            fd, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(state, output, ensure_ascii=False, indent=2)
                Path(temporary).replace(self.file)
                self.file.chmod(0o600)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return self.public()


def safe_ai_error(exc: BaseException) -> str:
    """Do not echo provider bodies, request headers, or secrets into UI/logs."""
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return safe_ai_error(exc.exceptions[0])
    code = getattr(exc, "status_code", None)
    if code == 401:
        return "AI 身份验证失败（401），请检查密钥和服务地址。"
    if code == 403:
        return "AI 服务拒绝访问（403），请检查账号权限。"
    if code == 404:
        return "模型或接口不存在（404），请核对模型名称及 API 基础地址。"
    if code == 429:
        return "AI 服务限流或额度不足（429），请稍后重试并检查额度。"
    if code:
        return f"AI 服务返回 HTTP {code}，请检查接口、模型及工具调用支持。"
    if "Timeout" in type(exc).__name__:
        return "AI 连接超时，请检查网络，或在配置中增加超时时间。"
    if "Connection" in type(exc).__name__:
        return "无法连接 AI 服务，请检查网络、代理和 HTTPS 证书。"
    return f"AI 执行未完成（{type(exc).__name__}）。请检查连接或调整任务后重试。"
