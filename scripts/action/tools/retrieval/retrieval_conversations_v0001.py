# ============================================================
# 文件名: retrieval_conversations_v0001.py
# 中文名: 时间定位与会话级只读检索
# 版本号: v0001
# 主层级: action
# 层级: tools / retrieval / execution
# 脚本定位: 受控执行器的显式会话查询能力
# 职责说明: 先定位原始会话，再汇总模型身份与双方消息
# 本脚本做什么: 根据原始 mapping 路径分组并提供有界可追溯证据
# 本脚本不做什么: 不把 asset 当唯一会话、不猜测父子消息、不写业务库
# 制度边界声明: 权限白名单、超时与大小限制；保留库覆盖限制
# 可更新: True
# ============================================================
# ALIAS_META
# alias: retrieval_conversations_v0001
# family: retrieval_conversations
# role: read_only_conversation_query
# version: v0001
# status: active
# entry_point: scripts/action/tools/retrieval/retrieval_conversations_v0001.py
# input: authorized source and bounded temporal query
# output: conversation summaries with sampled messages and coverage
# depends_on: experience_store_v0001, experience_contract_v0001
# used_by: retrieval_executor_v0002, action_chat_retrieval_v0004
# ============================================================
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import date, timedelta

from .experience_store_v0001 import read_connection
from .experience_contract_v0001 import ExperienceError, canonical

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "retrieval_conversations"
SCRIPT_NAME = "retrieval_conversations_v0001"
SCRIPT_VERSION = "v0001"
LOCATOR = ("asset_id", "path", "value_index", "segment_index", "sentence_index", "char_start", "char_end")
JOIN = ",".join(LOCATOR)
ROOT = "substr(t.event_time_source_path,1,instr(t.event_time_source_path,'/mapping/'))"
BASE = f" FROM data_text_unit_source_timestamps t JOIN data_text_unit_source_identities i USING ({JOIN}) "
VALID = "t.event_time_status='resolved' AND i.message_identity_status='resolved' AND i.message_role IN ('user','assistant') AND instr(t.event_time_source_path,'/mapping/')>0"


def conversation_plan(question, sources, context):
    """Only generic day/nearest conversation requests; topic constraints stay with AI."""
    anchor = context.get("anchor_date")
    if not anchor or 'action_source_history' not in sources:
        return None
    generic = re.search(r"聊|对话|会话|哪个模型|哪些模型", question)
    if not generic:
        return None
    # Do not silently discard subject, model, role or time-of-day constraints.
    remainder = re.sub(r"\d{4}[-年/]\d{1,2}[-月/]\d{1,2}(?:日|号)?", "", question)
    for word in sorted(("这个时间点", "那个时间点", "这个日期", "最近的", "最近", "分别", "发生", "什么时候", "哪个模型", "哪些模型", "什么模型", "两次", "两场", "两个", "一次", "一场", "一共", "聊天", "对话", "会话", "聊了什么", "聊了啥", "聊什么", "聊啥", "这个", "那个", "当天", "那天", "这天", "前后", "之前", "之后", "以前", "以后", "向前", "向后", "时间点", "时间", "我和", "我与", "和我", "与我", "模型", "内容", "查一下", "查询", "看看", "请问", "在", "时", "离", "我", "和", "了", "呢", "啊", "的", "是", "啥", "什么", "AI"), key=len, reverse=True):
        remainder = remainder.replace(word, "")
    if remainder.strip(" ，,。.!！?？：:、"):
        return None
    nearest = "最近" in question or "前后" in question or any(w in question for w in ("之前", "之后", "以前", "以后"))
    direction = "before" if any(w in question for w in ("之前", "以前", "向前")) else "after" if any(w in question for w in ("之后", "以后", "向后")) else "both"
    count = 1 if any(w in question for w in ("一次", "一场")) else 2
    return {"source_id": "action_source_history", "query": "ACTION_CONVERSATIONS_V1",
            "parameters": ["nearest" if nearest else "day", anchor, count if nearest else 3, direction],
            "kind": "temporal_conversations", "scope": "UTC 日期；按 asset_id 加原始 mapping 所属路径区分会话；最近按会话中距锚点日零点最近的已保留消息排序，两次指两个不同会话，并非前后各一次。"}


def conversations(source, parameters, limits):
    if "conversations_v1" not in source.get("capabilities", []):
        raise ExperienceError("CONVERSATION_CAPABILITY_DENIED")
    required = {"data_text_units": {*LOCATOR, "text"},
                "data_text_unit_source_timestamps": {*LOCATOR, "event_time", "event_time_status", "event_time_source_path"},
                "data_text_unit_source_identities": {*LOCATOR, "message_role", "message_identity_status", "identity_message_id", "message_model_slug"}}
    if any(not fields <= set(source["tables"].get(table, [])) for table, fields in required.items()):
        raise ExperienceError("CONVERSATION_COLUMNS_DENIED")
    if not isinstance(parameters, list) or len(parameters) != 4:
        raise ExperienceError("CONVERSATION_PARAMETERS_INVALID")
    mode, anchor, count, direction = parameters
    if mode not in {"day", "nearest"} or direction not in {"both", "before", "after"} or type(count) is not int or not 1 <= count <= 3:
        raise ExperienceError("CONVERSATION_PARAMETERS_INVALID")
    try:
        day = date.fromisoformat(anchor)
    except (TypeError, ValueError):
        raise ExperienceError("CONVERSATION_DATE_INVALID") from None
    start, end = day.isoformat()+"T00:00:00Z", (day+timedelta(days=1)).isoformat()+"T00:00:00Z"
    conn = read_connection(source["path"])
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic() + limits.timeout_ms / 1000 * .9
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 500)
    try:
        where, args = VALID, []
        if mode == "day":
            where += " AND t.event_time >= ? AND t.event_time < ?"; args = [start, end]
        elif direction != "both":
            where += " AND t.event_time " + ("<" if direction == "before" else ">=") + " ?"; args = [start]
        groups = conn.execute(f"SELECT t.asset_id,{ROOT} AS conversation_path,MIN(t.event_time) AS first_time,MAX(t.event_time) AS last_time,MIN(abs(julianday(t.event_time)-julianday(?))) AS distance_days" + BASE + " WHERE " + where + " GROUP BY t.asset_id,conversation_path ORDER BY distance_days,first_time,t.asset_id,conversation_path", [start, *args]).fetchall()
        rows = []
        # Missing/unmapped identities are explicitly excluded, never interpreted as no history.
        for group in groups[:min(count, limits.max_rows)]:
            g = dict(group)
            clause = VALID + f" AND t.asset_id=? AND {ROOT}=?"
            scope_args = [g["asset_id"], g["conversation_path"]]
            if mode == "day":
                clause += " AND t.event_time >= ? AND t.event_time < ?"; scope_args += [start, end]
            elif direction == "both":
                # Exact group extrema allow indexed time filtering without changing scope.
                clause += " AND t.event_time >= ? AND t.event_time <= ?"
                scope_args += [g["first_time"], g["last_time"]]
            stats = conn.execute("SELECT i.message_role,COALESCE(NULLIF(i.message_model_slug,''),'unknown') AS model,COUNT(DISTINCT i.identity_message_id) AS messages,COUNT(*) AS text_units,MIN(t.event_time) AS first_time,MAX(t.event_time) AS last_time" + BASE + " WHERE " + clause + " GROUP BY i.message_role,model", scope_args).fetchall()
            samples = []
            # Select a user near the anchor, then fetch its chronological neighbors.
            center = conn.execute("SELECT t.event_time" + BASE + " WHERE " + clause + " AND i.message_role='user' ORDER BY abs(julianday(t.event_time)-julianday(?)),t.event_time LIMIT 1", [*scope_args, start]).fetchone()
            center_time = center[0] if center else g['first_time']
            for role in ("user", "assistant"):
                ids = conn.execute("SELECT i.identity_message_id,MIN(t.event_time) AS event_time" + BASE + " WHERE " + clause + " AND i.message_role=? AND i.identity_message_id IS NOT NULL GROUP BY i.identity_message_id ORDER BY abs(julianday(MIN(t.event_time))-julianday(?)),event_time,i.identity_message_id LIMIT 2", [*scope_args, role, center_time]).fetchall()
                for msg in ids:
                    units = conn.execute("SELECT u.text,t.path,t.value_index,t.segment_index,t.sentence_index,t.char_start,t.char_end,i.message_model_slug" + BASE + f" JOIN data_text_units u USING ({JOIN}) WHERE " + clause + " AND i.identity_message_id=? ORDER BY t.value_index,t.segment_index,t.sentence_index,t.char_start", [*scope_args, msg['identity_message_id']]).fetchall()
                    text = "\n".join(u['text'] for u in units)
                    samples.append({"message_id": msg['identity_message_id'], "role": role, "event_time": msg['event_time'],
                                    "model": units[0]['message_model_slug'] if units else None,
                                    "retained_text": text[:700], "text_truncated": len(text)>700,
                                    "retained_unit_count": len(units), "retained_text_sha256": hashlib.sha256(text.encode(DEFAULT_ENCODING)).hexdigest(),
                                    "locator": {"asset_id": g['asset_id'], "conversation_path": g['conversation_path'], "identity_message_id": msg['identity_message_id']}})
            samples.sort(key=lambda x:(x['event_time'], x['message_id']))
            rows.append({"conversation": g, "anchor_date": anchor, "timezone": "UTC", "mode": mode,
                         "selection_rule": "nearest distinct conversations by closest retained message to anchor UTC midnight; includes anchor-day conversation", "direction": direction,
                         "scope": "day-filtered retained messages" if mode == "day" else "whole selected retained conversation",
                         "matching_conversations": len(groups), "selected_conversations": min(count, limits.max_rows, len(groups)),
                         "identity_counts_complete_for_scope": True, "identity_counts": [dict(r) for r in stats],
                         "message_samples": samples, "message_samples_complete": sum(r['messages'] for r in stats) == len(samples),
                         "pairing": "Same original conversation and nearby times only; parent-child reply relationship is not verified.",
                         "coverage": "Retained resolved user/assistant units with mapping paths; not all original messages. Unknown assistant model remains unknown."})
        if not rows:
            rows = [{"anchor_date": anchor, "timezone": "UTC", "mode": mode, "matching_conversations": 0, "scope": "resolved retained user/assistant mapping conversations only"}]
        truncated = (mode == 'day' and len(groups)>min(count, limits.max_rows)) or min(count,len(groups))>limits.max_rows
        while rows and len(canonical({"rows": rows, "truncated": truncated}).encode(DEFAULT_ENCODING))>limits.max_bytes:
            rows.pop(); truncated = True
        return {"rows": rows, "truncated": truncated}
    finally:
        conn.close()
