# ============================================================
# 文件名: workbench_skill_consumer_v0001.py
# 中文名: 聊天 Agent 的本地 Skill 接取
# 版本号: v0001
#
# 主层级: action
# 层级: workbench / adapters / skills
# 脚本定位: 从工作台分配账本读取显式启用的 Agent 指令
#
# 职责说明:
# - 将 Skill 市场的分配接入真实聊天 Agent 请求并留下使用记录
#
# 本脚本做什么:
# - 只读已启用、已分配且未隐藏的 Skill，校验内容与目录
#
# 本脚本不做什么:
# - 不执行脚本，不授予工具权限，不自动下载或启用 Skill
#
# 制度边界声明:
# - 只读本机目录和账本，内容不超过明确预算；异常准确失败
# - 不读取 Agent 外接凭证，不把 Skill 正文写入普通日志
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: workbench_skill_consumer_v0001
# family: workbench_skill_consumer
# role: enabled_assigned_skill_instruction_consumer
# version: v0001
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_skill_consumer_v0001.py
# input:
#   - root, runtime Agent ID
# output:
#   - verified Skill instructions and provenance-only usage records
# depends_on:
#   - Python stdlib
# used_by:
#   - action_chat_providers_v0006
# ============================================================
from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "workbench_skill_consumer"
SCRIPT_NAME = "workbench_skill_consumer_v0001"
SCRIPT_VERSION = "v0001"


def assigned(root, agent_id):
    directory = Path(root).resolve() / "frontend/local_ai_workbench/.runtime"
    database = directory / "workbench.sqlite"
    if not database.is_file():
        return [], []
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        def read(key):
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else []
        agents, skills, hidden = read("studio_agents"), read("studio_skills"), set(read("studio_hidden"))
    finally:
        db.close()
    ids = {sid for agent in agents if agent["id"] not in hidden and (agent.get("runtime_agent") == agent_id or agent["id"] == agent_id)
           for sid in agent["skill_ids"]}
    instructions, records, total = [], [], 0
    for skill in skills:
        if skill["id"] not in ids or skill["id"] in hidden or not skill["enabled"]:
            continue
        file = (Path(skill["path"]) / "SKILL.md").resolve()
        if not file.is_relative_to((directory / "skill-market").resolve()) or not file.is_file() or file.stat().st_size > 262144:
            raise ValueError("assigned_skill_missing_or_outside_market")
        content = file.read_text(DEFAULT_ENCODING)
        digest = hashlib.sha256(content.encode(DEFAULT_ENCODING)).hexdigest()
        if digest != skill["sha256"]:
            raise ValueError("assigned_skill_modified_reimport_required")
        total += len(content)
        if total > 16000 or len(records) >= 8:
            raise ValueError("assigned_skill_context_budget_exceeded")
        instructions.append("Skill " + skill["name"] + "：\n" + content)
        records.append({"id": skill["id"], "name": skill["name"], "sha256": digest,
                        "bundle_sha256": skill.get("bundle_sha256"), "execution_mode": "instructions_only"})
    return instructions, records
