# ============================================================
# 文件名: workbench_vector_retrieval_v0001.py
# 中文名: 新旧向量检索路径适配
# 版本号: v0001
#
# 主层级: action
# 层级: workbench / adapters / retrieval
# 脚本定位: 新聊天入口的显式向量路径选择适配层
#
# 职责说明:
# - 根据持久化选择使用新段落索引或完整原检索路径
#
# 本脚本做什么:
# - 新路径调用独立服务，合并有来源核验的段落证据
# - 旧路径直接委托原检索器，保持旧代码、索引和配置
#
# 本脚本不做什么:
# - 不静默回退，不调用 Provider，不在 UI 中复制检索算法
#
# 制度边界声明:
# - 服务身份与根目录核验，凭证只留本地请求头
# - 上下文受预算约束；部分索引和服务失败明确展示
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: workbench_vector_retrieval_v0001
# family: workbench_vector_retrieval
# role: explicit_vector_path_adapter
# version: v0001
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_vector_retrieval_v0001.py
# input:
#   - original retriever, root, explicit query or router decision
# output:
#   - source-verified retrieval outcome and selected-path diagnostics
# depends_on:
#   - action_chat_contracts_v0001
#   - anchor_vector_service_v0001
# used_by:
#   - action_chat_server_v0012
# ============================================================
from __future__ import annotations
import json
import uuid
import threading
from dataclasses import replace, asdict
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "workbench_vector_retrieval"
SCRIPT_NAME = "workbench_vector_retrieval_v0001"
SCRIPT_VERSION = "v0001"


class VectorRetrieval:
    def __init__(self, original, root):
        self.original, self.root = original, Path(root).resolve()
        self.lock = threading.RLock()

    def __getattr__(self, name):
        return getattr(self.original, name)

    def selected(self):
        file = self.root / "runtime/local_ai_workbench/vector_selection_v0001.json"
        value = json.loads(file.read_text(DEFAULT_ENCODING))["selected"] if file.exists() else "paragraph"
        if value not in {"paragraph", "legacy"}:
            raise ValueError("vector_selection_invalid")
        return value

    def status(self, **kwargs):
        value = self.original.status(**kwargs)
        return {**value, "vector_path": self.selected(), "vector_path_switch": "workbench_runtime_center"}

    def update_settings(self, settings):
        self.original.update_settings(settings); return self.status()

    def set_mode(self, mode):
        self.original.set_mode(mode); return self.status()

    def process(self, message, decision):
        mode = self.original.settings["mode"]
        if mode == "off" or mode == "router" and not decision.get("need_retrieval"):
            return self.original.process(message, decision)
        return self._query(message, lambda: self.original.process(message, decision))

    def query(self, message):
        return self._query(message, lambda: self.original.query(message))

    def _query(self, message, invoke_original):
        with self.lock:
            return self._locked_query(message, invoke_original)

    def _locked_query(self, message, invoke_original):
        selected = self.selected()
        if selected == "legacy" or "action_vectors" not in self.original.settings["selected_sources"]:
            return invoke_original()
        # The service lock serializes chat operations; do not persist a temporary
        # source selection or touch the original retriever's configuration file.
        old = self.original.settings
        self.original.settings = {**old, "selected_sources": [x for x in old["selected_sources"] if x != "action_vectors"]}
        try:
            outcome = invoke_original()
        finally:
            self.original.settings = old
        if outcome.status == "needs_clarification":
            return outcome
        if (outcome.plan or {}).get('planning_method') == 'trusted_catalog_capability':
            # Precise catalog/statistic queries have already selected their
            # authoritative SQL source. Do not load a model or add unrelated
            # paragraph evidence to an exact count/date result.
            return replace(outcome, backend_status={**outcome.backend_status, 'action_vectors': {
                'status': 'skipped', 'vector_path': 'paragraph', 'reason': 'not_needed_for_trusted_sql_query'}},
                plan={**outcome.plan, 'selected_sources': old['selected_sources'], 'vector_path': 'paragraph'},
                merge_record={**outcome.merge_record, 'vector_path': 'paragraph'})
        backend = dict(outcome.backend_status)
        hits, refs = list(outcome.merged_items), list(outcome.result_refs)
        context = outcome.context_text
        try:
            legacy_config = self.root / 'config/action/config/concurrent_pipeline_v0001.json'
            if legacy_config.exists():
                port = json.loads(legacy_config.read_text(DEFAULT_ENCODING))['service_port']
                if type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError('legacy_port_invalid')
                try:
                    with urlopen(f'http://127.0.0.1:{port}/status', timeout=1) as response:
                        legacy_live = json.load(response)
                except OSError:
                    legacy_live = None
                if legacy_live and legacy_live.get('version') and legacy_live.get('pid'):
                    raise ValueError('legacy_model_owner_online_stop_before_new_query')
            state = json.loads((self.root / "runtime/anchor_vectors_v0001/service_ready.json").read_text(DEFAULT_ENCODING))
            address = urlparse(state["url"])
            if address.scheme != "http" or address.hostname != "127.0.0.1" or address.path != "/" or address.username or address.password or address.query or address.fragment:
                raise ValueError("paragraph_service_endpoint_invalid")
            with urlopen(state["url"] + "status", timeout=2) as response:
                health = json.load(response)
            if health["instance"] != state["instance"] or health["root"] != str(self.root) or health["pid"] != state["pid"]:
                raise ValueError("paragraph_service_identity_mismatch")
            request = Request(state["url"] + "query", data=json.dumps({"text": message, "n_results": old["top_k"]}).encode(DEFAULT_ENCODING),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + state["token"]})
            with urlopen(request, timeout=max(180, self.original.config["timeout_ms"] / 1000)) as response:
                result = json.load(response)
            if result["status"] != "completed":
                raise ValueError("paragraph_query_failed")
            added = 0
            for row in result["rows"]:
                paragraph = row["paragraph"]
                if row.get("source_verified") is not True:
                    raise ValueError("paragraph_evidence_unverified")
                hit = {"source_id": "action_vectors", "object_id": row["object_id"], "object_kind": "source_paragraph",
                       "source_verified": True, "text": paragraph["text"], "distance": row["distance"], "source_refs": [paragraph],
                       "match_reason": "anchor_paragraph_vector", "ranking_source": "cosine_distance"}
                hits.append(hit); refs.append({"source_id": "action_vectors", "paragraph_id": row["object_id"], "locator": paragraph["locator"]})
                evidence = "\n" + json.dumps({"paragraph_id": row["object_id"], "role": paragraph["role"], "event_time": paragraph["event_time"],
                                                    "message_id": paragraph["message_id"], "conversation_key": paragraph["conversation_key"],
                                                    "text": paragraph["text"], "source_verified": True}, ensure_ascii=False)
                remaining = self.original.config["max_context_chars"] - len(context)
                if len(evidence) <= remaining:
                    context += evidence; added += 1
            backend["action_vectors"] = {"status": "completed", "vector_path": "paragraph", "result_count": len(result["rows"]),
                                         "context_paragraphs": added, "derived_state": result["derived_state"], "context_budget_omitted": len(result["rows"]) - added}
            # There are no other selected sources in a vector-only request.
            status = "partial" if any(v.get("status") in {"failed", "rejected", "partial"} for v in backend.values()) else "completed"
        except Exception as exc:
            backend["action_vectors"] = {"status": "failed", "vector_path": "paragraph", "error_type": type(exc).__name__,
                                         "message": "新段落向量查询失败或未启动；没有自动使用旧路径。请在运行中心查看状态并启动新服务。"}
            status = "partial" if any(v.get("status") == "completed" for v in backend.values()) else "failed"
        final = replace(outcome, retrieval_transaction_id=outcome.retrieval_transaction_id or "apq_" + uuid.uuid4().hex,
                       status=status, backend_status=backend, merged_items=hits, context_text=context, result_refs=refs,
                       plan={**(outcome.plan or {}), "selected_sources": old["selected_sources"], "vector_path": "paragraph"},
                       executions=[*outcome.executions, {"backend": "action_vectors", **backend["action_vectors"]}],
                       merge_record={**outcome.merge_record, "vector_path": "paragraph", "deduplicated": False})
        archive = Path(self.original.config["archive_root"])
        if not archive.resolve().is_relative_to(self.root):
            raise ValueError("paragraph_archive_outside_project")
        archive.mkdir(parents=True, exist_ok=True)
        with (archive / (final.retrieval_transaction_id + "_paragraph_outcome.json")).open("x", encoding=DEFAULT_ENCODING) as handle:
            json.dump(asdict(final), handle, ensure_ascii=False)
        return final
