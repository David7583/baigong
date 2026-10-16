# ============================================================
# 文件名: action_chat_query_context_v0001.py
# 中文名: 聊天查询上下文解析
# 版本号: v0001
# 主层级: action
# 层级: development / independent_action_chat / retrieval
# 脚本定位: 当前会话的有界查询上下文
# 职责说明: 解析时间指代，保留来源，不从助手文本猜测事实
# 本脚本做什么: 传递用户追问和已执行查询锚点
# 本脚本不做什么: 不访问数据库、不调用模型、不跨会话取历史
# 制度边界声明: 多日期不擅自选取；查询证据不作为指令
# 可更新: True
# ============================================================
# ALIAS_META
# alias: action_chat_query_context_v0001
# family: action_chat_query_context
# role: query_context
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_query_context_v0001.py
# input: current question and current session turns
# output: bounded planning context with traceable date anchor
# depends_on: Python stdlib
# used_by: action_chat_service_v0005
# ============================================================
from __future__ import annotations

import re
from datetime import date

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_query_context"
SCRIPT_NAME = "action_chat_query_context_v0001"
SCRIPT_VERSION = "v0001"
DATE = re.compile(r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})(?:日|号)?")
REFERENCE = re.compile(r"这个时间|那个时间|这个日期|那一天|这一天|当天|那天|这天|该日|那个时候|这时|那时")


def dates(text):
    result = []
    for year, month, day in DATE.findall(text):
        try:
            value = date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue
        if value not in result:
            result.append(value)
    return result


def planning_context(question, turns):
    recent = turns[-6:]
    context = {"recent_user_questions": [t.get("user_message", "")[:700] for t in recent],
               "anchor_date": None, "anchor_source": None, "needs_clarification": False}
    explicit = dates(question)
    if len(explicit) == 1:
        context.update(anchor_date=explicit[0], anchor_source="current_user")
    elif REFERENCE.search(question) and not explicit:
        last = turns[-1] if turns else {}
        retrieval = last.get("retrieval") or {}
        plan = retrieval.get("plan") or {}
        previous = dates(last.get("user_message", ""))
        if len(previous) == 1:
            context.update(anchor_date=previous[0], anchor_source=last.get("turn_id"))
        elif not previous and (plan.get("conversation_context") or {}).get("anchor_date"):
            context.update(anchor_date=plan["conversation_context"]["anchor_date"], anchor_source=last.get("turn_id"))
        elif re.search(r"最早|最晚|最新", last.get("user_message", "")) and retrieval.get("status") in {"completed", "partial"}:
            earliest = "最早" in last.get("user_message", "")
            candidates = []
            for hit in retrieval.get("merged_items", []):
                for key, value in hit.get("row", {}).items():
                    if re.search(r"min|first|earliest" if earliest else r"max|last|latest", key, re.I) and isinstance(value, str):
                        candidates.extend(dates(value))
            unique = set(candidates)
            if len(unique) == 1:
                context.update(anchor_date=unique.pop(), anchor_source=last.get("turn_id"))
        context["needs_clarification"] = not bool(context["anchor_date"])
    # A new explicit date or unrelated question must never inherit an old date.
    if recent:
        prior_plan = (recent[-1].get("retrieval") or {}).get("plan") or {}
        context["previous_query"] = str(prior_plan.get("query_text", ""))[:1000]
    return context
