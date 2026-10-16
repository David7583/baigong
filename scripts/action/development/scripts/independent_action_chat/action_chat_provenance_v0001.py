# ============================================================
# 文件名: action_chat_provenance_v0001.py
# 中文名: 聊天检索来源身份与时间回查
# 版本号: v0001
# 主层级: action
# 层级: development / independent_action_chat / provenance
# 脚本定位: 多源命中与来源旁表的只读核验层
# 职责说明:
# - 以完整来源定位和原文一致性核验身份及时间
# - 形成有界、有效 JSON 回答上下文
# 本脚本做什么:
# - 使用显式配置的 SQLite 来源，不改库、不调用模型
# 本脚本不做什么:
# - 不把入库时间当作事件时间，不推断缺失身份
# 制度边界声明:
# - 原始命中与旁表证据分开保留；不覆盖事实或扩展查询权限
# - 聚合行不伪装原文；超时和来源缺失明确留痕
# 可更新: True
# ============================================================
# ALIAS_META
# alias: action_chat_provenance_v0001
# family: action_chat_provenance
# role: read_only_source_enrichment
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_provenance_v0001.py
# input:
#   - authorized sources and bounded retrieval hits
# output:
#   - verified provenance and bounded answer context
# depends_on:
#   - Python stdlib sqlite3, json, hashlib, time, pathlib, copy
# used_by:
#   - action_chat_retrieval_v0004.py
# ============================================================
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from copy import deepcopy
from pathlib import Path

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_provenance"
SCRIPT_NAME = "action_chat_provenance_v0001"
SCRIPT_VERSION = "v0001"
LOCATOR = ("asset_id", "path", "value_index", "segment_index", "sentence_index", "char_start", "char_end")


# ============================================================
# 工具函数区
# ============================================================
def readonly(path, deadline):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 500)
    return conn


def validate_provenance(config):
    binding = config.get("provenance")
    if not binding:
        return
    sources = config["sources"]
    for key in ("source_id", "instance_source_id"):
        if binding.get(key) not in sources or sources[binding[key]]["source"]["backend"] != "sqlite":
            raise ValueError("PROVENANCE_SOURCE_NOT_AUTHORIZED")
    metadata = sources[binding["source_id"]]["source"]["tables"]
    required = {
        "data_text_units": {*LOCATOR, "text"},
        "data_text_unit_source_identities": {*LOCATOR, "message_role", "message_author_name", "message_model_slug", "message_identity_status", "identity_message_id", "source_identity_annotation_id"},
        "data_text_unit_source_timestamps": {*LOCATOR, "event_time", "event_time_status", "event_time_kind", "event_time_source_path", "source_timestamp_annotation_id"},
    }
    if any(not cols <= set(metadata.get(table, [])) for table, cols in required.items()):
        raise ValueError("PROVENANCE_COLUMNS_NOT_AUTHORIZED")
    instances = sources[binding["instance_source_id"]]["source"]["tables"]
    if not {*LOCATOR, "content", "instance_id", "unit_text_id"} <= set(instances.get("instance_units", [])):
        raise ValueError("PROVENANCE_INSTANCE_COLUMNS_NOT_AUTHORIZED")


# ============================================================
# 核心业务组件
# ============================================================
def enrich_hits(hits, config, selected):
    """Only enrich when the user-selected source includes the provenance store."""
    binding = config.get("provenance")
    if not binding or binding["source_id"] not in selected:
        return deepcopy(hits), {"enabled": False, "reason": "source_history_not_selected"}
    validate_provenance(config)
    result = deepcopy(hits)
    deadline = time.monotonic() + config["timeout_ms"] / 1000
    data = action = None
    verified = missing = 0
    try:
        data = readonly(config["sources"][binding["source_id"]]["source"]["path"], deadline)
        action = readonly(config["sources"][binding["instance_source_id"]]["source"]["path"], deadline)
        for hit in result:
            if time.monotonic() > deadline:
                raise TimeoutError("PROVENANCE_TIMEOUT")
            row = hit["row"]
            candidates = []
            if all(key in row and row[key] is not None for key in LOCATOR):
                candidates = [row]
            elif isinstance(row.get("instance_id"), str):
                candidates = action.execute("SELECT " + ",".join((*LOCATOR, "content", "instance_id", "unit_text_id")) + " FROM instance_units WHERE instance_id=? LIMIT 2", (row["instance_id"],)).fetchall()
            elif isinstance(row.get("unit_text_id"), str):
                candidates = action.execute("SELECT " + ",".join((*LOCATOR, "content", "instance_id", "unit_text_id")) + " FROM instance_units WHERE unit_text_id=? LIMIT 6", (row["unit_text_id"],)).fetchall()
            else:
                hit["provenance_status"] = "not_applicable_to_aggregate_or_unlocated_row"
                continue
            evidence = []
            for candidate in candidates[:5]:
                values = tuple(candidate[k] for k in LOCATOR)
                where = " AND ".join(k + " IS ?" for k in LOCATOR)
                original = data.execute("SELECT text FROM data_text_units WHERE " + where, values).fetchone()
                candidate_text = dict(candidate).get("content", dict(candidate).get("text"))
                supplied_text = row.get("content", row.get("text", row.get("unit_text")))
                if original is None or (candidate_text is not None and candidate_text != original[0]) or (supplied_text is not None and supplied_text != original[0]):
                    continue
                source_hash = hashlib.sha256(original[0].encode(DEFAULT_ENCODING)).hexdigest()
                if row.get("content_hash") and row["content_hash"] != source_hash:
                    continue
                ident = data.execute("SELECT message_role,message_author_name,message_model_slug,message_identity_status,identity_message_id,source_identity_annotation_id FROM data_text_unit_source_identities WHERE " + where, values).fetchone()
                stamp = data.execute("SELECT event_time,event_time_status,event_time_kind,event_time_source_path,source_timestamp_annotation_id FROM data_text_unit_source_timestamps WHERE " + where, values).fetchone()
                evidence.append({"source_id": binding["source_id"], "source_locator": dict(zip(LOCATOR, values)), "source_hash": source_hash,
                                 "text_verified": True, "identity": dict(ident) if ident else {"message_identity_status": "missing"},
                                 "timestamp": dict(stamp) if stamp else {"event_time_status": "missing"}})
            hit["provenance"] = evidence
            hit["provenance_status"] = "verified" if evidence else "missing_or_mismatched"
            hit["provenance_truncated"] = len(candidates) > 5
            if evidence:
                verified += 1
            else:
                missing += 1
        return result, {"enabled": True, "source_id": binding["source_id"], "verified_hits": verified, "unverified_hits": missing}
    except (sqlite3.Error, OSError, TimeoutError) as exc:
        return result, {"enabled": True, "status": "partial", "error_type": type(exc).__name__, "verified_hits": verified,
                        "note": "旁表核验未完成；没有明确核验标记的身份和时间不可采信"}
    finally:
        if data is not None:
            data.close()
        if action is not None:
            action.close()


def build_context(status, statuses, hits, max_chars, provenance):
    """Keep compact evidence once; full rows, SQL and locators remain in archives."""
    sources = {}
    for sid, detail in statuses.items():
        sources[sid] = {k: detail[k] for k in ("status", "result_count", "truncated", "errors", "error", "reason") if k in detail}
        # Aggregate scope is needed for interpretation; text queries need only filters.
        sources[sid]["query_scope"] = [
            {"sql": q["sql"][:1000], "parameters": q["parameters"]}
            if any(token in q["sql"].upper() for token in ("COUNT(", "MIN(", "MAX(", "GROUP BY", "ACTION_USER_KEYWORDS"))
            else {"parameters": q["parameters"], "scope": "bounded text lookup, not a corpus statistic"}
            for q in detail.get("queries", [])][:8]
    unique = {}
    for hit in hits:
        row = hit.get("row", {})
        refs = hit.get("provenance", [])
        first = refs[0] if refs else {}
        identity = first.get("identity", {})
        stamp = first.get("timestamp", {})
        raw_text = row.get("content", row.get("text", row.get("unit_text")))
        if raw_text is None:
            raw_text = next((r.get("text") for r in hit.get("source_refs", []) if r.get("text") is not None), None)
        # Same text from distinct message identities must remain distinct evidence.
        key = (first.get("source_hash"), identity.get("identity_message_id"), identity.get("message_role")) if first else (hit["source_id"], json.dumps(row, sort_keys=True, ensure_ascii=False))
        if key in unique:
            unique[key]["also_sources"].append(hit["source_id"])
            continue
        item = {"source_id": hit["source_id"], "also_sources": [], "query_ref": hit.get("query_ref"),
                "source_verified": hit.get("source_verified", False), "provenance_status": hit.get("provenance_status")}
        if raw_text is not None:
            item.update(text=raw_text[:900], text_truncated=len(raw_text)>900,
                        identity={k: identity.get(k) for k in ("message_role", "message_model_slug", "message_identity_status", "identity_message_id")},
                        timestamp={k: stamp.get(k) for k in ("event_time", "event_time_status")},
                        source_hash=first.get("source_hash"))
            if len(refs)>1 or hit.get("provenance_truncated"):
                item["additional_provenance_in_archive"] = True
        else:
            item.update(row=row, object_kind="query_row")
        unique[key]=item
    # User originals are most useful for personal questions; aggregates remain first.
    ordered = sorted(unique.values(), key=lambda h: (0 if "row" in h else 1 if h.get("identity",{}).get("message_role")=="user" else 2))
    envelope = {"retrieval_status": status, "sources": sources, "provenance": provenance, "evidence": [],
                "raw_hit_count": len(hits), "unique_evidence_count": len(unique), "duplicate_hits": len(hits)-len(unique),
                "context_truncated": False, "omitted_unique_evidence": 0,
                "interpretation": "Aggregates are SQL results; user/assistant/tool roles are distinct. Retained text units do not equal all original messages. Full audit data and text are available by query_ref."}
    included=0
    for item in ordered:
        envelope["evidence"].append(item)
        if len(json.dumps(envelope,ensure_ascii=False))+200 > max_chars:
            envelope["evidence"].pop()
            continue
        included+=1
    truncated = included < len(unique) or any(h.get("text_truncated") for h in envelope["evidence"])
    envelope.update(context_truncated=truncated,omitted_unique_evidence=len(unique)-included)
    value=json.dumps(envelope,ensure_ascii=False)
    if len(value)>max_chars:
        value=json.dumps({"retrieval_status":status,"context_truncated":True,"evidence":[]})
        included=0;truncated=True
        if len(value)>max_chars:
            value="{}" if max_chars>=2 else "0"
    return value,{"context_truncated":truncated,"included_hits":included,"omitted_hits":len(unique)-included,
                  "raw_hit_count":len(hits),"unique_evidence_count":len(unique),"duplicate_hits":len(hits)-len(unique),"deduplicated":True}
