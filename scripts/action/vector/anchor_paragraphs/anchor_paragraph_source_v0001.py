# ============================================================
# 文件名: anchor_paragraph_source_v0001.py
# 中文名: 身份与坐标锚点段落恢复
# 版本号: v0001
#
# 主层级: action
# 层级: workbench / independent_tools / anchor_vectors
# 脚本定位: 新向量流水线的独立只读 JSON 文件任务入口
#
# 职责说明:
# - 按完整七字段关联身份、时间旁表，恢复原始段落
#
# 本脚本做什么:
# - 流式读取已授权的本地规范化来源，校验文本哈希与坐标
# - 返回段落正文、全部锚点和原始消息身份
# - 通过文件任务批量恢复与核验，单轮流式缓存，完成后关闭只读句柄
#
# 本脚本不做什么:
# - 不合并不同消息，不调用模型，不写原始资料或业务数据库
#
# 制度边界声明:
# - 只读 SQLite；缺失、冲突或超限准确失败，禁止补写未知文字
# - 规范化来源限项目目录内，禁止链接逃逸；内存按文本值限额
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: anchor_paragraph_source_v0001
# family: anchor_paragraph_source
# role: anchored_paragraph_reconstruction
# version: v0001
# status: active
# entry_point: scripts/action/vector/anchor_paragraphs/anchor_paragraph_source_v0001.py
# input:
#   - paragraph-source.v1 request/response or paragraph-source-session.v1 JSON
# output:
#   - verified paragraphs/anchor checks in atomic JSON responses
# depends_on:
#   - Python stdlib
# used_by:
#   - anchor_vector_pipeline_v0001
# ============================================================
from __future__ import annotations

import hashlib
import argparse
import json
import os
import re
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "anchor_paragraph_source"
SCRIPT_NAME = "anchor_paragraph_source_v0001"
SCRIPT_VERSION = "v0001"
KEYS = ("asset_id", "path", "value_index", "segment_index", "sentence_index", "char_start", "char_end")
TEXT_PATH = "/conversations/*/nodes/*/message/content/parts/*/text"


class ReconstructionError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode(DEFAULT_ENCODING)).hexdigest()


# ============================================================
# 流式来源读取；不把数 GiB 的会话文件整体装入内存
# ============================================================
class JsonTextStream:
    def __init__(self, handle):
        self.handle, self.buffer, self.position = handle, "", 0

    def peek(self):
        if self.position == len(self.buffer):
            self.buffer, self.position = self.handle.read(65536), 0
        return self.buffer[self.position:self.position + 1]

    def take(self):
        char = self.peek()
        if not char:
            raise ReconstructionError("source_json_unexpected_end")
        self.position += 1
        return char

    def whitespace(self):
        while self.peek() and self.peek().isspace():
            self.take()

    def string(self):
        if self.take() != '"':
            raise ReconstructionError("source_json_string_required")
        pieces, escaped = ['"'], False
        while True:
            char = self.take()
            pieces.append(char)
            if len(pieces) > 16 * 1024 * 1024:
                raise ReconstructionError("source_string_too_large")
            if char == '"' and not escaped:
                break
            escaped = char == "\\" and not escaped
        return json.loads("".join(pieces))

    def values(self, path=(), depth=0):
        if depth > 128:
            raise ReconstructionError("source_json_depth_limit")
        self.whitespace()
        char = self.peek()
        if char == "{":
            self.take(); self.whitespace()
            if self.peek() == "}":
                self.take(); return
            while True:
                self.whitespace(); key = self.string(); self.whitespace()
                if self.take() != ":":
                    raise ReconstructionError("source_json_colon_required")
                yield from self.values((*path, key), depth + 1)
                self.whitespace(); char = self.take()
                if char == "}":
                    return
                if char != ",":
                    raise ReconstructionError("source_json_comma_required")
        elif char == "[":
            self.take(); self.whitespace(); index = 0
            if self.peek() == "]":
                self.take(); return
            while True:
                yield from self.values((*path, index), depth + 1)
                index += 1; self.whitespace(); char = self.take()
                if char == "]":
                    return
                if char != ",":
                    raise ReconstructionError("source_json_array_separator_required")
        elif char == '"':
            text = self.string()
            if (len(path) == 9 and path[0] == "conversations" and path[2] == "nodes"
                    and path[4:7] == ("message", "content", "parts") and path[8] == "text"):
                yield text
        else:
            token = []
            while self.peek() and self.peek() not in ",]} \r\n\t":
                token.append(self.take())
                if len(token) > 128:
                    raise ReconstructionError("source_json_primitive_limit")
            json.loads("".join(token))


class SourceValues:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.cache, self.streams, self.files = OrderedDict(), {}, {}

    def _file(self, asset):
        if asset in self.files:
            return self.files[asset]
        if not re.fullmatch(r"[\w.-]{1,120}", asset):
            raise ReconstructionError("invalid_asset_id")
        matches, visited = [], 0
        for base in (self.root / "actioning", self.root / "runtime/concurrent_pipeline"):
            for directory, dirs, files in os.walk(base, followlinks=False):
                dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
                visited += 1
                if visited > 10000:
                    raise ReconstructionError("source_discovery_limit")
                if asset + ".json" in files:
                    file = (Path(directory) / (asset + ".json")).resolve()
                    if file.is_relative_to(self.root) and not file.is_symlink():
                        matches.append(file)
        if not matches:
            raise ReconstructionError("canonical_source_missing")
        self.files[asset] = matches[0]
        return matches[0]

    def value(self, asset, index, sha1):
        key = (asset, index, sha1)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        state = self.streams.get(asset)
        if state is None or state[2] > index:
            if state:
                state[0].close()
            handle = self._file(asset).open("r", encoding="utf-8-sig")
            state = [handle, JsonTextStream(handle).values(), 0]
            self.streams[asset] = state
        text = None
        while state[2] <= index:
            try:
                text = next(state[1])
            except StopIteration:
                raise ReconstructionError("source_value_index_missing") from None
            state[2] += 1
        if hashlib.sha1(text.encode(DEFAULT_ENCODING)).hexdigest() != sha1:
            raise ReconstructionError("source_value_hash_conflict")
        self.cache[key] = text
        # A few recent message values; no unbounded source cache.
        while len(self.cache) > 32 or sum(len(x) for x in self.cache.values()) > 2_000_000:
            self.cache.popitem(last=False)
        return text

    def close(self):
        for state in self.streams.values():
            state[0].close()
        self.streams.clear()


# ============================================================
# 锚点段落恢复；原段落边界来自 segment_index，绝不按固定字符数切块
# ============================================================
class ParagraphSource:
    def __init__(self, root, db_path):
        self.root = Path(root).resolve()
        self.path = (self.root / db_path).resolve()
        if not self.path.is_relative_to(self.root) or not self.path.is_file():
            raise ReconstructionError("source_database_missing_or_outside_root")
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=3)
        self.db.row_factory = sqlite3.Row
        self.values = SourceValues(self.root)

    def seeds(self, after, limit=100):
        return [dict(row) for row in self.db.execute(
            "SELECT rowid AS source_rowid,asset_id,path,value_index,segment_index FROM data_text_units WHERE rowid>? ORDER BY rowid LIMIT ?", (after, limit))]

    def paragraph(self, seed):
        join = ",".join(KEYS)
        rows = [dict(r) for r in self.db.execute(
            "SELECT u.rowid AS source_rowid,u.*,i.message_role,i.message_author_name,i.message_model_slug,"
            "i.identity_message_id,i.identity_mapping_node_id,i.message_identity_status,i.message_identity_evidence_json,"
            "t.event_time,t.event_time_status,t.event_time_source_path FROM data_text_units u "
            f"LEFT JOIN data_text_unit_source_identities i USING ({join}) LEFT JOIN data_text_unit_source_timestamps t USING ({join}) "
            "WHERE u.asset_id=? AND u.path=? AND u.value_index=? AND u.segment_index=? ORDER BY u.char_start,u.char_end,u.sentence_index",
            tuple(seed[k] for k in ("asset_id", "path", "value_index", "segment_index")))]
        if not rows or len(rows) > 10000:
            raise ReconstructionError("paragraph_missing_or_too_many_units")
        identities = {(r["identity_message_id"], r["identity_mapping_node_id"], r["message_role"], r["message_author_name"], r["message_model_slug"], r["event_time"], r["source_value_sha1"]) for r in rows}
        if len(identities) != 1 or any(r["message_identity_status"] != "resolved" or not r["identity_message_id"] for r in rows):
            raise ReconstructionError("paragraph_identity_conflict_or_missing")
        if any(r["event_time_status"] != "resolved" for r in rows):
            raise ReconstructionError("paragraph_time_unresolved")
        if any(r["char_start"] < 0 or r["char_end"] <= r["char_start"] or len(r["text"]) != r["char_end"] - r["char_start"] for r in rows):
            raise ReconstructionError("paragraph_coordinate_mismatch")
        if any(a["char_end"] > b["char_start"] for a, b in zip(rows, rows[1:])):
            raise ReconstructionError("paragraph_coordinate_overlap")
        evidence = json.loads(rows[0]["message_identity_evidence_json"] or "{}")
        ref = evidence.get("source_ref") or {}
        if any((json.loads(r['message_identity_evidence_json'] or '{}').get('source_ref') or {}) != ref for r in rows):
            raise ReconstructionError('paragraph_source_reference_conflict')
        source_path = ref.get("source_path") or ""
        if "/mapping/" not in source_path:
            raise ReconstructionError("conversation_scope_unresolved")
        locator = {k: rows[0][k] for k in ("asset_id", "path", "value_index", "segment_index")}
        original = self.values.value(rows[0]["asset_id"], rows[0]["value_index"], rows[0]["source_value_sha1"])
        if any(original[r["char_start"]:r["char_end"]] != r["text"] for r in rows):
            raise ReconstructionError("source_slice_mismatch")
        # Parser paragraph boundaries are newlines. Include original whitespace and
        # any intervening content; the full text value hash was checked above.
        start = original.rfind("\n", 0, rows[0]["char_start"]) + 1
        end = original.find("\n", rows[-1]["char_end"])
        end = len(original) if end < 0 else end
        if "\n" in original[start:end]:
            raise ReconstructionError("paragraph_crosses_original_boundary")
        text = original[start:end]
        if not text.strip():
            raise ReconstructionError("paragraph_empty")
        return {"paragraph_id": "ap_" + fingerprint(locator), "locator": locator, "char_start": start, "char_end": end,
                "text": text, "text_sha256": hashlib.sha256(text.encode(DEFAULT_ENCODING)).hexdigest(),
                "message_id": rows[0]["identity_message_id"], "mapping_node_id": rows[0]["identity_mapping_node_id"],
                "role": rows[0]["message_role"], "author": rows[0]["message_author_name"], "model": rows[0]["message_model_slug"],
                "event_time": rows[0]["event_time"], "conversation_key": fingerprint([ref.get("source_file_id"), source_path.split("/mapping/")[0]]),
                "source_ref": ref, "source_value_sha1": rows[0]["source_value_sha1"],
                "anchors": [{k: r[k] for k in KEYS} for r in rows], "source_rowids": [r["source_rowid"] for r in rows],
                "restoration": "source_value_sha1_and_every_retained_slice_verified", "policy_version": SCRIPT_VERSION}

    def close(self):
        self.values.close(); self.db.close()

    def verify(self, paragraph):
        """Check retained paragraph anchors against the current authoritative facts."""
        where = " AND ".join(k + "=?" for k in KEYS)
        for anchor in paragraph["anchors"]:
            args = tuple(anchor[k] for k in KEYS)
            unit = self.db.execute("SELECT source_value_sha1,text FROM data_text_units WHERE " + where, args).fetchone()
            if not unit or unit[0] != paragraph["source_value_sha1"]:
                raise ReconstructionError("query_source_anchor_changed")
            offset = anchor["char_start"] - paragraph["char_start"]
            if unit[1] != paragraph["text"][offset:offset + anchor["char_end"] - anchor["char_start"]]:
                raise ReconstructionError("query_source_text_changed")
            identity = self.db.execute("SELECT identity_message_id,identity_mapping_node_id,message_role,message_author_name,message_model_slug,message_identity_status FROM data_text_unit_source_identities WHERE " + where, args).fetchone()
            timestamp = self.db.execute("SELECT event_time,event_time_status FROM data_text_unit_source_timestamps WHERE " + where, args).fetchone()
            if not identity or tuple(identity) != (paragraph["message_id"], paragraph["mapping_node_id"], paragraph["role"], paragraph["author"], paragraph["model"], "resolved") or not timestamp or tuple(timestamp) != (paragraph["event_time"], "resolved"):
                raise ReconstructionError("query_source_identity_or_time_changed")


def task_path(path, root):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400:
            raise ReconstructionError("source_task_link_forbidden")
        if item == root:
            break
    value = path.resolve()
    if not value.is_relative_to(root):
        raise ReconstructionError("source_task_outside_root")
    return value


def source_task(request, existing_source=None):
    root = Path(request["root"]).resolve(strict=True)
    database = task_path(root / request["source_db"], root)
    source = existing_source or ParagraphSource(root, str(database))
    try:
        if request["operation"] == "batch":
            after, limit = request["after"], request["limit"]
            if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
                raise ReconstructionError("source_batch_limits_invalid")
            items = []
            for seed in source.seeds(after, limit):
                try:
                    paragraph = source.paragraph(seed)
                    if len(paragraph["text"]) > 32000:
                        raise ReconstructionError("paragraph_exceeds_token_budget_not_truncated")
                    items.append({"seed": seed, "paragraph": paragraph})
                except ReconstructionError as exc:
                    items.append({"seed": seed, "error": str(exc)})
            return {"items": items}
        if request["operation"] == "verify":
            paragraphs = request["paragraphs"]
            if not isinstance(paragraphs, list) or len(paragraphs) > 100:
                raise ReconstructionError("source_verification_limits_invalid")
            for paragraph in paragraphs:
                source.verify(paragraph)
            return {"verified_ids": [p["paragraph_id"] for p in paragraphs]}
        raise ReconstructionError("source_task_operation_invalid")
    finally:
        if existing_source is None:
            source.close()


def session_worker(file):
    session = json.loads(file.read_text(DEFAULT_ENCODING))
    root = Path(session["root"]).resolve(strict=True)
    owner = root / "runtime/intermediate_tasks" / session["run_id"]
    if not re.fullmatch(r"paragraph_[a-f0-9]{32}", session["run_id"]) or task_path(file, root).parent != owner:
        raise ReconstructionError("source_session_invalid")
    with task_path(owner / "task.lock", root).open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            return _session_worker(file)
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _session_worker(file):
    session = json.loads(file.read_text(DEFAULT_ENCODING))
    root = Path(session["root"]).resolve(strict=True)
    owner = root / "runtime/intermediate_tasks" / session["run_id"]
    if session.get("schema_version") != "paragraph-source-session.v1" or not re.fullmatch(r"paragraph_[a-f0-9]{32}", session["run_id"]) or task_path(file, root).parent != owner:
        raise ReconstructionError("source_session_invalid")
    database = task_path(root / session["source_db"], root)
    source = ParagraphSource(root, str(database))
    handled, last = set(), time.monotonic()
    try:
        while time.monotonic() - last < 300:
            for incoming in sorted(owner.glob("request_*.json")):
                if incoming.name in handled:
                    continue
                outgoing = incoming.with_name(incoming.name.replace("request_", "response_", 1))
                try:
                    if task_path(incoming, root).stat().st_size > 16 * 1024 * 1024:
                        raise ReconstructionError("source_request_too_large")
                    request = json.loads(incoming.read_text(DEFAULT_ENCODING))
                    if request.get("schema_version") != "paragraph-source.v1" or request.get("run_id") != session["run_id"] or request.get("root") != str(root) or request.get("source_db") != session["source_db"]:
                        raise ReconstructionError("source_session_request_invalid")
                    result = source_task(request, source)
                    value = {"status": "completed", "result": result}
                except Exception as exc:
                    value = {"status": "failed", "detail": str(exc) if isinstance(exc, ReconstructionError) else "source_task_failed"}
                value.update(schema_version="paragraph-source.v1", run_id=session["run_id"], producer=SCRIPT_NAME, producer_version=SCRIPT_VERSION)
                task_path(outgoing, root)
                temporary = outgoing.with_suffix(".tmp")
                with temporary.open("w", encoding=DEFAULT_ENCODING) as stream:
                    json.dump(value, stream, ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
                temporary.replace(outgoing)
                handled.add(incoming.name); last = time.monotonic()
            if (owner / "stop.json").exists():
                stop = json.loads(task_path(owner / "stop.json", root).read_text(DEFAULT_ENCODING))
                if stop != {"run_id": session["run_id"], "operation": "close"}:
                    raise ReconstructionError("source_session_stop_invalid")
                return 0
            time.sleep(.02)
        # An abandoned read-only actor expires; unacknowledged files remain for recovery.
        return 3
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path)
    parser.add_argument("--response", type=Path)
    parser.add_argument("--session", type=Path)
    args = parser.parse_args()
    try:
        if args.session:
            if args.request or args.response:
                raise ReconstructionError("source_entry_modes_conflict")
            return session_worker(args.session)
        if not args.request or not args.response:
            raise ReconstructionError("source_request_response_required")
        if args.request.stat().st_size > 16 * 1024 * 1024:
            raise ReconstructionError("source_request_too_large")
        request = json.loads(args.request.read_text(DEFAULT_ENCODING))
        if request.get("schema_version") != "paragraph-source.v1" or not re.fullmatch(r"[a-zA-Z0-9_-]{8,100}", request.get("run_id", "")):
            raise ReconstructionError("source_request_contract_invalid")
        root = Path(request["root"]).resolve(strict=True)
        owner = root / "runtime/intermediate_tasks" / request["run_id"]
        if task_path(args.request, root).parent != owner or task_path(args.response, root).parent != owner or args.request.resolve() == args.response.resolve():
            raise ReconstructionError("source_task_ownership_invalid")
        result = source_task(request)
        value = {"schema_version": "paragraph-source.v1", "run_id": request["run_id"], "producer": SCRIPT_NAME,
                 "producer_version": SCRIPT_VERSION, "status": "completed", "result": result}
        temporary = args.response.with_suffix(".tmp")
        with temporary.open("w", encoding=DEFAULT_ENCODING) as stream:
            json.dump(value, stream, ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(args.response)
        return 0
    except Exception as exc:
        print(canonical({"status": "failed", "error_type": type(exc).__name__,
                         "detail": str(exc) if isinstance(exc, ReconstructionError) else "source_task_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
