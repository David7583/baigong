# ============================================================
# 文件名: retrieval_statistics_v0001.py
# 中文名: 用户文本关键词候选统计
# 版本号: v0001
# 主层级: action
# 层级: tools / retrieval / statistics
# 脚本定位: 受控执行器中的显式只读统计能力
# 职责说明:
# - 统计已解析用户文本单元中的高频短语候选
# 本脚本做什么:
# - 使用固定联合键和角色过滤，全量扫描或明确报告资源截断
# 本脚本不做什么:
# - 不将字符片段当作分词结果或语义重要性，不调用模型
# 制度边界声明:
# - 必须在配置中授权能力和字段；仅只读，限制时间/字符/词表
# - 频率按含候选的文本单元计数，不伪称消息数或全原始库
# 可更新: True
# ============================================================
# ALIAS_META
# alias: retrieval_statistics_v0001
# family: retrieval_statistics
# role: read_only_keyword_statistics
# version: v0001
# status: active
# entry_point: scripts/action/tools/retrieval/retrieval_statistics_v0001.py
# input:
#   - authorized SQLite source and bounded query limits
# output:
#   - candidate document frequencies and explicit coverage
# depends_on:
#   - Python stdlib re, collections, time
#   - experience_store_v0001
# used_by:
#   - retrieval_executor_v0002
# ============================================================
from __future__ import annotations
import re
import time
from collections import Counter
from .experience_store_v0001 import read_connection
from .experience_contract_v0001 import ExperienceError

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "retrieval_statistics"
SCRIPT_NAME = "retrieval_statistics_v0001"
SCRIPT_VERSION = "v0001"
LOCATOR = ("asset_id", "path", "value_index", "segment_index", "sentence_index", "char_start", "char_end")
STOPWORDS = set("一个 这个 那个 什么 为什么 怎么 如何 哪些 多少 时候 现在 之前 我们 你们 他们 自己 这样 那样 可以 需要 知道 认为 就是 还是 或者 以及 关于 进行 能够 可能 一些 一下 通过 对于 因为 所以 是否 还有 的话 中的 不是 没有 不能 应该 这些 那些 这种 那种 一种 已经 如果 然后 但是 比如 例如 其实 只是 就是 也是 作为 这里 那里 告诉 看看 帮我 解释 说明 分析 问题 关系 这句话 这段话".split())
STOPWORDS.update("是一 是在 东西 对于 所谓 有些 所有 不同 本身 当中 其中 之中 至少 例如 比较 之后 之前 里面 外面 一定 具体 直接 可能 比较 另外 这样子".split())
STOP_PATTERN = re.compile("|".join(re.escape(s) for s in STOPWORDS))
ENGLISH_STOPWORDS = set("the and for that this with from are was were have has had not but they their them you your our ours his her its who what why how when where which can could would should will shall may might must into onto out about over under between through than then there these those such some any all each both other only also very more most much many less been being does did doing use used using none null true false return def class self import else elif try except pass print http https com www org edu pdf html div span xml json text name value type data href src utf file path str int len key keys list dict format get set todo use one two new let var end if else it is on to in as of at by an be or we he she do us no so up".split())


def catalog_plan(question, sources):
    """Narrow, auditable catalog/statistical intents; open questions still use AI SQL."""
    if 'action_source_history' not in sources:
        return None
    if re.search(r'\d{4}|今年|去年|上个月|本月|仅|只看|只统计|按月|按年|不包含|排除|过滤', question):
        return None  # A scoped question must keep its filters in an AI-generated query.
    if any(word in question for word in ('关键词', '关键字', '一般会聊', '一般聊', '常聊', '经常聊', '聊天主题')):
        return {'kind': 'user_keyword_candidates', 'source_id': 'action_source_history',
                'query': 'ACTION_USER_KEYWORDS_V1', 'parameters': [],
                'scope': '已解析用户文本单元的短语候选频率；不是语义重要性，也不是所有角色的词频'}
    if any(word in question for word in ('哪些模型', '模型有哪些', '模型清单')) and not any(word in question for word in ('理论', '数学', '经济', '数据模型', '建模')):
        return {'kind': 'model_identity_catalog', 'source_id': 'action_source_history',
                'query': "SELECT COALESCE(NULLIF(message_model_slug,''),'unknown') AS model_identity, COUNT(*) AS text_unit_count, COUNT(DISTINCT identity_message_id) AS distinct_message_count FROM data_text_unit_source_identities WHERE message_identity_status = ? AND message_role = ? GROUP BY message_model_slug ORDER BY text_unit_count DESC LIMIT 100",
                'parameters': ['resolved', 'assistant'], 'scope': '历史 assistant 作者模型身份，不是正文提及的理论模型；未知身份单列'}
    if any(word in question for word in ('时间范围', '时间覆盖', '什么时候到什么时候', '从什么时候到什么时候')) and not re.search(r'\d{4}|今年|去年|用户|助手', question):
        return {'kind': 'source_time_coverage', 'source_id': 'action_source_history',
                'query': "SELECT COUNT(*) AS text_unit_count, SUM(CASE WHEN event_time_status='resolved' THEN 1 ELSE 0 END) AS resolved_time_count, SUM(CASE WHEN event_time_status<>'resolved' THEN 1 ELSE 0 END) AS unresolved_time_count, MIN(CASE WHEN event_time_status='resolved' THEN event_time END) AS first_event_time_utc, MAX(CASE WHEN event_time_status='resolved' THEN event_time END) AS last_event_time_utc FROM data_text_unit_source_timestamps",
                'parameters': [], 'scope': '已入库文本单元的来源时间；不使用入库时间、同步时间和 run_id'}
    return None


def user_keywords(source, parameters, limits):
    if parameters != [] or "user_keywords_v1" not in source.get("capabilities", []):
        raise ExperienceError("STATISTICS_CAPABILITY_DENIED")
    required = {"data_text_units": {*LOCATOR, "text"}, "data_text_unit_source_identities": {*LOCATOR, "message_role", "message_identity_status"}}
    if any(not fields <= set(source["tables"].get(table, [])) for table, fields in required.items()):
        raise ExperienceError("STATISTICS_COLUMNS_DENIED")
    conn = read_connection(source["path"])
    started = time.monotonic()
    deadline = started + limits.timeout_ms / 1000 * .85
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 500)
    frequency = Counter()
    scanned = chars = 0
    complete = True
    reason = None
    examples = []
    try:
        total = conn.execute("SELECT count(*) FROM data_text_unit_source_identities WHERE message_role='user' AND message_identity_status='resolved'").fetchone()[0]
        query = "SELECT u.text FROM data_text_units u JOIN data_text_unit_source_identities i USING (" + ",".join(LOCATOR) + ") WHERE i.message_role='user' AND i.message_identity_status='resolved'"
        for (text,) in conn.execute(query):
            if time.monotonic() > deadline or chars + len(text) > 20000000 or len(frequency) > 2000000:
                complete, reason = False, "scan_resource_limit"
                break
            scanned += 1
            chars += len(text)
            candidates = set()
            for run in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9_-]{2,30}", text):
                if run.isascii():
                    if run.lower() not in ENGLISH_STOPWORDS:
                        candidates.add(run.lower())
                    continue
                for size in (2, 3, 4):
                    for index in range(len(run)-size+1):
                        word = run[index:index+size]
                        if word in STOPWORDS or word[0] in "的了我你他她它这那" or word[-1] in "的了呢吗吧啊":
                            continue
                        if STOP_PATTERN.search(word):
                            continue
                        candidates.add(word)
            frequency.update(candidates)
        # Prefer a longer phrase only when it explains most occurrences of a fragment.
        pool = frequency.most_common(150)
        chosen = []
        for word, count in pool:
            if count < 3:
                continue
            if any(word != other and word in other and other_count >= count * .7 for other, other_count in pool):
                continue
            chosen.append((word, count))
            if len(chosen) >= min(10, limits.max_rows):
                break
        rows = [{"rank": rank+1, "candidate": word, "text_unit_frequency": count,
                 "scanned_user_text_units": scanned, "total_resolved_user_text_units": total,
                 "coverage_complete": complete and scanned == total,
                 "method": "Chinese 2-4 character/English token document frequency with explicit stopword and nested-fragment filters; candidate phrases, not semantic importance",
                 "scope": "resolved user text units only; retained ingest coverage, not all original messages",
                 "limit_reason": reason} for rank, (word, count) in enumerate(chosen)]
        if not rows:
            rows = [{"candidate": None, "scanned_user_text_units": scanned, "total_resolved_user_text_units": total,
                     "coverage_complete": complete and scanned == total, "limit_reason": reason}]
        return {"rows": rows, "truncated": not complete}
    finally:
        conn.close()
