#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: data_action_chain_pipeline_v0004.py
# 中文名: Data-Action-Data半循环总编排脚本
# Version: v0004
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 在v0003数据锚点基础上，支持temp隔离路径中的显式真实模型验收。
# Scope: 单次Data -> Action -> Data半循环，终点为ready_for_data_discovery。
#
# 职责说明:
# 1. 复用v0001总编排的可靠串联与v0002向量全局门禁。
# 2. 从结构治理完成结果中取得language_parse_lite的text_units JSONL。
# 3. 在Action锚点验证前初始化data_text_units并增量写入文本单元。
# 4. 将数据层SQLite初始化、入库及其原始输出纳入总运行证据。
# 5. 测试模式调用真实模型时传递独立确认，不放宽任何temp路径边界。
#
# 明确不做的事情:
# 1. 不创建第二套解析逻辑，不修改language_parse_lite输出。
# 2. 不把Action SQLite配置误用为Data SQLite schema。
# 3. 不自动进入Understanding，不形成无限循环。
# 4. 不绕过数据库、向量写入和模型调用的显式授权。
# ============================================================

# ============================================================
# ALIAS_META
# alias: data_action_chain_pipeline
# family: data_action_chain_pipeline
# role: data_action_data_cycle_orchestrator
# version: v0004
# status: active
# entry_point: scripts/orchestration/action/data_action_chain_pipeline_v0004.py
# input: one data_raw target, an empty or existing Data SQLite path, and isolated Action store paths
# output: populated data_text_units, completed Action manifests, lineage and Action return manifest
# depends_on: scripts/orchestration/action/data_action_chain_pipeline_v0001.py, scripts/orchestration/action/vector_embedding_pipeline_v0003.py, scripts/tools/init_data_schema_v0001.py, scripts/anchor/ingest_data_text_units_v0001.py
# used_by: future total state machine, Data-Action release acceptance
# ============================================================

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping


SCRIPT_NAME = "data_action_chain_pipeline_v0004.py"
SCRIPT_FAMILY = "data_action_chain_pipeline"
SCRIPT_VERSION = "v0004"
DATA_TABLE = "data_text_units"


def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise RuntimeError(f"Cannot locate project root from: {start}")


def _load_v0001(project_root: Path):
    source = project_root / "scripts" / "orchestration" / "action" / "data_action_chain_pipeline_v0001.py"
    spec = importlib.util.spec_from_file_location("data_action_chain_pipeline_v0001_base", source)
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
    raw_business_dir = structural_result.get("business_run_dir")
    if not isinstance(raw_business_dir, str) or not raw_business_dir:
        raise RuntimeError("structural completion does not expose business_run_dir")
    language_dir = Path(raw_business_dir).resolve() / "03_language_parse"
    candidates = sorted(language_dir.glob("*_text_units.jsonl"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one language text_units JSONL, found {len(candidates)} in {language_dir}"
        )
    return candidates[0].resolve()


def main() -> int:
    project_root = _find_project_root(Path(__file__).resolve().parent)
    base = _load_v0001(project_root)
    original_completion_path = base.completion_path_from_result
    original_require_file = base.require_file

    if "--help" not in sys.argv and "-h" not in sys.argv:
        data_table = _argument_value(sys.argv, "--data-table")
        if data_table != DATA_TABLE:
            raise RuntimeError(
                f"v0004 requires --data-table {DATA_TABLE}; received {data_table}"
            )

    def require_file(path: Path, *, label: str) -> None:
        if label == "data_db" and not path.exists():
            return
        original_require_file(path, label=label)

    def completion_path_from_result(
        result: Mapping[str, Any], *, output_root: Path, family: str, version: str, run_id: str
    ) -> Path:
        effective_version = "v0003" if family == "vector_embedding_pipeline" else version
        return original_completion_path(
            result,
            output_root=output_root,
            family=family,
            version=effective_version,
            run_id=run_id,
        )

    base.SCRIPT_NAME = SCRIPT_NAME
    base.SCRIPT_VERSION = SCRIPT_VERSION
    base.PINNED_SCRIPTS["vector"] = (
        "scripts/orchestration/action/vector_embedding_pipeline_v0003.py"
    )
    base.PINNED_SCRIPTS["data_schema"] = "scripts/tools/init_data_schema_v0001.py"
    base.PINNED_SCRIPTS["data_ingest"] = "scripts/anchor/ingest_data_text_units_v0001.py"
    base.require_file = require_file
    base.completion_path_from_result = completion_path_from_result

    original_run_step = base.PipelineRun.run_step
    completed_vector_targets: set[str] = set()
    structural_language_output: Path | None = None

    def run_step(self, name: str, key: str, args: list[str]):
        nonlocal structural_language_output
        effective_args = list(args)

        if key == "vector" and "--target" in effective_args:
            target_index = effective_args.index("--target")
            target = effective_args[target_index + 1]
            if (
                "--test-mode" in effective_args
                and "--mock-api" not in effective_args
                and "--confirm-model-api" in sys.argv
            ):
                effective_args.append("--confirm-real-model-in-test-mode")
            if target == "instance" and "concept" in completed_vector_targets:
                effective_args.extend(["--integrity-target", "all"])
            result = original_run_step(self, name, key, effective_args)
            completed_vector_targets.add(target)
            return result

        if key == "structural":
            result = original_run_step(self, name, key, effective_args)
            structural_language_output = _language_output(result)
            return result

        if key == "anchor":
            if structural_language_output is None:
                raise RuntimeError("data anchor initialization requires completed structural language output")
            data_db = Path(_argument_value(effective_args, "--data-db")).resolve()
            data_table = _argument_value(effective_args, "--data-table")
            if data_table != DATA_TABLE:
                raise RuntimeError(f"anchor table must be {DATA_TABLE}")
            schema_result = original_run_step(
                self,
                "initialize_data_anchor_schema",
                "data_schema",
                ["--db-path", str(data_db), "--init"],
            )
            if schema_result.get("status") != "ok":
                raise RuntimeError("data anchor schema initialization did not return status=ok")
            ingest_result = original_run_step(
                self,
                "ingest_data_text_units",
                "data_ingest",
                [
                    "--inputs", str(structural_language_output),
                    "--db", str(data_db),
                    "--run-meta", str(self.run_dir / "data_text_units_ingest_run_meta.json"),
                ],
            )
            stats = ingest_result.get("stats") or {}
            input_records = int(stats.get("input_records", -1))
            inserted_records = int(stats.get("inserted_records", -1))
            skipped_duplicate = int(stats.get("skipped_duplicate", -1))
            invalid_records = int(stats.get("invalid_records", -1))
            if (
                ingest_result.get("status") != "ok"
                or input_records < 1
                or invalid_records != 0
                or inserted_records + skipped_duplicate != input_records
            ):
                raise RuntimeError(
                    "data text-unit ingestion failed conservation or validity checks"
                )

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
