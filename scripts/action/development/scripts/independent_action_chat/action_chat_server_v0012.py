# ============================================================
# 文件名: action_chat_server_v0012.py
# 中文名: 新版独立 Action Chat HTTP 服务入口脚本
# 版本号: v0012
#
# 主层级: action
# 层级: development / independent_action_chat / http_server
# 脚本定位: 本地浏览器与 ActionChatService 之间的唯一 HTTP/静态文件入口
#
# 职责说明:
# - 在 127.0.0.1 提供版本化 JSON API 和静态 Web 页面
# - 校验请求大小、路径和 JSON，并管理服务 PID/关闭入口
#
# 本脚本做什么:
# - 将 HTTP 方法映射到稳定 Chat Service 方法
# - 在第二阶段模块存在时装配只读 Retrieval Extension
# - 提供显式本机管理员许可接口，通过总控公开 API 修改并审计权限
#
# 本脚本不做什么:
# - 不实现 Router、Provider、检索或会话业务，不监听外部网卡
#
# 制度边界声明:
# - 静态路径必须位于配置的 Web 根目录，响应不含密钥
# - 分类错误返回 JSON；服务导入时不自动启动
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_server_v0008
# family: action_chat_server
# role: local_http_entrypoint
# version: v0012
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_server_v0012.py
# input:
#   - local HTTP requests and versioned configuration
# output:
#   - local JSON API and Web UI
# depends_on:
#   - Python stdlib: argparse, http.server, json, mimetypes, pathlib, threading, urllib
#   - action_chat_config_v0002.py
#   - action_chat_service_v0005.py
#   - action_chat_providers_v0006.py
#   - action_chat_retrieval_v0006.py
# used_by:
#   - start_action_chat_v0008.ps1
#   - status_action_chat_v0001.ps1
#   - stop_action_chat_v0001.ps1
# ============================================================

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .action_chat_config_v0002 import (
    open_controller,
    find_project_root,
    load_chat_config,
    resolve_project_path,
)
from .action_chat_contracts_v0001 import ActionChatError, classified_error, stable_json
from .action_chat_service_v0005 import ActionChatService
from .action_chat_retrieval_v0006 import DEFAULT_RETRIEVAL_CONFIG, load_retrieval_config


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_server"
SCRIPT_NAME = "action_chat_server_v0012"
SCRIPT_VERSION = "v0012"
DEFAULT_CHAT_CONFIG = "config/action/config/independent_action_chat_config_v0003.local.yml"
MAX_REQUEST_BYTES = 12 * 1024 * 1024


# ============================================================
# 异常类型
# ============================================================

class HttpRequestError(ActionChatError):
    """Raised for malformed, unsupported, or oversized local HTTP requests."""


# ============================================================
# 数据结构
# ============================================================

# Handler dependencies are injected as class attributes by build_server().


# ============================================================
# 工具函数区
# ============================================================

def _resolve_static_path(web_root: Path, request_path: str) -> Path:
    relative = request_path.lstrip("/") or "index_v0009.html"
    target = (web_root / relative).resolve()
    try:
        target.relative_to(web_root.resolve())
    except ValueError as exc:
        raise HttpRequestError("static path escapes web root") from exc
    if not target.is_file():
        raise FileNotFoundError(relative)
    return target


# ============================================================
# 默认映射
# ============================================================

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


# ============================================================
# 核心类
# ============================================================

class ActionChatRequestHandler(BaseHTTPRequestHandler):
    jobs: Any
    service: ActionChatService
    web_root: Path
    server_version = "IndependentActionChat/0.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        del format_string, args

    def _json(self, status_code: int, payload: Dict[str, Any]) -> None:
        body = stable_json(payload).encode(DEFAULT_ENCODING)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise HttpRequestError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise HttpRequestError("request body is too large")
        body = self.rfile.read(length)
        try:
            value = json.loads(body.decode(DEFAULT_ENCODING) or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpRequestError("request body must be a UTF-8 JSON object") from exc
        if not isinstance(value, dict):
            raise HttpRequestError("request body must be a JSON object")
        return value

    def _handle_error(self, exc: BaseException) -> None:
        status = HTTPStatus.BAD_REQUEST if isinstance(exc, ActionChatError) else HTTPStatus.INTERNAL_SERVER_ERROR
        self._json(int(status), {"status": "error", "error": classified_error(exc)})

    def _permission_status(self, control):
        app_id = self.service.config.raw["ai_controller"]["app_id"]
        snapshot = control.status(after="", limit=1000)
        app = next((a for a in snapshot["applications"] if a["app_id"] == app_id), None)
        if app is None:
            raise HttpRequestError("聊天应用尚未登记")
        return {"status": "ok", "global_enabled": snapshot["enabled"],
                "app_id": app_id, "app_allowed": bool(app["allowed"]),
                "app_running": bool(app["running"]), "retired": bool(app["retired"])}

    def _permissions(self, body=None):
        # Explicit local administrator route; never called by normal chat invocation.
        expected = f"127.0.0.1:{self.server.server_address[1]}"
        if self.headers.get("Host") != expected:
            raise HttpRequestError("本机管理接口 Host 无效")
        if body is not None:
            if (self.headers.get("Origin") != f"http://{expected}"
                    or self.headers.get("X-Action-Admin") != "1"
                    or self.headers.get_content_type() != "application/json"):
                raise HttpRequestError("许可修改必须来自本机聊天页面")
            if (set(body) - {"scope", "enabled", "expected_session_id"}
                    or body.get("scope") not in {"global", "application"}
                    or type(body.get("enabled")) is not bool):
                raise HttpRequestError("许可请求无效")
        config = self.service.config
        with open_controller(config.project_root, config.raw["ai_controller"]) as control:
            self._permission_status(control)
            if body is not None:
                if body["scope"] == "global":
                    control.set_global(body["enabled"])
                else:
                    control.set_permission(config.raw["ai_controller"]["app_id"], body["enabled"])
            return self._permission_status(control)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/jobs/status":
                self._json(200, self.jobs.status())
                return

            if parsed.path == "/api/admin/ai-permissions":
                self._json(200, self._permissions())
                return
            if parsed.path == "/api/chat/attachments/status":
                self._json(200, self.service.attachment_status())
                return
            if parsed.path.startswith("/api/chat/attachments/"):
                query = parse_qs(parsed.query)
                session = query.get("session_id", [self.service.state["session_id"]])[0]
                record, data = self.service.attachments.read(session, parsed.path.rsplit("/", 1)[-1])
                self.send_response(200)
                self.send_header("Content-Type", record["mime"])
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", "inline" if record["kind"] == "image" else "attachment")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.path == "/api/status":
                self._json(200, self.service.status())
                return
            if parsed.path == "/api/ai/settings":
                self._json(200, self.service.ai_settings())
                return
            if parsed.path == "/api/providers":
                self._json(200, self.service.provider_catalog())
                return
            if parsed.path == "/api/router/status":
                self._json(200, {"status": "ok", **self.service.router_status()})
                return
            if parsed.path == "/api/retrieval/status":
                probe = parse_qs(parsed.query).get("probe", ["0"])[0] in {"1", "true", "yes"}
                self._json(200, {"status": "ok", **self.service.retrieval.status(probe=probe)})
                return
            if parsed.path == "/api/sessions":
                self._json(200, self.service.list_sessions())
                return
            if parsed.path == "/api/chat/messages":
                query = parse_qs(parsed.query)
                try:
                    limit = int(query.get("limit", ["100"])[0])
                    before = int(query["before"][0]) if "before" in query else None
                except ValueError as exc:
                    raise HttpRequestError("invalid history cursor or limit") from exc
                self._json(200, self.service.session_history(
                    query.get("session_id", [None])[0], before=before, limit=limit,
                ))
                return
            target = _resolve_static_path(self.web_root, parsed.path)
            body = target.read_bytes()
            content_type = CONTENT_TYPES.get(target.suffix.lower(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._json(404, {"status": "error", "error": {"error_type": "NotFound", "detail": "resource not found"}})
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            body = self._body()
            if parsed.path.startswith("/api/jobs/"):
                if self.headers.get("X-Action-Jobs") != "1":
                    raise HttpRequestError("Explicit jobs UI request required")
                if parsed.path == "/api/jobs/scan":
                    result=self.jobs.spawn("scan",folder=body.get("folder"))
                elif parsed.path == "/api/jobs/enqueue":
                    result=self.jobs.enqueue(body.get("ids"))
                elif parsed.path == "/api/jobs/start":
                    if body.get("confirm_write") is not True:
                        raise HttpRequestError("请确认写入业务库并保留中间产物")
                    result=self.jobs.spawn("run",vector=body.get("vector_target","none"))
                elif parsed.path == "/api/jobs/vectors/start":
                    if body.get("confirm_model") is not True:raise HttpRequestError("请确认启动本地向量计算")
                    result=self.jobs.vectors(True)
                elif parsed.path == "/api/jobs/vectors/pause":
                    result=self.jobs.vectors(False)
                elif parsed.path == "/api/jobs/stores/start":
                    result=self.jobs.ensure_store()
                elif parsed.path == "/api/jobs/pause":
                    self.jobs.set("pause",True)
                    result={"status":"pause_requested","message":"当前来源完成后暂停；不会中途丢弃事务"}
                else:
                    raise HttpRequestError("Unknown jobs endpoint")
                self._json(200,result)
                return

            if parsed.path == "/api/admin/ai-permissions":
                self._json(200, self._permissions(body))
                return
            with self.service.session_scope(body.get("expected_session_id")):
                if parsed.path == "/api/chat/attachments/upload":
                    self._json(200, self.service.upload_attachment(body.get("name"), body.get("data")))
                elif parsed.path == "/api/chat/attachments/validate":
                    self._json(200, self.service.validate_attachments(body.get("message"), body.get("attachments", [])))
                elif parsed.path == "/api/chat/send":
                    self._json(200, self.service.send(body.get("message"), body.get("attachments", [])))
                elif parsed.path == "/api/ai/settings":
                    self._json(200, self.service.update_ai_stage(body.get("stage"),body.get("settings")))
                elif parsed.path == "/api/ai/test":
                    self._json(200, self.service.test_ai_stage(body.get("stage")))
                elif parsed.path == "/api/ai/running":
                    self._json(200, self.service.set_ai_running(body.get("running")))
                elif parsed.path == "/api/chat/new":
                    self._json(200, self.service.new_session())
                elif parsed.path == "/api/chat/save":
                    self._json(200, self.service.save_session())
                elif parsed.path == "/api/chat/restore":
                    self._json(200, self.service.restore_session(str(body.get("session_id", ""))))
                elif parsed.path == "/api/router/mode":
                    self._json(200, self.service.set_router_mode(str(body.get("mode", ""))))
                elif parsed.path == "/api/router/selection":
                    self._json(200, self.service.select_router(str(body.get("provider", "")), str(body.get("model", "")), body.get("temperature"), body.get("mode")))
                elif parsed.path == "/api/model/selection":
                    self._json(200, self.service.select_answer_model(str(body.get("provider", "")), str(body.get("model", "")), body.get("temperature")))
                elif parsed.path == "/api/retrieval/mode":
                    self._json(200, self.service.set_retrieval_mode(str(body.get("mode", ""))))
                elif parsed.path == "/api/retrieval/settings":
                    settings = body.get("settings", {})
                    if not isinstance(settings, dict):
                        raise HttpRequestError("settings must be a JSON object")
                    self._json(200, self.service.update_retrieval_settings(settings))
                elif parsed.path == "/api/retrieval/query":
                    from dataclasses import asdict
                    self._json(200, asdict(self.service.retrieval.query(body.get("message"))))
                elif parsed.path == "/api/admin/shutdown":
                    self._json(200, {"status": "stopping"})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self._json(404, {"status": "error", "error": {"error_type": "NotFound", "detail": "API route not found"}})
        except Exception as exc:
            self._handle_error(exc)


def build_server(service: ActionChatService, host: str, port: int, web_root: Path, jobs_config=None) -> ThreadingHTTPServer:
    class BoundHandler(ActionChatRequestHandler):
        pass

    import sys
    sys.path.insert(0,str(service.config.project_root / "scripts/action/infrastructures"))
    from ingestion_jobs_v0003 import Jobs
    BoundHandler.jobs = Jobs(root=service.config.project_root,config_path=jobs_config)
    BoundHandler.service = service
    BoundHandler.web_root = web_root.resolve()
    return ThreadingHTTPServer((host, port), BoundHandler)


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def build_service(chat_config_path: Path, retrieval_config_path: Optional[Path]) -> ActionChatService:
    project_root = find_project_root(chat_config_path)
    chat_config = load_chat_config(chat_config_path, project_root=project_root)
    from .action_chat_providers_v0006 import ProviderRegistry
    providers = ProviderRegistry(chat_config.providers, connection=chat_config.raw["ai_controller"], project_root=project_root)
    try:
        retrieval_extension = None
        if retrieval_config_path and retrieval_config_path.exists():
            retrieval_config = load_retrieval_config(retrieval_config_path, project_root=project_root)
            from .action_chat_retrieval_v0006 import RetrievalManager
            retrieval_extension = RetrievalManager(retrieval_config, embedding_gateway=providers, embedding_selection=chat_config.raw["embedding"])
        if retrieval_extension is not None:
            import importlib.util, sys
            name="_workbench_vector_retrieval_v0001"
            file=project_root/"frontend/local_ai_workbench/adapters/workbench_vector_retrieval_v0001.py"
            if name not in sys.modules:
                spec=importlib.util.spec_from_file_location(name,file)
                module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
            retrieval_extension=sys.modules[name].VectorRetrieval(retrieval_extension,project_root)
        return ActionChatService(chat_config, retrieval=retrieval_extension, provider_registry=providers)
    except Exception:
        providers.close()
        raise


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Independent Action Chat local HTTP service")
    parser.add_argument("--config", default=DEFAULT_CHAT_CONFIG)
    parser.add_argument("--retrieval-config", default=DEFAULT_RETRIEVAL_CONFIG)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--web-root", default=None)
    parser.add_argument("--jobs-config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        chat_path = Path(args.config).resolve()
        retrieval_path = Path(args.retrieval_config).resolve() if args.retrieval_config else None
        project_root = find_project_root(chat_path)
        config = load_chat_config(chat_path, project_root=project_root)
        if retrieval_path and retrieval_path.exists():
            load_retrieval_config(retrieval_path, project_root=project_root)
        host = args.host or str(config.server["host"])
        port = args.port or int(config.server["port"])
        if host != "127.0.0.1":
            raise HttpRequestError("host must be 127.0.0.1")
        web_root = Path(args.web_root).resolve() if args.web_root else Path(__file__).resolve().parent / "web"
        if not web_root.is_dir():
            raise HttpRequestError(f"web root not found: {web_root}")
        if args.dry_run:
            print(stable_json({"status": "dry_run", "host": host, "port": port, "web_root": str(web_root)}))
            return 0
        service = build_service(chat_path, retrieval_path)
        server = build_server(service, host, port, web_root,args.jobs_config)
        print(stable_json({"status": "started", "host": host, "port": server.server_address[1]}), flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
            service.close()
        return 0
    except ActionChatError as exc:
        print(stable_json({"status": "error", "error": classified_error(exc)}))
        return 2
    except Exception as exc:
        print(stable_json({"status": "error", "error": classified_error(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
