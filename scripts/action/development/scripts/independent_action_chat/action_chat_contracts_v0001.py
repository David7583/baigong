# ============================================================
# 文件名: action_chat_contracts_v0001.py
# 中文名: 新版独立 Action Chat 公共契约脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / independent_action_chat / contracts
# 脚本定位: Chat、Router、Provider 与检索扩展共享的数据契约边界
#
# 职责说明:
# - 定义稳定的运行结果、路由决策、检索计划和状态结构
# - 提供 UTC 时间、稳定哈希、脱敏和契约校验工具
#
# 本脚本做什么:
# - 将内部对象转换为可留档的 JSON 结构
# - 拒绝字段缺失、类型错误和不安全的运行值
#
# 本脚本不做什么:
# - 不调用模型，不连接数据库，不写入会话文件
#
# 制度边界声明:
# - 公共字段不得静默改义；敏感字段进入日志前必须脱敏
# - 契约失败必须显式抛错，不返回伪成功
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_contracts_v0001
# family: action_chat_contracts
# role: public_contracts
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_contracts_v0001.py
# input:
#   - runtime values
# output:
#   - validated dataclasses and JSON mappings
# depends_on:
#   - Python stdlib: dataclasses, datetime, hashlib, json, re, typing
# used_by:
#   - action_chat_config_v0001.py
#   - action_chat_providers_v0001.py
#   - action_chat_router_v0001.py
#   - action_chat_session_store_v0001.py
#   - action_chat_retrieval_v0001.py
#   - action_chat_service_v0001.py
# ============================================================

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_contracts"
SCRIPT_NAME = "action_chat_contracts_v0001"
SCRIPT_VERSION = "v0001"

ROUTER_MODES = {"off", "machine_only", "hybrid", "ai_only"}
RETRIEVAL_MODES = {"off", "always", "router"}
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE_KEYS = {"authorization", "cookie", "api_key", "api-key", "password", "token", "secret"}


# ============================================================
# 异常类型
# ============================================================

class ActionChatError(RuntimeError):
    """Base error for classified Action Chat failures."""


class ContractError(ActionChatError):
    """Raised when a public object violates its stable contract."""


class ConfigError(ActionChatError):
    """Raised when configuration is missing, unsafe, or incompatible."""


class ProviderError(ActionChatError):
    """Raised for provider validation and invocation failures."""


class RouterError(ActionChatError):
    """Raised for invalid router configuration or output."""


class SessionError(ActionChatError):
    """Raised for session persistence and replay failures."""


class RetrievalError(ActionChatError):
    """Raised for classified retrieval failures."""


class SecurityError(ActionChatError):
    """Raised when an operation violates a read-only or path boundary."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    configured: bool
    available: bool
    message: str
    models: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ChatResult:
    status: str
    provider: str
    model: str
    response_text: str
    usage: Dict[str, int]
    duration_ms: int
    error: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    raw_finish_reason: Optional[str] = None


@dataclass(frozen=True)
class RouterDecision:
    matched: bool
    confidence: float
    ambiguity: bool
    escalate_to_ai: bool
    intent: str
    reason: str
    need_retrieval: bool = False
    optional_backends: List[str] = field(default_factory=list)
    source: str = "machine"
    fallback_reason: Optional[str] = None


@dataclass(frozen=True)
class BackendStatus:
    backend: str
    configured: bool
    reachable: bool
    schema_compatible: bool
    non_empty: bool
    query_ready: bool
    record_count: Optional[int]
    latency_ms: int
    message: str
    dependency_available: bool = True


@dataclass(frozen=True)
class BackendResult:
    backend: str
    query_type: str
    status: str
    duration_ms: int
    result_count: int
    items: List[Dict[str, Any]]
    provenance: Dict[str, Any]
    error: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class QueryPlan:
    retrieval_transaction_id: str
    mode: str
    need_retrieval: bool
    required_pipeline: List[str]
    optional_backends: List[str]
    parameters: Dict[str, Dict[str, Any]]
    reason: str
    corrections: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetrievalOutcome:
    retrieval_transaction_id: Optional[str]
    status: str
    plan: Optional[Dict[str, Any]]
    backend_status: Dict[str, Dict[str, Any]]
    executions: List[Dict[str, Any]]
    merged_items: List[Dict[str, Any]]
    context_text: str
    result_refs: List[Dict[str, Any]]
    baseline_status: str
    sqlite_supplement_permitted: bool
    merge_record: Dict[str, Any]


# ============================================================
# 工具函数区
# ============================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode(DEFAULT_ENCODING)).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode(DEFAULT_ENCODING)).hexdigest()


def as_mapping(value: Any) -> Dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise ContractError(f"cannot convert {type(value).__name__} to mapping")


def require_mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be a mapping")
    return value


def require_non_empty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{name} must be a non-empty string")
    return value.strip()


def validate_identifier(value: str, name: str) -> str:
    value = require_non_empty(value, name)
    if SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SecurityError(f"{name} contains unsafe identifier characters")
    return value


def redact(value: Any, parent_key: str = "") -> Any:
    key_lower = parent_key.lower()
    if any(token in key_lower for token in SENSITIVE_KEYS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(key): redact(item, str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, parent_key) for item in value]
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return "[REDACTED]"
    return value


def classified_error(exc: BaseException) -> Dict[str, Any]:
    return {"error_type": type(exc).__name__, "detail": str(exc)}


# ============================================================
# 默认映射
# ============================================================

DEFAULT_USAGE = {"input_tokens": 0, "output_tokens": 0}


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def validate_router_decision(raw: Dict[str, Any], *, source: str) -> RouterDecision:
    required = {"matched", "confidence", "ambiguity", "escalate_to_ai", "intent", "reason"}
    missing = sorted(required.difference(raw))
    if missing:
        raise ContractError(f"router decision missing fields: {missing}")
    confidence = float(raw["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ContractError("router confidence must be between 0 and 1")
    optional = raw.get("optional_backends", [])
    if not isinstance(optional, list) or not all(isinstance(item, str) for item in optional):
        raise ContractError("optional_backends must be a string list")
    return RouterDecision(
        matched=bool(raw["matched"]),
        confidence=confidence,
        ambiguity=bool(raw["ambiguity"]),
        escalate_to_ai=bool(raw["escalate_to_ai"]),
        intent=require_non_empty(raw["intent"], "intent"),
        reason=require_non_empty(raw["reason"], "reason"),
        need_retrieval=bool(raw.get("need_retrieval", False)),
        optional_backends=list(dict.fromkeys(optional)),
        source=source,
        fallback_reason=raw.get("fallback_reason"),
    )


def validate_chat_result(result: ChatResult) -> ChatResult:
    if result.status not in {"ok", "error"}:
        raise ContractError("chat result status must be ok or error")
    require_non_empty(result.provider, "provider")
    require_non_empty(result.model, "model")
    if result.status == "ok" and not isinstance(result.response_text, str):
        raise ContractError("successful response_text must be a string")
    for key in ("input_tokens", "output_tokens"):
        if int(result.usage.get(key, 0)) < 0:
            raise ContractError(f"usage.{key} must be non-negative")
    return result


def validate_backend_result(result: BackendResult) -> BackendResult:
    if result.status not in {"ok", "error", "skipped", "timeout"}:
        raise ContractError("invalid backend result status")
    if result.result_count != len(result.items):
        raise ContractError("backend result_count does not match items")
    return result
