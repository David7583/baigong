#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: data_action_chain_pipeline_v0001.py
# 中文名: Data-Action-Data半循环总编排脚本
# Version: v0001
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 可靠串联数据发现、接纳、Action处理、来源结束和Action产物回流。
# Scope: 单次执行Data -> Action -> Data半循环，终点为ready_for_data_discovery。
#
# 职责说明:
# 1. 固定并校验各子编排版本、顺序、输入输出和状态；
# 2. 贯穿同一cycle_id和Action lineage record_id；
# 3. 捕获子编排stdout、stderr、退出码和完成清单；
# 4. 只在全部已启用Action节点成功后生成回流清单并结束Action lineage；
# 5. 失败时停止下游并尽力将已开启Action lineage冻结为failed。
#
# 明确不做的事情:
# 1. 不自动进入Understanding，不形成无限循环；
# 2. 不运行Action开发登记编排，不写sql/action.db；
# 3. 不替代任何单功能脚本或子编排的业务逻辑；
# 4. 不在缺少显式授权时写数据库、Neo4j、Chroma或调用模型API。
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: data_action_chain_pipeline
# family: data_action_chain_pipeline
# role: data_action_data_cycle_orchestrator
# version: v0001
# status: active
# entry_point: scripts/orchestration/action/data_action_chain_pipeline_v0001.py
# input:
#   - one data_raw target
#   - data-layer SQLite anchor table
#   - Action data SQLite database and writer configuration
# output:
#   - completed child orchestration manifests
#   - completed Action lineage record
#   - Action return manifest ready_for_data_discovery
# depends_on:
#   - scripts/orchestration/data/data_discovery_parse_preparation_pipeline_v0001.py
#   - scripts/orchestration/data/data_admission_lineage_pipeline_v0002.py
#   - scripts/orchestration/action/structural_unit_governance_graph_pipeline_v0002.py
#   - scripts/orchestration/action/action_anchor_persistence_pipeline_v0002.py
#   - scripts/orchestration/action/action_derivation_materialization_pipeline_v0002.py
#   - scripts/orchestration/action/vector_embedding_pipeline_v0001.py
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


SCRIPT_NAME = "data_action_chain_pipeline_v0001.py"
SCRIPT_FAMILY = "data_action_chain_pipeline"
SCRIPT_VERSION = "v0001"
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
DEFAULT_STEP_TIMEOUT_SECONDS = 1800
PINNED_SCRIPTS: Mapping[str, str] = {
    "discovery": "scripts/orchestration/data/data_discovery_parse_preparation_pipeline_v0001.py",
    "admission": "scripts/orchestration/data/data_admission_lineage_pipeline_v0002.py",
    "structural": "scripts/orchestration/action/structural_unit_governance_graph_pipeline_v0002.py",
    "anchor": "scripts/orchestration/action/action_anchor_persistence_pipeline_v0002.py",
    "derivation": "scripts/orchestration/action/action_derivation_materialization_pipeline_v0002.py",
    "vector": "scripts/orchestration/action/vector_embedding_pipeline_v0001.py",
    "lineage": "scripts/action/infrastructures/register_action_lineage_v0002.py",
}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
VERSION_PATTERN = re.compile(r"^v\d{4}$")


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

    def run_step(self, name: str, script_key: str, arguments: Sequence[str]) -> Dict[str, Any]:
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
    }
    for key in ("data_db", "action_db", "sql_writer_config", "duckdb_config", "embed_config"):
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
    if args.test_mode:
        temp_root = (project_root / "temp").resolve()
        for key, path in paths.items():
            if key in {"duckdb_config", "embed_config"}:
                continue
            require_within(path, temp_root, label=f"test {key}")
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
        admission_completion = completion_path_from_result(admission, output_root=paths["output_root"], family="data_admission_lineage_pipeline", version="v0002", run_id=admission_run_id)
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
        anchor_completion = completion_path_from_result(anchor, output_root=paths["output_root"], family="action_anchor_persistence_pipeline", version="v0002", run_id=anchor_run_id)
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
        derivation_completion = completion_path_from_result(derivation, output_root=paths["output_root"], family="action_derivation_materialization_pipeline", version="v0002", run_id=derivation_run_id)
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
            vector_completion = completion_path_from_result(vector, output_root=paths["output_root"], family="vector_embedding_pipeline", version="v0001", run_id=vector_run_id)
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute_pipeline(args)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
