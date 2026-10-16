# ============================================================
# 文件名: action_ai_controller_v0005.py
# 中文名: 行动端 AI 能力总控
# 版本号: v0005
#
# 主层级: action
# 层级: infrastructures / ai_controller
# 脚本定位: 应用唯一接入的 AI 权限、配置解析与调用协调入口
#
# 职责说明:
# - 分离总许可、应用许可、应用启停与模型选择
# - 集中解析公开目录和密钥，使用独立执行器发出请求
#
# 本脚本做什么:
# - 持久化应用登记、权限、选择、并发租约及无正文审计
# - 继承 ai_adapter_v0003.py 的裁决、开关、日志职责及
#   register_ai_capability_v0001.py 的登记职责；新家族从 v0001 开始
# - 继承 action_ai_controller_v0001，新增受控本地向量推理，保持旧运行库和权限语义
#
# 本脚本不做什么:
# - 不控制应用的非 AI 功能，不检索、不保存聊天正文、不执行工具
# - 不创建两端之上的总控，不操作 action.db 开发登记库
#
# 制度边界声明:
# - 模型由应用选择，Provider/地址/密钥从本端配置解析，Prompt 由应用传入
# - 写配置指定的独立运行 SQLite；任务应用凭证独立保存，永不进入普通响应或审计
# - 任务 JSON 文件接口只操作本应用身份；工作进程不能授予许可
# - 默认总许可关闭；禁止自动换 Provider、下载模型、重试非幂等生成
# - 撤销阻止新准入，已发送的请求可能完成；本机可信进程接口非操作系统沙箱
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_ai_controller_v0005
# family: action_ai_controller
# role: ai_permission_and_call_controller
# version: v0005
# status: active
# entry_point: scripts/action/infrastructures/action_ai_controller_v0005.py
# input:
#   - config/action/ai/action_ai_controller_config_v0003.yml
#   - application identity, selection and AI request
# output:
#   - versioned result envelope and isolated runtime audit
# depends_on:
#   - action_ai_transport_v0001
#   - action_ai_agents_v0001
#   - action_ai_local_embedding_v0001
#   - Python stdlib
#   - PyYAML
# used_by:
#   - workbench_ai_inventory_v0002
#   - workbench_tasks_v0002
#   - workbench_task_worker_v0002
# ============================================================

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import importlib.util
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_ai_controller"
SCRIPT_NAME = "action_ai_controller_v0005"
SCRIPT_VERSION = "v0005"
SIDE = "action"
_LOCAL_EMBEDDING_LOCK = threading.RLock()
ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


# ============================================================
# 异常类型
# ============================================================

class ControlError(RuntimeError):
    """Stable safe code only; never include caller text or credential values."""


class ConfigError(ControlError):
    pass


class DataError(ControlError):
    pass


class PermissionDenied(ControlError):
    pass


class BusyError(ControlError):
    pass


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class Selection:
    provider: str
    model: str
    profile: str = "default"


# ============================================================
# 工具函数区
# ============================================================

class StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader: StrictLoader, node: Any, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ConfigError("duplicate_or_nonstring_yaml_key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise DataError("invalid_json_value") from None


def _id(value: Any) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise DataError("invalid_identifier")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise DataError("boolean_required")
    return value


def _positive(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ConfigError("positive_number_required")
    return value


def _root() -> Path:
    for path in Path(__file__).resolve().parents:
        if (path / "scripts").is_dir() and (path / "config").is_dir():
            return path
    raise ConfigError("project_root_not_found")


def _path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ConfigError("project_relative_path_required")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ConfigError("path_outside_project")
    return resolved


def _load_transport():
    name = f"_{SIDE}_ai_transport_v0001_internal"
    if name not in sys.modules:
        path = Path(__file__).with_name(f"{SIDE}_ai_transport_v0001.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise ConfigError("transport_unavailable") from None
    return sys.modules[name]


def _safe_url(base: str, suffix: str, local: bool) -> str:
    parsed = urlsplit(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("invalid_provider_url")
    if local:
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("ollama_must_be_loopback")
    elif parsed.scheme != "https" or not parsed.hostname or "{" in base:
        raise ConfigError("cloud_requires_resolved_https_url")
    if not suffix.startswith("/") or suffix.startswith("//") or any(x in suffix for x in ("..", "?", "#", "\\")):
        raise ConfigError("invalid_operation_path")
    return base.rstrip("/") + suffix


# ============================================================
# 默认映射与安全默认值
# ============================================================

RESERVED_FIELDS = {"model", "stream", "api_key", "headers", "base_url", "url", "credential"}


# ============================================================
# 核心类
# ============================================================

class ConfigCache:
    """Cache a bounded set of source files, recheck metadata on every use."""

    def __init__(self):
        self._lock = threading.RLock()
        self._items = {}

    def load(self, path: Path) -> dict:
        with self._lock:
            try:
                stat = path.stat()
                fingerprint = (stat.st_mtime_ns, stat.st_size)
                if path in self._items and self._items[path][0] == fingerprint:
                    return self._items[path][1]
                if stat.st_size > 16 * 1024 * 1024:
                    raise ConfigError("configuration_too_large")
                data = yaml.load(path.read_text(encoding=DEFAULT_ENCODING), Loader=StrictLoader)
                if not isinstance(data, dict):
                    raise ConfigError("configuration_mapping_required")
                self._items[path] = (fingerprint, data)
                return data
            except ConfigError:
                raise
            except Exception:
                self._items.pop(path, None)
                raise ConfigError("configuration_unreadable") from None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class AIController:
    """Local administrator API. Give applications only client(app_id, token)."""

    def __init__(self, config: Path | None = None, *, root: Path | None = None,
                 transport: Any = None):
        self.root = (root or _root()).resolve()
        self.cache = ConfigCache()
        config_path = config or self.root / f"config/{SIDE}/ai/{SIDE}_ai_controller_config_v0001.yml"
        self.config = copy.deepcopy(self.cache.load(Path(config_path)))
        self._validate_config()
        self.db_path = _path(self.root, self.config["state_path"])
        self.transport_module = _load_transport()
        self.transport = transport or self.transport_module.JsonTransport(self.config["max_concurrent"])
        self._closed = False
        # Construction/import never creates the runtime database. initialize is explicit.

    def _validate_config(self) -> None:
        required = {"schema_version", "side", "state_path", "sources", "max_concurrent",
                    "per_app_concurrent", "connect_timeout_seconds", "read_timeout_seconds",
                    "total_timeout_seconds", "max_request_bytes", "max_response_bytes",
                    "operations", "source_switch_policy"}
        if set(self.config) != required or self.config["schema_version"] != "1.0" or self.config["side"] != SIDE:
            raise ConfigError("controller_schema_mismatch")
        if self.config["source_switch_policy"] != "controller_owns_permission":
            raise ConfigError("unsupported_switch_policy")
        if not {"keys", "catalog", "ollama"} <= set(self.config["sources"]) <= {"keys", "catalog", "ollama", "local_embeddings", "agents"}:
            raise ConfigError("invalid_sources")
        for value in self.config["sources"].values():
            _path(self.root, value)
        for key in ("max_concurrent", "per_app_concurrent", "max_request_bytes", "max_response_bytes"):
            if type(self.config[key]) is not int:
                raise ConfigError("integer_limit_required")
            _positive(self.config[key])
        for key in ("connect_timeout_seconds", "read_timeout_seconds", "total_timeout_seconds"):
            _positive(self.config[key])
        if self.config["per_app_concurrent"] > self.config["max_concurrent"]:
            raise ConfigError("per_app_limit_exceeds_total")
        if not isinstance(self.config["operations"], dict):
            raise ConfigError("operations_mapping_required")
        for protocol, operations in self.config["operations"].items():
            _id(protocol)
            if not isinstance(operations, dict):
                raise ConfigError("invalid_operations")
            for name, route in operations.items():
                _id(name)
                if set(route) != {"path", "model_in_body", "required"} or type(route["model_in_body"]) is not bool:
                    raise ConfigError("invalid_operation_contract")
                if not isinstance(route["required"], list) or not all(isinstance(x,str) for x in route["required"]):
                    raise ConfigError("invalid_operation_contract")
                _safe_url("https://example.invalid", route["path"].replace("{model}", "model"), False)

    def close(self) -> None:
        self.transport.close()
        self.cache.clear()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def _db(self, write: bool = False):
        if self._closed:
            raise ControlError("controller_closed")
        if not self.db_path.is_file():
            raise ConfigError("controller_not_initialized")
        conn = None
        try:
            conn = sqlite3.connect(self.db_path.as_uri() + "?mode=rw", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            meta = conn.execute("SELECT side,version FROM control WHERE id=1").fetchone()
            if not meta or meta["side"] != SIDE or meta["version"] != 1:
                raise ConfigError("runtime_schema_or_side_mismatch")
            yield conn
            conn.commit()
        except sqlite3.Error:
            if conn:
                conn.rollback()
            raise ControlError("runtime_database_error") from None
        finally:
            if conn:
                conn.close()

    def initialize(self, *, dry_run: bool = False) -> dict:
        if self.db_path.exists():
            with self._db():
                pass
            return {"status": "existing", "side": SIDE}
        if dry_run:
            return {"status": "dry_run", "side": SIDE}
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path, timeout=5)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY CHECK(id=1),
                    version INTEGER NOT NULL, side TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
                CREATE TABLE IF NOT EXISTS applications(app_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                    allowed INTEGER NOT NULL DEFAULT 0 CHECK(allowed IN (0,1)),
                    running INTEGER NOT NULL DEFAULT 0 CHECK(running IN (0,1)),
                    retired INTEGER NOT NULL DEFAULT 0 CHECK(retired IN (0,1)),
                    selection TEXT, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS calls(call_id TEXT PRIMARY KEY, app_id TEXT NOT NULL,
                    provider TEXT, model TEXT, operation TEXT NOT NULL, state TEXT NOT NULL,
                    started_at REAL NOT NULL, finished_at REAL, lease_until REAL NOT NULL,
                    error_code TEXT, usage TEXT, duration_ms REAL,
                    FOREIGN KEY(app_id) REFERENCES applications(app_id));
                CREATE INDEX IF NOT EXISTS calls_active ON calls(state,lease_until,app_id);
                CREATE INDEX IF NOT EXISTS calls_app ON calls(app_id,started_at);
                CREATE TABLE IF NOT EXISTS audit(event_id INTEGER PRIMARY KEY, at REAL NOT NULL,
                    event TEXT NOT NULL, app_id TEXT, detail TEXT NOT NULL);
            """)
            conn.execute("INSERT OR IGNORE INTO control VALUES(1,1,?,0)", (SIDE,))
            conn.commit()
        with self._db():
            pass
        return {"status": "initialized", "side": SIDE, "enabled": False}

    def _audit(self, conn, event: str, app_id: str | None, detail: dict) -> None:
        conn.execute("INSERT INTO audit(at,event,app_id,detail) VALUES(?,?,?,?)",
                     (time.time(), event, app_id, _json(detail)))

    def register(self, app_id: str, *, dry_run: bool = False) -> dict:
        _id(app_id)
        with self._db(write=not dry_run) as conn:
            if conn.execute("SELECT 1 FROM applications WHERE app_id=?", (app_id,)).fetchone():
                return {"status": "existing", "app_id": app_id}
            if dry_run:
                return {"status": "dry_run", "app_id": app_id}
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO applications(app_id,token_hash,updated_at) VALUES(?,?,?)",
                         (app_id, hashlib.sha256(token.encode()).hexdigest(), time.time()))
            self._audit(conn, "register", app_id, {})
            return {"status": "registered", "app_id": app_id, "token": token}

    def rotate_token(self, app_id: str) -> dict:
        _id(app_id)
        token = secrets.token_urlsafe(32)
        with self._db(True) as conn:
            self._app(conn, app_id)
            conn.execute("UPDATE applications SET token_hash=?,updated_at=? WHERE app_id=?",
                         (hashlib.sha256(token.encode()).hexdigest(), time.time(), app_id))
            self._audit(conn, "rotate_token", app_id, {})
        return {"status": "rotated", "app_id": app_id, "token": token}

    def set_global(self, enabled: bool, *, dry_run: bool = False) -> dict:
        _bool(enabled)
        with self._db(not dry_run) as conn:
            if not dry_run:
                conn.execute("UPDATE control SET enabled=? WHERE id=1", (int(enabled),))
                self._audit(conn, "global_permission", None, {"enabled": enabled})
        return {"status": "dry_run" if dry_run else "updated", "enabled": enabled}

    def set_permission(self, app_id: str, allowed: bool, *, dry_run: bool = False) -> dict:
        _id(app_id); _bool(allowed)
        with self._db(not dry_run) as conn:
            self._app(conn, app_id)
            if not dry_run:
                conn.execute("UPDATE applications SET allowed=?,updated_at=? WHERE app_id=?",
                             (int(allowed), time.time(), app_id))
                self._audit(conn, "application_permission", app_id, {"allowed": allowed})
        return {"status": "dry_run" if dry_run else "updated", "app_id": app_id, "allowed": allowed}

    def retire(self, app_id: str, *, dry_run: bool = False) -> dict:
        _id(app_id)
        with self._db(not dry_run) as conn:
            self._app(conn, app_id)
            if not dry_run:
                conn.execute("UPDATE applications SET retired=1,allowed=0,running=0,updated_at=? WHERE app_id=?",
                             (time.time(), app_id))
                self._audit(conn, "retire", app_id, {})
        return {"status": "dry_run" if dry_run else "retired", "app_id": app_id}

    def _app(self, conn, app_id: str, token: str | None = None):
        row = conn.execute("SELECT * FROM applications WHERE app_id=?", (app_id,)).fetchone()
        if row is None or row["retired"]:
            raise PermissionDenied("application_unavailable")
        if token is not None:
            if not isinstance(token, str) or not hmac.compare_digest(row["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
                raise PermissionDenied("invalid_application_token")
        return row

    def client(self, app_id: str, token: str):
        _id(app_id)
        if not isinstance(token, str) or not token:
            raise PermissionDenied("invalid_application_token")
        with self._db() as conn:
            self._app(conn, app_id, token)
        return ApplicationClient(self, app_id, token)

    def status(self, *, after: str = "", limit: int = 100) -> dict:
        if type(limit) is not int or not 1 <= limit <= 1000 or not isinstance(after, str):
            raise DataError("invalid_page")
        with self._db() as conn:
            enabled = bool(conn.execute("SELECT enabled FROM control WHERE id=1").fetchone()[0])
            rows = conn.execute("SELECT app_id,allowed,running,retired,selection FROM applications WHERE app_id>? ORDER BY app_id LIMIT ?", (after, limit)).fetchall()
        apps = [{**dict(row), "selection": json.loads(row["selection"]) if row["selection"] else None} for row in rows]
        return {"side": SIDE, "enabled": enabled, "applications": apps, "next_after": apps[-1]["app_id"] if apps else None}


    def inventory(self) -> dict:
        """Credential-free observation. Does not register, enable, start or invoke."""
        problems = []
        try:
            state = self.status(limit=1000)
            apps = list(state["applications"])
            cursor = state["next_after"]
            while cursor:
                page = self.status(after=cursor, limit=1000)
                apps.extend(page["applications"])
                cursor = page["next_after"]
            state["applications"] = apps
        except ControlError as exc:
            state = {"side": SIDE, "enabled": None, "applications": []}
            problems.append({"source": "controller_state", "error": str(exc)})
        providers = []
        try:
            names = list(self._source("catalog")["providers"])
        except Exception:
            names = []
            problems.append({"source": "catalog", "error": "catalog_unavailable"})
        names += [name for name in ("ollama", "local_embeddings") if name in self.config["sources"]]
        if "agents" in self.config["sources"]:
            names.append("local_agents")
        for name in dict.fromkeys(names):
            try:
                page = self.models(name, limit=1000)
                models = list(page["models"])
                while len(models) < page["total"]:
                    more = self.models(name, offset=len(models), limit=1000)["models"]
                    if not more:
                        raise DataError("catalog_page_incomplete")
                    models.extend(more)
                providers.append({**page, "models": models,
                                  "verification": "configured_unverified"})
            except Exception:
                problems.append({"source": name, "error": "source_unavailable"})
        return {**state, "providers": providers, "problems": problems,
                "observed_at": time.time(), "status": "partial" if problems else "connected",
                "semantics": "Catalog and permission facts; running is controller intent, not process liveness. No provider probe or model invocation."}

    def _source(self, name: str) -> dict:
        return self.cache.load(_path(self.root, self.config["sources"][name]))

    def _agent_module(self):
        name = "_action_ai_agents_v0001_internal"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("action_ai_agents_v0001.py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[name]

    def models(self, provider: str, *, offset: int = 0, limit: int = 100) -> dict:
        """Public catalog discovery, never returns credentials or enables a model."""
        _id(provider)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise DataError("invalid_page")
        if provider == "local_agents":
            entries = self._source("agents")["agents"]
            models = [{"model": key, "capabilities": ["completion"], "display_name": value.get("display_name", key),
                       **self._agent_module().descriptor_status(value)} for key,value in entries.items()]
            return {"provider":provider,"profiles":["default"],"total":len(models),"models":models[offset:offset+limit]}
        if provider == "local_embeddings":
            items = [{"model_id": name, "capabilities": ["embedding"]}
                     for name in self._source("local_embeddings")["models"]]
            profiles = ["default"]
        elif provider == "ollama":
            items = self._source("ollama")["model_inventory"]
            profiles = ["default"]
        else:
            catalog = self._source("catalog")
            if provider not in catalog["providers"]:
                raise DataError("unknown_provider")
            items = [x for x in catalog["models"] if x["provider"] == provider]
            profiles = list(catalog["providers"][provider]["profiles"])
        return {"provider": provider, "profiles": profiles, "total": len(items),
                "models": [{"model": x["model_id"], "capabilities": copy.deepcopy(x.get("capabilities", []))}
                           for x in items[offset:offset+limit]]}

    def _resolve(self, selection: Selection) -> dict:
        if not isinstance(selection, Selection):
            raise DataError("selection_required")
        _id(selection.provider); _id(selection.profile)
        if not isinstance(selection.model, str) or not selection.model or len(selection.model) > 200:
            raise DataError("invalid_model")
        if selection.provider == "local_agents":
            source = self._source("agents")
            if source.get("schema_version") != "1.0" or selection.profile != "default":
                raise ConfigError("agent_schema_or_profile_mismatch")
            descriptor = source.get("agents", {}).get(selection.model)
            if not isinstance(descriptor, dict) or not self._agent_module().descriptor_status(descriptor)["available"]:
                raise ConfigError("agent_not_configured")
            return {"executor":"agent","local":False,"protocol":"openai","descriptor":descriptor}
        if selection.provider == "local_embeddings":
            source = self._source("local_embeddings")
            if source.get("schema_version") != "1.0" or selection.profile != "default":
                raise ConfigError("local_embedding_schema_mismatch")
            try:
                descriptor = source["models"][selection.model]
                if descriptor["local_files_only"] is not True or descriptor["normalize_embeddings"] is not True:
                    raise ConfigError("local_embedding_policy_mismatch")
                path = _path(self.root, descriptor["path"])
                for key in ("dimension", "max_texts", "max_text_chars"):
                    if type(descriptor[key]) is not int:
                        raise ConfigError("invalid_embedding_limit")
                    _positive(descriptor[key])
                if not path.is_dir():
                    raise ConfigError("local_embedding_model_missing")
            except (KeyError, TypeError):
                raise ConfigError("local_embedding_model_unconfigured") from None
            return {"executor": "local_embedding", "local": False, "path": path, "descriptor": descriptor}
        if selection.provider == "ollama":
            source = self._source("ollama")
            if source.get("schema_version") != "1.0" or source.get("provider") != "ollama" or selection.profile != "default":
                raise ConfigError("ollama_schema_or_profile_mismatch")
            if source.get("connection", {}).get("authentication", {}).get("mode") != "none":
                raise ConfigError("unsupported_ollama_authentication")
            inventory = {x["model_id"]: x for x in source["model_inventory"]}
            if selection.model not in inventory or selection.model.endswith("cloud"):
                raise DataError("local_model_not_in_inventory")
            return {"base_url": source["connection"]["base_url"], "protocol": "ollama_native",
                    "headers": {}, "defaults": source.get("request_defaults", {}),
                    "local": True, "capabilities": inventory[selection.model]["capabilities"],
                    "source": source}
        catalog = self._source("catalog")
        keys = self._source("keys")
        if keys.get("schema_version") != "2.0":
            raise ConfigError("keys_schema_mismatch")
        try:
            provider = catalog["providers"][selection.provider]
            profile = provider["profiles"][selection.profile]
            credential_name = profile["credential"]
            binding = keys.get("provider_bindings", {}).get(selection.provider, {})
            if binding.get("profile") == selection.profile:
                credential_name = binding.get("credential", credential_name)
            credential = keys["credentials"][credential_name]
        except (KeyError, TypeError):
            raise ConfigError("provider_profile_or_credential_missing") from None
        if credential.get("provider") != selection.provider or credential.get("scope") != "inference":
            raise ConfigError("credential_provider_or_scope_mismatch")
        if credential.get("source") == "environment":
            secret = os.environ.get(credential.get("environment_variable", ""), "")
        elif credential.get("source") == "inline":
            secret = credential.get("value", "")
        else:
            raise ConfigError("unsupported_credential_source")
        if not isinstance(secret, str) or not secret.strip():
            raise ConfigError("credential_missing")
        if "\n" in secret or "\r" in secret:
            raise ConfigError("invalid_credential")
        auth = profile.get("authentication", provider["authentication"])
        if auth.get("scheme") not in {"Bearer", "raw"}:
            raise ConfigError("unsupported_authentication")
        headers = dict(profile.get("headers", {}))
        headers[auth["header"]] = ("Bearer " if auth["scheme"] == "Bearer" else "") + secret
        # Catalog entries include historical and account-unverified models; explicit selection
        # is necessary and upstream errors remain authoritative. No automatic substitution.
        if not any(x["provider"] == selection.provider and x["model_id"] == selection.model for x in catalog["models"]):
            raise DataError("model_not_in_catalog")
        return {"base_url": profile["base_url"], "protocol": profile["protocol"], "headers": headers,
                "defaults": provider.get("request_defaults", {}), "local": False}

    def _plan(self, selection: Selection, operation: str, payload: dict):
        _id(operation)
        if not isinstance(payload, dict) or RESERVED_FIELDS.intersection(payload):
            raise DataError("payload_contains_reserved_fields")
        if len(_json(payload).encode(DEFAULT_ENCODING)) > self.config["max_request_bytes"]:
            raise DataError("request_too_large")
        resolved = self._resolve(selection)
        if resolved.get("executor") == "agent":
            if operation != "chat" or set(payload)-{"messages","temperature","max_tokens","max_completion_tokens"}:
                raise DataError("agent_operation_or_payload_invalid")
            if not isinstance(payload.get("messages"),list) or not payload["messages"]:
                raise DataError("agent_messages_required")
            return None, resolved
        if resolved.get("executor") == "local_embedding":
            if operation != "embed" or set(payload) != {"input"}:
                raise DataError("local_embedding_operation_mismatch")
            texts = [payload["input"]] if isinstance(payload["input"], str) else payload["input"]
            limits = resolved["descriptor"]
            if not isinstance(texts, list) or not 1 <= len(texts) <= limits["max_texts"] or not all(isinstance(x,str) and 0 < len(x) <= limits["max_text_chars"] for x in texts):
                raise DataError("local_embedding_input_out_of_bounds")
            return None, resolved
        try:
            route = self.config["operations"][resolved["protocol"]][operation]
        except KeyError:
            raise DataError("unsupported_operation_for_protocol") from None
        body = copy.deepcopy(resolved["defaults"])
        body.update(copy.deepcopy(payload))
        body.pop("stream", None)
        for key in route["required"]:
            if key not in body or body[key] is None or body[key] == [] or body[key] == "":
                raise DataError("operation_required_field_missing")
        for key in ("messages", "contents"):
            if key in body and (not isinstance(body[key], list) or not body[key] or not all(isinstance(x, dict) for x in body[key])):
                raise DataError("message_array_required")
        if "max_tokens" in body and (type(body["max_tokens"]) is not int or body["max_tokens"] <= 0):
            raise DataError("positive_max_tokens_required")
        if resolved["local"]:
            need = "embedding" if operation == "embed" else "completion"
            if need not in resolved["capabilities"]:
                raise DataError("local_model_capability_missing")
            # These safety policies are enforced, not just copied into documentation.
            policy = resolved["source"]["policies"]
            if policy.get("allow_remote_models") is not False or policy.get("auto_pull") is not False:
                raise ConfigError("local_only_policy_required")
        if route["model_in_body"]:
            body["model"] = ("models/" if resolved["protocol"] == "google_genai" else "") + selection.model
        if operation in {"chat", "generate", "messages", "responses"}:
            body["stream"] = False
        suffix = route["path"].replace("{model}", quote(selection.model, safe=""))
        plan = self.transport_module.RequestPlan(
            url=_safe_url(resolved["base_url"], suffix, resolved["local"]),
            headers=resolved["headers"], payload=body,
            connect_timeout=self.config["connect_timeout_seconds"], read_timeout=self.config["read_timeout_seconds"],
            total_timeout=self.config["total_timeout_seconds"], max_response_bytes=self.config["max_response_bytes"])
        return plan, resolved

    def _set_application(self, app_id: str, token: str, *, running: bool | None = None,
                         selection: Selection | None = None, dry_run: bool = False) -> dict:
        # Authenticate before touching any provider configuration.
        if not isinstance(token, str) or not token:
            raise PermissionDenied("invalid_application_token")
        with self._db() as conn:
            self._app(conn, app_id, token)
        if running is not None:
            _bool(running)
        if selection is not None:
            self._resolve(selection)
        with self._db(not dry_run) as conn:
            self._app(conn, app_id, token)
            if not dry_run:
                if running is not None:
                    conn.execute("UPDATE applications SET running=?,updated_at=? WHERE app_id=?", (int(running), time.time(), app_id))
                if selection is not None:
                    conn.execute("UPDATE applications SET selection=?,updated_at=? WHERE app_id=?", (_json(selection.__dict__), time.time(), app_id))
                self._audit(conn, "application_settings", app_id,
                            {"running": running, "selection": selection.__dict__ if selection else None})
        return {"status": "dry_run" if dry_run else "updated", "app_id": app_id}

    def _admit(self, conn, app_id: str, token: str):
        if not isinstance(token, str) or not token:
            raise PermissionDenied("invalid_application_token")
        app = self._app(conn, app_id, token)
        if not conn.execute("SELECT enabled FROM control WHERE id=1").fetchone()[0]:
            raise PermissionDenied("global_ai_disabled")
        if not app["allowed"]:
            raise PermissionDenied("application_ai_denied")
        if not app["running"]:
            raise PermissionDenied("application_ai_paused")
        if not app["selection"]:
            raise DataError("application_model_not_selected")
        return Selection(**json.loads(app["selection"]))

    def invoke(self, app_id: str, token: str, operation: str, payload: dict, *, dry_run: bool = False) -> dict:
        _id(app_id); _id(operation)
        try:
            with self._db() as conn:
                selection = self._admit(conn, app_id, token)
        except PermissionDenied as exc:
            if not dry_run:
                with self._db(True) as conn:
                    # Invalid identities are not written as attacker-controlled audit content.
                    self._app(conn, app_id, token)
                    self._audit(conn, "request_denied", app_id, {"operation": operation, "reason": str(exc)})
            raise
        plan, resolved = self._plan(selection, operation, payload)
        if dry_run:
            return {"status": "dry_run", "side": SIDE, "app_id": app_id, "selection": selection.__dict__, "operation": operation}
        call_id = str(uuid.uuid4())
        now = time.time()
        with self._db(True) as conn:
            if self._admit(conn, app_id, token) != selection:
                raise BusyError("selection_changed_retry_explicitly")
            conn.execute("UPDATE calls SET state='unknown',error_code='lease_expired',finished_at=? WHERE state='running' AND lease_until<?", (now, now))
            count = conn.execute("SELECT COUNT(*),COALESCE(SUM(app_id=?),0) FROM calls WHERE state='running'", (app_id,)).fetchone()
            if count[0] >= self.config["max_concurrent"] or count[1] >= self.config["per_app_concurrent"]:
                raise BusyError("concurrency_limit_reached")
            lease = now + self.config["total_timeout_seconds"] + self.config["read_timeout_seconds"] + self.config["connect_timeout_seconds"] * 3 + 30
            conn.execute("INSERT INTO calls(call_id,app_id,provider,model,operation,state,started_at,lease_until) VALUES(?,?,?,?,?,'running',?,?)",
                         (call_id, app_id, selection.provider, selection.model, operation, now, lease))
        started = time.monotonic()
        try:
            if resolved["local"]:
                # /api/show is metadata only; rejects local names that proxy cloud models.
                local_plan = self.transport_module.RequestPlan(
                    _safe_url(resolved["base_url"], resolved["source"]["endpoints"]["show_model"], True),
                    {}, {"model": selection.model}, min(plan.connect_timeout, 5), min(plan.read_timeout, 10),
                    min(plan.total_timeout, 15), plan.max_response_bytes)
                details = self.transport.execute(local_plan)
                if details.get("remote_host") or details.get("remote_model"):
                    raise PermissionDenied("remote_ollama_model_denied")
                need = "embedding" if operation == "embed" else "completion"
                if need not in details.get("capabilities", []):
                    raise DataError("live_model_capability_missing")
                # A switch might have changed while checking local metadata.
                with self._db() as conn:
                    if self._admit(conn, app_id, token) != selection:
                        raise BusyError("selection_changed_retry_explicitly")
            if resolved.get("executor") == "agent":
                module = self._agent_module()
                try:
                    descriptor = resolved["descriptor"]
                    credential = None
                    if descriptor["adapter"] == "deepseek_headless":
                        upstream = self._resolve(Selection(descriptor["provider"],descriptor["model"],"default"))
                        credential = upstream["headers"]["Authorization"].removeprefix("Bearer ")
                    result = module.execute(descriptor, payload, root=self.root, credentials=credential,
                                            timeout=self.config["total_timeout_seconds"], max_bytes=self.config["max_response_bytes"])
                except module.AgentError as exc:
                    raise ControlError(str(exc)) from None
            elif resolved.get("executor") == "local_embedding":
                result = self._embed_local(resolved, payload)
            else:
                result = self.transport.execute(plan)
            if not isinstance(result, dict) or "error" in result:
                raise ControlError("invalid_provider_result")
            expected = {"chat": "message" if resolved["local"] else "choices",
                        "messages": "content", "responses": "output", "generate_content": "candidates",
                        "embed": "embeddings" if resolved["local"] or resolved.get("executor") == "local_embedding" else "embedding" if resolved["protocol"] == "google_genai" else "data",
                        "images": "data", "generate": "response", "count_tokens": "input_tokens"}.get(operation)
            if expected and (expected not in result or result[expected] is None):
                raise ControlError("provider_result_contract_mismatch")
            if expected in {"choices", "content", "output", "candidates", "embeddings", "data"} and not isinstance(result[expected], list):
                raise ControlError("provider_result_contract_mismatch")
            if expected == "message" and not isinstance(result[expected], dict):
                raise ControlError("provider_result_contract_mismatch")
            if resolved["local"] and operation != "embed" and result.get("done") is not True:
                raise ControlError("incomplete_provider_result")
            usage = result.get("usage", result.get("usageMetadata", {}))
            if resolved["local"]:
                usage = {"input_tokens": result.get("prompt_eval_count"), "output_tokens": result.get("eval_count")}
            # Persist numeric usage only: arbitrary provider fields never enter audit storage.
            usage_fields = {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
                            "promptTokenCount", "candidatesTokenCount", "totalTokenCount", "thoughtsTokenCount",
                            "cache_creation_input_tokens", "cache_read_input_tokens"}
            safe_usage = {k:v for k,v in usage.items() if k in usage_fields and type(v) in (int, float) and math.isfinite(v)} if isinstance(usage, dict) else {}
            self._finish(call_id, "completed", None, safe_usage, started)
            return {"schema_version": "1.0", "status": "completed", "side": SIDE,
                    "lineage": SIDE, "app_id": app_id, "call_id": call_id,
                    "provider": selection.provider, "model": selection.model, "operation": operation,
                    "usage": safe_usage, "data": result}
        except BaseException as exc:
            code = exc.code if isinstance(exc, self.transport_module.TransportError) else str(exc) if isinstance(exc, ControlError) else "unexpected_execution_error"
            if isinstance(exc, self.transport_module.TransportError) and exc.http_status is not None:
                code = f"{code}_{exc.http_status}"
            self._finish(call_id, "failed", code, {}, started)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ControlError(code) from None

    def _embed_local(self, resolved: dict, payload: dict) -> dict:
        name = "_action_ai_local_embedding_v0001_internal"
        with _LOCAL_EMBEDDING_LOCK:
            if name not in sys.modules:
                spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("action_ai_local_embedding_v0001.py"))
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(name, None)
                    raise ConfigError("local_embedding_executor_unavailable") from None
            module = sys.modules[name]
        data = resolved["descriptor"]
        descriptor = module.EmbeddingModel(resolved["path"], data["device"], data["dimension"], data["max_texts"], data["max_text_chars"],
                                           dtype=data.get("dtype", "float32"), encode_batch_size=data.get("encode_batch_size", 1), truncate_dim=data.get("truncate_dim"))
        try:
            return module.LocalEmbeddingExecutor().execute(descriptor, payload)
        except module.LocalEmbeddingError as exc:
            raise ControlError(str(exc)) from None

    def _finish(self, call_id: str, state: str, error: str | None, usage: dict, started: float):
        with self._db(True) as conn:
            conn.execute("UPDATE calls SET state=?,finished_at=?,error_code=?,usage=?,duration_ms=? WHERE call_id=?",
                         (state, time.time(), error, _json(usage), (time.monotonic()-started)*1000, call_id))

    def calls(self, *, after: float = 0, limit: int = 100) -> list:
        if type(limit) is not int or not 1 <= limit <= 1000 or type(after) not in (int,float):
            raise DataError("invalid_page")
        with self._db() as conn:
            return [dict(x) for x in conn.execute("SELECT * FROM calls WHERE started_at>? ORDER BY started_at,call_id LIMIT ?", (after,limit))]


class ApplicationClient:
    """Per-application handle: exposes no global or per-socket permission setter."""

    def __init__(self, controller: AIController, app_id: str, token: str):
        self._controller = controller
        self.app_id = app_id
        self._token = token

    def select(self, provider: str, model: str, profile: str = "default", *, dry_run: bool = False):
        return self._controller._set_application(self.app_id, self._token,
                                                selection=Selection(provider, model, profile), dry_run=dry_run)

    def start(self, *, dry_run: bool = False):
        return self._controller._set_application(self.app_id, self._token, running=True, dry_run=dry_run)

    def pause(self, *, dry_run: bool = False):
        return self._controller._set_application(self.app_id, self._token, running=False, dry_run=dry_run)

    def invoke(self, operation: str, payload: dict, *, dry_run: bool = False):
        return self._controller.invoke(self.app_id, self._token, operation, payload, dry_run=dry_run)

    def models(self, provider: str, *, offset: int = 0, limit: int = 100):
        return self._controller.models(provider, offset=offset, limit=limit)


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser():
    parser = argparse.ArgumentParser(description="Local AI permission and invocation controller")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("command", choices=["init", "status", "models", "register", "global", "permit", "retire", "rotate-token", "select", "start", "pause", "invoke", "calls"])
    parser.add_argument("--app-id")
    parser.add_argument("--enabled", choices=["true", "false"])
    parser.add_argument("--token-env", default="AI_APPLICATION_TOKEN")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--operation", default="chat")
    parser.add_argument("--payload-file", type=Path)
    parser.add_argument("--after", default="")
    parser.add_argument("--limit", type=int, default=100)
    return parser


def main() -> int:
    try:
        args = _build_parser().parse_args()
        with AIController(args.config, root=args.root) as control:
            dry = args.dry_run
            command = args.command
            if command == "init":
                result = control.initialize(dry_run=dry)
            elif command == "status":
                result = control.status(after=args.after, limit=args.limit)
            elif command == "calls":
                result = control.calls(after=float(args.after or 0), limit=args.limit)
            elif command == "models":
                result = control.models(args.provider, offset=int(args.after or 0), limit=args.limit)
            elif command == "register":
                result = control.register(args.app_id, dry_run=dry)
            elif command in {"global", "permit"}:
                if args.enabled is None:
                    raise DataError("enabled_required")
                enabled = args.enabled == "true"
                result = control.set_global(enabled, dry_run=dry) if command == "global" else control.set_permission(args.app_id, enabled, dry_run=dry)
            elif command == "retire":
                result = control.retire(args.app_id, dry_run=dry)
            elif command == "rotate-token":
                if dry:
                    raise DataError("rotate_token_dry_run_unsupported")
                result = control.rotate_token(args.app_id)
            else:
                client = control.client(args.app_id, os.environ.get(args.token_env, ""))
                if command == "select":
                    result = client.select(args.provider, args.model, args.profile, dry_run=dry)
                elif command in {"start", "pause"}:
                    result = getattr(client, command)(dry_run=dry)
                else:
                    if args.payload_file is None:
                        raise DataError("payload_file_required")
                    if args.payload_file.stat().st_size > control.config["max_request_bytes"]:
                        raise DataError("request_too_large")
                    payload = json.loads(args.payload_file.read_text(encoding=DEFAULT_ENCODING))
                    result = client.invoke(args.operation, payload, dry_run=dry)
        print(_json(result))
        return 0
    except ControlError as exc:
        print(_json({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}))
        return 2
    except Exception:
        print(_json({"status": "error", "error_type": "UnexpectedError", "detail": "operation_failed"}))
        return 3



def inventory_file_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    raw = args.inventory_request.read_bytes()
    result = {"schema_version": "workbench.ai-inventory.v1",
              "producer": {"script": SCRIPT_NAME, "version": SCRIPT_VERSION},
              "source_sha256": hashlib.sha256(raw).hexdigest()}
    code = 0
    try:
        if len(raw) > 16384:
            raise DataError("request_too_large")
        req = json.loads(raw)
        result["run_id"] = req.get("run_id")
        if (req.get("schema_version") != result["schema_version"]
                or req.get("operation") != "inventory"
                or req.get("producer") not in ({"script": "workbench_ai_inventory_v0001", "version": "v0001"}, {"script": "workbench_ai_inventory_v0002", "version": "v0002"}, {"script": "workbench_ai_inventory_v0003", "version": "v0003"})
                or not re.fullmatch(r"ai_[a-f0-9]{32}", str(req.get("run_id", "")))):
            raise DataError("inventory_contract")
        config_path = _path(args.root.resolve(), req["config"])
        with AIController(config_path, root=args.root) as control:
            result.update(status="completed", result=control.inventory())
    except Exception as exc:
        code = 2
        result.update(status="failed", error=str(exc) if isinstance(exc, ControlError) else "inventory_unavailable")
    args.response.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.response.with_suffix(".tmp")
    temporary.write_text(_json(result), encoding=DEFAULT_ENCODING)
    temporary.replace(args.response)
    return code


# ============================================================
# Task application file boundary: own identity, existing admission and transport
# ============================================================
def task_file_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-request', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    raw = args.task_request.read_bytes()
    result = {'schema_version': 'workbench.task.ai.v1', 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION},
              'source_sha256': hashlib.sha256(raw).hexdigest()}
    code = 0
    try:
        req = json.loads(raw)
        result['run_id'] = req.get('run_id')
        if (len(raw) > 500000 or req.get('schema_version') != result['schema_version']
                or req.get('producer') not in ({'script': 'workbench_tasks_v0002', 'version': 'v0002'},
                                             {'script': 'workbench_task_worker_v0002', 'version': 'v0002'})
                or not re.fullmatch(r'task_[a-f0-9]{32}(?:_[a-z0-9]+)?', str(req.get('run_id', '')))):
            raise DataError('task_contract_invalid')
        root = args.root.resolve()
        identity = root / 'config/action/apps/workbench_tasks.json'
        operation = req.get('operation')
        if operation in {'provision', 'permission'} and req['producer']['script'] != 'workbench_tasks_v0002':
            raise PermissionDenied('task_worker_cannot_grant_permission')
        with AIController(_path(root, req['config']), root=root) as control:
            if operation == 'provision':
                registered = control.register('workbench_tasks')
                if registered.get('token'):
                    identity.parent.mkdir(parents=True, exist_ok=True)
                    temporary = identity.with_suffix('.tmp')
                    temporary.write_text(_json(registered), encoding=DEFAULT_ENCODING)
                    temporary.replace(identity)
                if not identity.is_file():
                    raise ConfigError('task_identity_missing_admin_repair_required')
                result['result'] = {'registered': True, 'permission_unchanged': True}
            elif operation == 'permission':
                if type(req.get('allowed')) is not bool or not identity.is_file():
                    raise DataError('task_identity_or_permission_invalid')
                result['result'] = control.set_permission('workbench_tasks', req['allowed'])
            elif operation == 'invoke':
                if not identity.is_file():
                    raise PermissionDenied('task_application_not_registered')
                credential = json.loads(identity.read_text(encoding=DEFAULT_ENCODING))
                if credential.get('app_id') != 'workbench_tasks':
                    raise PermissionDenied('task_identity_invalid')
                selected = req['selection']
                client = control.client('workbench_tasks', credential['token'])
                client.select(selected['provider'], selected['model'], selected.get('profile', 'default'))
                client.start()  # Intent only. Never grants global or application permission.
                selection = Selection(selected['provider'], selected['model'], selected.get('profile', 'default'))
                with control._db() as conn:
                    control._admit(conn, 'workbench_tasks', credential['token'])
                resolved = control._resolve(selection)
                messages = req['messages']
                if (not isinstance(messages, list) or not 1 <= len(messages) <= 30
                        or any(not isinstance(m, dict) or m.get('role') not in {'system', 'user', 'assistant'}
                               or not isinstance(m.get('content'), str) for m in messages)):
                    raise DataError('task_messages_invalid')
                limit = req.get('max_output_tokens', 2048)
                if type(limit) is not int or not 256 <= limit <= 8192:
                    raise DataError('task_output_budget_invalid')
                protocol = resolved['protocol']
                system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
                chat = [m for m in messages if m['role'] != 'system']
                if protocol.startswith('anthropic'):
                    op, payload = 'messages', {'system': system, 'messages': chat, 'max_tokens': limit}
                elif protocol == 'google_genai':
                    op, payload = 'generate_content', {'systemInstruction': {'parts': [{'text': system}]},
                        'contents': [{'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': [{'text': m['content']}]} for m in chat],
                        'generationConfig': {'maxOutputTokens': limit}}
                elif protocol == 'openai_responses':
                    op, payload = 'responses', {'input': messages, 'max_output_tokens': limit}
                elif protocol == 'ollama_native':
                    op, payload = 'chat', {'messages': messages, 'options': {'num_predict': limit}}
                else:
                    op, payload = 'chat', {'messages': messages}
                    if not resolved.get('executor'):
                        payload['max_completion_tokens' if selection.model.startswith(('gpt-5', 'gpt-6', 'o1', 'o3', 'o4')) else 'max_tokens'] = limit
                output = client.invoke(op, payload)
                data = output['data']
                if op == 'messages':
                    content = '\n'.join(x.get('text', '') for x in data.get('content', []) if x.get('type') == 'text')
                    finished = data.get('stop_reason') not in {'max_tokens', 'tool_use'}
                elif op == 'generate_content':
                    candidate = (data.get('candidates') or [{}])[0]
                    content = '\n'.join(x.get('text', '') for x in candidate.get('content', {}).get('parts', []))
                    finished = candidate.get('finishReason') == 'STOP'
                elif op == 'responses':
                    content = '\n'.join(p.get('text', '') for item in data.get('output', []) for p in item.get('content', []) if p.get('type') == 'output_text')
                    finished = data.get('status') == 'completed'
                elif resolved.get('local'):
                    content = data.get('message', {}).get('content', '')
                    finished = data.get('done') is True and data.get('done_reason') != 'length'
                else:
                    choice = (data.get('choices') or [{}])[0]
                    content = choice.get('message', {}).get('content', '')
                    finished = choice.get('finish_reason') in {'stop', 'end_turn'}
                if not finished or not isinstance(content, str) or not content.strip():
                    raise ControlError('task_result_incomplete_or_tool_request')
                result['result'] = {'text': content, 'call_id': output['call_id'], 'usage': output['usage'],
                                    'provider': selection.provider, 'model': selection.model}
            else:
                raise DataError('unknown_task_operation')
        result['status'] = 'completed'
    except Exception as exc:
        code = 2
        result.update(status='failed', error=str(exc) if isinstance(exc, ControlError) else 'task_controller_failed')
    args.response.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.response.with_suffix('.tmp')
    temporary.write_text(_json(result), encoding=DEFAULT_ENCODING)
    temporary.replace(args.response)
    return code


if __name__ == '__main__':
    raise SystemExit(task_file_main() if '--task-request' in sys.argv else inventory_file_main() if '--inventory-request' in sys.argv else main())

