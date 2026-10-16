# ============================================================
# File: capability_invoker_v0001.py
# 中文名: 能力按需调用编排
# Version: v0001
# Main Layer: action
# Layer: infrastructures
# Responsibilities: 核验能力分配与内容后编排单次调用。
# Does: 通过 JSON 文件请求目录解析，再调度 Skill 读取、JSON CLI 或 MCP。
# Does not: 导入业务脚本、自动执行仓库或库、隐式授权、调用模型。
# Boundary: 仅启用且分配的能力；超时结果未明不自动重放；保留审计。
# Updatable: True
# ============================================================
# ALIAS_META
# alias: capability_invoker_v0001
# family: capability_invoker
# role: capability_orchestration
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/capability_invoker_v0001.py
# input:
#   - capability.invoke.v1 JSON request
# output:
#   - capability.invoke.v1 JSON result and invocation audit
# depends_on:
#   - capability_repository_v0001
#   - mcp_tool_client_v0001
# used_by:
#   - workbench_capabilities_v0001
# ============================================================
import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

SCRIPT_NAME = 'capability_invoker_v0001'
SCRIPT_FAMILY = 'capability_invoker'
SCRIPT_VERSION = 'v0001'
DEFAULT_ENCODING = 'utf-8'
SCHEMA = 'capability.invoke.v1'


class InvocationError(Exception):
    pass


def handoff(root, script, schema, operation, data, folder):
    run_id = str(uuid.uuid4())
    request = folder / (run_id + '.request.json')
    response = folder / (run_id + '.response.json')
    raw = json.dumps({'schema_version': schema, 'run_id': run_id, 'root': str(root), 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION}, 'operation': operation, 'input': data}, ensure_ascii=False).encode(DEFAULT_ENCODING)
    request.write_bytes(raw)
    try:
        run = subprocess.run([sys.executable, '-B', str(script), '--root', str(root), '--request', str(request), '--response', str(response)], timeout=80, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise InvocationError('EXECUTION_TIMEOUT_OUTCOME_UNKNOWN') from None
    if not response.exists() or response.stat().st_size > 5 * 1024 * 1024:
        raise InvocationError('HANDOFF_RESPONSE_MISSING_OR_OVERSIZE')
    value = json.loads(response.read_text(DEFAULT_ENCODING))
    expected_version = script.stem.rsplit('_', 1)[-1]
    if not re.fullmatch(r'v\d{4}', expected_version):
        expected_version = 'v0001'
    if value.get('schema_version') != schema or value.get('run_id') != run_id or value.get('source_sha256') != hashlib.sha256(raw).hexdigest() or value.get('producer', {}).get('script') != script.stem or value.get('producer', {}).get('version') != expected_version:
        raise InvocationError('HANDOFF_RESPONSE_IDENTITY')
    if run.returncode or value.get('status') != 'completed':
        raise InvocationError(value.get('error', 'HANDOFF_FAILED'))
    return value['result']


def invoke(root, data, folder):
    principal = data.get('principal', 'user')
    if not re.fullmatch(r'user|system|(agent|project):[A-Za-z0-9_.-]{1,100}', principal):
        raise InvocationError('PRINCIPAL_INVALID')
    scripts = Path(__file__).parent
    item = handoff(root, scripts / 'capability_repository_v0001.py', 'capability.repository.v1', 'resolve', {'id': data.get('id'), 'principal': principal}, folder)
    if item['kind'] == 'skill':
        if data.get('operation') != 'read':
            raise InvocationError('SKILL_READ_ONLY')
        return item
    if item['kind'] in {'github', 'library'}:
        if data.get('operation') != 'reference':
            raise InvocationError('REFERENCE_NOT_EXECUTABLE')
        return {'kind': item['kind'], 'id': item['id'], 'path': item.get('resolved_path'), 'source': item['source'], 'version': item['version'], 'bundle_hash': item['bundle_hash']}
    if item['kind'] == 'mcp':
        if data.get('operation') not in {'discover', 'call'}:
            raise InvocationError('MCP_OPERATION')
        if data.get('operation') == 'call' and data.get('confirm_execute') is not True:
            raise InvocationError('MCP_EXECUTION_CONFIRMATION')
        return handoff(root, scripts / 'mcp_tool_client_v0001.py', 'capability.mcp.v1', data['operation'], {**data, 'descriptor': item['descriptor']}, folder)
    if item['kind'] == 'json_cli':
        if data.get('operation') != 'call' or data.get('confirm_execute') is not True:
            raise InvocationError('CLI_EXECUTION_CONFIRMATION')
        entry = (Path(item['resolved_path']) / item['descriptor']['entry']).resolve()
        if not entry.is_relative_to(Path(item['resolved_path'])):
            raise InvocationError('CLI_ENTRY_OUTSIDE_ASSET')
        # A JSON CLI must implement the same strict handoff identity contract.
        return handoff(root, entry, 'capability.tool.v1', 'call', {'arguments': data.get('arguments', {}), 'capability_id': item['id'], 'principal': principal, 'project_context': data.get('project_context')}, folder)
    raise InvocationError('CAPABILITY_KIND_UNSUPPORTED')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--response', required=True)
    args = parser.parse_args()
    raw = Path(args.request).read_bytes()
    request = json.loads(raw)
    root = Path(args.root).resolve()
    run_id = request.get('run_id')
    try:
        if len(raw) > 262144 or request.get('schema_version') != SCHEMA or request.get('root') != str(root) or request.get('producer', {}).get('script') not in {'standalone', 'workbench_capabilities_v0001'} or not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run_id):
            raise InvocationError('CONTRACT_IDENTITY')
        folder = Path(args.request).resolve().parent
        output = {'status': 'completed', 'result': invoke(root, request.get('input', {}), folder)}
        code = 0
    except Exception as error:
        output = {'status': 'failed', 'error': str(error) if isinstance(error, InvocationError) else type(error).__name__}
        code = 2
    output.update({'schema_version': SCHEMA, 'run_id': run_id, 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION}, 'source_sha256': hashlib.sha256(raw).hexdigest()})
    response = Path(args.response)
    response.parent.mkdir(parents=True, exist_ok=True)
    temporary = response.with_suffix('.tmp')
    temporary.write_text(json.dumps(output, ensure_ascii=False), encoding=DEFAULT_ENCODING)
    temporary.replace(response)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
