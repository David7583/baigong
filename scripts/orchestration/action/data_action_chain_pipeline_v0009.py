#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# 文件名: data_action_chain_pipeline_v0009.py
# 中文名: 独立 Data-Action-Data 完整坐标锚定总编排脚本
# 版本号: v0009
#
# 主层级: action
# 层级: orchestration / data_action_chain / supplemental_annotation
# 脚本定位: 独立执行含完整入口契约、发布预检、Action 库初始化和来源受控补充标注的 Data-Action-Data 半循环
#
# 职责说明:
# - 固定并校验全部子编排版本、顺序、输入输出、发布配置和 Action 数据库状态
# - 在统一格式识别和结构解析完成后，经公共来源访问闸门恢复消息时间
# - 贯穿 cycle_id、Action lineage、完成清单、失败冻结和回流证据
#
# 本脚本做什么:
# - 可显式初始化并验证隔离的 Action 业务数据库，执行运行配置预检
# - 生成入口信封、canonical data、补充标注证据、时间戳旁表、Action 身份、派生、向量、lineage 与回流清单
#
# 本脚本不做什么:
# - 不导入、加载或调用任何旧版 data_action_chain_pipeline 总编排器
# - 不猜测未知格式，不从正文推断时间，不修改 canonical text，不自动进入 Understanding
#
# 制度边界声明:
# - 正式执行必须显式确认；测试模式的数据库和产物必须位于项目 temp 目录
# - 时间提取只能读取结构解析完成清单授权的工作副本和坐标
# - 子步骤失败立即停止下游并保留失败证据，不返回伪完成，不写 sql/action.db
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: data_action_chain_pipeline
# family: data_action_chain_pipeline
# role: data_action_data_cycle_orchestrator
# version: v0009
# status: experimental
# entry_point: scripts/orchestration/action/data_action_chain_pipeline_v0009.py
# input:
#   - one data_raw target, isolated stores and explicit semantic JSON paths
#   - Action data SQLite database, schema configuration and writer configuration
# output:
#   - canonical data and Supplemental Annotation evidence
#   - Action identities, derivations, vectors, lineage and return manifest
# depends_on:
#   - scripts/adapters/ingress/canonical_ingress_gateway_v0001.py
#   - scripts/orchestration/data/data_discovery_parse_preparation_pipeline_v0002.py
#   - scripts/orchestration/data/data_admission_lineage_pipeline_v0003.py
#   - scripts/orchestration/action/structural_unit_governance_graph_pipeline_v0002.py
#   - scripts/tools/init_data_schema_v0001.py
#   - scripts/anchor/ingest_data_text_units_v0001.py
#   - scripts/action/native_plugin/supplemental_annotation/plugins/source_timestamp_annotation/*.py
#   - scripts/action/adapters/select_semantic_text_units_v0001.py
#   - scripts/orchestration/action/action_anchor_persistence_pipeline_v0004.py
#   - scripts/orchestration/action/action_derivation_materialization_pipeline_v0003.py
#   - scripts/orchestration/action/vector_embedding_pipeline_v0003.py
#   - scripts/action/tools/init_action_data_sql_schema_v0001.py
#   - scripts/action/infrastructures/register_action_lineage_v0002.py
# used_by:
#   - future total state machine
# governance:
#   level: high
#   principle: reliable_sequence_only
# ============================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


DEFAULT_ENCODING = "utf-8"
SCRIPT_NAME = "data_action_chain_pipeline_v0009.py"
SCRIPT_FAMILY = "data_action_chain_pipeline"
SCRIPT_VERSION = "v0009"
DEFAULT_OUTPUT_ROOT = Path("scripts") / "orchestration" / "outputs" / "action"
DEFAULT_DATA_ROOT = Path("data")
DEFAULT_DATA_INTERMEDIATE_ROOT = Path("reports") / "orchestration"
DEFAULT_DATA_WORKSPACE_ROOT = Path("parse_workspace") / "orchestration"
DEFAULT_ADMISSION_TARGET_ROOT = Path("data_processed") / "data_action_chain"
DEFAULT_REGISTRY_DIR = Path("actioning") / "registry"
DEFAULT_STRUCTURAL_BUSINESS_ROOT = Path("actioning") / "pipelines" / "orchestration"
DEFAULT_ANCHOR_BUSINESS_ROOT = Path("actioning") / "anchor" / "orchestration"
DEFAULT_DERIVATION_BUSINESS_ROOT = Path("actioning") / "derivation" / "orchestration"
DEFAULT_RETURN_ROOT = Path("data") / "action_returns"
DEFAULT_VECTOR_PIPELINE_ROOT = Path("vector") / "pipeline"
DEFAULT_CHROMADB_PATH = Path("chromadb") / "action" / "action_data" / "vectors"
DEFAULT_VECTOR_STATE_INDEX = Path("vector") / "state" / "active_index.jsonl"
DEFAULT_VECTOR_REPLACED_ARCHIVE = Path("chromadb") / "action" / "action_data" / "replaced"
DEFAULT_DUCKDB_CONFIG = Path("config") / "action" / "init_schema" / "action_data_duckdb_schema_config_v0001.yml"
DEFAULT_EMBED_CONFIG = Path("config") / "action" / "config" / "embedding_generator_config_v0002.yml"
DEFAULT_ACTION_SCHEMA_CONFIG = Path("config") / "action" / "init_schema" / "action_data_sql_schema_config_v0001.yml"
DEFAULT_STEP_TIMEOUT_SECONDS = 1800
DATA_TABLE = "data_text_units"
SEMANTIC_INSTANCE_CONFIG = Path("config") / "action" / "config" / "ingest_instance_units_semantic_config_v0001.yml"
SOURCE_TIMESTAMP_ROOT = Path("scripts") / "action" / "native_plugin" / "supplemental_annotation" / "plugins" / "source_timestamp_annotation"
SOURCE_TIMESTAMP_CONFIG = SOURCE_TIMESTAMP_ROOT / "source_timestamp_rules_v0001.json"
INGRESS_REGISTRY = Path("config") / "contracts" / "format_fingerprint_registry_v0001.json"
INGRESS_CONTRACT = Path("config") / "contracts" / "canonical_conversation_ingress_v0001.schema.json"
INGRESS_ENVELOPE_CONTRACT = Path("config") / "contracts" / "canonical_ingress_envelope_v0001.schema.json"
SOURCE_ACCESS_CONTRACT = Path("config") / "contracts" / "supplemental_annotation_source_access_v0001.schema.json"
SUPPLEMENTAL_ROOT = Path("scripts") / "action" / "native_plugin" / "supplemental_annotation"
PINNED_SCRIPTS: Mapping[str, str] = {
    "canonical_ingress": "scripts/adapters/ingress/canonical_ingress_gateway_v0001.py",
    "discovery": "scripts/orchestration/data/data_discovery_parse_preparation_pipeline_v0002.py",
    "admission": "scripts/orchestration/data/data_admission_lineage_pipeline_v0003.py",
    "structural": "scripts/orchestration/action/structural_unit_governance_graph_pipeline_v0002.py",
    "source_access": str(SUPPLEMENTAL_ROOT / "framework" / "source_access_gateway_v0001.py"),
    "supplemental_runner": str(SUPPLEMENTAL_ROOT / "supplemental_annotation_runner_v0002.py"),
    "data_schema": "scripts/tools/init_data_schema_v0001.py",
    "data_ingest": "scripts/anchor/ingest_data_text_units_v0001.py",
    "timestamp_extract": str(SOURCE_TIMESTAMP_ROOT / "extract_source_message_timestamps_v0002.py"),
    "timestamp_attach": str(SOURCE_TIMESTAMP_ROOT / "attach_source_timestamps_to_text_units_v0001.py"),
    "timestamp_validate": str(SOURCE_TIMESTAMP_ROOT / "validate_source_timestamp_attachment_v0001.py"),
    "timestamp_schema": str(SOURCE_TIMESTAMP_ROOT / "init_source_timestamp_annotation_schema_v0001.py"),
    "timestamp_write": str(SOURCE_TIMESTAMP_ROOT / "write_source_timestamp_annotations_v0001.py"),
    "timestamp_db_validate": str(SOURCE_TIMESTAMP_ROOT / "validate_source_timestamp_data_db_v0001.py"),
    "semantic_selector": "scripts/action/adapters/select_semantic_text_units_v0001.py",
    "anchor": "scripts/orchestration/action/action_anchor_persistence_pipeline_v0004.py",
    "derivation": "scripts/orchestration/action/action_derivation_materialization_pipeline_v0004.py",
    "vector": "scripts/orchestration/action/vector_embedding_pipeline_v0003.py",
    "action_schema": "scripts/action/tools/init_action_data_sql_schema_v0001.py",
    "lineage": "scripts/action/infrastructures/register_action_lineage_v0002.py",
}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
VERSION_PATTERN = re.compile(r"^v\d{4}$")
REQUIRED_CONFIGS = (
    Path("config/action/config/parse_eligibility_policy_v0002.yml"),
    Path("config/action/config/language_parse_lite_policy_v0002.yml"),
    Path("config/action/config/normalize_unit_variants_policy_v0001.yml"),
    Path("config/action/config/filter_structural_noise_v0001.yml"),
    Path("config/action/config/validate_unit_boundaries_v0001.yml"),
    Path("config/action/config/decide_unit_prominence_policy_v0001.yml"),
    Path("config/action/config/ingest_concept_units_config_v0001.yml"),
    Path("config/action/config/ingest_attribute_units_config_v0001.yml"),
    SEMANTIC_INSTANCE_CONFIG,
)


class PipelineError(RuntimeError):
    """Raised when the Data-Action-Data half-cycle cannot safely continue."""


def utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"cycle_{stamp}_{uuid.uuid4().hex[:8]}"


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")


def load_json_file(path: Path, *, label: str) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"{label} must contain a JSON object: {path}")
    return payload


def parse_json_text(text: str, *, label: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"{label} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"{label} must contain a JSON object")
    return payload


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_project_root(raw: str) -> Path:
    if raw:
        candidate = Path(raw).resolve()
        if not (candidate / "AGENTS.md").is_file():
            raise PipelineError(f"project root does not contain AGENTS.md: {candidate}")
        return candidate
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "AGENTS.md").is_file():
            return candidate.resolve()
    raise PipelineError("unable to locate project root; provide --project-root")


def resolve_path(project_root: Path, raw: str, *, label: str) -> Path:
    path = Path(raw)
    resolved = (path if path.is_absolute() else project_root / path).resolve()
    require_within(resolved, project_root, label=label)
    return resolved


def require_within(path: Path, root: Path, *, label: str, allow_equal: bool = True) -> None:
    root = root.resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as exc:
        raise PipelineError(f"{label} escapes allowed root {root}: {path}") from exc
    if not allow_equal and relative == Path("."):
        raise PipelineError(f"{label} must be below, not equal to, {root}")


def require_file(path: Path, *, label: str) -> None:
    if not path.is_file():
        raise PipelineError(f"{label} is not a file: {path}")


def validate_pinned_scripts(project_root: Path) -> Dict[str, Path]:
    scripts: Dict[str, Path] = {}
    for key, relative in PINNED_SCRIPTS.items():
        path = (project_root / relative).resolve()
        require_file(path, label=f"pinned {key} script")
        scripts[key] = path
    return scripts


def validate_version(value: str) -> str:
    if not VERSION_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("version must use vNNNN format")
    return value


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def argument_value(arguments: Sequence[str], option: str) -> str:
    try:
        index = list(arguments).index(option)
        return str(arguments[index + 1])
    except (ValueError, IndexError) as exc:
        raise PipelineError(f"missing required orchestration argument: {option}") from exc


def database_vector_counts(path: Path) -> Dict[str, int]:
    require_file(path, label="Action data SQLite database")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        counts: Dict[str, int] = {}
        for target, table in (("concept", "concept_units"), ("instance", "instance_units")):
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                raise PipelineError(f"Action database is missing table {table}")
            counts[target] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return counts
    finally:
        connection.close()


def invalidate_return_manifest(path: Path) -> None:
    if not path.is_file():
        return
    payload = load_json_file(path, label="incomplete Action return manifest")
    policy = payload.get("return_policy")
    if not isinstance(policy, dict):
        raise PipelineError("Action return manifest is missing return_policy")
    payload["status"] = "invalidated_by_pipeline_failure"
    payload["invalidated_at"] = utc_now_z()
    policy["eligible_for_rediscovery"] = False
    write_json_atomic(path, payload)


def business_dir(result: Mapping[str, Any]) -> Path:
    raw = result.get("business_run_dir")
    if not isinstance(raw, str) or not raw:
        raise PipelineError("structural completion does not expose business_run_dir")
    return Path(raw).resolve()


def language_output(structural_result: Mapping[str, Any]) -> Path:
    candidates = sorted((business_dir(structural_result) / "03_language_parse").glob("*_text_units.jsonl"))
    if len(candidates) != 1:
        raise PipelineError(f"expected exactly one language text_units JSONL, found {len(candidates)}")
    return candidates[0].resolve()


def normalized_output(structural_result: Mapping[str, Any]) -> Path:
    candidate = business_dir(structural_result) / "04_normalized" / "normalized_text_units.jsonl"
    require_file(candidate, label="normalized text units")
    return candidate.resolve()


def unique_asset_id(text_units_path: Path) -> str:
    asset_ids: set[str] = set()
    with text_units_path.open("r", encoding=DEFAULT_ENCODING) as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PipelineError(f"invalid language text unit JSON at line {line_number}: {exc}") from exc
            asset_id = record.get("asset_id") if isinstance(record, dict) else None
            if not isinstance(asset_id, str) or not asset_id:
                raise PipelineError(f"language text unit line {line_number} has no asset_id")
            asset_ids.add(asset_id)
    if len(asset_ids) != 1:
        raise PipelineError(f"source timestamp extraction requires exactly one asset_id, found {len(asset_ids)}")
    return next(iter(asset_ids))


def load_yaml_mapping(path: Path, *, label: str) -> Dict[str, Any]:
    require_file(path, label=label)
    try:
        import yaml
    except ImportError as exc:
        raise PipelineError("PyYAML is required for Data-Action preflight") from exc
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise PipelineError(f"{label} is invalid YAML: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"{label} must contain a YAML mapping: {path}")
    return payload


def run_action_schema(*, python_executable: str, script: Path, config: Path, database: Path, action: str) -> Dict[str, Any]:
    result = subprocess.run(
        [python_executable, str(script), "--config", str(config), "--db-path", str(database), action],
        cwd=script.parents[3],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Action data schema {action} did not return JSON: {result.stderr.strip() or result.stdout.strip()}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"Action data schema {action} did not return an object")
    if result.returncode != 0 or payload.get("status") != "ok" or payload.get("issues"):
        raise PipelineError(f"Action data schema {action} failed: {payload.get('error') or payload.get('issues') or result.stderr.strip()}")
    return payload


def preflight_configs(*, project_root: Path, paths: Mapping[str, Path], action_db: Path) -> Dict[str, Any]:
    config_paths = [project_root / relative for relative in REQUIRED_CONFIGS]
    config_paths.extend([paths["sql_writer_config"], paths["duckdb_config"], paths["embed_config"]])
    loaded: Dict[Path, Dict[str, Any]] = {}
    checked: List[str] = []
    for path in config_paths:
        resolved = Path(path).resolve()
        if resolved not in loaded:
            loaded[resolved] = load_yaml_mapping(resolved, label="runtime config")
            checked.append(str(resolved))
    sql_config = paths["sql_writer_config"].resolve()
    connection = loaded[sql_config].get("connection")
    if not isinstance(connection, dict) or not isinstance(connection.get("sqlite"), dict):
        raise PipelineError("sql_writer config must contain connection.sqlite")
    configured_raw = connection["sqlite"].get("path")
    if not isinstance(configured_raw, str) or not configured_raw.strip():
        raise PipelineError("sql_writer config connection.sqlite.path is missing")
    configured_path = Path(configured_raw)
    if not configured_path.is_absolute():
        configured_path = project_root / configured_path
    if configured_path.resolve() != action_db.resolve():
        raise PipelineError(f"sql_writer configuration database does not match --action-db: {configured_path.resolve()} != {action_db.resolve()}")
    return {"status": "ready", "checked_count": len(checked), "checked": checked}


class PipelineRun:
    def __init__(self, *, project_root: Path, output_root: Path, run_id: str, scripts: Mapping[str, Path], timeout: int, test_mode: bool, plan: Mapping[str, Any]) -> None:
        self.project_root = project_root
        self.run_id = run_id
        self.scripts = dict(scripts)
        self.timeout = timeout
        self.run_dir = output_root / SCRIPT_FAMILY / SCRIPT_VERSION / run_id
        self.stdout_dir = self.run_dir / "captured_stdout"
        self.stderr_dir = self.run_dir / "captured_stderr"
        self.step_status_path = self.run_dir / "step_status.jsonl"
        self.manifest_path = self.run_dir / "run_manifest.json"
        self.steps: List[Dict[str, Any]] = []
        self.completed_vector_targets: set[str] = set()
        self.structural_language_output: Optional[Path] = None
        self.structural_normalized_output: Optional[Path] = None
        self.structural_completion: Optional[Path] = None
        self.ingress_result: Optional[Dict[str, Any]] = None
        self.source_access_manifest: Optional[Path] = None
        self.manifest: Dict[str, Any] = {
            "schema_version": "data_action_chain_run_v0001",
            "pipeline": {"script": SCRIPT_NAME, "family": SCRIPT_FAMILY, "version": SCRIPT_VERSION},
            "run_id": run_id,
            "cycle_id": run_id,
            "status": "running",
            "started_at": utc_now_z(),
            "finished_at": None,
            "test_mode": test_mode,
            "project_root": str(project_root),
            "run_dir": str(self.run_dir),
            "pinned_scripts": dict(PINNED_SCRIPTS),
            "steps": self.steps,
            "plan": dict(plan),
            "error": None,
        }

    def initialize(self) -> None:
        if self.run_dir.exists():
            raise PipelineError(f"run directory already exists: {self.run_dir}")
        self.stdout_dir.mkdir(parents=True, exist_ok=False)
        self.stderr_dir.mkdir(parents=True, exist_ok=False)
        write_json_atomic(self.manifest_path, self.manifest)

    def persist(self) -> None:
        write_json_atomic(self.manifest_path, self.manifest)

    def _run_process_step(self, name: str, script_key: str, arguments: Sequence[str]) -> Dict[str, Any]:
        command = [sys.executable, str(self.scripts[script_key]), *map(str, arguments)]
        index = len(self.steps) + 1
        stdout_path = self.stdout_dir / f"{index:02d}_{name}.txt"
        stderr_path = self.stderr_dir / f"{index:02d}_{name}.txt"
        started_at = utc_now_z()
        started_clock = time.monotonic()
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        try:
            result = subprocess.run(command, cwd=self.project_root, env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout, check=False, shell=False)
            stdout, stderr, returncode, timed_out = result.stdout or "", result.stderr or "", int(result.returncode), False
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            returncode, timed_out = -1, True
        write_text_atomic(stdout_path, stdout)
        write_text_atomic(stderr_path, stderr)
        step = {
            "index": index,
            "name": name,
            "script": self.scripts[script_key].name,
            "command": command,
            "started_at": started_at,
            "finished_at": utc_now_z(),
            "duration_ms": int((time.monotonic() - started_clock) * 1000),
            "returncode": returncode,
            "timed_out": timed_out,
            "status": "success" if returncode == 0 and not timed_out else "failure",
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "shell": False,
        }
        self.steps.append(step)
        append_jsonl(self.step_status_path, step)
        self.persist()
        if timed_out:
            raise PipelineError(f"step {name} exceeded {self.timeout} seconds")
        if returncode != 0:
            raise PipelineError(f"step {name} failed: {stderr.strip() or stdout.strip() or 'no output'}")
        return parse_json_text(stdout, label=f"{name} stdout")

    def run_step(self, name: str, script_key: str, arguments: Sequence[str]) -> Dict[str, Any]:
        effective_args = list(arguments)
        if script_key == "discovery":
            ingress_dir = self.run_dir / "canonical_data" / "data_raw" / "ingress"
            self.ingress_result = self._run_process_step(
                "canonical_ingress_detection_and_adaptation",
                "canonical_ingress",
                [
                    "--input", str(Path(self.manifest["plan"]["target_path"]).resolve()),
                    "--output-dir", str(ingress_dir),
                    "--run-id", f"{self.run_id}_ingress",
                    "--registry", str(self.project_root / INGRESS_REGISTRY),
                ],
            )
            if self.ingress_result.get("status") != "completed" or self.ingress_result.get("raw_unchanged") is not True:
                raise PipelineError("canonical ingress did not complete with immutable source verification")
            canonical_path = Path(str(self.ingress_result.get("canonical_path", ""))).resolve()
            require_file(canonical_path, label="canonical ingress output")
            canonical_root = (self.run_dir / "canonical_data").resolve()
            effective_args[effective_args.index("--data-root") + 1] = str(canonical_root)
            effective_args[effective_args.index("--target") + 1] = canonical_path.relative_to(canonical_root).as_posix()
            effective_args[effective_args.index("--intermediate-root") + 1] = "r"
            effective_args[effective_args.index("--workspace-root") + 1] = "w"
        if script_key == "admission":
            if self.ingress_result is None:
                raise PipelineError("admission requires completed canonical ingress")
            effective_args.extend(["--ingress-envelope", str(self.ingress_result["envelope_path"])])
        if script_key == "vector" and "--target" in effective_args:
            target = effective_args[effective_args.index("--target") + 1]
            if "--test-mode" in effective_args and "--mock-api" not in effective_args and "--confirm-model-api" in sys.argv:
                effective_args.append("--confirm-real-model-in-test-mode")
            if target == "instance" and "concept" in self.completed_vector_targets:
                effective_args.extend(["--integrity-target", "all"])
            result = self._run_process_step(name, script_key, effective_args)
            self.completed_vector_targets.add(target)
            return result
        if script_key == "structural":
            result = self._run_process_step(name, script_key, effective_args)
            self.structural_language_output = language_output(result)
            self.structural_normalized_output = normalized_output(result)
            completion_value = result.get("completion_manifest")
            if not isinstance(completion_value, str) or not completion_value:
                raise PipelineError("structural pipeline did not expose completion_manifest")
            self.structural_completion = Path(completion_value).resolve()
            require_file(self.structural_completion, label="structural completion manifest")
            return result
        if script_key == "anchor":
            effective_args = self._prepare_anchor(effective_args)
        return self._run_process_step(name, script_key, effective_args)

    def _run_source_access_gateway(self) -> Path:
        if self.structural_completion is None or self.structural_language_output is None or self.ingress_result is None:
            raise PipelineError("source access requires completed ingress and structural parsing")
        structural = load_json_file(self.structural_completion, label="structural completion manifest")
        if structural.get("status") != "completed":
            raise PipelineError("source access requires structural completion status=completed")
        working_copy = Path(str((structural.get("source_inputs") or {}).get("working_copy", ""))).resolve()
        require_file(working_copy, label="structural working copy")
        access_dir = self.run_dir / "supplemental_annotation" / "source_access"
        access_manifest = access_dir / "source_access_manifest.json"
        access_result = self._run_process_step(
            "authorize_supplemental_annotation_source_access",
            "source_access",
            [
                "--structural-completion", str(self.structural_completion),
                "--ingress-envelope", str(self.ingress_result["envelope_path"]),
                "--source", str(working_copy),
                "--language-units", str(self.structural_language_output),
                "--requester-name", SCRIPT_NAME,
                "--requester-version", SCRIPT_VERSION,
                "--purpose", "source_fact_annotation",
                "--manifest", str(access_manifest),
                "--allowed-coordinates", str(access_dir / "allowed_coordinates.jsonl"),
            ],
        )
        if access_result.get("status") != "authorized" or int(access_result.get("allowed_coordinate_count", 0)) < 1:
            raise PipelineError("supplemental annotation source access was not authorized")
        require_file(access_manifest, label="source access manifest")
        self.source_access_manifest = access_manifest
        return access_manifest

    def _run_public_supplemental_interface(self, access_manifest: Path) -> Dict[str, Any]:
        if self.structural_language_output is None:
            raise PipelineError("Supplemental Annotation requires structural language output")
        output_dir = self.run_dir / "supplemental_annotation" / "framework"
        result = self._run_process_step(
            "run_public_supplemental_annotation_interface",
            "supplemental_runner",
            [
                "--input", str(self.structural_language_output),
                "--output-db", str(output_dir / "supplemental_annotations.sqlite3"),
                "--manifest", str(output_dir / "run_manifest.json"),
                "--run-id", f"{self.run_id}_supplemental",
                "--registry", str(self.project_root / SUPPLEMENTAL_ROOT / "config" / "plugin_registry_v0002.json"),
                "--source-access-manifest", str(access_manifest),
            ] + (["--test-mode"] if self.manifest["test_mode"] else []),
        )
        if result.get("status") != "completed":
            raise PipelineError("public Supplemental Annotation interface did not complete")
        self.manifest["supplemental_annotation_interface"] = {
            "status": "completed",
            "framework_version": result.get("framework_version"),
            "source_access_id": (result.get("source_access") or {}).get("access_id"),
            "manifest": str(output_dir / "run_manifest.json"),
            "output_db": str(output_dir / "supplemental_annotations.sqlite3"),
        }
        self.persist()
        return result

    def _prepare_anchor(self, effective_args: List[str]) -> List[str]:
        if self.structural_language_output is None or self.structural_normalized_output is None:
            raise PipelineError("semantic anchor requires completed language and normalization outputs")
        access_manifest = self._run_source_access_gateway()
        self._run_public_supplemental_interface(access_manifest)
        data_db = Path(argument_value(effective_args, "--data-db")).resolve()
        data_table = argument_value(effective_args, "--data-table")
        if data_table != DATA_TABLE:
            raise PipelineError(f"anchor table must be {DATA_TABLE}")
        annotation_dir = self.run_dir / "source_timestamp_annotation"
        annotations = annotation_dir / "source_message_timestamp_annotations.jsonl"
        extraction_result = self._run_process_step(
            "extract_source_message_timestamps",
            "timestamp_extract",
            [
                "--source-access-manifest", str(access_manifest),
                "--annotations", str(annotations),
                "--issues", str(annotation_dir / "source_message_timestamp_issues.jsonl"),
                "--manifest", str(annotation_dir / "extraction_manifest.json"),
                "--run-id", f"{self.run_id}_timestamp_extract",
            ],
        )
        if extraction_result.get("status") != "completed" or int(extraction_result.get("records_created", -1)) < 1:
            raise PipelineError("source timestamp extraction produced no validated annotations")
        enriched_language = annotation_dir / "language_text_units_with_source_timestamps.jsonl"
        language_attach_result = self._run_process_step(
            "attach_source_timestamps_to_language_units",
            "timestamp_attach",
            [
                "--units", str(self.structural_language_output), "--annotations", str(annotations),
                "--output", str(enriched_language), "--manifest", str(annotation_dir / "language_attachment_manifest.json"),
                "--run-id", f"{self.run_id}_timestamp_language_attach",
            ],
        )
        if language_attach_result.get("status") != "completed" or int(language_attach_result.get("input_records", -1)) != int(language_attach_result.get("output_records", -2)):
            raise PipelineError("language timestamp attachment failed conservation")
        language_validation = self._run_process_step(
            "validate_language_timestamp_attachment",
            "timestamp_validate",
            [
                "--original", str(self.structural_language_output), "--enriched", str(enriched_language),
                "--report", str(annotation_dir / "language_attachment_validation.json"),
                "--run-id", f"{self.run_id}_timestamp_language_validate",
            ],
        )
        if language_validation.get("status") != "passed":
            raise PipelineError("language timestamp attachment validation failed")
        enriched_normalized = annotation_dir / "normalized_text_units_with_source_timestamps.jsonl"
        normalized_attach_result = self._run_process_step(
            "attach_source_timestamps_to_normalized_units",
            "timestamp_attach",
            [
                "--units", str(self.structural_normalized_output), "--annotations", str(annotations),
                "--output", str(enriched_normalized), "--manifest", str(annotation_dir / "normalized_attachment_manifest.json"),
                "--run-id", f"{self.run_id}_timestamp_normalized_attach",
            ],
        )
        if normalized_attach_result.get("status") != "completed" or int(normalized_attach_result.get("input_records", -1)) != int(normalized_attach_result.get("output_records", -2)):
            raise PipelineError("normalized timestamp attachment failed conservation")
        normalized_validation = self._run_process_step(
            "validate_normalized_timestamp_attachment",
            "timestamp_validate",
            [
                "--original", str(self.structural_normalized_output), "--enriched", str(enriched_normalized),
                "--report", str(annotation_dir / "normalized_attachment_validation.json"),
                "--run-id", f"{self.run_id}_timestamp_normalized_validate",
            ],
        )
        if normalized_validation.get("status") != "passed":
            raise PipelineError("normalized timestamp attachment validation failed")
        schema_result = self._run_process_step("initialize_data_anchor_schema", "data_schema", ["--db-path", str(data_db), "--init"])
        if schema_result.get("status") != "ok":
            raise PipelineError("data anchor schema initialization did not return status=ok")
        ingest_result = self._run_process_step(
            "ingest_data_text_units",
            "data_ingest",
            ["--inputs", str(enriched_language), "--db", str(data_db), "--run-meta", str(self.run_dir / "data_text_units_ingest_run_meta.json")],
        )
        stats = ingest_result.get("stats") or {}
        input_records = int(stats.get("input_records", -1))
        if ingest_result.get("status") != "ok" or input_records < 1 or int(stats.get("invalid_records", -1)) != 0 or int(stats.get("inserted_records", -1)) + int(stats.get("skipped_duplicate", -1)) != input_records:
            raise PipelineError("data text-unit ingestion failed conservation or validity checks")
        timestamp_schema_result = self._run_process_step("initialize_source_timestamp_side_table", "timestamp_schema", ["--db", str(data_db)])
        if timestamp_schema_result.get("status") != "completed":
            raise PipelineError("source timestamp side-table initialization failed")
        timestamp_write_result = self._run_process_step(
            "write_source_timestamp_side_table",
            "timestamp_write",
            [
                "--units", str(enriched_language), "--db", str(data_db),
                "--manifest", str(annotation_dir / "database_write_manifest.json"),
                "--run-id", f"{self.run_id}_timestamp_write",
            ],
        )
        if timestamp_write_result.get("status") != "completed" or int(timestamp_write_result.get("input_records", -1)) != input_records:
            raise PipelineError("source timestamp side-table write failed conservation")
        database_validation = self._run_process_step(
            "validate_source_timestamp_database",
            "timestamp_db_validate",
            [
                "--units", str(enriched_language), "--db", str(data_db),
                "--report", str(annotation_dir / "database_validation.json"),
                "--run-id", f"{self.run_id}_timestamp_db_validate",
            ],
        )
        if database_validation.get("status") != "passed":
            raise PipelineError("source timestamp database validation failed")
        self.manifest["source_timestamp_propagation"] = {
            "status": "completed",
            "source_semantics": "verified_canonical_message_created_event",
            "source_access_manifest": str(access_manifest),
            "source_access_manifest_sha256": file_sha256(access_manifest),
            "source_access_id": extraction_result.get("source_access_id"),
            "source": (load_json_file(access_manifest, label="source access manifest").get("canonical_source") or {}).get("project_relative_path"),
            "source_sha256": extraction_result.get("source_sha256"),
            "annotations": str(annotations),
            "annotations_sha256": extraction_result.get("annotations_sha256"),
            "records_created": extraction_result.get("records_created"),
            "extraction_status_counts": extraction_result.get("status_counts"),
            "language_output": str(enriched_language),
            "language_output_sha256": language_attach_result.get("output_sha256"),
            "normalized_output": str(enriched_normalized),
            "normalized_output_sha256": normalized_attach_result.get("output_sha256"),
            "input_records": input_records,
            "database_table": "data_text_unit_source_timestamps",
            "database_records": database_validation.get("side_table_records"),
            "database_validation": database_validation.get("status"),
        }
        self.persist()
        semantic_dir = self.run_dir / "semantic_source"
        semantic_output = semantic_dir / "semantic_text_units.jsonl"
        selector_args = ["--input", str(enriched_normalized), "--output", str(semantic_output), "--run-meta", str(semantic_dir / "run_meta.json")]
        for semantic_path in self.manifest["plan"]["semantic_paths"]:
            selector_args.extend(["--include-path", semantic_path])
        semantic_limit = self.manifest["plan"].get("semantic_limit")
        if semantic_limit is not None:
            selector_args.extend(["--max-records", str(semantic_limit)])
        if self.manifest["test_mode"]:
            selector_args.append("--test-mode")
        selector_result = self._run_process_step("select_semantic_text_units", "semantic_selector", selector_args)
        selected = int((selector_result.get("stats") or {}).get("rows_selected", 0))
        if selector_result.get("status") != "completed" or selected < 1 or not semantic_output.is_file():
            raise PipelineError("semantic text-unit selection produced no validated output")
        effective_args.extend(["--identity-input", str(semantic_output), "--semantic-instance-config", str(self.project_root / SEMANTIC_INSTANCE_CONFIG)])
        return effective_args

    def finish_success(self, completion_path: Path) -> None:
        self.manifest.update({"status": "completed", "finished_at": utc_now_z(), "completion_manifest": str(completion_path)})
        self.persist()

    def finish_failure(self, error: Exception) -> Path:
        error_record = {"type": type(error).__name__, "message": str(error)}
        self.manifest.update({"status": "failed", "finished_at": utc_now_z(), "error": error_record})
        self.persist()
        path = self.run_dir / "failure.json"
        write_json_atomic(path, {"schema_version": "data_action_chain_failure_v0001", "pipeline": SCRIPT_FAMILY, "run_id": self.run_id, "cycle_id": self.run_id, "status": "failed", "failed_at": utc_now_z(), "error": error_record, "completed_steps": [step["name"] for step in self.steps if step["status"] == "success"], "automatic_retry": False})
        return path


def completion_path_from_result(result: Mapping[str, Any], *, output_root: Path, family: str, version: str, run_id: str) -> Path:
    raw = result.get("completion_manifest")
    path = Path(str(raw)).resolve() if isinstance(raw, str) and raw else (output_root / family / version / run_id / "completion_manifest.json").resolve()
    require_file(path, label=f"{family} completion manifest")
    completion = load_json_file(path, label=f"{family} completion manifest")
    if completion.get("status") != "completed":
        raise PipelineError(f"{family} completion status is not completed")
    return path


def validate_args_and_plan(args: argparse.Namespace) -> Dict[str, Any]:
    project_root = find_project_root(args.project_root)
    for contract_path in (INGRESS_REGISTRY, INGRESS_CONTRACT, INGRESS_ENVELOPE_CONTRACT, SOURCE_ACCESS_CONTRACT):
        require_file(project_root / contract_path, label=f"contract config {contract_path.as_posix()}")
    scripts = validate_pinned_scripts(project_root)
    paths = {
        "data_root": resolve_path(project_root, args.data_root, label="data root"),
        "output_root": resolve_path(project_root, args.output_root, label="output root"),
        "admission_target_root": resolve_path(project_root, args.admission_target_root, label="admission target root"),
        "registry_dir": resolve_path(project_root, args.registry_dir, label="Action registry directory"),
        "structural_business_root": resolve_path(project_root, args.structural_business_root, label="structural business root"),
        "anchor_business_root": resolve_path(project_root, args.anchor_business_root, label="anchor business root"),
        "derivation_business_root": resolve_path(project_root, args.derivation_business_root, label="derivation business root"),
        "return_root": resolve_path(project_root, args.return_root, label="Action return root"),
        "data_db": resolve_path(project_root, args.data_db, label="data SQLite database"),
        "action_db": resolve_path(project_root, args.action_db, label="Action data SQLite database"),
        "sql_writer_config": resolve_path(project_root, args.sql_writer_config, label="SQL writer config"),
        "duckdb_path": resolve_path(project_root, args.duckdb_path, label="DuckDB path"),
        "duckdb_config": resolve_path(project_root, args.duckdb_config, label="DuckDB config"),
        "vector_pipeline_root": resolve_path(project_root, args.vector_pipeline_root, label="vector pipeline root"),
        "chromadb_path": resolve_path(project_root, args.chromadb_path, label="Chroma path"),
        "vector_state_index": resolve_path(project_root, args.vector_state_index, label="vector state index"),
        "vector_replaced_archive": resolve_path(project_root, args.vector_replaced_archive, label="vector replaced archive"),
        "embed_config": resolve_path(project_root, args.embed_config, label="embedding config"),
        "action_schema_config": resolve_path(project_root, args.action_db_schema_config, label="Action data SQLite schema config"),
    }
    for key in ("sql_writer_config", "duckdb_config", "embed_config", "action_schema_config"):
        require_file(paths[key], label=key)
    target_path = (paths["data_root"] / args.target).resolve()
    require_within(target_path, paths["data_root"] / "data_raw", label="data target", allow_equal=False)
    require_file(target_path, label="data target")

    run_id = args.run_id or new_run_id()
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise PipelineError("run_id contains unsupported characters")
    if args.vector_target != "none" and not args.mock_api and not args.confirm_model_api:
        raise PipelineError("non-mock vector execution requires --confirm-model-api")
    if args.derivation_target in {"neo4j", "all"} and not args.confirm_neo4j_write:
        raise PipelineError("Neo4j execution requires --confirm-neo4j-write")
    if args.data_table != DATA_TABLE:
        raise PipelineError(f"v0009 requires --data-table {DATA_TABLE}; received {args.data_table}")
    if args.semantic_limit is not None and not args.test_mode:
        raise PipelineError("--semantic-limit is permitted only with --test-mode")
    if args.test_mode:
        temp_root = (project_root / "temp").resolve()
        for key, path in paths.items():
            if key in {"duckdb_config", "embed_config", "action_schema_config"}:
                continue
            require_within(path, temp_root, label=f"test {key}")
    if args.init_action_db:
        if args.dry_run:
            raise PipelineError("--init-action-db cannot be combined with --dry-run")
        if not args.confirm_database_write:
            raise PipelineError("--init-action-db requires --confirm-database-write")
        run_action_schema(
            python_executable=sys.executable,
            script=scripts["action_schema"],
            config=paths["action_schema_config"],
            database=paths["action_db"],
            action="--init",
        )
    if paths["action_db"].is_file():
        validation = run_action_schema(
            python_executable=sys.executable,
            script=scripts["action_schema"],
            config=paths["action_schema_config"],
            database=paths["action_db"],
            action="--validate",
        )
        action_db_status = {"status": "ready", "path": str(paths["action_db"]), "tables_checked": validation.get("tables_checked", [])}
    elif args.dry_run:
        action_db_status = {"status": "missing", "path": str(paths["action_db"]), "bootstrap_required": True}
    else:
        raise PipelineError(f"action_db is not a file: {paths['action_db']}")
    config_status = preflight_configs(project_root=project_root, paths=paths, action_db=paths["action_db"])
    plan = {
        "project_root": project_root,
        "scripts": scripts,
        "paths": paths,
        "target": args.target,
        "target_path": target_path,
        "run_id": run_id,
        "asset_version": args.asset_version,
        "derivation_target": args.derivation_target,
        "vector_target": args.vector_target,
        "test_mode": bool(args.test_mode),
        "semantic_paths": list(dict.fromkeys(args.semantic_path)),
        "semantic_limit": args.semantic_limit,
        "semantic_limit_test_only": args.semantic_limit is not None,
        "preflight": {"ready": action_db_status["status"] == "ready", "action_data_db": action_db_status, "configs": config_status},
        "action_db_bootstrap": {"requested": bool(args.init_action_db), "schema_config": str(paths["action_schema_config"])},
        "source_timestamp_annotation": {
            "source_access_contract": SOURCE_ACCESS_CONTRACT.as_posix(),
            "source_semantics": "verified_canonical_message_created_event",
            "insertion_point": "after_structural_parse_before_data_ingest",
            "canonical_storage": "data_text_units_unchanged",
            "annotation_storage": "data_text_unit_source_timestamps",
        },
        "canonical_ingress": {
            "registry": INGRESS_REGISTRY.as_posix(),
            "contract": INGRESS_CONTRACT.as_posix(),
            "unknown_format_policy": "reject",
            "ambiguous_format_policy": "reject",
        },
    }
    return plan


def execute_pipeline(args: argparse.Namespace) -> Dict[str, Any]:
    resolved = validate_args_and_plan(args)
    project_root: Path = resolved["project_root"]
    scripts: Mapping[str, Path] = resolved["scripts"]
    paths: Mapping[str, Path] = resolved["paths"]
    run_id: str = resolved["run_id"]
    plan_public = {key: (str(value) if isinstance(value, Path) else value) for key, value in resolved.items() if key not in {"project_root", "scripts", "paths"}}
    plan_public["paths"] = {key: str(value) for key, value in paths.items()}
    plan_public["pinned_scripts"] = dict(PINNED_SCRIPTS)
    plan_public["dry_run"] = bool(args.dry_run)
    if args.dry_run:
        plan_public.update({"pipeline": SCRIPT_FAMILY, "version": SCRIPT_VERSION, "status": "dry-run"})
        return plan_public
    if not args.confirm_execution or not args.confirm_database_write:
        raise PipelineError("actual half-cycle requires --confirm-execution and --confirm-database-write")

    run = PipelineRun(project_root=project_root, output_root=paths["output_root"], run_id=run_id, scripts=scripts, timeout=args.step_timeout, test_mode=bool(args.test_mode), plan=plan_public)
    run.initialize()
    lineage_record_id: Optional[str] = None
    lineage_finalized = False
    completion_manifests: List[Path] = []
    child_results: Dict[str, Any] = {}
    return_manifest_path = paths["return_root"] / run_id / "action_return_manifest.json"
    child_prefix = run_id[:48]
    try:
        discovery_run_id = f"{child_prefix}_d"
        discovery = run.run_step("data_discovery_parse_preparation", "discovery", ["--project-root", str(project_root), "--data-root", str(paths["data_root"]), "--target", args.target, "--output-root", str(paths["output_root"]), "--intermediate-root", args.data_intermediate_root, "--workspace-root", args.data_workspace_root, "--run-id", discovery_run_id, "--step-timeout", str(args.step_timeout)])
        if discovery.get("status") != "ready_for_admission":
            raise PipelineError("discovery pipeline did not reach ready_for_admission")
        handoff_path = Path(str(discovery.get("handoff_manifest", ""))).resolve()
        require_file(handoff_path, label="discovery handoff manifest")
        child_results["discovery"] = discovery

        admission_run_id = f"{child_prefix}_a"
        admission_args = ["--project-root", str(project_root), "--handoff-manifest", str(handoff_path), "--target-root", str(paths["admission_target_root"]), "--registry-dir", str(paths["registry_dir"]), "--asset-version", args.asset_version, "--output-root", str(paths["output_root"]), "--run-id", admission_run_id, "--step-timeout", str(args.step_timeout), "--confirm-admission"]
        if args.test_mode:
            admission_args.append("--test-mode")
        admission = run.run_step("data_admission_open_action_lineage", "admission", admission_args)
        admission_completion = completion_path_from_result(admission, output_root=paths["output_root"], family="data_admission_lineage_pipeline", version="v0003", run_id=admission_run_id)
        lineage_path = Path(str(admission.get("action_lineage_record", ""))).resolve()
        lineage_record = load_json_file(lineage_path, label="Action lineage record")
        lineage_record_id = lineage_record.get("record_id")
        if not isinstance(lineage_record_id, str) or lineage_record.get("status") != "ready_for_action":
            raise PipelineError("admission did not produce a ready Action lineage record")
        child_results["admission"] = admission

        structural_run_id = f"{child_prefix}_s"
        structural_args = ["--project-root", str(project_root), "--completion-manifest", str(admission_completion), "--output-root", str(paths["output_root"]), "--business-root", str(paths["structural_business_root"]), "--run-id", structural_run_id, "--step-timeout", str(args.step_timeout), "--confirm-execution"]
        if args.test_mode:
            structural_args.append("--test-mode")
        structural = run.run_step("action_structural_governance", "structural", structural_args)
        structural_completion = completion_path_from_result(structural, output_root=paths["output_root"], family="structural_unit_governance_graph_pipeline", version="v0002", run_id=structural_run_id)
        completion_manifests.append(structural_completion)
        child_results["structural"] = structural

        anchor_run_id = f"{child_prefix}_p"
        anchor_args = ["--project-root", str(project_root), "--completion-manifest", str(structural_completion), "--data-db", str(paths["data_db"]), "--data-table", args.data_table, "--action-db", str(paths["action_db"]), "--sql-writer-config", str(paths["sql_writer_config"]), "--output-root", str(paths["output_root"]), "--business-root", str(paths["anchor_business_root"]), "--run-id", anchor_run_id, "--step-timeout", str(args.step_timeout), "--confirm-database-write"]
        if args.test_mode:
            anchor_args.append("--test-mode")
        anchor = run.run_step("action_anchor_persistence", "anchor", anchor_args)
        anchor_completion = completion_path_from_result(anchor, output_root=paths["output_root"], family="action_anchor_persistence_pipeline", version="v0004", run_id=anchor_run_id)
        completion_manifests.append(anchor_completion)
        child_results["anchor"] = anchor

        derivation_run_id = f"{child_prefix}_m"
        derivation_args = ["--anchor-completion-manifest", str(anchor_completion), "--structural-completion-manifest", str(structural_completion), "--target", args.derivation_target, "--duckdb-path", str(paths["duckdb_path"]), "--duckdb-config", str(paths["duckdb_config"]), "--output-root", str(paths["output_root"]), "--business-output-root", str(paths["derivation_business_root"]), "--run-id", derivation_run_id, "--python-executable", sys.executable, "--timeout-seconds", str(args.step_timeout)]
        if args.derivation_target in {"duckdb", "all"}:
            derivation_args.append("--confirm-duckdb-write")
        if args.derivation_target in {"neo4j", "all"}:
            derivation_args.append("--confirm-neo4j-write")
        if args.test_mode:
            derivation_args.append("--test-mode")
        derivation = run.run_step("action_derivation_materialization", "derivation", derivation_args)
        derivation_completion = completion_path_from_result(derivation, output_root=paths["output_root"], family="action_derivation_materialization_pipeline", version="v0004", run_id=derivation_run_id)
        completion_manifests.append(derivation_completion)
        child_results["derivation"] = derivation

        counts = database_vector_counts(paths["action_db"])
        if args.vector_target == "none":
            vector_targets: List[str] = []
        elif args.vector_target == "available":
            vector_targets = [target for target in ("concept", "instance") if counts[target] > 0]
        elif args.vector_target == "both":
            vector_targets = ["concept", "instance"]
        else:
            vector_targets = [args.vector_target]
        child_results["vector_selection"] = {"requested": args.vector_target, "source_counts": counts, "selected": vector_targets, "skipped": not vector_targets}
        for target in vector_targets:
            if counts[target] < 1:
                raise PipelineError(f"vector target {target} has no source records")
            vector_run_id = f"{child_prefix}_v{target[0]}"
            vector_args = ["--target", target, "--run-id", vector_run_id, "--sql-db", str(paths["action_db"]), "--sql-config", str(paths["sql_writer_config"]), "--embed-config", str(paths["embed_config"]), "--pipeline-root", str(paths["vector_pipeline_root"]), "--chromadb-path", str(paths["chromadb_path"]), "--state-index", str(paths["vector_state_index"]), "--replaced-archive", str(paths["vector_replaced_archive"]), "--output-root", str(paths["output_root"]), "--python-executable", sys.executable, "--timeout-seconds", str(args.step_timeout), "--confirm-vector-write"]
            if args.mock_api:
                vector_args.append("--mock-api")
            if args.test_mode:
                vector_args.append("--test-mode")
            vector = run.run_step(f"action_vector_{target}", "vector", vector_args)
            vector_completion = completion_path_from_result(vector, output_root=paths["output_root"], family="vector_embedding_pipeline", version="v0003", run_id=vector_run_id)
            completion_manifests.append(vector_completion)
            child_results[f"vector_{target}"] = vector

        return_manifest = {
            "schema_version": "action_return_manifest_v0001",
            "pipeline": SCRIPT_FAMILY,
            "pipeline_version": SCRIPT_VERSION,
            "run_id": run_id,
            "cycle_id": run_id,
            "status": "ready_for_data_discovery",
            "created_at": utc_now_z(),
            "source": {"target": str(resolved["target_path"]), "sha256": file_sha256(resolved["target_path"]), "admission_completion": str(admission_completion), "action_lineage_record_id": lineage_record_id},
            "action_outputs": [{"pipeline": load_json_file(path, label="child completion").get("pipeline"), "completion_manifest": str(path), "sha256": file_sha256(path)} for path in completion_manifests],
            "return_policy": {"kind": "manifest_only", "business_artifacts_remain_in_authoritative_action_locations": True, "eligible_for_rediscovery": True, "automatic_loop": False},
        }
        if return_manifest_path.exists():
            raise PipelineError(f"Action return manifest already exists: {return_manifest_path}")
        write_json_atomic(return_manifest_path, return_manifest)

        finalize_args = ["--project-root", str(project_root), "finalize", "--record-id", lineage_record_id]
        for path in completion_manifests:
            finalize_args.extend(["--completion-manifest", str(path)])
        finalize_args.extend(["--return-manifest", str(return_manifest_path), "--registry-dir", str(paths["registry_dir"])])
        finalized = run.run_step("finalize_action_lineage", "lineage", finalize_args)
        if finalized.get("status") != "completed" or finalized.get("record_id") != lineage_record_id:
            raise PipelineError("Action lineage did not finalize as completed")
        lineage_finalized = True
        child_results["lineage_finalization"] = {"record_id": lineage_record_id, "status": "completed", "record_path": str(lineage_path)}

        completion = {
            "schema_version": "data_action_chain_completion_v0001",
            "pipeline": SCRIPT_FAMILY,
            "pipeline_version": SCRIPT_VERSION,
            "run_id": run_id,
            "cycle_id": run_id,
            "status": "completed",
            "readiness": "ready_for_data_discovery",
            "completed_at": utc_now_z(),
            "test_mode": bool(args.test_mode),
            "action_lineage": {"record_id": lineage_record_id, "record_path": str(lineage_path), "status": "completed"},
            "child_completion_manifests": [str(path) for path in completion_manifests],
            "action_return_manifest": str(return_manifest_path),
            "vector_selection": child_results["vector_selection"],
            "source_timestamp_propagation": run.manifest.get("source_timestamp_propagation"),
            "canonical_ingress": run.ingress_result,
            "supplemental_annotation_interface": run.manifest.get("supplemental_annotation_interface"),
            "excluded": {"understanding_orchestration": True, "automatic_next_cycle": True, "action_development_registration": True},
        }
        completion_path = run.run_dir / "completion_manifest.json"
        write_json_atomic(completion_path, completion)
        run.finish_success(completion_path)
        return {"pipeline": SCRIPT_FAMILY, "version": SCRIPT_VERSION, "run_id": run_id, "cycle_id": run_id, "status": "completed", "readiness": "ready_for_data_discovery", "run_dir": str(run.run_dir), "completion_manifest": str(completion_path), "action_return_manifest": str(return_manifest_path), "action_lineage_record": str(lineage_path), "child_completion_count": len(completion_manifests), "vector_selection": child_results["vector_selection"]}
    except Exception as exc:
        failure_path = run.finish_failure(exc)
        if return_manifest_path.is_file() and not lineage_finalized:
            invalidate_return_manifest(return_manifest_path)
        if lineage_record_id:
            command = [sys.executable, str(scripts["lineage"]), "--project-root", str(project_root), "fail", "--record-id", lineage_record_id, "--reason", f"Data-Action chain failed: {type(exc).__name__}: {exc}", "--failure-manifest", str(failure_path), "--registry-dir", str(paths["registry_dir"])]
            result = subprocess.run(command, cwd=project_root, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.step_timeout, check=False, shell=False)
            write_text_atomic(run.run_dir / "lineage_failure_stdout.txt", result.stdout or "")
            write_text_atomic(run.run_dir / "lineage_failure_stderr.txt", result.stderr or "")
            run.manifest["lineage_failure_freeze"] = {"attempted": True, "returncode": int(result.returncode)}
            run.persist()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reliably execute one Data-Action-Data half-cycle.")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--target", required=True)
    parser.add_argument("--data-intermediate-root", default=str(DEFAULT_DATA_INTERMEDIATE_ROOT))
    parser.add_argument("--data-workspace-root", default=str(DEFAULT_DATA_WORKSPACE_ROOT))
    parser.add_argument("--admission-target-root", default=str(DEFAULT_ADMISSION_TARGET_ROOT))
    parser.add_argument("--registry-dir", default=str(DEFAULT_REGISTRY_DIR))
    parser.add_argument("--asset-version", type=validate_version, required=True)
    parser.add_argument("--data-db", required=True)
    parser.add_argument("--data-table", required=True)
    parser.add_argument("--action-db", required=True)
    parser.add_argument("--init-action-db", action="store_true")
    parser.add_argument("--action-db-schema-config", default=str(DEFAULT_ACTION_SCHEMA_CONFIG))
    parser.add_argument("--sql-writer-config", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--structural-business-root", default=str(DEFAULT_STRUCTURAL_BUSINESS_ROOT))
    parser.add_argument("--anchor-business-root", default=str(DEFAULT_ANCHOR_BUSINESS_ROOT))
    parser.add_argument("--derivation-business-root", default=str(DEFAULT_DERIVATION_BUSINESS_ROOT))
    parser.add_argument("--derivation-target", choices=("duckdb", "neo4j", "all"), default="duckdb")
    parser.add_argument("--duckdb-path", required=True)
    parser.add_argument("--duckdb-config", default=str(DEFAULT_DUCKDB_CONFIG))
    parser.add_argument("--vector-target", choices=("none", "available", "concept", "instance", "both"), default="available")
    parser.add_argument("--vector-pipeline-root", default=str(DEFAULT_VECTOR_PIPELINE_ROOT))
    parser.add_argument("--chromadb-path", default=str(DEFAULT_CHROMADB_PATH))
    parser.add_argument("--vector-state-index", default=str(DEFAULT_VECTOR_STATE_INDEX))
    parser.add_argument("--vector-replaced-archive", default=str(DEFAULT_VECTOR_REPLACED_ARCHIVE))
    parser.add_argument("--embed-config", default=str(DEFAULT_EMBED_CONFIG))
    parser.add_argument("--return-root", default=str(DEFAULT_RETURN_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--step-timeout", type=positive_integer, default=DEFAULT_STEP_TIMEOUT_SECONDS)
    parser.add_argument("--mock-api", action="store_true")
    parser.add_argument("--confirm-model-api", action="store_true")
    parser.add_argument("--confirm-neo4j-write", action="store_true")
    parser.add_argument("--confirm-database-write", action="store_true")
    parser.add_argument("--confirm-execution", action="store_true")
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--semantic-path", action="append", required=True)
    parser.add_argument("--semantic-limit", type=positive_integer, default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute_pipeline(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "pipeline": SCRIPT_FAMILY,
                    "pipeline_version": SCRIPT_VERSION,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
