# ============================================================
# File: coarse_slice_conversations_v0001.py
# 中文名: ChatGPT 对话数据粗切片脚本
# Version: v0001
# Layer: execution
# Main Layer: Data
# Updatable: True
#
# 职责说明:
# - 本脚本用于在 parse_workspace 的 task-dir 内，
#   将 ChatGPT conversations JSON 按 conversation 粒度进行粗切片。
# - 本脚本只做结构拆封与隔离，不进行任何语义理解、
#   内容清洗、顺序推断或 schema 修复。
#
# 明确不做的事情:
# - 不解析 message 语义
# - 不展平 mapping 结构
# - 不重建对话顺序
# - 不删除或修改任何原始字段
#
# 制度属性:
# - 本脚本为 execution 型脚本
# - 仅在人工触发下运行
# - 运行上下文限定在 task-dir 内
# - 生成的所有产物均可整体删除与回放
# ============================================================


# ============================================================
# ALIAS_META
# ============================================================
# alias: coarse_slice_conversations
# family: data_slice
# role: conversation_coarse_slicer
# version: v0001
# status: active
# entry_point: coarse_slice_conversations_v0001.py
# input: conversations JSON under parse_workspace task-dir
# output: conversation-level slices under task-dir
# depends_on: prepare_json_parse_task_v0001.py
# used_by: downstream parsers / analyzers
# ============================================================


import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Tuple


DEFAULT_OUT_SUBDIR = "slices"
DEFAULT_MODE = "jsonl"


def load_conversations(json_path: str) -> Iterable[Tuple[int, Dict[str, Any]]]:
    """
    Load conversations JSON using utf-8-sig to tolerate BOM.
    Root structure must be an array.
    """
    with open(json_path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Root JSON structure is not an array.")

    for idx, conv in enumerate(data):
        if not isinstance(conv, dict):
            raise ValueError(f"Conversation at index {idx} is not an object.")
        yield idx, conv


def build_meta(source_file: str, index: int, conversation: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build minimal meta information.
    """
    return {
        "source_file": os.path.basename(source_file),
        "conversation_index": index,
        "conversation_id": conversation.get("conversation_id"),
        "sliced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def run_coarse_slice(
    task_dir: str,
    input_file: str,
    out_subdir: str,
    mode: str,
    start_index: int,
    limit: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    abs_task_dir = os.path.abspath(task_dir)
    input_path = os.path.join(abs_task_dir, input_file)
    out_dir = os.path.join(abs_task_dir, out_subdir)

    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    ensure_dir(out_dir)

    written = 0
    source_name = os.path.basename(input_path)

    jsonl_fp = None
    if mode == "jsonl" and not dry_run:
        jsonl_path = os.path.join(out_dir, "conversations.jsonl")
        jsonl_fp = open(jsonl_path, "w", encoding="utf-8")

    try:
        for idx, conv in load_conversations(input_path):
            if idx < start_index:
                continue
            if limit is not None and written >= limit:
                break

            meta = build_meta(source_name, idx, conv)
            record = {
                "_meta": meta,
                "conversation": conv,
            }

            if dry_run:
                print(f"[DRY-RUN] would emit conversation index {idx}")
            else:
                if mode == "jsonl":
                    jsonl_fp.write(json.dumps(record, ensure_ascii=False) + "\n")
                    jsonl_fp.flush()
                else:
                    conv_id = conv.get("conversation_id")
                    conv_id_short = conv_id[:8] if isinstance(conv_id, str) else "unknown"
                    fname = f"conv_{idx:06d}_{conv_id_short}.json"
                    fpath = os.path.join(out_dir, fname)
                    tmp_path = fpath + ".tmp"

                    with open(tmp_path, "w", encoding="utf-8") as wf:
                        json.dump(record, wf, ensure_ascii=False, indent=2)
                    os.replace(tmp_path, fpath)

            written += 1

            if verbose:
                print(f"[OK] sliced conversation index {idx}")

    finally:
        if jsonl_fp:
            jsonl_fp.close()

    if not dry_run:
        manifest = {
            "source_file": source_name,
            "slice_count": written,
            "mode": mode,
            "sliced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sliced_by": "coarse_slice_conversations_v0001.py",
            "task_dir": abs_task_dir,
            "output_subdir": out_subdir,
        }
        manifest_path = os.path.join(out_dir, "slice_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                "task_dir": abs_task_dir,
                "input_file": input_file,
                "output_subdir": out_subdir,
                "mode": mode,
                "written": written,
                "dry_run": dry_run,
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Coarse slice ChatGPT conversations JSON into conversation-level units (task-dir scoped)."
    )
    parser.add_argument("--task-dir", required=True, help="Task directory under parse_workspace.")
    parser.add_argument("--input-file", required=True, help="Path to conversations JSON relative to task-dir.")
    parser.add_argument("--out-subdir", default=DEFAULT_OUT_SUBDIR, help="Output subdirectory under task-dir.")
    parser.add_argument("--mode", choices=["jsonl", "files"], default=DEFAULT_MODE, help="Output mode.")
    parser.add_argument("--start-index", type=int, default=0, help="Start slicing from conversation index.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of conversations to slice.")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without writing files.")
    parser.add_argument("--verbose", action="store_true", help="Verbose output.")

    args = parser.parse_args()

    run_coarse_slice(
        task_dir=args.task_dir,
        input_file=args.input_file,
        out_subdir=args.out_subdir,
        mode=args.mode,
        start_index=args.start_index,
        limit=args.limit,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
