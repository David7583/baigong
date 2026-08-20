#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# File: action_anchor_persistence_pipeline_v0003.py
# 中文名: Action语义正文锚点持久化编排兼容脚本
# Version: v0003
# Layer: orchestration
# Main Layer: Action
# Updatable: True
# 功能说明: 让既有锚点可靠写入链接收显式选择的标准化正文单位。
#
# ALIAS_META
# alias: action_anchor_persistence_pipeline
# family: action_anchor_persistence_pipeline
# role: action_anchor_persistence_orchestrator
# version: v0003
# status: active
# entry_point: scripts/orchestration/action/action_anchor_persistence_pipeline_v0003.py
# input: structural completion manifest and selected normalized semantic units
# output: semantic concept and instance declarations, SQLite evidence, completion manifest
# depends_on: scripts/orchestration/action/action_anchor_persistence_pipeline_v0001.py
# used_by: data_action_chain_pipeline
# ============================================================

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


SCRIPT_NAME = "action_anchor_persistence_pipeline_v0003.py"
SCRIPT_VERSION = "v0003"
BASE_RELATIVE = Path("scripts") / "orchestration" / "action" / "action_anchor_persistence_pipeline_v0001.py"
DEFAULT_INSTANCE_CONFIG = (
    Path("config") / "action" / "config" / "ingest_instance_units_semantic_config_v0001.yml"
)


def find_project_root() -> Path:
    for candidate in [Path.cwd(), *Path(__file__).resolve().parents]:
        if (candidate / "AGENTS.md").is_file() and (candidate / "scripts").is_dir():
            return candidate.resolve()
    raise RuntimeError("unable to locate project root")


def load_base(project_root: Path) -> Any:
    path = (project_root / BASE_RELATIVE).resolve()
    if not path.is_file():
        raise RuntimeError(f"pinned base orchestrator is missing: {path}")
    spec = importlib.util.spec_from_file_location("action_anchor_pipeline_v0001_semantic_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load pinned base orchestrator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base(find_project_root())


def build_parser() -> Any:
    BASE.SCRIPT_NAME = SCRIPT_NAME
    BASE.SCRIPT_VERSION = SCRIPT_VERSION
    BASE.UPSTREAM_VERSION = "v0002"
    BASE.PINNED_SCRIPTS["instance"] = "ingest_instance_units_v0003.py"
    parser = BASE.build_parser()
    parser.description = "Persist selected semantic text units through the validated Action Anchor chain."
    parser.add_argument("--identity-input", required=True)
    parser.add_argument("--semantic-instance-config", default=str(DEFAULT_INSTANCE_CONFIG))
    return parser


def _rewrite_completion(result: dict[str, Any], args: Any, project_root: Path) -> None:
    identity_input = Path(args.identity_input).resolve()
    previous_input = result.get("input") if isinstance(result.get("input"), dict) else {}
    result["input"] = {
        "identity_source": "selected_normalized_semantic_text_units",
        "semantic_text_units": str(identity_input),
        "sha256": BASE.file_sha256(identity_input),
        "source_inputs": previous_input.get("source_inputs", []),
    }
    result["identity_source_contract"] = {
        "decision_gate_required": False,
        "stable_coordinates_required": True,
        "structural_prominence_is_not_used_as_semantic_eligibility": True,
    }
    output_root = BASE.resolve_path(args.output_root, project_root)
    completion_path = (
        output_root
        / BASE.SCRIPT_FAMILY
        / SCRIPT_VERSION
        / result["run_id"]
        / "completion_manifest.json"
    )
    BASE.write_json_atomic(completion_path, result)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = BASE.resolve_project_root(Path(__file__).resolve(), args.project_root)
    identity_input = BASE.resolve_path(args.identity_input, project_root)
    if not identity_input.is_file():
        print(f"ERROR identity input is missing: {identity_input}", file=sys.stderr)
        return 1
    instance_config = BASE.resolve_path(args.semantic_instance_config, project_root)
    if not instance_config.is_file():
        print(f"ERROR semantic instance config is missing: {instance_config}", file=sys.stderr)
        return 1
    if args.test_mode:
        BASE.require_within(identity_input, (project_root / "temp").resolve(), "test identity input")

    original_validate = BASE.validate_upstream_manifest

    def validate_upstream_manifest(path: Path, root: Path) -> dict[str, Any]:
        upstream = original_validate(path, root)
        upstream["structural_prominence_decisions"] = upstream["prominence_decisions"]
        upstream["prominence_decisions"] = str(identity_input)
        upstream["semantic_identity_input"] = str(identity_input)
        return upstream

    BASE.validate_upstream_manifest = validate_upstream_manifest
    original_write_sql_payload = BASE.write_sql_payload

    def write_sql_payload(run: Any, *, writer: Any, concepts: Any, instances: Any, attributes: Any, input_hash: str):
        coordinates = {
            str(row["instance_id"]): row["observed_at"]
            for row in instances
        }

        class CoordinateAwareWriter:
            def __getattr__(self, name: str) -> Any:
                return getattr(writer, name)

            def write_instance(self, **kwargs: Any) -> Any:
                observed = coordinates[kwargs["instance_id"]]
                kwargs["value_index"] = observed["value_index"]
                kwargs["sentence_index"] = observed["sentence_index"]
                return writer.write_instance(**kwargs)

        return original_write_sql_payload(
            run,
            writer=CoordinateAwareWriter(),
            concepts=concepts,
            instances=instances,
            attributes=attributes,
            input_hash=input_hash,
        )

    BASE.write_sql_payload = write_sql_payload
    BASE.PINNED_CONFIGS["instance"] = instance_config.relative_to(project_root)
    try:
        result = BASE.execute(args)
        if not args.dry_run and result.get("status") == "completed":
            _rewrite_completion(result, args, project_root)
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
