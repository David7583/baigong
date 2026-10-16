# ============================================================
# 文件名: check_retrieval_backends_v0001.py
# 中文名: 新版独立 Action Chat 检索后端只读检查脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / independent_action_chat / retrieval / diagnostics
# 脚本定位: 独立于 Chat 启动流程的四库 query_ready 检查入口
#
# 职责说明:
# - 报告配置、依赖、连接、Schema、数据量和 query_ready 状态
# - 保持每个物理后端的独立状态和耗时
#
# 本脚本做什么:
# - dry-run 只校验配置；正式检查仅执行只读探测
#
# 本脚本不做什么:
# - 不创建、迁移、修复、重建或写入数据库，不完整加载 BGE-M3
#
# 制度边界声明:
# - 数据库不可用不伪装成功，检查结果不影响普通 Chat 启动
# - stdout 只输出机器可读 JSON
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: check_retrieval_backends_v0001
# family: check_retrieval_backends
# role: readonly_backend_probe
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/check_retrieval_backends_v0001.py
# input:
#   - config/action/config/independent_action_chat_retrieval_config_v0001.yml
# output:
#   - JSON backend readiness report
# depends_on:
#   - Python stdlib: argparse, dataclasses, pathlib
#   - action_chat_config_v0001.py
#   - action_chat_contracts_v0001.py
#   - action_chat_retrieval_v0001.py
# used_by:
#   - local operator and phase2 acceptance workflow
# ============================================================

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from .action_chat_config_v0001 import DEFAULT_RETRIEVAL_CONFIG, find_project_root, load_retrieval_config
from .action_chat_contracts_v0001 import ActionChatError, classified_error, stable_json
from .action_chat_retrieval_v0001 import BackendRegistry


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "check_retrieval_backends"
SCRIPT_NAME = "check_retrieval_backends_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 异常类型
# ============================================================

# Classified project exceptions are reused from shared contracts.


# ============================================================
# 数据结构
# ============================================================

# BackendStatus is the stable output object.


# ============================================================
# 工具函数区
# ============================================================

def check_backends(config_path: Path, *, dry_run: bool = False) -> dict:
    project_root = find_project_root(config_path)
    config = load_retrieval_config(config_path, project_root=project_root)
    if dry_run:
        return {
            "status": "dry_run",
            "config_hash": config.config_hash,
            "configured_backends": list(config.retrieval["backends"]),
            "side_effects": "none",
        }
    registry = BackendRegistry(config.retrieval["backends"], project_root)
    try:
        statuses = registry.probe_all()
        return {
            "status": "completed",
            "config_hash": config.config_hash,
            "backends": {name: asdict(value) for name, value in statuses.items()},
            "query_ready": [name for name, value in statuses.items() if value.query_ready],
        }
    finally:
        registry.close()


# ============================================================
# 默认映射
# ============================================================

# Default config path is imported from the versioned config loader.


# ============================================================
# 核心类
# ============================================================

# The standalone function is the complete diagnostic unit.


# ============================================================
# Schema / 契约辅助函数
# ============================================================

# Probe results are serialized directly from BackendStatus.


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only retrieval backend readiness checker")
    parser.add_argument("--config", default=DEFAULT_RETRIEVAL_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = check_backends(Path(args.config).resolve(), dry_run=bool(args.dry_run))
        print(stable_json(result))
        return 0
    except ActionChatError as exc:
        print(stable_json({"status": "error", "error": classified_error(exc)}))
        return 2
    except Exception as exc:
        print(stable_json({"status": "error", "error": classified_error(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
