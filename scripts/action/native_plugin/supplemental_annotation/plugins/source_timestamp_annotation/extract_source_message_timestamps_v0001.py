#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 文件名: extract_source_message_timestamps_v0001.py
# 中文名: 原始消息时间提取脚本
# 版本号: v0001
#
# 主层级: action
# 层级: native_plugin / supplemental_annotation / source_timestamp
# 脚本定位: 从不可变原始会话 JSON 生成可审计的消息时间标注中间文件
#
# 职责说明:
# - 按版本化来源规则提取消息 create_time 并建立解析器 path/value_index 对照
#
# 本脚本做什么:
# - 保留原始时间值、具体证据路径、消息身份、正文哈希和规范 UTC 时间
# - 原子写出确定性 JSONL、issues 与运行 manifest
#
# 本脚本不做什么:
# - 不读取或修改 text units，不写数据库，不识别正文时间表达式
# - 不使用运行时间、文件时间或数据库时间填补来源时间
#
# 制度边界声明:
# - 原始 JSON 只读；重复输入和规则产生相同 annotation_id 与业务输出顺序
# - 配置、来源结构或时间值非法时保留证据并准确分类，不返回伪 resolved
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: extract_source_message_timestamps_v0001
# family: extract_source_message_timestamps
# role: source_message_timestamp_extractor
# version: v0001
# status: active
# entry_point: scripts/action/native_plugin/supplemental_annotation/plugins/source_timestamp_annotation/extract_source_message_timestamps_v0001.py
# input:
#   - immutable source conversation JSON
#   - source_timestamp_rules_v0001.json
# output:
#   - source_message_timestamp_annotations_v0001.jsonl
#   - timestamp_extraction_issues_v0001.jsonl
#   - timestamp_extraction_manifest_v0001.json
# depends_on:
#   - Python standard library
# used_by:
#   - attach_source_timestamps_to_text_units_v0001
# ============================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "extract_source_message_timestamps"
SCRIPT_NAME = "extract_source_message_timestamps_v0001.py"
SCRIPT_VERSION = "v0001"
RULE_SCHEMA_VERSION = "source_timestamp_rules_v0001"
OUTPUT_SCHEMA_VERSION = "source_message_timestamp_annotation_v0001"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


# ============================================================
# 异常类型
# ============================================================

class SourceTimestampExtractionError(RuntimeError):
    """Raised when source timestamp extraction cannot be executed safely."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class SourceProfile:
    name: str
    conversation_container_path: str
    parser_text_path: str


@dataclass(frozen=True)
class ExtractionConfig:
    schema_version: str
    rule_version: str
    profiles: Tuple[SourceProfile, ...]


# ============================================================
# 工具函数区
# ============================================================

def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode(DEFAULT_ENCODING)).hexdigest()


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding=DEFAULT_ENCODING, newline="\n")
    os.replace(temporary, path)


def _write_jsonl_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    text = "".join(_canonical_json(dict(row)) + "\n" for row in rows)
    _atomic_write_text(path, text)


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SourceTimestampExtractionError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SourceTimestampExtractionError(f"{label} is invalid JSON: {path}: {exc}") from exc


def _json_pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _select_values(root: Any, pointer: str) -> List[Tuple[Any, str]]:
    segments = [segment for segment in pointer.split("/") if segment]
    current: List[Tuple[Any, str]] = [(root, "")]
    for segment in segments:
        following: List[Tuple[Any, str]] = []
        for value, concrete in current:
            if segment == "*":
                if isinstance(value, list):
                    following.extend((item, f"{concrete}/{index}") for index, item in enumerate(value))
                elif isinstance(value, dict):
                    following.extend((item, f"{concrete}/{_json_pointer_escape(str(key))}") for key, item in value.items())
            elif isinstance(value, dict) and segment in value:
                following.append((value[segment], f"{concrete}/{_json_pointer_escape(segment)}"))
            elif isinstance(value, list):
                try:
                    index = int(segment)
                except ValueError:
                    continue
                if 0 <= index < len(value):
                    following.append((value[index], f"{concrete}/{index}"))
        current = following
    return current


def _iter_strings(value: Any, concrete_path: str) -> Iterator[Tuple[str, str]]:
    if isinstance(value, str):
        yield value, concrete_path
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, f"{concrete_path}/{index}")


def _normalize_epoch_seconds(raw: Any) -> Tuple[Optional[str], str, Optional[str]]:
    if raw is None:
        return None, "missing", None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, "invalid", "timestamp must be an integer or decimal Unix second value"
    try:
        value = Decimal(str(raw))
        if not value.is_finite():
            raise InvalidOperation
        whole = int(value.to_integral_value(rounding=ROUND_FLOOR))
        micros = int(((value - Decimal(whole)) * Decimal(1_000_000)).to_integral_value())
        moment = datetime.fromtimestamp(whole, tz=timezone.utc) + timedelta(microseconds=micros)
    except (InvalidOperation, OverflowError, OSError, ValueError) as exc:
        return None, "invalid", f"timestamp is outside the supported UTC range: {exc}"
    rendered = moment.isoformat(timespec="microseconds" if moment.microsecond else "seconds").replace("+00:00", "Z")
    return rendered, "resolved", None


def _load_config(path: Path) -> ExtractionConfig:
    payload = _load_json(path, label="source timestamp config")
    if not isinstance(payload, dict) or payload.get("schema_version") != RULE_SCHEMA_VERSION:
        raise SourceTimestampExtractionError(f"config schema_version must be {RULE_SCHEMA_VERSION}")
    rule_version = payload.get("rule_version")
    profiles_raw = payload.get("profiles")
    if not isinstance(rule_version, str) or not rule_version or not isinstance(profiles_raw, list) or not profiles_raw:
        raise SourceTimestampExtractionError("config requires rule_version and non-empty profiles")
    profiles: List[SourceProfile] = []
    for item in profiles_raw:
        if not isinstance(item, dict):
            raise SourceTimestampExtractionError("each source profile must be an object")
        values = (item.get("name"), item.get("conversation_container_path"), item.get("parser_text_path"))
        if not all(isinstance(value, str) and value for value in values):
            raise SourceTimestampExtractionError("source profile fields must be non-empty strings")
        profiles.append(SourceProfile(*values))
    return ExtractionConfig(str(payload["schema_version"]), rule_version, tuple(profiles))


# ============================================================
# 默认映射
# ============================================================

EVENT_TIME_KIND = "message_created"


# ============================================================
# 核心业务组件
# ============================================================

def extract_annotations(source: Any, *, asset_id: str, config: ExtractionConfig) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    annotations: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    status_counts = {"resolved": 0, "missing": 0, "invalid": 0, "ambiguous": 0}
    profiles_matched = 0
    for profile in config.profiles:
        containers = _select_values(source, profile.conversation_container_path)
        conversations: List[Tuple[Any, str]] = []
        for value, concrete in containers:
            if isinstance(value, list):
                conversations.extend((item, f"{concrete}/{index}") for index, item in enumerate(value))
            elif isinstance(value, dict):
                conversations.append((value, concrete))
        if not conversations:
            continue
        profiles_matched += 1
        value_index = 0
        for conversation, conversation_path in conversations:
            if not isinstance(conversation, dict) or not isinstance(conversation.get("mapping"), dict):
                continue
            for mapping_node_id, node in conversation["mapping"].items():
                if not isinstance(node, dict):
                    continue
                message = node.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                parts = content.get("parts") if isinstance(content, dict) else None
                if not isinstance(parts, list):
                    continue
                raw_time = message.get("create_time")
                event_time, status, error = _normalize_epoch_seconds(raw_time)
                source_path = f"{conversation_path}/mapping/{_json_pointer_escape(str(mapping_node_id))}/message/create_time"
                for part_index, part in enumerate(parts):
                    for text, concrete_text_path in _iter_strings(part, f"{conversation_path}/mapping/{_json_pointer_escape(str(mapping_node_id))}/message/content/parts/{part_index}"):
                        identity_payload = {
                            "asset_id": asset_id,
                            "target_path": profile.parser_text_path,
                            "value_index": value_index,
                            "mapping_node_id": str(mapping_node_id),
                            "message_id": message.get("id"),
                            "event_time_raw": raw_time,
                            "event_time_kind": EVENT_TIME_KIND,
                            "event_time_source_path": source_path,
                            "rule_version": config.rule_version,
                        }
                        annotation = {
                            "schema_version": OUTPUT_SCHEMA_VERSION,
                            "annotation_id": "sha256:" + _sha256_bytes(_canonical_json(identity_payload).encode(DEFAULT_ENCODING)),
                            "asset_id": asset_id,
                            "target_path": profile.parser_text_path,
                            "value_index": value_index,
                            "source_value_sha1": _sha1_text(text),
                            "mapping_node_id": str(mapping_node_id),
                            "message_id": message.get("id"),
                            "event_time_raw": raw_time,
                            "event_time": event_time,
                            "event_time_kind": EVENT_TIME_KIND,
                            "event_time_source_path": source_path,
                            "event_time_status": status,
                            "event_time_rule_version": config.rule_version,
                            "source_text_path": concrete_text_path,
                        }
                        annotations.append(annotation)
                        status_counts[status] += 1
                        if error:
                            issues.append({"annotation_id": annotation["annotation_id"], "status": status, "detail": error, "source_path": source_path})
                        value_index += 1
    if profiles_matched == 0:
        raise SourceTimestampExtractionError("no configured source profile matched the input JSON")
    annotations.sort(key=lambda row: (row["asset_id"], row["target_path"], row["value_index"], row["annotation_id"]))
    issues.sort(key=lambda row: row["annotation_id"])
    return annotations, issues, status_counts


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def _require_output_paths_distinct(paths: Sequence[Path]) -> None:
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise SourceTimestampExtractionError("annotation, issues and manifest outputs must be distinct")


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract source message timestamps without modifying raw data.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--issues", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        source_path = Path(args.source).resolve()
        config_path = Path(args.config).resolve()
        annotation_path = Path(args.annotations).resolve()
        issues_path = Path(args.issues).resolve()
        manifest_path = Path(args.manifest).resolve()
        if not source_path.is_file() or not config_path.is_file():
            raise SourceTimestampExtractionError("source and config must be existing files")
        if not isinstance(args.asset_id, str) or not args.asset_id.strip() or not RUN_ID_PATTERN.fullmatch(args.run_id):
            raise SourceTimestampExtractionError("asset_id and run_id must be non-empty and run_id must use safe characters")
        _require_output_paths_distinct((annotation_path, issues_path, manifest_path))
        config = _load_config(config_path)
        source = _load_json(source_path, label="source JSON")
        annotations, issues, status_counts = extract_annotations(source, asset_id=args.asset_id, config=config)
        manifest = {
            "schema_version": "source_timestamp_extraction_manifest_v0001",
            "script": SCRIPT_NAME,
            "script_version": SCRIPT_VERSION,
            "run_id": args.run_id,
            "status": "dry_run" if args.dry_run else "completed",
            "source": str(source_path),
            "source_sha256": _sha256_file(source_path),
            "config": str(config_path),
            "config_sha256": _sha256_file(config_path),
            "rule_version": config.rule_version,
            "annotations": str(annotation_path),
            "issues": str(issues_path),
            "records_created": len(annotations),
            "issues_created": len(issues),
            "status_counts": status_counts,
            "fallbacks_used": [],
            "generated_at": _utc_now_z(),
        }
        if not args.dry_run:
            _write_jsonl_atomic(annotation_path, annotations)
            _write_jsonl_atomic(issues_path, issues)
            manifest["annotations_sha256"] = _sha256_file(annotation_path)
            manifest["issues_sha256"] = _sha256_file(issues_path)
            _atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(_canonical_json(manifest))
        return 0
    except SourceTimestampExtractionError as exc:
        print(_canonical_json({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}))
        return 2
    except Exception as exc:
        print(_canonical_json({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
