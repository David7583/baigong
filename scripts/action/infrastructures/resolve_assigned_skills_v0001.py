# ============================================================
# 文件名: resolve_assigned_skills_v0001.py
# 中文名: 已分配 Skill 指令校验
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / skill_contract
# 脚本定位: 独立 JSON 文件任务入口
# 职责说明:
# - 按运行身份解析已启用 Skill 的指令和来源
# 本脚本做什么:
# - 校验分配快照、文件归属、内容哈希和上下文预算
# 本脚本不做什么:
# - 不执行 Skill，不调用模型，不读取工作台数据库或凭证
# 制度边界声明:
# - 输入只读，输出限自身任务目录，失败不生成完成结果
# 可更新: True
# ============================================================
# ALIAS_META
# alias: resolve_assigned_skills_v0001
# family: resolve_assigned_skills
# role: assigned_skill_instruction_resolver
# version: v0001
# status: staging
# entry_point: scripts/action/infrastructures/resolve_assigned_skills_v0001.py
# input:
#   - skill-resolution.v1 JSON request with source snapshot SHA256
# output:
#   - atomic verified instructions and provenance JSON response
# depends_on:
#   - Python stdlib
# used_by:
#   - action_chat_providers_v0006
# ============================================================
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'resolve_assigned_skills'
SCRIPT_NAME = 'resolve_assigned_skills_v0001'
SCRIPT_VERSION = 'v0001'


def no_links(path, root):
    if not path.is_relative_to(root):
        raise ValueError('skill_path_outside_root')
    for part in (path, *path.parents):
        if part.is_symlink() or part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('skill_link_forbidden')
        if part == root:
            break


def resolve(request):
    if request.get('schema_version') != 'skill-resolution.v1' or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', request.get('run_id', '')):
        raise ValueError('skill_request_invalid')
    root = Path(request['root']).resolve(strict=True)
    if request.get('agent_id') not in {'codex', 'deepseek_harness'}:
        raise ValueError('skill_runtime_identity_invalid')
    file = root / 'runtime/skill_assignments_v0001.json'
    if not file.exists():
        if request.get('source_sha256') is not None:
            raise ValueError('skill_assignment_source_missing')
        return {'instructions': [], 'records': [], 'source_sha256': None}
    no_links(file, root)
    if file.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('skill_assignment_too_large')
    raw = file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != request.get('source_sha256'):
        raise ValueError('skill_assignment_changed_retry')
    value = json.loads(raw)
    if value.get('schema_version') != 'skill-assignments.v1' or value.get('producer') != {'script': 'workbench_store_v0001', 'version': 'v0001'}:
        raise ValueError('skill_assignment_contract_invalid')
    ids = {sid for agent in value['agents'] if agent.get('runtime_agent') == request['agent_id'] or agent['id'] == request['agent_id'] for sid in agent['skill_ids']}
    instructions, records, total = [], [], 0
    for skill in value['skills']:
        if skill['id'] not in ids or not skill['enabled']:
            continue
        file = Path(skill['path']) / 'SKILL.md'
        no_links(file.absolute(), root)
        file = file.resolve(strict=True)
        # The market directory is a resource binding, not an imported UI module.
        market = Path(value['market_root']).absolute()
        no_links(market, root)
        if not file.is_relative_to(market) or not file.is_file() or file.stat().st_size > 262144:
            raise ValueError('assigned_skill_missing_or_outside_market')
        content = file.read_text(DEFAULT_ENCODING)
        sha = hashlib.sha256(content.encode(DEFAULT_ENCODING)).hexdigest()
        if sha != skill['sha256']:
            raise ValueError('assigned_skill_modified_reimport_required')
        total += len(content)
        if total > 16000 or len(records) >= 8:
            raise ValueError('assigned_skill_context_budget_exceeded')
        instructions.append('Skill ' + skill['name'] + '：\n' + content)
        records.append({'id': skill['id'], 'name': skill['name'], 'sha256': sha, 'bundle_sha256': skill.get('bundle_sha256'), 'execution_mode': 'instructions_only'})
    return {'instructions': instructions, 'records': records, 'source_sha256': digest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--response', required=True)
    args = parser.parse_args()
    try:
        request_path, response = Path(args.request).absolute(), Path(args.response).absolute()
        request = json.loads(request_path.read_text(DEFAULT_ENCODING))
        root = Path(request['root']).resolve(strict=True)
        owner = root / 'runtime/intermediate_tasks' / request['run_id']
        no_links(request_path, root); no_links(response, root)
        if request_path.parent != owner or response.parent != owner or request_path == response:
            raise ValueError('skill_task_ownership_invalid')
        result = resolve(request)
        payload = {'schema_version': 'skill-resolution.v1', 'run_id': request['run_id'], 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION}, 'status': 'completed', 'result': result}
        temporary = response.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False), DEFAULT_ENCODING)
        os.replace(temporary, response)
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__, 'detail': str(exc) if isinstance(exc, ValueError) else 'skill_resolution_failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
