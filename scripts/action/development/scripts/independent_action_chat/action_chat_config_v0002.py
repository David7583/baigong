# ============================================================
# 文件名: action_chat_config_v0002.py
# 中文名: 新版独立 Action Chat 配置加载脚本
# 版本号: v0002
#
# 主层级: action
# 层级: development / independent_action_chat / configuration
# 脚本定位: 运行前的配置、路径、版本和环境变量校验边界
#
# 职责说明:
# - 加载并校验 Chat 与 Retrieval 的版本化 YAML 配置
# - 从项目根解析业务路径，经总控获取公开模型目录；不加载 .env
#
# 本脚本做什么:
# - 校验 Provider/Model、Router、运行参数和检索基础管线
# - 生成不含密钥值的稳定配置快照哈希
#
# 本脚本不做什么:
# - 不连接模型或数据库，不创建缺失数据库，不返回密钥给浏览器
#
# 制度边界声明:
# - 正式配置不得含明文密钥；路径不得逃逸项目根
# - 配置错误在产生运行副作用前失败
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_config_v0002
# family: action_chat_config
# role: configuration_loader
# version: v0002
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_config_v0002.py
# input:
#   - config/action/config/independent_action_chat_config_v0002.yml
#   - config/action/config/independent_action_chat_retrieval_config_v0001.yml
#   - application controller connection descriptor
# output:
#   - validated ChatConfig and RetrievalConfig
# depends_on:
#   - Python stdlib: dataclasses, importlib, pathlib, typing
#   - action_ai_controller_v0002
#   - PyYAML: yaml
#   - action_chat_contracts_v0001.py
# used_by:
#   - action_chat_service_v0005.py
#   - action_chat_server_v0006.py
#   - action_chat_retrieval_v0003.py
# ============================================================

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .action_chat_contracts_v0001 import (
    ConfigError,
    RETRIEVAL_MODES,
    ROUTER_MODES,
    redact,
    sha256_json,
)


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_config"
SCRIPT_NAME = "action_chat_config_v0002"
SCRIPT_VERSION = "v0002"
CONFIG_SCHEMA_VERSION = 1
MAX_ROOT_SEARCH_DEPTH = 12


# ============================================================
# 异常类型
# ============================================================

class PathBoundaryError(ConfigError):
    """Raised when a configured filesystem path escapes the project root."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class ChatConfig:
    project_root: Path
    source_path: Path
    raw: Dict[str, Any]
    providers: Dict[str, Dict[str, Any]]
    runtime: Dict[str, Any]
    router: Dict[str, Any]
    storage: Dict[str, Any]
    server: Dict[str, Any]
    config_hash: str


@dataclass(frozen=True)
class RetrievalConfig:
    project_root: Path
    source_path: Path
    raw: Dict[str, Any]
    retrieval: Dict[str, Any]
    config_hash: str


# ============================================================
# 工具函数区
# ============================================================

def find_project_root(start: Path) -> Path:
    current = start.resolve()
    for _ in range(MAX_ROOT_SEARCH_DEPTH):
        if (
            (current / "scripts").is_dir()
            and (current / "config").is_dir()
            and (current / "AGENTS.md").is_file()
            and (current / "PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md").is_file()
        ):
            return current
        if current.parent == current:
            break
        current = current.parent
    raise ConfigError(f"project root not found from {start}")


def resolve_project_path(project_root: Path, raw_path: str, *, must_exist: bool = False) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ConfigError("configured path must be a non-empty string")
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as exc:
        raise PathBoundaryError(f"configured path escapes project root: {raw_path}") from exc
    if must_exist and not resolved.exists():
        raise ConfigError(f"configured path does not exist: {raw_path}")
    return resolved


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding=DEFAULT_ENCODING))
    if not isinstance(raw, dict):
        raise ConfigError(f"config must be a mapping: {path}")
    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigError(f"config schema_version must be {CONFIG_SCHEMA_VERSION}: {path.name}")
    return raw


def _require_mapping(raw: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"missing or invalid mapping: {key}")
    return value


def _require_positive_int(mapping: Dict[str, Any], key: str, *, allow_zero: bool = False) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}")
    return value


def _validate_provider_catalog(providers: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not providers:
        raise ConfigError("controller-backed provider catalog is empty")
    validated: Dict[str, Dict[str, Any]] = {}
    for provider_id, item in providers.items():
        if not isinstance(item, dict):
            raise ConfigError(f"provider {provider_id} must be a mapping")
        if item.get("provider_id") != provider_id:
            raise ConfigError(f"provider_id mismatch for {provider_id}")
        adapter = item.get("adapter")
        if adapter != "controller":
            raise ConfigError(f"unsupported provider adapter for {provider_id}: {adapter}")
        models = item.get("models")
        if not isinstance(models, list) or not models:
            raise ConfigError(f"provider {provider_id} requires at least one model")
        model_ids = set()
        for model in models:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"]:
                raise ConfigError(f"provider {provider_id} has invalid model")
            if model["id"] in model_ids:
                raise ConfigError(f"provider {provider_id} contains duplicate model id")
            model_ids.add(model["id"])
            capabilities = model.get("capabilities", {})
            if not isinstance(capabilities, dict):
                raise ConfigError(f"provider {provider_id} model capabilities must be a mapping")
        if item.get("default_model") not in model_ids:
            raise ConfigError(f"provider {provider_id} default_model is not registered")
        validated[provider_id] = dict(item)
    return validated


def _validate_selection(providers: Dict[str, Dict[str, Any]], provider_id: Any, model_id: Any, name: str) -> None:
    if provider_id not in providers:
        raise ConfigError(f"{name} provider is not registered: {provider_id}")
    models = {item["id"] for item in providers[provider_id]["models"] if item.get("enabled", True)}
    if model_id not in models:
        raise ConfigError(f"{name} model is unavailable: {provider_id}/{model_id}")


# ============================================================
# 默认映射
# ============================================================

DEFAULT_CHAT_CONFIG = "config/action/config/independent_action_chat_config_v0002.yml"
DEFAULT_RETRIEVAL_CONFIG = "config/action/config/independent_action_chat_retrieval_config_v0001.yml"


# ============================================================
# 核心类
# ============================================================

def load_chat_config(config_path: str | Path, *, project_root: Optional[Path] = None) -> ChatConfig:
    path = Path(config_path).resolve()
    root = project_root.resolve() if project_root else find_project_root(path)
    raw = _load_yaml(path)
    if "providers" in raw:
        raise ConfigError("legacy provider connection config is not accepted by the controlled app")
    connection = _require_mapping(raw, "ai_controller")
    if not isinstance(connection.get("start_on_launch"), bool):
        raise ConfigError("ai_controller.start_on_launch must be boolean")
    with open_controller(root, connection) as controller:
        public = {}
        for provider_id, definition in _require_mapping(raw, "provider_profiles").items():
            catalog = controller.models(definition["controller_provider"], limit=1000)
            available = {m["model"] for m in catalog["models"]}
            if definition.get("profile", "default") not in catalog["profiles"]:
                raise ConfigError("application profile is not in controller catalog")
            models = [dict(m) for m in definition["models"] if m["id"] in available]
            if definition["controller_provider"] == "local_agents":
                metadata = {m["model"]:m for m in catalog["models"]}
                for model in models:
                    model["agent_status"] = metadata[model["id"]]
                    model["display_name"] = metadata[model["id"]]["display_name"]
                    model["enabled"] = metadata[model["id"]]["available"]
            if not models:
                continue
            public[provider_id] = {**definition, "provider_id":provider_id, "adapter":"controller",
                                   "enabled":True, "models":models, "default_model":models[0]["id"]}
    providers = _validate_provider_catalog(public)
    runtime = _require_mapping(raw, "runtime")
    router = _require_mapping(raw, "router")
    storage = _require_mapping(raw, "storage")
    server = _require_mapping(raw, "server")

    _validate_selection(providers, runtime.get("default_answer_provider"), runtime.get("default_answer_model"), "answer")
    _validate_selection(providers, router.get("default_provider"), router.get("default_model"), "router")
    if router.get("default_mode") not in ROUTER_MODES:
        raise ConfigError("router.default_mode is invalid")
    threshold = float(router.get("machine_confidence_threshold", -1))
    if not 0.0 <= threshold <= 1.0:
        raise ConfigError("router.machine_confidence_threshold must be between 0 and 1")
    for key in ("request_timeout_ms", "context_max_messages", "context_max_chars"):
        _require_positive_int(runtime, key)
    _require_positive_int(runtime, "retry_count", allow_zero=True)
    if server.get("host") != "127.0.0.1":
        raise ConfigError("server.host must be 127.0.0.1")
    _require_positive_int(server, "port")
    for key in ("runtime_dir", "action_output_dir", "log_dir"):
        resolve_project_path(root, str(storage.get(key, "")))
    return ChatConfig(
        project_root=root,
        source_path=path,
        raw=raw,
        providers=providers,
        runtime=runtime,
        router=router,
        storage=storage,
        server=server,
        config_hash=sha256_json(redact(raw)),
    )


def open_controller(root: Path, connection: Dict[str, Any]):
    """Load only the public controller entry, not provider credential files."""
    path = resolve_project_path(root, connection["script"], must_exist=True)
    name = "_independent_chat_controller_" + sha256_json(str(path))[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise ConfigError("AI controller could not be imported") from None
    return sys.modules[name].AIController(resolve_project_path(root, connection["config"], must_exist=True), root=root)


def load_retrieval_config(config_path: str | Path, *, project_root: Optional[Path] = None) -> RetrievalConfig:
    path = Path(config_path).resolve()
    root = project_root.resolve() if project_root else find_project_root(path)
    raw = _load_yaml(path)
    retrieval = _require_mapping(raw, "retrieval")
    if retrieval.get("default_mode") not in RETRIEVAL_MODES:
        raise ConfigError("retrieval.default_mode is invalid")
    if retrieval.get("required_pipeline") != ["chroma", "sqlite"]:
        raise ConfigError("retrieval.required_pipeline must be ['chroma', 'sqlite'] in this order")
    _require_positive_int(retrieval, "total_timeout_ms")
    _require_positive_int(retrieval, "max_context_chars")
    backends = _require_mapping(retrieval, "backends")
    for required in ("chroma", "sqlite", "duckdb", "neo4j"):
        if required not in backends or not isinstance(backends[required], dict):
            raise ConfigError(f"retrieval backend missing: {required}")
    if backends["chroma"].get("adapter") != "chroma" or backends["sqlite"].get("adapter") != "sqlite":
        raise ConfigError("required pipeline adapters must be chroma and sqlite")
    if backends["sqlite"].get("read_only") is not True:
        raise ConfigError("SQLite must be configured read_only")
    if backends["duckdb"].get("read_only") is not True:
        raise ConfigError("DuckDB must be configured read_only")
    for backend in backends.values():
        if isinstance(backend, dict) and backend.get("path"):
            resolve_project_path(root, str(backend["path"]))
    return RetrievalConfig(
        project_root=root,
        source_path=path,
        raw=raw,
        retrieval=retrieval,
        config_hash=sha256_json(redact(raw)),
    )
