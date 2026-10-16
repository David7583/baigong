# ============================================================
# 文件名: action_chat_retrieval_v0001.py
# 中文名: 新版独立 Action Chat 只读检索扩展脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / independent_action_chat / retrieval
# 脚本定位: Chat Service 可插拔 RetrievalExtension 与四库只读适配器实现
#
# 职责说明:
# - 提供配置驱动的后端注册表、探测、Query Plan、基础管线和合并器
# - 固定执行 Chroma 语义召回后同轮 SQLite 原文回查的基础顺序
#
# 本脚本做什么:
# - 实现 Chroma、SQLite、DuckDB、Neo4j 只读 Adapter
# - 隔离后端错误并输出可追溯检索上下文、执行记录和结果引用
#
# 本脚本不做什么:
# - 不创建、迁移、修复或写入数据库，不执行任意 SQL/Cypher
# - 不把检索结果写回长期记忆或总系统正式数据区
#
# 制度边界声明:
# - off 模式不实例化 Adapter、不加载 BGE-M3、不连接数据库
# - 基础管线失败时禁止 SQLite 补充和全部附加后端，普通聊天继续
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_retrieval_v0001
# family: action_chat_retrieval
# role: readonly_retrieval_extension
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_retrieval_v0001.py
# input:
#   - validated retrieval config, user message and Router decision
# output:
#   - RetrievalOutcome with plan, status, executions, context and references
# depends_on:
#   - Python stdlib: abc, concurrent.futures, importlib, os, pathlib, sqlite3, time, uuid
#   - optional: chromadb, sentence-transformers, duckdb, neo4j
#   - action_chat_config_v0001.py
#   - action_chat_contracts_v0001.py
# used_by:
#   - action_chat_service_v0001.py
#   - check_retrieval_backends_v0001.py
# ============================================================

from __future__ import annotations

import importlib.util
import os
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Type

from .action_chat_config_v0001 import RetrievalConfig, resolve_project_path
from .action_chat_contracts_v0001 import (
    BackendResult,
    BackendStatus,
    ContractError,
    QueryPlan,
    RETRIEVAL_MODES,
    RetrievalError,
    RetrievalOutcome,
    SecurityError,
    classified_error,
    sha256_json,
    sha256_text,
    validate_backend_result,
    validate_identifier,
)


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_retrieval"
SCRIPT_NAME = "action_chat_retrieval_v0001"
SCRIPT_VERSION = "v0001"

REQUIRED_PIPELINE = ["chroma", "sqlite"]
OPTIONAL_BACKENDS = {"sqlite_supplement", "duckdb", "neo4j"}


# ============================================================
# 异常类型
# ============================================================

class BackendUnavailableError(RetrievalError):
    """Raised when a configured backend is not query-ready."""


class QueryValidationError(SecurityError):
    """Raised when a query does not match an allowed read-only template."""


class BaselineOrderError(RetrievalError):
    """Raised when the Chroma plus SQLite transaction order is violated."""


# ============================================================
# 数据结构
# ============================================================

# Shared BackendStatus, BackendResult, QueryPlan and RetrievalOutcome live in contracts.


# ============================================================
# 工具函数区
# ============================================================

def _duration_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _error_result(backend: str, query_type: str, started: float, exc: BaseException) -> BackendResult:
    return BackendResult(backend, query_type, "error", _duration_ms(started), 0, [], {}, classified_error(exc))


def _skip_result(backend: str, query_type: str, reason: str) -> BackendResult:
    return BackendResult(backend, query_type, "skipped", 0, 0, [], {"reason": reason}, None)


def _object_key(item: Dict[str, Any]) -> str:
    for key in ("stable_object_id", "instance_id", "object_id", "unit_text_id", "source_hash", "content_hash"):
        value = item.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    return "content_hash:" + sha256_text(str(item.get("content", item)))


def _safe_items(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise QueryValidationError("items must be a list")
    if not all(isinstance(item, dict) for item in value):
        raise QueryValidationError("every item must be an object")
    return [dict(item) for item in value]


def _read_only_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


# ============================================================
# 默认映射
# ============================================================

DEFAULT_LIMITS = {
    "top_k": (1, 100),
    "distance_threshold": (0.0, 2.0),
    "result_limit": (1, 500),
    "max_depth": (1, 5),
    "timeout_ms": (100, 120000),
}


# ============================================================
# 核心类
# ============================================================

class BackendAdapter(ABC):
    backend_name = "base"

    def __init__(self, config: Dict[str, Any], project_root: Path):
        self.config = dict(config)
        self.project_root = project_root.resolve()

    @abstractmethod
    def probe(self) -> BackendStatus:
        raise NotImplementedError

    @abstractmethod
    def validate_query(self, request: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def query(self, request: Dict[str, Any]) -> BackendResult:
        raise NotImplementedError

    def normalize(self, raw_result: BackendResult) -> BackendResult:
        return validate_backend_result(raw_result)

    def close(self) -> None:
        return None


class ChromaAdapter(BackendAdapter):
    backend_name = "chroma"

    def __init__(self, config: Dict[str, Any], project_root: Path):
        super().__init__(config, project_root)
        self._model = None
        self._collection = None

    def probe(self) -> BackendStatus:
        started = time.perf_counter()
        configured = bool(self.config.get("enabled") and self.config.get("path") and self.config.get("collection"))
        dependencies = _module_available("chromadb") and _module_available("sentence_transformers")
        path = resolve_project_path(self.project_root, str(self.config.get("path", ".")))
        reachable = path.is_dir()
        model_cache = self.config.get("embedding_model_cache_path")
        model_available = bool(model_cache and resolve_project_path(self.project_root, str(model_cache)).exists())
        count: Optional[int] = None
        schema_compatible = False
        message = "not configured"
        if configured and not dependencies:
            message = "missing chromadb or sentence-transformers dependency"
        elif configured and not reachable:
            message = "Chroma path not found"
        elif configured:
            try:
                metadata_db = path / "chroma.sqlite3"
                if not metadata_db.is_file():
                    message = "Chroma metadata database not found"
                else:
                    connection = sqlite3.connect(_read_only_uri(metadata_db), uri=True, timeout=5)
                    try:
                        connection.execute("PRAGMA query_only = ON")
                        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        schema_compatible = {"collections", "embeddings"}.issubset(tables)
                        collection_row = connection.execute("SELECT id FROM collections WHERE name = ? LIMIT 1", (str(self.config["collection"]),)).fetchone() if schema_compatible else None
                        if collection_row is not None:
                            count = int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
                            message = "available (read-only metadata probe)"
                        else:
                            schema_compatible = False
                            message = "configured Chroma collection not found"
                    finally:
                        connection.close()
            except Exception as exc:
                message = f"probe failed: {type(exc).__name__}"
        if configured and schema_compatible and not model_available and self.config.get("model_local_only", True):
            message = "local BGE-M3 cache not found; database metadata is readable"
        ready = bool(configured and dependencies and reachable and schema_compatible and (model_available or not self.config.get("model_local_only", True)))
        return BackendStatus("chroma", configured, reachable, schema_compatible, bool(count), ready, count, _duration_ms(started), message, dependencies)

    def validate_query(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if request.get("query_type") != "semantic":
            raise QueryValidationError("Chroma only accepts semantic query template")
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            raise QueryValidationError("Chroma query text is required")
        top_k = int(request.get("top_k", self.config.get("top_k", 10)))
        threshold = float(request.get("distance_threshold", self.config.get("distance_threshold", 0.5)))
        if not 1 <= top_k <= 100 or not 0.0 <= threshold <= 2.0:
            raise QueryValidationError("Chroma parameters are outside safe bounds")
        return {**request, "text": text.strip(), "top_k": top_k, "distance_threshold": threshold}

    def _ensure_runtime(self) -> None:
        if self._model is not None and self._collection is not None:
            return
        if not _module_available("chromadb") or not _module_available("sentence_transformers"):
            raise BackendUnavailableError("Chroma dependencies are unavailable")
        import chromadb
        from sentence_transformers import SentenceTransformer

        path = resolve_project_path(self.project_root, str(self.config["path"]), must_exist=True)
        model_source = self.config.get("embedding_model_cache_path") or self.config.get("embedding_model")
        self._model = SentenceTransformer(
            str(resolve_project_path(self.project_root, str(model_source))) if self.config.get("embedding_model_cache_path") else str(model_source),
            device=str(self.config.get("device", "cpu")),
            local_files_only=bool(self.config.get("model_local_only", True)),
        )
        self._collection = chromadb.PersistentClient(path=str(path)).get_collection(str(self.config["collection"]))

    def query(self, request: Dict[str, Any]) -> BackendResult:
        started = time.perf_counter()
        try:
            checked = self.validate_query(request)
            self._ensure_runtime()
            vector = self._model.encode([checked["text"]], normalize_embeddings=True)[0].tolist()
            raw = self._collection.query(query_embeddings=[vector], n_results=checked["top_k"], include=["metadatas", "documents", "distances"])
            ids = (raw.get("ids") or [[]])[0]
            metadata = (raw.get("metadatas") or [[]])[0]
            documents = (raw.get("documents") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]
            items: List[Dict[str, Any]] = []
            for rank, object_id in enumerate(ids, start=1):
                distance = float(distances[rank - 1]) if rank - 1 < len(distances) else 0.0
                if distance > checked["distance_threshold"]:
                    continue
                meta = metadata[rank - 1] if rank - 1 < len(metadata) and isinstance(metadata[rank - 1], dict) else {}
                items.append({
                    "stable_object_id": meta.get("instance_id") or meta.get("object_id") or object_id,
                    "instance_id": meta.get("instance_id") or object_id,
                    "content": documents[rank - 1] if rank - 1 < len(documents) else "",
                    "distance": distance,
                    "rank": rank,
                    "metadata": meta,
                })
            return self.normalize(BackendResult("chroma", "semantic", "ok", _duration_ms(started), len(items), items, {"collection": self.config["collection"]}))
        except Exception as exc:
            return _error_result("chroma", "semantic", started, exc)


class SQLiteAdapter(BackendAdapter):
    backend_name = "sqlite"

    def _path(self) -> Path:
        return resolve_project_path(self.project_root, str(self.config.get("path", ".")))

    def _mapping(self) -> Dict[str, str]:
        mapping = self.config.get("mapping", {})
        if not isinstance(mapping, dict):
            raise QueryValidationError("SQLite mapping must be an object")
        required = ("table", "id_field", "content_field")
        return {key: validate_identifier(str(mapping.get(key, "")), f"sqlite.mapping.{key}") for key in required}

    def _connect(self) -> sqlite3.Connection:
        if self.config.get("read_only") is not True:
            raise SecurityError("SQLite read_only=true is required")
        path = self._path()
        if not path.is_file():
            raise BackendUnavailableError("SQLite database file not found")
        connection = sqlite3.connect(_read_only_uri(path), uri=True, timeout=max(0.1, int(self.config.get("timeout_ms", 5000)) / 1000))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def probe(self) -> BackendStatus:
        started = time.perf_counter()
        configured = bool(self.config.get("enabled") and self.config.get("path") and self.config.get("read_only") is True)
        path = self._path()
        reachable = path.is_file()
        compatible = False
        count: Optional[int] = None
        message = "not configured"
        if configured and reachable:
            try:
                mapping = self._mapping()
                with self._connect() as connection:
                    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({mapping['table']})")}
                    compatible = {mapping["id_field"], mapping["content_field"]}.issubset(columns)
                    if compatible:
                        count = int(connection.execute(f"SELECT COUNT(*) FROM {mapping['table']}").fetchone()[0])
                        message = "available"
                    else:
                        message = "schema incompatible"
            except Exception as exc:
                message = f"probe failed: {type(exc).__name__}"
        elif configured:
            message = "SQLite database file not found"
        return BackendStatus("sqlite", configured, reachable, compatible, bool(count), bool(configured and reachable and compatible), count, _duration_ms(started), message)

    def validate_query(self, request: Dict[str, Any]) -> Dict[str, Any]:
        query_type = request.get("query_type")
        if query_type not in {"lookup_ids", "supplement"}:
            raise QueryValidationError("SQLite query_type is not allowed")
        checked = dict(request)
        if query_type == "lookup_ids":
            ids = request.get("ids")
            if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
                raise QueryValidationError("SQLite lookup_ids requires string ids")
            checked["ids"] = list(dict.fromkeys(ids))[:500]
        else:
            if request.get("baseline_completed") is not True:
                raise BaselineOrderError("SQLite supplement requires baseline_completed in the same transaction")
            template_id = request.get("template_id")
            if template_id not in {"content_contains", "exact_by_object_type"}:
                raise QueryValidationError("SQLite supplement template is not allowed")
            value = request.get("value")
            if not isinstance(value, str) or not value.strip():
                raise QueryValidationError("SQLite supplement template value is required")
            checked["value"] = value.strip()[:500]
        return checked

    def query(self, request: Dict[str, Any]) -> BackendResult:
        started = time.perf_counter()
        query_type = str(request.get("query_type", "unknown"))
        try:
            checked = self.validate_query(request)
            mapping = self._mapping()
            table, id_field, content_field = mapping["table"], mapping["id_field"], mapping["content_field"]
            limit = min(500, max(1, int(request.get("limit", self.config.get("result_limit", 50)))))
            with self._connect() as connection:
                if checked["query_type"] == "lookup_ids":
                    if not checked["ids"]:
                        rows = []
                    else:
                        placeholders = ",".join("?" for _ in checked["ids"])
                        rows = connection.execute(
                            f"SELECT {id_field}, {content_field} FROM {table} WHERE {id_field} IN ({placeholders}) LIMIT ?",
                            (*checked["ids"], limit),
                        ).fetchall()
                elif checked["template_id"] == "content_contains":
                    rows = connection.execute(
                        f"SELECT {id_field}, {content_field} FROM {table} WHERE {content_field} LIKE ? LIMIT ?",
                        (f"%{checked['value']}%", limit),
                    ).fetchall()
                else:
                    object_type_field = validate_identifier(str(self.config.get("mapping", {}).get("object_type_field", "object_type")), "sqlite.mapping.object_type_field")
                    rows = connection.execute(
                        f"SELECT {id_field}, {content_field} FROM {table} WHERE {object_type_field} = ? LIMIT ?",
                        (checked["value"], limit),
                    ).fetchall()
            items = [{"stable_object_id": str(row[0]), "instance_id": str(row[0]), "content": str(row[1])} for row in rows]
            return self.normalize(BackendResult("sqlite", checked["query_type"], "ok", _duration_ms(started), len(items), items, {"template": checked.get("template_id", "lookup_ids")}))
        except Exception as exc:
            return _error_result("sqlite", query_type, started, exc)


class DuckDBAdapter(BackendAdapter):
    backend_name = "duckdb"

    def _path(self) -> Path:
        return resolve_project_path(self.project_root, str(self.config.get("path", ".")))

    def probe(self) -> BackendStatus:
        started = time.perf_counter()
        dependency = _module_available("duckdb")
        configured = bool(self.config.get("enabled") and self.config.get("path") and self.config.get("read_only") is True)
        reachable = self._path().is_file()
        compatible = False
        count: Optional[int] = None
        message = "not configured"
        if configured and not dependency:
            message = "missing duckdb dependency"
        elif configured and not reachable:
            message = "DuckDB file not found"
        elif configured:
            try:
                import duckdb

                table = validate_identifier(str(self.config.get("table", "")), "duckdb.table")
                connection = duckdb.connect(str(self._path()), read_only=True)
                try:
                    count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    compatible = True
                    message = "available"
                finally:
                    connection.close()
            except Exception as exc:
                message = f"probe failed: {type(exc).__name__}"
        return BackendStatus("duckdb", configured, reachable, compatible, bool(count), bool(configured and dependency and reachable and compatible), count, _duration_ms(started), message, dependency)

    def validate_query(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if request.get("query_type") != "aggregate" or request.get("template_id") != "counts_by_type":
            raise QueryValidationError("DuckDB only accepts counts_by_type aggregate template")
        limit = min(500, max(1, int(request.get("limit", self.config.get("result_limit", 50)))))
        return {**request, "limit": limit}

    def query(self, request: Dict[str, Any]) -> BackendResult:
        started = time.perf_counter()
        try:
            checked = self.validate_query(request)
            if self.config.get("read_only") is not True:
                raise SecurityError("DuckDB read_only=true is required")
            import duckdb

            table = validate_identifier(str(self.config.get("table", "")), "duckdb.table")
            group_field = validate_identifier(str(self.config.get("group_field", "object_type")), "duckdb.group_field")
            connection = duckdb.connect(str(self._path()), read_only=True)
            try:
                rows = connection.execute(
                    f"SELECT {group_field}, COUNT(*) AS item_count FROM {table} GROUP BY {group_field} ORDER BY item_count DESC LIMIT ?",
                    [checked["limit"]],
                ).fetchall()
            finally:
                connection.close()
            items = [{"stable_object_id": f"aggregate:{row[0]}", "group": row[0], "count": int(row[1]), "content": f"{row[0]}: {row[1]}"} for row in rows]
            return self.normalize(BackendResult("duckdb", "aggregate", "ok", _duration_ms(started), len(items), items, {"template": "counts_by_type"}))
        except Exception as exc:
            return _error_result("duckdb", "aggregate", started, exc)


class Neo4jAdapter(BackendAdapter):
    backend_name = "neo4j"

    def _credentials(self) -> Dict[str, str]:
        return {
            "uri": os.environ.get(str(self.config.get("uri_env", "NEO4J_URI")), "").strip(),
            "user": os.environ.get(str(self.config.get("user_env", "NEO4J_USER")), "").strip(),
            "password": os.environ.get(str(self.config.get("password_env", "NEO4J_PASSWORD")), "").strip(),
        }

    def probe(self) -> BackendStatus:
        started = time.perf_counter()
        dependency = _module_available("neo4j")
        credentials = self._credentials()
        configured = bool(self.config.get("enabled") and all(credentials.values()) and self.config.get("read_only") is True)
        reachable = False
        compatible = False
        count: Optional[int] = None
        message = "not configured or credentials missing"
        if configured and not dependency:
            message = "missing neo4j dependency"
        elif configured:
            try:
                from neo4j import GraphDatabase, READ_ACCESS

                driver = GraphDatabase.driver(credentials["uri"], auth=(credentials["user"], credentials["password"]), connection_timeout=max(1, int(self.config.get("timeout_ms", 5000)) // 1000))
                try:
                    driver.verify_connectivity()
                    reachable = True
                    label = validate_identifier(str(self.config.get("label", "StructuralUnit")), "neo4j.label")
                    with driver.session(database=str(self.config.get("database", "neo4j")), default_access_mode=READ_ACCESS) as session:
                        count = int(session.run(f"MATCH (n:{label}) RETURN count(n) AS count").single()["count"])
                    compatible = True
                    message = "available"
                finally:
                    driver.close()
            except Exception as exc:
                message = f"probe failed: {type(exc).__name__}"
        return BackendStatus("neo4j", configured, reachable, compatible, bool(count), bool(configured and dependency and reachable and compatible), count, _duration_ms(started), message, dependency)

    def validate_query(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if request.get("query_type") != "graph" or request.get("template_id") != "neighbors_by_ids":
            raise QueryValidationError("Neo4j only accepts neighbors_by_ids graph template")
        ids = request.get("ids")
        if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
            raise QueryValidationError("Neo4j neighbors query requires string ids")
        depth = int(request.get("max_depth", self.config.get("max_depth", 2)))
        limit = min(200, max(1, int(request.get("limit", self.config.get("result_limit", 20)))))
        if not 1 <= depth <= 5:
            raise QueryValidationError("Neo4j max_depth is outside safe bounds")
        return {**request, "ids": list(dict.fromkeys(ids))[:200], "max_depth": depth, "limit": limit}

    def query(self, request: Dict[str, Any]) -> BackendResult:
        started = time.perf_counter()
        try:
            checked = self.validate_query(request)
            if self.config.get("read_only") is not True:
                raise SecurityError("Neo4j read_only=true is required")
            credentials = self._credentials()
            if not all(credentials.values()):
                raise BackendUnavailableError("Neo4j credentials are unavailable")
            from neo4j import GraphDatabase, READ_ACCESS

            label = validate_identifier(str(self.config.get("label", "StructuralUnit")), "neo4j.label")
            id_field = validate_identifier(str(self.config.get("id_field", "unit_id")), "neo4j.id_field")
            depth = checked["max_depth"]
            cypher = f"MATCH (n:{label}) WHERE n.{id_field} IN $ids MATCH p=(n)-[*1..{depth}]-(m) RETURN n.{id_field} AS source_id, m.{id_field} AS target_id, labels(m) AS labels LIMIT $limit"
            driver = GraphDatabase.driver(credentials["uri"], auth=(credentials["user"], credentials["password"]), connection_timeout=max(1, int(self.config.get("timeout_ms", 5000)) // 1000))
            try:
                with driver.session(database=str(self.config.get("database", "neo4j")), default_access_mode=READ_ACCESS) as session:
                    records = list(session.run(cypher, ids=checked["ids"], limit=checked["limit"]))
            finally:
                driver.close()
            items = [{"stable_object_id": f"graph:{row['source_id']}:{row['target_id']}", "source_id": row["source_id"], "target_id": row["target_id"], "labels": row["labels"], "content": f"{row['source_id']} -> {row['target_id']}"} for row in records]
            return self.normalize(BackendResult("neo4j", "graph", "ok", _duration_ms(started), len(items), items, {"template": "neighbors_by_ids", "max_depth": depth}))
        except Exception as exc:
            return _error_result("neo4j", "graph", started, exc)


class BackendRegistry:
    def __init__(self, configs: Dict[str, Dict[str, Any]], project_root: Path):
        self.configs = {name: dict(config) for name, config in configs.items()}
        self.project_root = project_root.resolve()
        self.adapter_types: Dict[str, Type[BackendAdapter]] = dict(DEFAULT_ADAPTER_TYPES)
        self.instances: Dict[str, BackendAdapter] = {}

    def register_adapter_type(self, adapter_name: str, adapter_type: Type[BackendAdapter]) -> None:
        if not adapter_name or not issubclass(adapter_type, BackendAdapter):
            raise RetrievalError("invalid backend adapter registration")
        self.adapter_types[adapter_name] = adapter_type

    def get(self, backend: str) -> BackendAdapter:
        physical = "sqlite" if backend == "sqlite_supplement" else backend
        if physical not in self.configs:
            raise BackendUnavailableError(f"backend is not configured: {physical}")
        if physical not in self.instances:
            config = self.configs[physical]
            adapter_name = str(config.get("adapter", ""))
            adapter_type = self.adapter_types.get(adapter_name)
            if adapter_type is None:
                raise BackendUnavailableError(f"adapter type is not registered: {adapter_name}")
            self.instances[physical] = adapter_type(config, self.project_root)
        return self.instances[physical]

    def probe_all(self) -> Dict[str, BackendStatus]:
        return {name: self.get(name).probe() for name in self.configs}

    def close(self) -> None:
        for adapter in self.instances.values():
            adapter.close()


class QueryPlanValidator:
    def __init__(self, allowed_backends: Iterable[str], parameters: Dict[str, Dict[str, Any]]):
        self.allowed_backends = set(allowed_backends)
        self.parameters = parameters

    def build(self, mode: str, router_decision: Dict[str, Any]) -> QueryPlan:
        if mode not in RETRIEVAL_MODES:
            raise QueryValidationError(f"invalid retrieval mode: {mode}")
        need = mode == "always" or (mode == "router" and bool(router_decision.get("need_retrieval")))
        optional = sorted(self.allowed_backends) if mode == "always" else [item for item in router_decision.get("optional_backends", []) if item in self.allowed_backends]
        raw = {
            "retrieval_transaction_id": f"retr_{uuid.uuid4().hex}",
            "mode": mode,
            "need_retrieval": need,
            "required_pipeline": list(REQUIRED_PIPELINE) if need else [],
            "optional_backends": optional if need else [],
            "parameters": self.parameters,
            "reason": "always 模式确定性计划" if mode == "always" else str(router_decision.get("reason", "Router 未要求检索")),
        }
        return self.validate(raw)

    def validate(self, raw: Dict[str, Any]) -> QueryPlan:
        need = bool(raw.get("need_retrieval"))
        required = raw.get("required_pipeline", [])
        if need and required != REQUIRED_PIPELINE:
            raise QueryValidationError("retrieval requires ordered Chroma plus SQLite baseline")
        if not need and required:
            raise QueryValidationError("non-retrieval plan cannot include required pipeline")
        optional = raw.get("optional_backends", [])
        if not isinstance(optional, list) or any(item not in OPTIONAL_BACKENDS for item in optional):
            raise QueryValidationError("query plan contains unknown optional backend")
        corrections: List[str] = []
        filtered = []
        for item in optional:
            if item in self.allowed_backends and item not in filtered:
                filtered.append(item)
            elif item not in self.allowed_backends:
                corrections.append(f"removed_not_allowed:{item}")
        mode = str(raw.get("mode", ""))
        if mode not in RETRIEVAL_MODES:
            raise QueryValidationError("query plan mode is invalid")
        parameters = raw.get("parameters", {})
        if not isinstance(parameters, dict):
            raise QueryValidationError("query plan parameters must be an object")
        chroma = parameters.get("chroma", {})
        top_k = int(chroma.get("top_k", 10))
        threshold = float(chroma.get("distance_threshold", 0.5))
        if not 1 <= top_k <= 100 or not 0.0 <= threshold <= 2.0:
            raise QueryValidationError("query plan Chroma parameters are unsafe")
        return QueryPlan(
            retrieval_transaction_id=str(raw.get("retrieval_transaction_id", f"retr_{uuid.uuid4().hex}")),
            mode=mode,
            need_retrieval=need,
            required_pipeline=list(required),
            optional_backends=filtered,
            parameters={key: dict(value) for key, value in parameters.items() if isinstance(value, dict)},
            reason=str(raw.get("reason", "")),
            corrections=corrections,
        )


class ResultMerger:
    def merge(self, executions: List[BackendResult], max_context_chars: int) -> tuple[List[Dict[str, Any]], str, List[Dict[str, Any]], Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        original_count = 0
        for result in executions:
            if result.status != "ok":
                continue
            for item in result.items:
                original_count += 1
                key = _object_key(item)
                evidence = {"backend": result.backend, "query_type": result.query_type, "rank": item.get("rank"), "distance": item.get("distance")}
                if key not in grouped:
                    grouped[key] = {**item, "context_owner": True, "evidence": [evidence]}
                else:
                    grouped[key]["evidence"].append(evidence)
        merged: List[Dict[str, Any]] = []
        context_parts: List[str] = []
        references: List[Dict[str, Any]] = []
        used = 0
        for key, item in grouped.items():
            content = str(item.get("content", ""))
            prefix = f"[{len(merged) + 1}] {key}\n"
            remaining = max_context_chars - used
            if remaining <= 0:
                break
            entry = (prefix + content)[:remaining]
            context_parts.append(entry)
            used += len(entry)
            merged.append(item)
            references.append({"object_key": key, "evidence": item["evidence"], "content_hash": sha256_text(content)})
        record = {
            "input_item_count": original_count,
            "unique_item_count": len(grouped),
            "used_item_count": len(merged),
            "context_chars": used,
            "max_context_chars": max_context_chars,
            "truncated": len(merged) < len(grouped),
        }
        return merged, "\n\n".join(context_parts), references, record


class RetrievalManager:
    def __init__(self, config: RetrievalConfig):
        self.config = config
        self._base_config_hash = config.config_hash
        retrieval = config.retrieval
        self.mode = str(retrieval["default_mode"])
        self.allowed_backends = list(retrieval.get("allowed_backends", []))
        self.parameters = {key: dict(value) for key, value in retrieval.get("parameters", {}).items() if isinstance(value, dict)}
        self.registry = BackendRegistry(retrieval["backends"], config.project_root)
        self._status_cache: Dict[str, BackendStatus] = {}
        self.merger = ResultMerger()
        self._refresh_hash()

    def _refresh_hash(self) -> None:
        self.config_hash = sha256_json({
            "base": self._base_config_hash,
            "mode": self.mode,
            "allowed_backends": self.allowed_backends,
            "parameters": self.parameters,
        })

    def status(self, *, probe: bool = False) -> Dict[str, Any]:
        if probe:
            self._status_cache = self.registry.probe_all()
        statuses = {name: asdict(value) for name, value in self._status_cache.items()}
        return {
            "mode": self.mode,
            "allowed_backends": list(self.allowed_backends),
            "configured_backends": list(self.registry.configs),
            "available_backends": [name for name, value in self._status_cache.items() if value.query_ready],
            "parameters": self.parameters,
            "backend_status": statuses,
            "adapters_instantiated": sorted(self.registry.instances),
        }

    def set_mode(self, mode: str) -> Dict[str, Any]:
        if mode not in RETRIEVAL_MODES:
            raise QueryValidationError(f"invalid retrieval mode: {mode}")
        self.mode = mode
        self._refresh_hash()
        return self.status()

    def update_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        allowed = settings.get("allowed_backends", self.allowed_backends)
        if not isinstance(allowed, list) or any(item not in OPTIONAL_BACKENDS for item in allowed):
            raise QueryValidationError("allowed_backends contains unsupported value")
        parameters = settings.get("parameters", {})
        if not isinstance(parameters, dict):
            raise QueryValidationError("parameters must be an object")
        merged = {key: dict(value) for key, value in self.parameters.items()}
        for backend, values in parameters.items():
            if not isinstance(values, dict):
                raise QueryValidationError(f"parameters.{backend} must be an object")
            merged.setdefault(backend, {}).update(values)
        QueryPlanValidator(allowed, merged).build("always", {})
        self.allowed_backends = list(dict.fromkeys(allowed))
        self.parameters = merged
        self._refresh_hash()
        return self.status()

    def _probe_required(self) -> Dict[str, BackendStatus]:
        for name in REQUIRED_PIPELINE:
            self._status_cache[name] = self.registry.get(name).probe()
        return {name: self._status_cache[name] for name in REQUIRED_PIPELINE}

    def _query_with_timeout(self, backend: str, request: Dict[str, Any]) -> BackendResult:
        physical = "sqlite" if backend == "sqlite_supplement" else backend
        adapter = self.registry.get(backend)
        backend_config = self.registry.configs.get(physical, {})
        timeout_ms = int(request.get("timeout_ms", backend_config.get("timeout_ms", self.config.retrieval["total_timeout_ms"])))
        timeout_ms = min(int(self.config.retrieval["total_timeout_ms"]), max(100, timeout_ms))
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"retrieval-{backend}")
        future = executor.submit(adapter.query, request)
        try:
            return future.result(timeout=timeout_ms / 1000.0)
        except FutureTimeoutError:
            future.cancel()
            adapter.close()
            return BackendResult(
                backend,
                str(request.get("query_type", "unknown")),
                "timeout",
                timeout_ms,
                0,
                [],
                {"timeout_ms": timeout_ms},
                {"error_type": "BackendTimeout", "detail": f"{backend} exceeded {timeout_ms} ms"},
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def process(self, message: str, router_decision: Dict[str, Any]) -> RetrievalOutcome:
        if self.mode == "off":
            return RetrievalOutcome(None, "off", None, {}, [], [], "", [], "not_started", False, {})
        validator = QueryPlanValidator(self.allowed_backends, self.parameters)
        plan = validator.build(self.mode, router_decision)
        plan_map = asdict(plan)
        if not plan.need_retrieval:
            return RetrievalOutcome(plan.retrieval_transaction_id, "skipped_router", plan_map, {}, [], [], "", [], "not_started", False, {})
        required_status = self._probe_required()
        status_map = {name: asdict(value) for name, value in required_status.items()}
        if not all(value.query_ready for value in required_status.values()):
            return RetrievalOutcome(plan.retrieval_transaction_id, "skipped_unavailable", plan_map, status_map, [], [], "", [], "skipped_unavailable", False, {})

        executions: List[BackendResult] = []
        chroma_parameters = plan.parameters.get("chroma", {})
        chroma = self._query_with_timeout("chroma", {"query_type": "semantic", "text": message, **chroma_parameters})
        executions.append(chroma)
        if chroma.status != "ok":
            return self._finish(plan, status_map, executions, "failed_chroma", False)
        ids = [str(item.get("instance_id") or item.get("stable_object_id")) for item in chroma.items if item.get("instance_id") or item.get("stable_object_id")]
        sqlite = self._query_with_timeout("sqlite", {"query_type": "lookup_ids", "ids": ids, "limit": plan.parameters.get("sqlite", {}).get("result_limit", 100)})
        executions.append(sqlite)
        if sqlite.status != "ok":
            return self._finish(plan, status_map, executions, "failed_sqlite", False)

        sqlite_permitted = True
        optional_requests: Dict[str, Dict[str, Any]] = {}
        if "sqlite_supplement" in plan.optional_backends:
            supplement = plan.parameters.get("sqlite_supplement", {})
            optional_requests["sqlite_supplement"] = {
                "query_type": "supplement",
                "template_id": supplement.get("template_id", "content_contains"),
                "value": supplement.get("value") or message,
                "limit": supplement.get("result_limit", 30),
                "baseline_completed": True,
                "retrieval_transaction_id": plan.retrieval_transaction_id,
            }
        if "duckdb" in plan.optional_backends:
            optional_requests["duckdb"] = {
                "query_type": "aggregate",
                "template_id": "counts_by_type",
                "limit": plan.parameters.get("duckdb", {}).get("result_limit", 50),
                "timeout_ms": plan.parameters.get("duckdb", {}).get("timeout_ms", 5000),
            }
        if "neo4j" in plan.optional_backends:
            optional_requests["neo4j"] = {"query_type": "graph", "template_id": "neighbors_by_ids", "ids": ids, **plan.parameters.get("neo4j", {})}

        for name in list(optional_requests):
            physical = "sqlite" if name == "sqlite_supplement" else name
            if physical == "sqlite":
                status = required_status["sqlite"]
            else:
                status = self.registry.get(physical).probe()
                self._status_cache[physical] = status
                status_map[physical] = asdict(status)
            if not status.query_ready:
                executions.append(_skip_result(name, str(optional_requests[name].get("query_type")), "backend not query_ready"))
                del optional_requests[name]

        if optional_requests:
            max_workers = min(4, len(optional_requests))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="retrieval") as executor:
                futures = {executor.submit(self._query_with_timeout, name, request): name for name, request in optional_requests.items()}
                for future, name in futures.items():
                    try:
                        executions.append(future.result())
                    except Exception as exc:
                        executions.append(_error_result(name, str(optional_requests[name].get("query_type")), time.perf_counter(), exc))
        return self._finish(plan, status_map, executions, "completed", sqlite_permitted)

    def _finish(
        self,
        plan: QueryPlan,
        status_map: Dict[str, Dict[str, Any]],
        executions: List[BackendResult],
        baseline_status: str,
        sqlite_permitted: bool,
    ) -> RetrievalOutcome:
        merged, context, refs, record = self.merger.merge(executions, int(self.config.retrieval["max_context_chars"]))
        execution_maps = [asdict(result) for result in executions]
        status = "completed" if baseline_status == "completed" else "degraded"
        return RetrievalOutcome(
            plan.retrieval_transaction_id,
            status,
            asdict(plan),
            status_map,
            execution_maps,
            merged,
            context,
            refs,
            baseline_status,
            sqlite_permitted,
            record,
        )


# ============================================================
# Schema / 契约辅助函数
# ============================================================

DEFAULT_ADAPTER_TYPES: Dict[str, Type[BackendAdapter]] = {
    "chroma": ChromaAdapter,
    "sqlite": SQLiteAdapter,
    "duckdb": DuckDBAdapter,
    "neo4j": Neo4jAdapter,
}
