# ============================================================
# 文件名: action_chat_session_store_v0002.py
# 中文名: 新版独立 Action Chat 会话存储与输出包脚本
# 版本号: v0002
#
# 主层级: action
# 层级: development / independent_action_chat / session_store
# 脚本定位: 运行内存状态与可校验 Action 会话输出包之间的持久化边界
#
# 职责说明:
# - 追加保存消息、轮次、切换事件和检索留痕
# - 原子生成 manifest、JSONL、校验和及可重放输出包
#
# 本脚本做什么:
# - 保存和恢复会话运行状态，验证输出包完整性
# - 将大检索结果留在独立文件并通过哈希引用
#
# 本脚本不做什么:
# - 不写总系统数据库，不修改原始数据，不执行模型或检索
#
# 制度边界声明:
# - 消息、轮次和事件只追加；状态快照与导出文件原子替换
# - 保存失败不得删除内存状态或返回伪完整输出包
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_session_store_v0002
# family: action_chat_session_store
# role: session_output_store
# version: v0002
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_session_store_v0002.py
# input:
#   - validated runtime state, messages, turns, events and retrieval traces
# output:
#   - replayable session package with SHA-256 checksums
# depends_on:
#   - Python stdlib: json, os, pathlib, re, shutil, tempfile, uuid
#   - action_chat_contracts_v0001.py
# used_by:
#   - action_chat_service_v0001.py
# ============================================================

from __future__ import annotations

import json
import hashlib
from copy import deepcopy
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .action_chat_contracts_v0001 import (
    DEFAULT_ENCODING,
    SessionError,
    sha256_text,
    stable_json,
    utc_now_iso,
)


SCRIPT_FAMILY = "action_chat_session_store"
SCRIPT_NAME = "action_chat_session_store_v0002"
SCRIPT_VERSION = "v0002"
PACKAGE_SCHEMA_VERSION = 2
SESSION_ID_PATTERN = re.compile(r"^session_[0-9a-f]{32}$")


# ============================================================
# 异常类型
# ============================================================

class PackageIntegrityError(SessionError):
    """Raised when a session package is incomplete or has a checksum mismatch."""


# ============================================================
# 数据结构
# ============================================================

# Session records intentionally remain versioned JSON mappings for replay compatibility.


# ============================================================
# 工具函数区
# ============================================================

def new_session_id() -> str:
    return f"session_{uuid.uuid4().hex}"


def new_turn_id() -> str:
    return f"turn_{uuid.uuid4().hex}"


def _validate_session_id(session_id: str) -> str:
    if SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise SessionError("invalid session_id")
    return session_id


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding=DEFAULT_ENCODING, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding=DEFAULT_ENCODING, newline="\n") as handle:
        handle.write(stable_json(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding=DEFAULT_ENCODING))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"cannot read JSON file: {path.name}") from exc
    if not isinstance(value, dict):
        raise SessionError(f"JSON file must contain an object: {path.name}")
    return value


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding=DEFAULT_ENCODING).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionError(f"invalid JSONL in {path.name} line {line_number}") from exc
        if not isinstance(value, dict):
            raise SessionError(f"JSONL object required in {path.name} line {line_number}")
        records.append(value)
    return records


def _file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ============================================================
# 默认映射
# ============================================================

CORE_PACKAGE_FILES = ("manifest.json", "messages.jsonl", "turns.jsonl", "events.jsonl")
RETRIEVAL_PACKAGE_FILES = (
    "retrieval/plans.jsonl",
    "retrieval/executions.jsonl",
    "retrieval/result_refs.jsonl",
    "backend_status.json",
)


# ============================================================
# 核心类
# ============================================================


SESSION_TYPES = {
    "normal_chat": {"name": "普通聊天", "convert_to": ["tool_development"]},
    "tool_development": {"name": "工具开发", "convert_to": []},
}
DEVELOPMENT_STATES = {
    "requirements_discussion": "需求讨论中", "awaiting_confirmation": "等待开发确认",
    "developing": "开发中", "testing": "测试与验收中", "integrated": "已接入工作台",
    "paused": "已暂停", "failed": "失败",
}

def normalize_session(state):
    state = deepcopy(state)
    state.setdefault("session_type", "normal_chat")
    state.setdefault("conversion_history", [])
    if state["session_type"] == "tool_development":
        development = state.setdefault("development", {})
        for key, value in {"schema_version": "tool-development.v1", "revision": 0,
                           "task_state": "requirements_discussion", "requirements": "", "plan": "",
                           "requirements_history": [], "project": None, "executions": [],
                           "tests": [], "tools": [], "approvals": []}.items():
            development.setdefault(key, value)
    return state

class SessionStore:
    def __init__(self, runtime_dir: Path, action_output_dir: Path):
        self.runtime_dir = runtime_dir.resolve()
        self.action_output_dir = action_output_dir.resolve()
        self.sessions_dir = self.runtime_dir / "sessions"
        self.active_pointer = self.runtime_dir / "active_session.json"

    def create(self, initial_state: Dict[str, Any]) -> Dict[str, Any]:
        session_id = new_session_id()
        now = utc_now_iso()
        state = {
            **initial_state,
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "turn_count": 0,
        }
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            raise SessionError("new session id collision")
        session_dir.mkdir(parents=True)
        state = normalize_session(state)
        self.save_state(state)
        self.append_event(session_id, {"event_type": "session_created", "timestamp": now})
        self.set_active(session_id)
        return state

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / _validate_session_id(session_id)

    def save_state(self, state: Dict[str, Any]) -> None:
        session_id = _validate_session_id(str(state.get("session_id", "")))
        state = {**state, "updated_at": utc_now_iso()}
        _atomic_write_text(self._session_dir(session_id) / "state.json", stable_json(state))

    def load_state(self, session_id: str) -> Dict[str, Any]:
        path = self._session_dir(session_id) / "state.json"
        if not path.exists():
            raise SessionError(f"session not found: {session_id}")
        return normalize_session(_read_json(path))

    def set_active(self, session_id: str) -> None:
        self.load_state(session_id)
        _atomic_write_text(self.active_pointer, stable_json({"session_id": session_id, "updated_at": utc_now_iso()}))

    def load_active(self) -> Optional[Dict[str, Any]]:
        if not self.active_pointer.exists():
            return None
        pointer = _read_json(self.active_pointer)
        session_id = pointer.get("session_id")
        if not isinstance(session_id, str):
            raise SessionError("active session pointer is invalid")
        return self.load_state(session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        if not self.sessions_dir.exists():
            return []
        states: List[Dict[str, Any]] = []
        for path in self.sessions_dir.glob("session_*/state.json"):
            try:
                states.append(normalize_session(_read_json(path)))
            except SessionError:
                continue
        return sorted(states, key=lambda item: str(item.get("updated_at", "")), reverse=True)

    def append_message(self, session_id: str, record: Dict[str, Any]) -> None:
        _append_jsonl(self._session_dir(session_id) / "messages.jsonl", record)

    def append_turn(self, session_id: str, record: Dict[str, Any]) -> None:
        _append_jsonl(self._session_dir(session_id) / "turns.jsonl", record)

    def append_event(self, session_id: str, record: Dict[str, Any]) -> None:
        payload = {"schema_version": PACKAGE_SCHEMA_VERSION, "session_id": session_id, **record}
        if "timestamp" not in payload:
            payload["timestamp"] = utc_now_iso()
        _append_jsonl(self._session_dir(session_id) / "events.jsonl", payload)

    def append_retrieval(self, session_id: str, kind: str, record: Dict[str, Any]) -> None:
        if kind not in {"plans", "executions", "result_refs"}:
            raise SessionError("invalid retrieval record kind")
        _append_jsonl(self._session_dir(session_id) / "retrieval" / f"{kind}.jsonl", record)

    def write_backend_status(self, session_id: str, status: Dict[str, Any]) -> None:
        _atomic_write_text(self._session_dir(session_id) / "backend_status.json", stable_json(status))

    def write_retrieval_payload(self, session_id: str, transaction_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not re.fullmatch(r"retr_[0-9a-f]{32}", transaction_id):
            raise SessionError("invalid retrieval transaction id")
        relative = Path("retrieval") / "payloads" / f"{transaction_id}.json"
        target = self._session_dir(session_id) / relative
        text = stable_json(payload)
        _atomic_write_text(target, text)
        return {"path": relative.as_posix(), "sha256": sha256_text(text), "size_chars": len(text)}

    def messages(self, session_id: str) -> List[Dict[str, Any]]:
        return _read_jsonl(self._session_dir(session_id) / "messages.jsonl")

    def turns(self, session_id: str) -> List[Dict[str, Any]]:
        return _read_jsonl(self._session_dir(session_id) / "turns.jsonl")

    def events(self, session_id: str) -> List[Dict[str, Any]]:
        return _read_jsonl(self._session_dir(session_id) / "events.jsonl")


    def update_mode(self, session_id, target, *, actor="user", reason="用户主动转换"):
        before = self.load_state(session_id)
        original = before["session_type"]
        if target not in SESSION_TYPES or target not in SESSION_TYPES.get(original, {}).get("convert_to", []):
            raise SessionError("当前会话不支持此类型转换")
        development = before.get("development", {})
        if development.get("project") or development.get("executions") or development.get("task_state") in {"developing", "testing"}:
            raise SessionError("会话存在工程或任务，不能改变类型")
        after = normalize_session({**before, "session_type": target})
        event = {"id": "conversion_" + uuid.uuid4().hex, "from_type": original, "to_type": target,
                 "at": utc_now_iso(), "actor": actor, "reason": reason, "grants_execution": False,
                 "original_created_at": before.get("created_at"), "original_updated_at": before.get("updated_at")}
        after["conversion_history"].append(event)
        self.commit_change(before, after, "session_type_converted", event)
        return self.load_state(session_id)

    def commit_change(self, before, after, event_type, detail):
        session_id = before["session_id"]
        if after.get("session_id") != session_id:
            raise SessionError("会话身份不得修改")
        now = utc_now_iso()
        after = {**after, "updated_at": now}
        # Journal first, then atomic state; execution starts only after the host receives success.
        change = {"id": "change_" + uuid.uuid4().hex, "event_type": event_type, "at": now,
                  "before": before, "before_sha256": sha256_text(stable_json(before)), "after": after, "detail": detail}
        journal = self._session_dir(session_id) / "changes" / (change["id"] + ".json")
        _atomic_write_text(journal, stable_json({**change, "status": "prepared"}))
        _atomic_write_text(self._session_dir(session_id) / "state.json", stable_json(after))
        _atomic_write_text(journal, stable_json({**change, "status": "committed"}))
        self.append_event(session_id, {"event_type": event_type, "change_id": change["id"], **detail})
        return self.load_state(session_id)

    def export(self, session_id: str, *, config_hashes: Dict[str, str]) -> Dict[str, Any]:
        state = self.load_state(session_id)
        source = self._session_dir(session_id)
        destination = self.action_output_dir / session_id
        destination.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "session_id": session_id,
            "created_at": state.get("created_at"),
            "exported_at": utc_now_iso(),
            "turn_count": state.get("turn_count", 0),
            "session_type": state.get("session_type", "normal_chat"),
            "development": state.get("development"),
            "conversion_history": state.get("conversion_history", []),
            "status": "completed",
            "config_hashes": dict(config_hashes),
            "causal_parent": None,
            "producer": f"{SCRIPT_NAME}:{SCRIPT_VERSION}",
        }
        _atomic_write_text(destination / "manifest.json", stable_json(manifest))
        _atomic_write_text(destination / "state.json", stable_json(state))
        for name in ("messages.jsonl", "turns.jsonl", "events.jsonl"):
            origin = source / name
            _atomic_write_text(destination / name, origin.read_text(encoding=DEFAULT_ENCODING) if origin.exists() else "")
        for journal in (source / "changes").glob("*.json"):
            _atomic_write_text(destination / "changes" / journal.name, journal.read_text(encoding=DEFAULT_ENCODING))
        retrieval_source = source / "retrieval"
        if retrieval_source.exists():
            for relative in ("plans.jsonl", "executions.jsonl", "result_refs.jsonl"):
                origin = retrieval_source / relative
                if origin.exists():
                    _atomic_write_text(destination / "retrieval" / relative, origin.read_text(encoding=DEFAULT_ENCODING))
            payloads = retrieval_source / "payloads"
            if payloads.exists():
                target_payloads = destination / "retrieval" / "payloads"
                target_payloads.mkdir(parents=True, exist_ok=True)
                for origin in payloads.glob("*.json"):
                    _atomic_write_text(target_payloads / origin.name, origin.read_text(encoding=DEFAULT_ENCODING))
        status_file = source / "backend_status.json"
        if status_file.exists():
            _atomic_write_text(destination / "backend_status.json", status_file.read_text(encoding=DEFAULT_ENCODING))
        from .action_chat_attachments_v0001 import AttachmentStore
        attachments = AttachmentStore(self.runtime_dir / "attachments")
        referenced = {r["id"] for message in self.messages(session_id) for r in message.get("attachments", [])}
        for attachment_id in sorted(referenced):
            record, data = attachments.read(session_id, attachment_id)
            target = destination / "attachments"
            target.mkdir(parents=True, exist_ok=True)
            (target / (attachment_id + ".bin")).write_bytes(data)
            _atomic_write_text(target / (attachment_id + ".json"), stable_json(record))
        checksums: Dict[str, str] = {}
        for path in sorted(item for item in destination.rglob("*") if item.is_file() and item.name != "checksums.json"):
            checksums[path.relative_to(destination).as_posix()] = _file_checksum(path)
        _atomic_write_text(destination / "checksums.json", stable_json({"algorithm": "sha256", "files": checksums}))
        verification = self.verify_export(destination)
        return {"status": "completed", "path": str(destination), **verification}

    def verify_export(self, package_dir: Path) -> Dict[str, Any]:
        package_dir = package_dir.resolve()
        missing = [name for name in (*CORE_PACKAGE_FILES, "checksums.json") if not (package_dir / name).exists()]
        if missing:
            raise PackageIntegrityError(f"package missing files: {missing}")
        checksums = _read_json(package_dir / "checksums.json")
        if checksums.get("algorithm") != "sha256" or not isinstance(checksums.get("files"), dict):
            raise PackageIntegrityError("invalid checksums.json")
        verified = 0
        for relative, expected in checksums["files"].items():
            target = (package_dir / relative).resolve()
            try:
                target.relative_to(package_dir)
            except ValueError as exc:
                raise PackageIntegrityError("checksum path escapes package") from exc
            if not target.is_file() or _file_checksum(target) != expected:
                raise PackageIntegrityError(f"checksum mismatch: {relative}")
            verified += 1
        manifest = _read_json(package_dir / "manifest.json")
        _validate_session_id(str(manifest.get("session_id", "")))
        _read_jsonl(package_dir / "messages.jsonl")
        _read_jsonl(package_dir / "turns.jsonl")
        _read_jsonl(package_dir / "events.jsonl")
        return {"verified": True, "verified_file_count": verified, "session_id": manifest["session_id"]}


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def replay_messages(package_dir: Path) -> List[Dict[str, Any]]:
    store = SessionStore(package_dir.parent, package_dir.parent)
    store.verify_export(package_dir)
    return _read_jsonl(package_dir / "messages.jsonl")
