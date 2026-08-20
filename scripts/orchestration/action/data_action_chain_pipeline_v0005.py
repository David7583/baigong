#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: data_action_chain_pipeline_v0005.py
# 中文名: Data-Action-Data语义正文向量总编排脚本
# Version: v0005
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 保留结构治理并将显式正文路径独立接入Action身份、BGE与Chroma链。
#
# ALIAS_META
# alias: data_action_chain_pipeline
# family: data_action_chain_pipeline
# role: data_action_data_cycle_orchestrator
# version: v0005
# status: active
# entry_point: scripts/orchestration/action/data_action_chain_pipeline_v0005.py
# input: one data_raw target, isolated stores, and explicit semantic JSON paths
# output: data facts, Action identities, derivations, vectors, lineage and return manifest
# depends_on: data_action_chain_pipeline_v0001, action_anchor_persistence_pipeline_v0003, action_derivation_materialization_pipeline_v0003, select_semantic_text_units_v0001, vector_embedding_pipeline_v0003
# used_by: future total state machine, Data-Action release acceptance
# ============================================================

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping


SCRIPT_NAME = "data_action_chain_pipeline_v0005.py"
SCRIPT_FAMILY = "data_action_chain_pipeline"
SCRIPT_VERSION = "v0005"
DATA_TABLE = "data_text_units"
SEMANTIC_INSTANCE_CONFIG = Path("config") / "action" / "config" / "ingest_instance_units_semantic_config_v0001.yml"


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise RuntimeError(f"Cannot locate project root from: {start}")


def _load_v0001(project_root: Path):
    source = project_root / "scripts" / "orchestration" / "action" / "data_action_chain_pipeline_v0001.py"
    spec = importlib.util.spec_from_file_location("data_action_chain_pipeline_v0001_semantic_base", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load compatibility base: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _argument_value(arguments: list[str], option: str) -> str:
    try:
        index = arguments.index(option)
        return arguments[index + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"Missing required orchestration argument: {option}") from exc


def _language_output(structural_result: Mapping[str, Any]) -> Path:
    business_dir = _business_dir(structural_result)
    candidates = sorted((business_dir / "03_language_parse").glob("*_text_units.jsonl"))
    if len(candidates) != 1:
        raise RuntimeError(f"expected exactly one language text_units JSONL, found {len(candidates)}")
    return candidates[0].resolve()


def _normalized_output(structural_result: Mapping[str, Any]) -> Path:
    candidate = _business_dir(structural_result) / "04_normalized" / "normalized_text_units.jsonl"
    if not candidate.is_file():
        raise RuntimeError(f"normalized text units are missing: {candidate}")
    return candidate.resolve()


def _business_dir(result: Mapping[str, Any]) -> Path:
    raw = result.get("business_run_dir")
    if not isinstance(raw, str) or not raw:
        raise RuntimeError("structural completion does not expose business_run_dir")
    return Path(raw).resolve()


def main() -> int:
    project_root = _find_project_root(Path(__file__).resolve().parent)
    base = _load_v0001(project_root)
    original_completion_path = base.completion_path_from_result
    original_require_file = base.require_file
    original_build_parser = base.build_parser

    def build_parser():
        parser = original_build_parser()
        parser.add_argument("--semantic-path", action="append", required=True)
        parser.add_argument("--semantic-limit", type=base.positive_integer, default=None)
        return parser

    base.build_parser = build_parser
    if "--help" not in sys.argv and "-h" not in sys.argv:
        data_table = _argument_value(sys.argv, "--data-table")
        if data_table != DATA_TABLE:
            raise RuntimeError(f"v0005 requires --data-table {DATA_TABLE}; received {data_table}")
        if "--semantic-limit" in sys.argv and "--test-mode" not in sys.argv:
            raise RuntimeError("--semantic-limit is permitted only with --test-mode")

    def require_file(path: Path, *, label: str) -> None:
        if label == "data_db" and not path.exists():
            return
        original_require_file(path, label=label)

    def completion_path_from_result(
        result: Mapping[str, Any], *, output_root: Path, family: str, version: str, run_id: str
    ) -> Path:
        effective_version = version
        if family == "vector_embedding_pipeline":
            effective_version = "v0003"
        elif family == "action_anchor_persistence_pipeline":
            effective_version = "v0003"
        elif family == "action_derivation_materialization_pipeline":
            effective_version = "v0003"
        return original_completion_path(
            result, output_root=output_root, family=family, version=effective_version, run_id=run_id
        )

    base.SCRIPT_NAME = SCRIPT_NAME
    base.SCRIPT_VERSION = SCRIPT_VERSION
    base.PINNED_SCRIPTS["anchor"] = "scripts/orchestration/action/action_anchor_persistence_pipeline_v0003.py"
    base.PINNED_SCRIPTS["derivation"] = "scripts/orchestration/action/action_derivation_materialization_pipeline_v0003.py"
    base.PINNED_SCRIPTS["vector"] = "scripts/orchestration/action/vector_embedding_pipeline_v0003.py"
    base.PINNED_SCRIPTS["data_schema"] = "scripts/tools/init_data_schema_v0001.py"
    base.PINNED_SCRIPTS["data_ingest"] = "scripts/anchor/ingest_data_text_units_v0001.py"
    base.PINNED_SCRIPTS["semantic_selector"] = "scripts/action/adapters/select_semantic_text_units_v0001.py"
    base.require_file = require_file
    base.completion_path_from_result = completion_path_from_result
    original_validate_args_and_plan = base.validate_args_and_plan

    def validate_args_and_plan(args):
        plan = original_validate_args_and_plan(args)
        plan["semantic_paths"] = list(dict.fromkeys(args.semantic_path))
        plan["semantic_limit"] = args.semantic_limit
        plan["semantic_limit_test_only"] = args.semantic_limit is not None
        return plan

    base.validate_args_and_plan = validate_args_and_plan

    original_run_step = base.PipelineRun.run_step
    completed_vector_targets: set[str] = set()
    structural_language_output: Path | None = None
    structural_normalized_output: Path | None = None

    def run_step(self, name: str, key: str, args: list[str]):
        nonlocal structural_language_output, structural_normalized_output
        effective_args = list(args)

        if key == "vector" and "--target" in effective_args:
            target = effective_args[effective_args.index("--target") + 1]
            if "--test-mode" in effective_args and "--mock-api" not in effective_args and "--confirm-model-api" in sys.argv:
                effective_args.append("--confirm-real-model-in-test-mode")
            if target == "instance" and "concept" in completed_vector_targets:
                effective_args.extend(["--integrity-target", "all"])
            result = original_run_step(self, name, key, effective_args)
            completed_vector_targets.add(target)
            return result

        if key == "structural":
            result = original_run_step(self, name, key, effective_args)
            structural_language_output = _language_output(result)
            structural_normalized_output = _normalized_output(result)
            return result

        if key == "anchor":
            if structural_language_output is None or structural_normalized_output is None:
                raise RuntimeError("semantic anchor requires completed language and normalization outputs")
            data_db = Path(_argument_value(effective_args, "--data-db")).resolve()
            data_table = _argument_value(effective_args, "--data-table")
            if data_table != DATA_TABLE:
                raise RuntimeError(f"anchor table must be {DATA_TABLE}")
            schema_result = original_run_step(self, "initialize_data_anchor_schema", "data_schema", ["--db-path", str(data_db), "--init"])
            if schema_result.get("status") != "ok":
                raise RuntimeError("data anchor schema initialization did not return status=ok")
            ingest_result = original_run_step(
                self,
                "ingest_data_text_units",
                "data_ingest",
                ["--inputs", str(structural_language_output), "--db", str(data_db), "--run-meta", str(self.run_dir / "data_text_units_ingest_run_meta.json")],
            )
            stats = ingest_result.get("stats") or {}
            if (
                ingest_result.get("status") != "ok"
                or int(stats.get("input_records", -1)) < 1
                or int(stats.get("invalid_records", -1)) != 0
                or int(stats.get("inserted_records", -1)) + int(stats.get("skipped_duplicate", -1)) != int(stats.get("input_records", -1))
            ):
                raise RuntimeError("data text-unit ingestion failed conservation or validity checks")

            semantic_dir = self.run_dir / "semantic_source"
            semantic_output = semantic_dir / "semantic_text_units.jsonl"
            selector_args = [
                "--input", str(structural_normalized_output),
                "--output", str(semantic_output),
                "--run-meta", str(semantic_dir / "run_meta.json"),
            ]
            semantic_paths = [sys.argv[index + 1] for index, value in enumerate(sys.argv[:-1]) if value == "--semantic-path"]
            for semantic_path in semantic_paths:
                selector_args.extend(["--include-path", semantic_path])
            if "--semantic-limit" in sys.argv:
                selector_args.extend(["--max-records", _argument_value(sys.argv, "--semantic-limit")])
            if "--test-mode" in sys.argv:
                selector_args.append("--test-mode")
            selector_result = original_run_step(self, "select_semantic_text_units", "semantic_selector", selector_args)
            selected = int((selector_result.get("stats") or {}).get("rows_selected", 0))
            if selector_result.get("status") != "completed" or selected < 1 or not semantic_output.is_file():
                raise RuntimeError("semantic text-unit selection produced no validated output")
            effective_args.extend([
                "--identity-input", str(semantic_output),
                "--semantic-instance-config", str(project_root / SEMANTIC_INSTANCE_CONFIG),
            ])

        return original_run_step(self, name, key, effective_args)

    base.PipelineRun.run_step = run_step
    return base.main()


def cli() -> int:
    try:
        return main()
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "pipeline": SCRIPT_FAMILY,
            "pipeline_version": SCRIPT_VERSION,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
