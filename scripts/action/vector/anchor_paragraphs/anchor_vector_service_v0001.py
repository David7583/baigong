# ============================================================
# 文件名: anchor_vector_service_v0001.py
# 中文名: 独立段落向量服务
# 版本号: v0001
#
# 主层级: action
# 层级: workbench / independent_tools / anchor_vectors
# 脚本定位: 独立流水线的回环 HTTP 入口
#
# 职责说明:
# - 对显式运行、暂停、查询提供受认证的服务接口
#
# 本脚本做什么:
# - 启动空闲服务、返回真实状态、承载新流水线
#
# 本脚本不做什么:
# - 不接管旧服务，不默认开始全量计算，不自动授予 AI 权限
#
# 制度边界声明:
# - 绑定 127.0.0.1，私有凭证只写自身 ready 文件；请求有大小限制
# - 原始资料只读，新索引和日志限新配置；模型通过 Action 总控
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: anchor_vector_service_v0001
# family: anchor_vector_service
# role: authenticated_anchor_vector_service
# version: v0001
# status: active
# entry_point: scripts/action/vector/anchor_paragraphs/anchor_vector_service_v0001.py
# input:
#   - root, config, instance and private ready-file
# output:
#   - authenticated loopback service, ready identity and pipeline status
# depends_on:
#   - anchor_vector_pipeline_v0001
# used_by:
#   - workbench_runtime_control_v0002
#   - workbench_chat_host_v0002
# ============================================================
from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

from anchor_vector_pipeline_v0001 import Pipeline, PipelineError, load_config, project_path
from anchor_paragraph_source_v0001 import canonical

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "anchor_vector_service"
SCRIPT_NAME = "anchor_vector_service_v0001"
SCRIPT_VERSION = "v0001"


def serve(root, config, ready, instance):
    pipeline = Pipeline(root, config)
    token = secrets.token_hex(32)
    admission = threading.RLock()
    pause_lock = threading.Lock()
    draining = threading.Event()

    def drain():
        # Stop HTTP admission, wait for accepted requests, then finish the worker.
        # server_close waits for existing query threads; close waits for commit.
        server.shutdown()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, value):
            data = canonical(value).encode(DEFAULT_ENCODING)
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            if self.path == "/status":
                return self.respond(200, {**pipeline.status(), "root": str(root), "instance": instance, "pid": os.getpid()})
            self.respond(404, {"status": "not_found"})

        def do_POST(self):
            if self.headers.get("Authorization") != "Bearer " + token:
                return self.respond(403, {"status": "denied"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1048576:
                    raise PipelineError("request_size_invalid")
                body = json.loads(self.rfile.read(length))
                if self.path == "/query":
                    with admission:
                        if draining.is_set():
                            raise PipelineError("service_draining")
                        # Holding admission until completion defines an accepted unit.
                        result = pipeline.query(body.get("text"), body.get("n_results", 10), body.get("query_embedding"))
                    return self.respond(200, result)
                if self.path == "/control":
                    operation = body.get("operation")
                    if operation == "start":
                        # This test-only injection is impossible through the HTTP interface.
                        settings = body.get("settings", {})
                        if set(settings) - {"max_paragraphs", "max_work_seconds"}:
                            raise PipelineError("settings_invalid")
                        for key, low, high in (("max_paragraphs", 1, 1000), ("max_work_seconds", 5, 3600)):
                            if key in settings and (type(settings[key]) is not int or not low <= settings[key] <= high):
                                raise PipelineError("settings_invalid")
                        with admission:
                            if draining.is_set():
                                raise PipelineError("service_draining")
                            result = pipeline.start(settings)
                        return self.respond(202, result)
                    if operation in {"pause", "shutdown"}:
                        # Publish the pause intention immediately; do not wait for
                        # a query holding admission before blocking future work.
                        with pause_lock:
                            if not draining.is_set():
                                draining.set()
                                pipeline.request_pause()
                                state["lifecycle"] = "pausing"
                                temporary.write_text(canonical(state), DEFAULT_ENCODING)
                                temporary.replace(ready)
                                self.respond(202, {"status": "pausing", "finish_current_unit": True, "gpu_released": False})
                                threading.Thread(target=drain, daemon=False).start()
                                return
                        return self.respond(202, {"status": "pausing", "finish_current_unit": True, "gpu_released": False})
                    if operation == "retry_rejected":
                        if pipeline.running or draining.is_set():
                            raise PipelineError("pause_before_retry")
                        with pipeline.db() as db:
                            db.execute("DELETE FROM processed_units WHERE paragraph_id IN (SELECT id FROM paragraphs WHERE state='rejected')")
                            db.execute("DELETE FROM paragraphs WHERE state='rejected'")
                            db.execute("INSERT OR REPLACE INTO control VALUES ('cursor','0')")
                            db.execute("INSERT OR REPLACE INTO control VALUES ('scan_complete','false')")
                        return self.respond(200, {"status": "ready_to_retry"})
                raise PipelineError("operation_unknown")
            except (ValueError, PipelineError) as exc:
                self.respond(400, {"status": "error", "error_type": type(exc).__name__, "detail": str(exc)})
            except Exception as exc:
                pipeline.event("request_failed", {"error_type": type(exc).__name__})
                self.respond(503, {"status": "error", "error_type": type(exc).__name__})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state = {"url": f"http://127.0.0.1:{server.server_address[1]}/", "token": token, "instance": instance,
             "root": str(root), "pid": os.getpid(), "version": SCRIPT_VERSION, "lifecycle": "ready"}
    ready.parent.mkdir(parents=True, exist_ok=True)
    temporary = ready.with_suffix(".tmp"); temporary.write_text(canonical(state), DEFAULT_ENCODING); temporary.replace(ready)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close(); pipeline.close()


def _build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", default="config/action/config/anchor_vector_pipeline_v0001.json")
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main():
    args = _build_parser().parse_args()
    # pythonw does not expose standard streams; persist diagnostic failures.
    if sys.stdout is None:
        log = Path(args.root).resolve() / 'logs/anchor_vector_service.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = log.open('a', encoding=DEFAULT_ENCODING, buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    try:
        root = Path(args.root).resolve(); config = load_config(root, args.config)
        ready = project_path(root, args.ready_file)
        if args.dry_run:
            print(canonical({"status": "dry-run", "config_valid": True})); return 0
        serve(root, config, ready, args.instance); return 0
    except Exception as exc:
        print(canonical({"status": "error", "error_type": type(exc).__name__})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
