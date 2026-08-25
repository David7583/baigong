#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 文件名: supplemental_annotation_runner_v0002.py
# 中文名: 带已解析来源门禁的补充标注框架运行脚本
# 版本号: v0002
#
# 主层级: action
# 层级: native_plugin / supplemental_annotation / runner
# 脚本定位: 在保留 v0001 插件执行与持久化契约的前提下增加框架级来源访问声明和门禁传递
#
# 职责说明:
# - 校验每个已登记插件显式声明 source_access=required|not_required
# - 对需要来源的插件强制校验已授权 source access manifest 并将其传入插件运行上下文
#
# 本脚本做什么:
# - 复用 supplemental_annotation_runner_v0001 已验收的批处理、插件隔离和 SQLite 追加写入
# - 校验 source access 的 language units 与本次 canonical input 为同一文件且 hash 一致
#
# 本脚本不做什么:
# - 不自行生成来源授权，不修改 v0001 插件业务算法
# - 不允许插件通过私有参数绕过公共 source access gateway
#
# 制度边界声明:
# - required 插件缺少或伪造来源授权时必须在插件执行前失败
# - 不需要来源的插件也必须显式声明 not_required，不允许默认猜测
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: supplemental_annotation_runner_v0002
# family: supplemental_annotation_runner
# role: supplemental_annotation_framework_runner
# version: v0002
# status: experimental
# entry_point: scripts/action/native_plugin/supplemental_annotation/supplemental_annotation_runner_v0002.py
# input:
#   - canonical language text-units JSONL
#   - explicit plugin registry with source access declarations
#   - optional authorized source access manifest
# output:
#   - supplemental annotation SQLite store and framework/plugin manifests
# depends_on:
#   - supplemental_annotation_runner_v0001
#   - source_access_gateway_v0001
#   - Python standard library
# used_by:
#   - data_action_chain_pipeline_v0008
# ============================================================

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "supplemental_annotation_runner"
SCRIPT_NAME = "supplemental_annotation_runner_v0002"
SCRIPT_VERSION = "v0002"
BASE_RELATIVE = Path("scripts") / "action" / "native_plugin" / "supplemental_annotation" / "supplemental_annotation_runner_v0001.py"
DEFAULT_REGISTRY_V2 = Path("scripts") / "action" / "native_plugin" / "supplemental_annotation" / "config" / "plugin_registry_v0002.json"
SOURCE_ACCESS_SCHEMA_VERSION = "supplemental_annotation_source_access_v0001"
ALLOWED_SOURCE_ACCESS_DECLARATIONS = frozenset({"required", "not_required"})


# ============================================================
# 异常类型
# ============================================================

class SupplementalAnnotationRunnerV2Error(RuntimeError):
    """Raised when source access declarations or authorization are invalid."""


# ============================================================
# 数据结构
# ============================================================

# The v0002 compatibility wrapper keeps the v0001 runtime data structures and
# adds only a versioned source-access context mapping.


# ============================================================
# 工具函数区
# ============================================================

def _find_project_root() -> Path:
    for candidate in (Path.cwd().resolve(), *Path(__file__).resolve().parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise SupplementalAnnotationRunnerV2Error("cannot locate project root")


def _load_base(project_root: Path) -> Any:
    path = (project_root / BASE_RELATIVE).resolve()
    if not path.is_file():
        raise SupplementalAnnotationRunnerV2Error(f"v0001 runner is missing: {path}")
    spec = importlib.util.spec_from_file_location("supplemental_annotation_runner_v0001_base", path)
    if spec is None or spec.loader is None:
        raise SupplementalAnnotationRunnerV2Error(f"cannot load v0001 runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SupplementalAnnotationRunnerV2Error(f"{label} must remain within project root") from exc


def _load_object(path: Path, label: str) -> Dict[str, Any]:
    if not path.is_file():
        raise SupplementalAnnotationRunnerV2Error(f"{label} is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupplementalAnnotationRunnerV2Error(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SupplementalAnnotationRunnerV2Error(f"{label} root must be an object")
    return payload


# ============================================================
# 默认映射
# ============================================================

PROJECT_ROOT = _find_project_root()
BASE = _load_base(PROJECT_ROOT)


# ============================================================
# 核心运行组件
# ============================================================

def _validate_registry_source_access(registry: Mapping[str, Any], enabled_names: Sequence[str]) -> Dict[str, str]:
    plugins = registry.get("plugins")
    if not isinstance(plugins, list):
        raise SupplementalAnnotationRunnerV2Error("plugin registry plugins must be a list")
    declarations: Dict[str, str] = {}
    for index, item in enumerate(plugins):
        if not isinstance(item, Mapping):
            raise SupplementalAnnotationRunnerV2Error(f"plugin registry item {index} must be an object")
        name = item.get("name")
        declaration = item.get("source_access")
        if not isinstance(name, str) or not name:
            raise SupplementalAnnotationRunnerV2Error(f"plugin registry item {index} has invalid name")
        if declaration not in ALLOWED_SOURCE_ACCESS_DECLARATIONS:
            raise SupplementalAnnotationRunnerV2Error(f"plugin {name} must declare source_access=required|not_required")
        declarations[name] = str(declaration)
    missing = sorted(set(enabled_names) - set(declarations))
    if missing:
        raise SupplementalAnnotationRunnerV2Error(f"enabled plugins are missing source access declarations: {missing}")
    return declarations


def _validate_access_manifest(access_path: Path, input_path: Path) -> Dict[str, Any]:
    access = _load_object(access_path, "source access manifest")
    if access.get("schema_version") != SOURCE_ACCESS_SCHEMA_VERSION or access.get("status") != "authorized":
        raise SupplementalAnnotationRunnerV2Error("source access manifest must be authorized v0001")
    authorization = access.get("authorization")
    if not isinstance(authorization, Mapping) or authorization.get("authorized") is not True:
        raise SupplementalAnnotationRunnerV2Error("source access authorization must be true")
    language = access.get("language_units")
    if not isinstance(language, Mapping):
        raise SupplementalAnnotationRunnerV2Error("source access manifest is missing language_units")
    authorized_input = _resolve(PROJECT_ROOT, str(language.get("project_relative_path", "")))
    if authorized_input != input_path:
        raise SupplementalAnnotationRunnerV2Error("runner input must be the exact language units authorized by source access")
    if BASE._file_sha256(input_path) != language.get("sha256"):
        raise SupplementalAnnotationRunnerV2Error("runner input hash does not match source access")
    return access


def execute(args: argparse.Namespace) -> Dict[str, Any]:
    input_path = _resolve(PROJECT_ROOT, args.input)
    registry_path = _resolve(PROJECT_ROOT, args.registry or DEFAULT_REGISTRY_V2)
    _require_within(input_path, PROJECT_ROOT, "canonical input")
    _require_within(registry_path, PROJECT_ROOT, "plugin registry")
    config_path = _resolve(PROJECT_ROOT, args.config) if args.config else (BASE.SUBSYSTEM_ROOT / BASE.DEFAULT_CONFIG).resolve()
    config_payload = _load_object(config_path, "framework config")
    configured_plugins = config_payload.get("plugins")
    if not isinstance(configured_plugins, Mapping):
        raise SupplementalAnnotationRunnerV2Error("framework config plugins must be an object")
    enabled_names = [str(name) for name, value in configured_plugins.items() if isinstance(value, Mapping) and value.get("enabled") is True]
    registry_payload = _load_object(registry_path, "plugin registry")
    declarations = _validate_registry_source_access(registry_payload, enabled_names)
    required_names = sorted(name for name in enabled_names if declarations[name] == "required")
    access: Optional[Dict[str, Any]] = None
    access_path: Optional[Path] = None
    if args.source_access_manifest:
        access_path = _resolve(PROJECT_ROOT, args.source_access_manifest)
        _require_within(access_path, PROJECT_ROOT, "source access manifest")
        access = _validate_access_manifest(access_path, input_path)
    if required_names and access is None:
        raise SupplementalAnnotationRunnerV2Error(f"source access is required by enabled plugins: {required_names}")
    original_invoke = BASE._invoke_plugin

    def invoke_with_source_access(loaded: Any, batch: Any, run_context: Mapping[str, Any], plugin_config: Any) -> Dict[str, Any]:
        context = dict(run_context)
        declaration = declarations.get(loaded.declaration.name)
        context["source_access"] = {
            "requirement": declaration,
            "manifest": str(access_path) if access_path is not None else None,
            "access_id": access.get("access_id") if access is not None else None,
            "status": access.get("status") if access is not None else "not_provided",
        }
        return original_invoke(loaded, batch, context, plugin_config)

    BASE._invoke_plugin = invoke_with_source_access
    BASE.SCRIPT_NAME = SCRIPT_NAME
    BASE.SCRIPT_VERSION = SCRIPT_VERSION
    try:
        result = BASE.execute(args)
    finally:
        BASE._invoke_plugin = original_invoke
    result["framework"] = SCRIPT_NAME
    result["framework_version"] = SCRIPT_VERSION
    result["source_access_contract_version"] = SOURCE_ACCESS_SCHEMA_VERSION
    result["source_access"] = {
        "provided": access is not None,
        "manifest": BASE._relative_display(access_path, PROJECT_ROOT) if access_path is not None else None,
        "access_id": access.get("access_id") if access is not None else None,
        "status": access.get("status") if access is not None else "not_provided",
        "required_plugins": required_names,
        "declarations": declarations,
    }
    if not args.dry_run:
        BASE._write_json_atomic(_resolve(PROJECT_ROOT, args.manifest), result)
    return result


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def framework_summary() -> Dict[str, Any]:
    return {
        "framework": SCRIPT_NAME,
        "version": SCRIPT_VERSION,
        "source_access_contract": SOURCE_ACCESS_SCHEMA_VERSION,
        "source_access_declaration_required": True,
    }


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = BASE._build_parser()
    parser.description = "Run Supplemental Annotation plugins with explicit parsed-source access declarations."
    parser.set_defaults(registry=str(DEFAULT_REGISTRY_V2))
    parser.add_argument("--source-access-manifest", default="")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = execute(args)
        print(BASE._safe_json(result))
        return 0
    except (SupplementalAnnotationRunnerV2Error, BASE.SupplementalAnnotationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(BASE._safe_json({"status": "error", "framework": SCRIPT_NAME, "version": SCRIPT_VERSION, "error_type": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 2
    except Exception as exc:
        print(BASE._safe_json({"status": "error", "framework": SCRIPT_NAME, "version": SCRIPT_VERSION, "error_type": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
