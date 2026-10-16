# ============================================================
# 文件名: action_chat_server_v0001.py
# 中文名: 新版独立 Action Chat HTTP 服务入口脚本
# 版本号: v0001
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
# alias: action_chat_server_v0001
# family: action_chat_server
# role: local_http_entrypoint
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_server_v0001.py
# input:
#   - local HTTP requests and versioned configuration
# output:
#   - local JSON API and Web UI
# depends_on:
#   - Python stdlib: argparse, http.server, json, mimetypes, pathlib, threading, urllib
#   - action_chat_config_v0001.py
#   - action_chat_service_v0001.py
# used_by:
#   - start_action_chat_v0001.ps1
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

from .action_chat_config_v0001 import (
    DEFAULT_CHAT_CONFIG,
    DEFAULT_RETRIEVAL_CONFIG,
    find_project_root,
    load_chat_config,
    load_env_file,
    load_retrieval_config,
    resolve_project_path,
)
from .action_chat_contracts_v0001 import ActionChatError, classified_error, stable_json
from .action_chat_service_v0001 import ActionChatService


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_server"
SCRIPT_NAME = "action_chat_server_v0001"
SCRIPT_VERSION = "v0001"
MAX_REQUEST_BYTES = 1_048_576


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
    relative = request_path.lstrip("/") or "index.html"
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

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self._json(200, self.service.status())
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
                self._json(200, {"status": "ok", "sessions": self.service.store.list_sessions()})
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
            if parsed.path == "/api/chat/send":
                self._json(200, self.service.send(body.get("message")))
            elif parsed.path == "/api/chat/new":
                self._json(200, self.service.new_session())
            elif parsed.path == "/api/chat/save":
                self._json(200, self.service.save_session())
            elif parsed.path == "/api/chat/restore":
                self._json(200, self.service.restore_session(str(body.get("session_id", ""))))
            elif parsed.path == "/api/router/mode":
                self._json(200, self.service.set_router_mode(str(body.get("mode", ""))))
            elif parsed.path == "/api/router/selection":
                self._json(200, self.service.select_router(str(body.get("provider", "")), str(body.get("model", ""))))
            elif parsed.path == "/api/model/selection":
                self._json(200, self.service.select_answer_model(str(body.get("provider", "")), str(body.get("model", ""))))
            elif parsed.path == "/api/retrieval/mode":
                self._json(200, self.service.set_retrieval_mode(str(body.get("mode", ""))))
            elif parsed.path == "/api/retrieval/settings":
                settings = body.get("settings", {})
                if not isinstance(settings, dict):
                    raise HttpRequestError("settings must be a JSON object")
                self._json(200, self.service.update_retrieval_settings(settings))
            elif parsed.path == "/api/admin/shutdown":
                self._json(200, {"status": "stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json(404, {"status": "error", "error": {"error_type": "NotFound", "detail": "API route not found"}})
        except Exception as exc:
            self._handle_error(exc)


def build_server(service: ActionChatService, host: str, port: int, web_root: Path) -> ThreadingHTTPServer:
    class BoundHandler(ActionChatRequestHandler):
        pass

    BoundHandler.service = service
    BoundHandler.web_root = web_root.resolve()
    return ThreadingHTTPServer((host, port), BoundHandler)


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def build_service(chat_config_path: Path, retrieval_config_path: Optional[Path]) -> ActionChatService:
    project_root = find_project_root(chat_config_path)
    package_dir = Path(__file__).resolve().parent
    load_env_file(package_dir / ".env")
    chat_config = load_chat_config(chat_config_path, project_root=project_root)
    retrieval_extension = None
    if retrieval_config_path and retrieval_config_path.exists():
        retrieval_config = load_retrieval_config(retrieval_config_path, project_root=project_root)
        try:
            from .action_chat_retrieval_v0001 import RetrievalManager

            retrieval_extension = RetrievalManager(retrieval_config)
        except ImportError:
            retrieval_extension = None
    return ActionChatService(chat_config, retrieval=retrieval_extension)


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
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        chat_path = Path(args.config).resolve()
        retrieval_path = Path(args.retrieval_config).resolve() if args.retrieval_config else None
        service = build_service(chat_path, retrieval_path)
        host = args.host or str(service.config.server["host"])
        port = args.port or int(service.config.server["port"])
        if host != "127.0.0.1":
            raise HttpRequestError("host must be 127.0.0.1")
        web_root = Path(args.web_root).resolve() if args.web_root else Path(__file__).resolve().parent / "web"
        if not web_root.is_dir():
            raise HttpRequestError(f"web root not found: {web_root}")
        if args.dry_run:
            print(stable_json({"status": "dry_run", "host": host, "port": port, "web_root": str(web_root)}))
            return 0
        server = build_server(service, host, port, web_root)
        print(stable_json({"status": "started", "host": host, "port": server.server_address[1]}), flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
        return 0
    except ActionChatError as exc:
        print(stable_json({"status": "error", "error": classified_error(exc)}))
        return 2
    except Exception as exc:
        print(stable_json({"status": "error", "error": classified_error(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
