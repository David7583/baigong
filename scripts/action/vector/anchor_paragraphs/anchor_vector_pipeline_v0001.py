# ============================================================
# 文件名: anchor_vector_pipeline_v0001.py
# 中文名: 独立锚点段落向量入库流水线
# 版本号: v0001
#
# 主层级: action
# 层级: workbench / independent_tools / anchor_vectors
# 脚本定位: 可脱离工作台执行的新向量编排入口
#
# 职责说明:
# - 恢复原段落，经 Action AI 总控计算并校验新索引
#
# 本脚本做什么:
# - 按完整锚点增量扫描、原文恢复、单条嵌入、验证提交、断点续跑
# - 提供状态、有限任务、查询及失败记录
#
# 本脚本不做什么:
# - 不修改旧向量路径，不修改事实库，不自动登记或授予 AI 许可
#
# 制度边界声明:
# - 只写新配置指定的独立 Chroma、派生 SQLite 和日志；原库只读
# - 模型请求统一经过 Action 总控；无凭证、拒绝或超长准确失败
# - 完整原段落不截断；测试数据写隔离目录，缓存与提交可重放
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: anchor_vector_pipeline_v0001
# family: anchor_vector_pipeline
# role: independent_anchor_vector_orchestration
# version: v0001
# status: active
# entry_point: scripts/action/vector/anchor_paragraphs/anchor_vector_pipeline_v0001.py
# input:
#   - root, versioned config, explicit bounded run or query
# output:
#   - independent verified vector store, resumable ledger and classified logs
# depends_on:
#   - anchor_paragraph_source_v0001 JSON file interface
#   - action_ai_controller_v0002
#   - chromadb, tokenizers
# used_by:
#   - anchor_vector_service_v0001
#   - workbench_runtime_control_v0002
# ============================================================
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "anchor_vector_pipeline"
SCRIPT_NAME = "anchor_vector_pipeline_v0001"
SCRIPT_VERSION = "v0001"


class PipelineError(ValueError):
    pass


class ReconstructionError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode(DEFAULT_ENCODING)).hexdigest()


def save_json(file, value):
    temporary = file.with_suffix(".tmp")
    with temporary.open("w", encoding=DEFAULT_ENCODING) as stream:
        json.dump(value, stream, ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(file)


class SourceFileTasks:
    """JSON file transport to one read-only source actor; no SQL or reconstruction here."""
    def __init__(self, root, database):
        self.root, self.database = Path(root).resolve(), database
        self.run_id = "paragraph_" + uuid.uuid4().hex
        self.folder = self.root / "runtime/intermediate_tasks" / self.run_id
        self.folder.mkdir(parents=True)
        self.manifest = self.folder / "task.json"
        self.state = {"schema_version": "intermediate-task.v1", "run_id": self.run_id,
                      "status": "running", "active": True, "result_verified": False,
                      "recovery_required": True, "consumers": [{"id": SCRIPT_NAME, "acknowledged": False}],
                      "intermediates": []}
        save_json(self.manifest, self.state)
        session = self.folder / "session.json"
        save_json(session, {"schema_version": "paragraph-source-session.v1", "run_id": self.run_id,
                            "root": str(self.root), "source_db": database})
        self.sequence, self.files = 0, [session]
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("anchor_paragraph_source_v0001.py")),
                                         "--session", str(session)], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)

    def request(self, operation, **payload):
        self.sequence += 1
        request, response = [self.folder / f"{kind}_{self.sequence:06d}.json" for kind in ("request", "response")]
        self.files.extend((request, response))
        save_json(request, {"schema_version": "paragraph-source.v1", "run_id": self.run_id,
                           "root": str(self.root), "source_db": self.database, "operation": operation,
                           "producer": SCRIPT_NAME, "producer_version": SCRIPT_VERSION, **payload})
        while not response.exists():
            if self.process.poll() is not None:
                raise PipelineError("paragraph_source_worker_exited")
            time.sleep(.02)
        if response.is_symlink() or response.stat().st_size > 16 * 1024 * 1024:
            raise PipelineError("paragraph_source_response_invalid")
        value = json.loads(response.read_text(DEFAULT_ENCODING))
        if value.get("schema_version") != "paragraph-source.v1" or value.get("run_id") != self.run_id or value.get("producer") != "anchor_paragraph_source_v0001" or value.get("producer_version") != "v0001":
            raise PipelineError("paragraph_source_response_contract_invalid")
        if value.get("status") != "completed":
            raise PipelineError(value.get("detail", "paragraph_source_worker_failed"))
        return value["result"]

    def finish(self, acknowledged):
        # Finish source reads and close their handles before acknowledging the
        # durable downstream result; no forced termination in the normal path.
        stop = self.folder / "stop.json"
        save_json(stop, {"run_id": self.run_id, "operation": "close"})
        self.files.append(stop)
        code = self.process.wait()
        acknowledged = acknowledged and code == 0
        self.state.update(status="completed" if acknowledged else "failed", active=False,
                          result_verified=acknowledged, recovery_required=not acknowledged)
        self.state["consumers"][0]["acknowledged"] = acknowledged
        self.state["intermediates"] = [{"path": p.relative_to(self.root).as_posix(),
                                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                                       "regenerable": True, "protected": False} for p in self.files if p.exists()]
        save_json(self.manifest, self.state)
        if acknowledged:
            worker = self.root / "scripts/action/infrastructures/intermediate_cleanup_v0001.py"
            if not worker.is_file():
                worker = Path(__file__).parents[4] / "scripts/action/infrastructures/intermediate_cleanup_v0001.py"
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            result = subprocess.run([sys.executable, "-B", str(worker), "--root", str(self.root),
                                     "--manifest", self.manifest.relative_to(self.root).as_posix()],
                                    capture_output=True, text=True, encoding=DEFAULT_ENCODING, **flags)
            if result.returncode:
                raise PipelineError("paragraph_source_cleanup_pending")
        elif code != 0:
            raise PipelineError("paragraph_source_worker_exit_failed")


def project_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise PipelineError("path_outside_project")
    return path


def load_config(root, filename):
    value = json.loads(project_path(root, filename).read_text(DEFAULT_ENCODING))
    if value.get("schema_version") != "anchor-vectors.v1":
        raise PipelineError("config_schema_mismatch")
    for key in ("source_db", "state_dir", "chroma_path", "tokenizer", "controller_config", "credential_file"):
        project_path(root, value[key])
    for key, low, high in (("max_tokens", 64, 4096), ("max_paragraphs", 1, 1000), ("max_work_seconds", 5, 3600), ("interval_seconds", 0, 30)):
        if type(value[key]) not in (int, float) or isinstance(value[key], bool) or not low <= value[key] <= high:
            raise PipelineError("config_resource_limit_invalid")
    if value["chroma_path"] == "chroma/action/concurrent_vectors" or value["state_dir"] == "runtime/concurrent_pipeline":
        raise PipelineError("legacy_path_reuse_forbidden")
    return value


class Pipeline:
    def __init__(self, root, config, embedder=None, token_counter=None):
        self.root = Path(root).resolve()
        self.config = config
        self.directory = project_path(self.root, config["state_dir"])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ledger = self.directory / "ledger.sqlite3"
        self.stop = threading.Event()
        self.lock, self.model_lock = threading.RLock(), threading.RLock()
        self.embedder, self.token_counter = embedder, token_counter
        self.controller = self.client = self.collection = None
        self.running, self.last_error, self.worker = False, None, None
        self.draining = False
        self.context = fingerprint({"policy": SCRIPT_VERSION, "model": config["model"], "dimension": config["dimension"],
                                    "tokenizer": config["tokenizer"], "max_tokens": config["max_tokens"],
                                    "model_revision": config.get("model_revision"), "tokenizer_sha256": config.get("tokenizer_sha256"),
                                    "expected_descriptor": config.get("expected_descriptor")})
        with self.db() as db:
            db.executescript("CREATE TABLE IF NOT EXISTS control(k TEXT PRIMARY KEY,v TEXT);"
                             "CREATE TABLE IF NOT EXISTS paragraphs(id TEXT PRIMARY KEY,payload TEXT NOT NULL,signature TEXT,state TEXT NOT NULL,error TEXT);"
                             "CREATE TABLE IF NOT EXISTS processed_units(source_rowid INTEGER PRIMARY KEY,paragraph_id TEXT);"
                             "CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,created REAL,kind TEXT,detail TEXT);")
            prior = db.execute("SELECT v FROM control WHERE k='context'").fetchone()
            if prior and prior[0] != self.context:
                raise PipelineError("encoding_context_changed_use_new_store")
            db.execute("INSERT OR IGNORE INTO control VALUES ('context',?)", (self.context,))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.ledger, timeout=5)
        db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, kind, detail):
        with self.db() as db:
            db.execute("INSERT INTO events(created,kind,detail) VALUES (?,?,?)", (time.time(), kind, canonical(detail)))

    def status(self):
        with self.db() as db:
            states = dict(db.execute("SELECT state,COUNT(*) FROM paragraphs GROUP BY state"))
            cursor = db.execute("SELECT v FROM control WHERE k='cursor'").fetchone()
            error = db.execute("SELECT detail FROM events WHERE kind='failed' ORDER BY seq DESC LIMIT 1").fetchone()
        return {"status": "pausing" if self.draining else "running" if self.running else "paused", "version": SCRIPT_VERSION, "path": "paragraph",
                "gpu_released": False,
                "indexed_paragraphs": states.get("committed", 0), "pending_paragraphs": states.get("pending", 0),
                "rejected_paragraphs": states.get("rejected", 0), "source_cursor": int(cursor[0]) if cursor else 0,
                "coverage": "incremental_partial_until_scan_complete", "scan_complete": self.get("scan_complete", False),
                "last_error": self.last_error or (json.loads(error[0]) if error else None),
                "limits": {k: self.config[k] for k in ("max_tokens", "max_paragraphs", "max_work_seconds", "interval_seconds")}}

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT v FROM control WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _collection(self):
        with self.lock:
            return self._locked_collection()

    def _locked_collection(self):
        if self.collection is None:
            import chromadb
            from chromadb.config import Settings
            client = chromadb.PersistentClient(path=str(project_path(self.root, self.config["chroma_path"])), settings=Settings(anonymized_telemetry=False))
            self.chroma_client = client
            self.collection = client.get_or_create_collection("anchor_paragraphs_v0001", metadata={"hnsw:space": "cosine", "encoding_context": self.context})
            if self.collection.metadata.get("encoding_context") != self.context:
                raise PipelineError("collection_encoding_context_conflict")
        return self.collection

    def _tokens(self, text):
        if self.token_counter:
            return self.token_counter(text)
        from tokenizers import Tokenizer
        if not hasattr(self, "tokenizer"):
            file = project_path(self.root, self.config["tokenizer"])
            if hashlib.sha256(file.read_bytes()).hexdigest() != self.config.get("tokenizer_sha256"):
                raise PipelineError("tokenizer_encoding_context_changed")
            self.tokenizer = Tokenizer.from_file(str(file))
        return len(self.tokenizer.encode(text).ids)

    def _embed(self, text):
        if self._tokens(text) + 64 > self.config["max_tokens"]:
            raise PipelineError("embedding_text_exceeds_resource_budget")
        with self.model_lock:
            if self.embedder:
                vector = self.embedder(text)
            else:
                if self.controller is None:
                    module_path = self.root / "scripts/action/infrastructures/action_ai_controller_v0002.py"
                    name = "_anchor_control_v0002"
                    if name not in sys.modules:
                        spec = importlib.util.spec_from_file_location(name, module_path)
                        module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
                    cls = sys.modules[name].AIController
                    credential = json.loads(project_path(self.root, self.config["credential_file"]).read_text(DEFAULT_ENCODING))
                    if credential.get("app_id") != self.config["app_id"]:
                        raise PipelineError("application_identity_mismatch")
                    self.controller = cls(project_path(self.root, self.config["controller_config"]), root=self.root)
                    descriptor = self.controller._source("local_embeddings")["models"][self.config["model"]]
                    if descriptor != self.config.get("expected_descriptor"):
                        raise PipelineError("model_descriptor_encoding_context_changed")
                    self.client = self.controller.client(credential["app_id"], credential["token"])
                    self.client.select("local_embeddings", self.config["model"]); self.client.start()
                result = self.client.invoke("embed", {"input": [text]})
                if result.get("status") != "completed":
                    raise PipelineError("controlled_embedding_not_completed")
                vector = result["data"]["embeddings"][0]
            if len(vector) != self.config["dimension"] or any(not math.isfinite(float(x)) for x in vector):
                raise PipelineError("vector_shape_or_value_invalid")
            return vector

    def commit(self, paragraph):
        text = paragraph["text"]
        if len(text) > 32000 or self._tokens(text) + 64 > self.config["max_tokens"]:
            raise ReconstructionError("paragraph_exceeds_token_budget_not_truncated")
        signature = fingerprint([self.context, paragraph["text_sha256"]])
        collection = self._collection()
        # A verified Chroma write may precede a crash before ledger commit.
        prior = collection.get(ids=[paragraph["paragraph_id"]], include=["metadatas", "embeddings"])
        if prior["ids"] and prior["metadatas"][0].get("signature") == signature:
            vector = [float(x) for x in prior["embeddings"][0]]
            if len(vector) != self.config["dimension"] or any(not math.isfinite(x) for x in vector):
                raise PipelineError("cached_vector_invalid")
        else:
            vector = self._embed(text)
        metadata = {"signature": signature, "text_sha256": paragraph["text_sha256"], "role": paragraph["role"],
                    "message_id": paragraph["message_id"], "conversation_key": paragraph["conversation_key"],
                    "event_time": paragraph["event_time"], "anchor_id": paragraph["paragraph_id"], "policy_version": SCRIPT_VERSION}
        collection.upsert(ids=[paragraph["paragraph_id"]], embeddings=[vector], metadatas=[metadata])
        actual = collection.get(ids=[paragraph["paragraph_id"]], include=["metadatas", "embeddings"])
        if actual["ids"] != [paragraph["paragraph_id"]] or actual["metadatas"][0] != metadata or len(actual["embeddings"][0]) != len(vector) or any(abs(float(a) - float(b)) > 1e-6 for a, b in zip(actual["embeddings"][0], vector)):
            raise PipelineError("vector_write_verification_failed")
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO paragraphs VALUES (?,?,?,'committed',NULL)", (paragraph["paragraph_id"], canonical(paragraph), signature))
            db.executemany("INSERT OR REPLACE INTO processed_units VALUES (?,?)", [(r, paragraph["paragraph_id"]) for r in paragraph["source_rowids"]])

    def run(self, max_paragraphs=None, max_work_seconds=None):
        with self.lock:
            if self.draining:
                raise PipelineError("pipeline_draining")
            if self.running:
                raise PipelineError("pipeline_already_running")
            self.running = True; self.stop.clear(); self.last_error = None
        limit = self.config["max_paragraphs"] if max_paragraphs is None else max_paragraphs
        seconds = self.config["max_work_seconds"] if max_work_seconds is None else max_work_seconds
        if type(limit) is not int or not 1 <= limit <= 1000 or type(seconds) not in (int, float) or not 5 <= seconds <= 3600:
            self.running = False; raise PipelineError("run_limits_invalid")
        source = None; acknowledged = False; attempted = 0; started = time.monotonic()
        try:
            source = SourceFileTasks(self.root, self.config["source_db"])
            while attempted < limit and time.monotonic() - started < seconds and not self.stop.is_set():
                items = source.request("batch", after=self.get("cursor", 0), limit=min(100, max(1, limit - attempted)))["items"]
                if not items:
                    with self.db() as db:
                        db.execute("INSERT OR REPLACE INTO control VALUES ('scan_complete','true')")
                    break
                with self.db() as db:
                    db.execute("INSERT OR REPLACE INTO control VALUES ('scan_complete','false')")
                for item in items:
                    seed = item["seed"]
                    if attempted >= limit or self.stop.is_set() or time.monotonic() - started >= seconds:
                        break
                    with self.db() as db:
                        done = db.execute("SELECT 1 FROM processed_units WHERE source_rowid=?", (seed["source_rowid"],)).fetchone()
                    if not done:
                        attempted += 1
                        group_id = "ap_" + fingerprint({k: seed[k] for k in ("asset_id", "path", "value_index", "segment_index")})
                        try:
                            if "error" in item:
                                raise ReconstructionError(item["error"])
                            paragraph = item["paragraph"]
                            with self.db() as db:
                                db.execute("INSERT OR REPLACE INTO paragraphs VALUES (?,?,NULL,'pending',NULL)", (group_id, canonical(paragraph)))
                            self.commit(paragraph)
                        except ReconstructionError as exc:
                            with self.db() as db:
                                db.execute("INSERT OR REPLACE INTO paragraphs VALUES (?,?,NULL,'rejected',?)", (group_id, canonical(seed), str(exc)))
                                db.execute("INSERT OR REPLACE INTO processed_units VALUES (?,?)", (seed["source_rowid"], group_id))
                            self.event("rejected", {"paragraph_id": group_id, "error_type": str(exc)})
                    # The current unit must durably finish before pause is observed.
                    with self.db() as db:
                        db.execute("INSERT OR REPLACE INTO control VALUES ('cursor',?)", (str(seed["source_rowid"]),))
                    if not self.stop.is_set():
                        self.stop.wait(self.config["interval_seconds"])
            acknowledged = True
            self.event("run_finished", {"attempted": attempted, "elapsed_seconds": round(time.monotonic() - started, 3)})
        except Exception as exc:
            safe_detail = str(exc) if type(exc).__name__ in {"PipelineError", "ReconstructionError", "LocalEmbeddingError", "PermissionDenied", "ConfigError", "DataError"} else "unexpected_operation_failure"
            self.last_error = {"error_type": type(exc).__name__, "detail": safe_detail, "recovery": "inspect_log_and_explicitly_resume"}
            self.event("failed", self.last_error)
            raise
        finally:
            try:
                if source:
                    source.finish(acknowledged)
            finally:
                self.running = False
        return self.status()

    def start(self, settings=None):
        settings = settings or {}
        with self.lock:
            if self.draining:
                raise PipelineError("pipeline_draining")
            if self.running or self.worker and self.worker.is_alive():
                raise PipelineError("pipeline_already_running")
            def work():
                try:
                    self.run(**settings)
                except Exception:
                    pass  # run persists classified failure before this thread exits.
            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()
        return {"status": "accepted", "bounded_run": True}

    def query(self, text, count=10, query_embedding=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000 or type(count) is not int or not 1 <= count <= 100:
            raise PipelineError("query_limits_invalid")
        with self.db() as db:
            total = db.execute("SELECT COUNT(*) FROM paragraphs WHERE state='committed'").fetchone()[0]
        if not total:
            raise PipelineError("paragraph_index_empty_run_pipeline_first")
        vector = query_embedding or self._embed(text)
        if len(vector) != self.config["dimension"] or any(not math.isfinite(float(x)) for x in vector):
            raise PipelineError("query_vector_invalid")
        raw = self._collection().query(query_embeddings=[vector], n_results=min(total, count), include=["metadatas", "distances"])
        rows = []
        source = SourceFileTasks(self.root, self.config["source_db"])
        acknowledged = False
        try:
            for index, pid in enumerate(raw["ids"][0]):
                with self.db() as db:
                    record = db.execute("SELECT payload,signature FROM paragraphs WHERE id=? AND state='committed'", (pid,)).fetchone()
                if not record or raw["metadatas"][0][index].get("signature") != record[1]:
                    continue
                paragraph = json.loads(record[0])
                rows.append({"object_id": pid, "distance": float(raw["distances"][0][index]), "paragraph": paragraph,
                             "source_verified": True, "verification": "source_hash_verified_restoration_and_live_SQLite_anchor_check"})
            verified = source.request("verify", paragraphs=[row["paragraph"] for row in rows])
            if verified.get("verified_ids") != [row["object_id"] for row in rows]:
                raise PipelineError("query_source_verification_response_invalid")
            acknowledged = True
        finally:
            source.finish(acknowledged)
        return {"status": "completed", "rows": rows, "derived_state": self.status(), "truncated": total > count}

    def request_pause(self):
        with self.lock:
            self.draining = True
            self.stop.set()
        return {"status": "pausing", "finish_current_unit": True, "gpu_released": False}

    def close(self):
        self.request_pause()
        if self.worker:
            # Normal pause never abandons an embedding or its verification/commit.
            self.worker.join()
        if self.controller and not self.running:
            self.controller.close()
        if getattr(self, "chroma_client", None) is not None and not self.running:
            # Chroma's process-local owner otherwise keeps Windows file handles
            # alive after tests/service shutdown; do not stop a running worker.
            self.chroma_client._system.stop()
            from chromadb.api.shared_system_client import SharedSystemClient
            SharedSystemClient._identifier_to_system.pop(self.chroma_client._identifier, None)
            self.collection = None
            self.chroma_client = None


def _build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", default="config/action/config/anchor_vector_pipeline_v0001.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-paragraphs", type=int)
    return parser


def main():
    args = _build_parser().parse_args()
    try:
        root = Path(args.root).resolve(); config = load_config(root, args.config)
        if args.dry_run:
            source = SourceFileTasks(root, config["source_db"])
            acknowledged = False
            try:
                items = source.request("batch", after=0, limit=1)["items"]
                if items and "error" in items[0]:
                    raise ReconstructionError(items[0]["error"])
                paragraph = items[0]["paragraph"] if items else None
                print(canonical({"status": "dry-run", "paragraph_verified": bool(paragraph), "units": len(paragraph["anchors"]) if paragraph else 0}))
                acknowledged = True
            finally:
                source.finish(acknowledged)
            return 0
        pipeline = Pipeline(root, config)
        try:
            print(canonical(pipeline.run(args.max_paragraphs)))
        finally:
            pipeline.close()
        return 0
    except (PipelineError, ReconstructionError) as exc:
        print(canonical({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)})); return 2
    except Exception as exc:
        print(canonical({"status": "error", "error_type": type(exc).__name__})); return 3


if __name__ == "__main__":
    raise SystemExit(main())
