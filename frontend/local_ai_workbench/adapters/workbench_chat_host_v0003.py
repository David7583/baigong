# ============================================================
# 文件名: workbench_chat_host_v0003.py
# 中文名: 独立聊天服务嵌入启动适配器
# 版本号: v0003
# 主层级: action
# 层级: workbench / adapters
# 脚本定位: 调用现有聊天服务，提供独立本地 HTTP 入口
# 职责说明: 使用应用原有总控权限、模型和检索配置，不复制聊天业务
# 本脚本做什么: 分配本机端口、隔离工作台聊天会话、输出可核对的实例身份
# 本脚本不做什么: 不发送模型请求，不修改原有配置与会话，不打开新窗口
# 制度边界声明: 所有写入留在本项目，服务可脱离工作台独立运行
# 可更新: True
# ============================================================
# ALIAS_META
# alias: workbench_chat_host
# family: workbench_chat_host
# role: independent_chat_embedding_adapter
# version: v0003
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_chat_host_v0003.py
# input:
#   - project root, ready file and instance identifier
# output:
#   - local chat endpoint and instance identity
# depends_on:
#   - independent_action_chat.action_chat_server_v0006
#   - PyYAML
# used_by:
#   - workbench_tools_v0001
# ============================================================
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

import traceback

DEFAULT_ENCODING = "utf-8"
SCRIPT_NAME = "workbench_chat_host_v0003"
SCRIPT_FAMILY = "workbench_chat_host"
SCRIPT_VERSION = "v0003"


# ============================================================
# CLI / main 接口区
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Run existing Action Chat independently without another window")
    parser.add_argument("--root", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--development-key-file", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    ready = Path(args.ready_file).resolve()
    ready.relative_to(root)
    ready.parent.mkdir(parents=True, exist_ok=True)
    # pythonw has no standard streams; persist startup and handler failures.
    if sys.stdout is None:
        sys.stdout = (ready.parent / "process.log").open("a", encoding=DEFAULT_ENCODING, buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    import yaml
    sys.path.insert(0, str(root / "scripts/action/development/scripts"))
    profiles = json.loads((Path(__file__).parent.parent / "config/workbench_chat_integrations_v0001.json").read_text(encoding=DEFAULT_ENCODING))["profiles"]
    profile = next(p for p in profiles if (root / p["launcher"]).is_file() and (root / p["config"]).is_file() and (root / f'scripts/action/development/scripts/independent_action_chat/{p["server"]}.py').is_file())
    module = importlib.import_module("independent_action_chat." + profile["server"])
    config = root / profile["config"]
    raw = yaml.safe_load(config.read_text(encoding=DEFAULT_ENCODING))
    # Existing standalone sessions remain intact; this persistent workspace has its own session store.
    storage = root / "runtime/workbench_action_chat"
    storage.mkdir(parents=True, exist_ok=True)
    raw["storage"] = {"runtime_dir": "runtime/workbench_action_chat/sessions",
                      "action_output_dir": "actioning/workbench_action_chat_pending",
                      "log_dir": "logs/workbench_action_chat"}
    copied = storage / "chat_config.yml"
    copied.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding=DEFAULT_ENCODING)
    service = module.build_service(copied, root / profile["retrieval"])
    server = module.build_server(service, "127.0.0.1", 0, Path(os.environ.get("WORKBENCH_FRONTEND_ROOT", str(root / "../baigong-workbench-frontend"))).resolve() / "chat")

    key_file = Path(args.development_key_file).resolve()
    key_file.relative_to(root)
    server.development_key = key_file.read_text(encoding="utf-8").strip()
    if len(server.development_key) != 64:
        raise ValueError("development host identity invalid")

    class Handler(server.RequestHandlerClass):
        def do_GET(self):
            if self.path == "/api/workbench-identity":
                return self._json(200, {"root": str(root), "instance": args.instance, "host_version": "tool-development-1"})
            return super().do_GET()

    server.RequestHandlerClass = Handler
    state = {"url": f"http://127.0.0.1:{server.server_address[1]}/", "pid": os.getpid(), "instance": args.instance, "host_version": "tool-development-1"}
    ready.write_text(json.dumps(state), encoding=DEFAULT_ENCODING)
    print(json.dumps({"status": "ready", **state}), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(3)
