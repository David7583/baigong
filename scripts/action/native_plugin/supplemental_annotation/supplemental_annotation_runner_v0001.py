# ============================================================
# 文件名: supplemental_annotation_runner_v0001.py
# 中文名: 补充标注框架运行脚本
# 版本号: v0001
#
# 主层级: action
# 层级: native_plugin / supplemental_annotation / runner
# 脚本定位: canonical data ingest 守恒通过后、semantic selector 之前的统一插件入口
#
# 职责说明:
# - 按显式配置和注册表顺序加载、调用 Supplemental Annotation Plugins
# - 绑定稳定身份，汇总 Framework 与 Plugin 证据并协调追加持久化
#
# 本脚本做什么:
# - 使用 Python 标准库流式读取 canonical text-unit JSONL 并按批调用插件
# - 生成 Framework manifest、独立 Plugin manifest 和 SQLite annotation store
#
# 本脚本不做什么:
# - 不实现时间识别或其他领域算法，不修改 canonical text
# - 不改变 semantic selector、Action identity、Vector 或四库业务契约
#
# 制度边界声明:
# - 只执行 registered, enabled, compatible 的固定版本插件，不扫描目录
# - required plugin 失败则 Framework 失败；已落标注保留并声明 side_effects_may_exist
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: supplemental_annotation_runner_v0001
# family: supplemental_annotation_runner
# role: supplemental_annotation_framework_runner
# version: v0001
# status: active
# entry_point: scripts/action/native_plugin/supplemental_annotation/supplemental_annotation_runner_v0001.py
# input:
#   - canonical language text-units JSONL
#   - config/supplemental_annotation_config_v0001.json
#   - config/plugin_registry_v0001.json
# output:
#   - supplemental annotation SQLite store
#   - framework and per-plugin run manifests
# depends_on:
#   - annotation_contract_v0001
#   - annotation_registry_v0001
#   - annotation_persistence_v0001
#   - plugin_runtime_v0001
#   - Python stdlib: argparse, datetime, hashlib, importlib, json, os, pathlib, subprocess, tempfile, typing
# used_by:
#   - data_action_chain_pipeline_v0006
# ============================================================

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "supplemental_annotation_runner"
SCRIPT_NAME = "supplemental_annotation_runner_v0001"
SCRIPT_VERSION = "v0001"

MAX_ROOT_SEARCH_DEPTH = 12
SUBSYSTEM_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path("config") / "supplemental_annotation_config_v0001.json"
DEFAULT_REGISTRY = Path("config") / "plugin_registry_v0001.json"
CONTRACT_MODULE = Path("framework") / "annotation_contract_v0001.py"
REGISTRY_MODULE = Path("framework") / "annotation_registry_v0001.py"
PERSISTENCE_MODULE = Path("framework") / "annotation_persistence_v0001.py"
PLUGIN_RUNTIME = Path("framework") / "plugin_runtime_v0001.py"


# ============================================================
# 异常类型
# ============================================================

class SupplementalAnnotationError(RuntimeError):
    """Base exception for framework validation and orchestration failures."""


class FrameworkConfigError(SupplementalAnnotationError):
    """Raised when framework configuration cannot be used safely."""


class CanonicalInputError(SupplementalAnnotationError):
    """Raised when canonical input is missing, malformed, or unstable."""


class RequiredPluginError(SupplementalAnnotationError):
    """Raised when a required plugin fails or violates the contract."""


# ============================================================
# 数据结构
# ============================================================

# Public runtime data is represented by versioned JSON mappings so plugin
# boundaries remain language-neutral and independently testable.


# ============================================================
# 工具函数区
# ============================================================

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_json(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _ipc_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode(DEFAULT_ENCODING)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_project_root(start: Path) -> Path:
    current = start.resolve()
    for _ in range(MAX_ROOT_SEARCH_DEPTH):
        if (current / "AGENTS.md").is_file() and (current / "scripts").is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    raise FrameworkConfigError(f"cannot locate project root from: {start}")


def _resolve_from_root(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _require_within(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FrameworkConfigError(f"{label} must remain within project root") from exc


def _relative_display(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    if not path.is_file():
        raise FrameworkConfigError(f"{label} is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding=DEFAULT_ENCODING))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrameworkConfigError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FrameworkConfigError(f"{label} root must be an object")
    return payload


def _load_module(name: str, path: Path) -> ModuleType:
    if not path.is_file():
        raise FrameworkConfigError(f"framework module is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise FrameworkConfigError(f"cannot load framework module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding=DEFAULT_ENCODING, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, _safe_json(dict(payload), pretty=True) + "\n")


def _iter_bound_batches(
    input_path: Path,
    batch_size: int,
    contract: ModuleType,
) -> Iterable[List[Dict[str, Any]]]:
    batch: List[Dict[str, Any]] = []
    with input_path.open("r", encoding=DEFAULT_ENCODING) as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CanonicalInputError(f"invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise CanonicalInputError(f"canonical record at line {line_number} must be an object")
            identity = contract.bind_stable_identity(record, line_number)
            batch.append({"record": record, "stable_identity": identity})
            if len(batch) >= batch_size:
                yield batch
                batch = []
    if batch:
        yield batch


def _empty_plugin_summary(name: str, version: str, required: bool, started_at: str) -> Dict[str, Any]:
    return {
        "plugin_name": name,
        "plugin_version": version,
        "required": required,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "records_scanned": 0,
        "annotations_created": 0,
        "annotations_skipped": 0,
        "annotations_invalid": 0,
        "stats": {},
        "error": None,
    }


def _merge_numeric_stats(target: Dict[str, int], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, int) and not isinstance(value, bool):
            target[key] = target.get(key, 0) + value


def _invoke_plugin(
    loaded: Any,
    batch: Sequence[Mapping[str, Any]],
    run_context: Mapping[str, Any],
    plugin_config: Any,
) -> Dict[str, Any]:
    runtime_path = (SUBSYSTEM_ROOT / PLUGIN_RUNTIME).resolve()
    if not runtime_path.is_file():
        raise FrameworkConfigError(f"plugin runtime is missing: {runtime_path}")
    payload = {
        "records": list(batch),
        "run_context": dict(run_context),
        "plugin_config": dict(plugin_config.config),
    }
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(runtime_path),
                "--plugin",
                str(loaded.entry_point),
                "--expected-name",
                loaded.declaration.name,
                "--expected-version",
                loaded.declaration.version,
            ],
            input=_ipc_json(payload),
            capture_output=True,
            text=True,
            encoding=DEFAULT_ENCODING,
            errors="replace",
            timeout=plugin_config.timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RequiredPluginError(
            f"plugin {loaded.declaration.name} exceeded {plugin_config.timeout_seconds} seconds"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no runtime output"
        raise RequiredPluginError(
            f"plugin runtime failed for {loaded.declaration.name}: {detail}"
        )
    try:
        wrapper = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RequiredPluginError(
            f"plugin runtime returned invalid JSON for {loaded.declaration.name}"
        ) from exc
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("result"), dict):
        raise RequiredPluginError(
            f"plugin runtime returned invalid envelope for {loaded.declaration.name}"
        )
    return wrapper


# ============================================================
# 默认映射
# ============================================================

FRAMEWORK_STATUS_COMPLETED = "completed"
FRAMEWORK_STATUS_FAILED = "failed"


# ============================================================
# 核心业务组件
# ============================================================

def execute(args: argparse.Namespace) -> Dict[str, Any]:
    project_root = _find_project_root(Path(__file__).resolve().parent)
    input_path = _resolve_from_root(project_root, args.input)
    output_db = _resolve_from_root(project_root, args.output_db)
    manifest_path = _resolve_from_root(project_root, args.manifest)
    config_path = (
        _resolve_from_root(project_root, args.config)
        if args.config
        else (SUBSYSTEM_ROOT / DEFAULT_CONFIG).resolve()
    )
    registry_path = (
        _resolve_from_root(project_root, args.registry)
        if args.registry
        else (SUBSYSTEM_ROOT / DEFAULT_REGISTRY).resolve()
    )
    for path, label in (
        (input_path, "canonical input"),
        (output_db, "annotation database"),
        (manifest_path, "framework manifest"),
        (config_path, "framework config"),
        (registry_path, "plugin registry"),
    ):
        _require_within(path, project_root, label)
    if not input_path.is_file():
        raise CanonicalInputError(f"canonical input is not a file: {input_path}")
    if output_db == input_path or manifest_path == input_path:
        raise FrameworkConfigError("framework outputs must not overwrite canonical input")
    if output_db == manifest_path:
        raise FrameworkConfigError("annotation database and manifest paths must differ")

    contract = _load_module(
        "supplemental_annotation_contract_runtime_v0001",
        (SUBSYSTEM_ROOT / CONTRACT_MODULE).resolve(),
    )
    registry_module = _load_module(
        "supplemental_annotation_registry_runtime_v0001",
        (SUBSYSTEM_ROOT / REGISTRY_MODULE).resolve(),
    )
    persistence = _load_module(
        "supplemental_annotation_persistence_runtime_v0001",
        (SUBSYSTEM_ROOT / PERSISTENCE_MODULE).resolve(),
    )
    config_payload = _load_json(config_path, "framework config")
    framework_config = contract.validate_framework_config(config_payload)
    registry = registry_module.PluginRegistry(SUBSYSTEM_ROOT, registry_path, contract)

    enabled_names = [
        name for name in framework_config.execution_order if framework_config.plugins[name].enabled
    ]
    run_id = args.run_id
    if not isinstance(run_id, str) or not run_id.strip():
        raise FrameworkConfigError("run_id must be a non-empty string")
    input_hash_before = _file_sha256(input_path)
    config_hash = _sha256_text(_safe_json(config_payload))
    started_at = _utc_now_iso()
    manifest: Dict[str, Any] = {
        "status": "dry-run" if args.dry_run else "running",
        "run_id": run_id,
        "framework": SCRIPT_NAME,
        "framework_version": SCRIPT_VERSION,
        "contract_version": contract.CONTRACT_VERSION,
        "started_at": started_at,
        "finished_at": None,
        "test_mode": bool(args.test_mode),
        "dry_run": bool(args.dry_run),
        "input_source": _relative_display(input_path, project_root),
        "input_hash_before": input_hash_before,
        "input_hash_after": None,
        "canonical_immutable": None,
        "config": _relative_display(config_path, project_root),
        "config_hash": config_hash,
        "registry": _relative_display(registry_path, project_root),
        "output_database": _relative_display(output_db, project_root),
        "manifest": _relative_display(manifest_path, project_root),
        "execution_order": list(framework_config.execution_order),
        "enabled_plugins": enabled_names,
        "records_scanned": 0,
        "plugins_completed": 0,
        "plugins_failed": 0,
        "annotations_created": 0,
        "annotations_skipped": 0,
        "annotations_invalid": 0,
        "side_effects_may_exist": False,
        "plugins": [],
        "error": None,
    }

    store: Optional[Any] = None
    run_started = False
    if not args.dry_run:
        store = persistence.AnnotationStore(output_db)
        store.initialize()
        store.start_run(
            {
                "run_id": run_id,
                "framework_version": SCRIPT_VERSION,
                "input_source": manifest["input_source"],
                "input_hash": input_hash_before,
                "config_hash": config_hash,
                "enabled_plugins": enabled_names,
                "started_at": started_at,
            }
        )
        run_started = True

    canonical_count: Optional[int] = None
    try:
        if not enabled_names:
            canonical_count = sum(
                len(batch)
                for batch in _iter_bound_batches(input_path, framework_config.batch_size, contract)
            )
        for name in framework_config.execution_order:
            plugin_config = framework_config.plugins[name]
            if not plugin_config.enabled:
                manifest["plugins"].append(
                    {
                        "plugin_name": name,
                        "plugin_version": plugin_config.version,
                        "required": plugin_config.required,
                        "status": "skipped",
                        "reason": "disabled_by_config",
                    }
                )
                continue
            plugin_started = _utc_now_iso()
            plugin_summary = _empty_plugin_summary(
                name, plugin_config.version, plugin_config.required, plugin_started
            )
            numeric_stats: Dict[str, int] = {}
            metadata: Dict[str, Any] = {}
            try:
                loaded = registry.load(plugin_config)
                for batch in _iter_bound_batches(input_path, framework_config.batch_size, contract):
                    wrapper = _invoke_plugin(
                        loaded,
                        batch,
                        {
                            "run_id": run_id,
                            "framework_version": SCRIPT_VERSION,
                            "contract_version": contract.CONTRACT_VERSION,
                            "input_source": manifest["input_source"],
                        },
                        plugin_config,
                    )
                    metadata = contract.validate_plugin_meta(
                        wrapper.get("plugin_meta"), loaded.declaration
                    )
                    result = contract.validate_plugin_result(
                        wrapper["result"], loaded.declaration, len(batch)
                    )
                    annotations = result["annotations"]
                    created = len(annotations)
                    skipped = 0
                    if store is not None:
                        created, skipped = store.append_annotations(
                            annotations,
                            run_id=run_id,
                            created_at=_utc_now_iso(),
                        )
                    plugin_summary["records_scanned"] += len(batch)
                    plugin_summary["annotations_created"] += created
                    plugin_summary["annotations_skipped"] += skipped
                    _merge_numeric_stats(numeric_stats, result.get("stats", {}))
                if canonical_count is None:
                    canonical_count = plugin_summary["records_scanned"]
                elif plugin_summary["records_scanned"] != canonical_count:
                    raise RequiredPluginError(
                        f"plugin {name} did not scan the canonical record count"
                    )
                plugin_summary.update(
                    {
                        "status": "completed",
                        "finished_at": _utc_now_iso(),
                        "entry_point": _relative_display(loaded.entry_point, project_root),
                        "rule_version": metadata.get("rule_version")
                        or plugin_config.config.get("rule_version"),
                        "stats": numeric_stats,
                    }
                )
                manifest["plugins_completed"] += 1
                manifest["annotations_created"] += plugin_summary["annotations_created"]
                manifest["annotations_skipped"] += plugin_summary["annotations_skipped"]
                if not args.dry_run:
                    plugin_manifest_path = (
                        manifest_path.parent
                        / "plugins"
                        / f"{name}_run_manifest.json"
                    )
                    _write_json_atomic(plugin_manifest_path, plugin_summary)
                    plugin_summary["run_manifest"] = _relative_display(
                        plugin_manifest_path, project_root
                    )
            except Exception as exc:
                plugin_summary.update(
                    {
                        "status": "failed",
                        "finished_at": _utc_now_iso(),
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "stats": numeric_stats,
                    }
                )
                manifest["plugins_failed"] += 1
                if plugin_config.required:
                    manifest["plugins"].append(plugin_summary)
                    raise RequiredPluginError(f"required plugin failed: {name}: {exc}") from exc
            manifest["plugins"].append(plugin_summary)

        manifest["records_scanned"] = canonical_count or 0
        input_hash_after = _file_sha256(input_path)
        manifest["input_hash_after"] = input_hash_after
        manifest["canonical_immutable"] = input_hash_before == input_hash_after
        if not manifest["canonical_immutable"]:
            raise CanonicalInputError("canonical input hash changed during annotation")
        manifest["status"] = "dry-run" if args.dry_run else FRAMEWORK_STATUS_COMPLETED
        manifest["finished_at"] = _utc_now_iso()
        if store is not None:
            store.finish_run(
                run_id,
                {
                    "finished_at": manifest["finished_at"],
                    "status": FRAMEWORK_STATUS_COMPLETED,
                    "records_scanned": manifest["records_scanned"],
                    "annotations_created": manifest["annotations_created"],
                    "annotations_skipped": manifest["annotations_skipped"],
                    "annotations_invalid": manifest["annotations_invalid"],
                    "side_effects_may_exist": False,
                    "error_summary": None,
                },
            )
        if not args.dry_run:
            _write_json_atomic(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = FRAMEWORK_STATUS_FAILED
        manifest["finished_at"] = _utc_now_iso()
        manifest["records_scanned"] = canonical_count or manifest["records_scanned"]
        manifest["side_effects_may_exist"] = manifest["annotations_created"] > 0
        manifest["error"] = {"type": type(exc).__name__, "message": str(exc)}
        try:
            input_hash_after = _file_sha256(input_path)
            manifest["input_hash_after"] = input_hash_after
            manifest["canonical_immutable"] = input_hash_before == input_hash_after
        except OSError:
            pass
        if store is not None and run_started:
            store.finish_run(
                run_id,
                {
                    "finished_at": manifest["finished_at"],
                    "status": FRAMEWORK_STATUS_FAILED,
                    "records_scanned": manifest["records_scanned"],
                    "annotations_created": manifest["annotations_created"],
                    "annotations_skipped": manifest["annotations_skipped"],
                    "annotations_invalid": manifest["annotations_invalid"],
                    "side_effects_may_exist": manifest["side_effects_may_exist"],
                    "error_summary": str(exc),
                },
            )
        if not args.dry_run:
            _write_json_atomic(manifest_path, manifest)
        raise


# ============================================================
# Schema / 契约辅助函数
# ============================================================

def framework_summary() -> Dict[str, Any]:
    return {
        "status": "completed",
        "framework": SCRIPT_NAME,
        "version": SCRIPT_VERSION,
        "plugin_discovery": "explicit_registry_only",
        "canonical_write_policy": "read_only",
    }


# ============================================================
# CLI / main 接口区
# ============================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run registered Supplemental Annotation plugins against canonical text units."
    )
    parser.add_argument("--input", required=True, help="Canonical language text-units JSONL.")
    parser.add_argument("--output-db", required=True, help="Independent annotation SQLite output.")
    parser.add_argument("--manifest", required=True, help="Framework run manifest JSON.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", default=None, help="Project-relative framework config override.")
    parser.add_argument("--registry", default=None, help="Project-relative plugin registry override.")
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = execute(args)
    except (
        SupplementalAnnotationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            _safe_json(
                {
                    "status": "error",
                    "framework": SCRIPT_NAME,
                    "version": SCRIPT_VERSION,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            _safe_json(
                {
                    "status": "error",
                    "framework": SCRIPT_NAME,
                    "version": SCRIPT_VERSION,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return 3
    print(_safe_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
