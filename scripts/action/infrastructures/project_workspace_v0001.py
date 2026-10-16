# ============================================================
# File: project_workspace_v0001.py
# 中文名: 独立项目上下文与资源绑定
# Version: v0001
# Main Layer: action
# Layer: infrastructures
# Responsibilities: 保存项目目录、上下文与能力引用。
# Does: 通过 JSON 请求创建、编辑、查询和归档项目。
# Does not: 移动项目资料、执行能力、修改原始业务库或调用 AI。
# Boundary: 独立 projects.sqlite3；保存引用，不接管项目文件。
# Updatable: True
# ============================================================
# ALIAS_META
# alias: project_workspace_v0001
# family: project_workspace
# role: project_context
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/project_workspace_v0001.py
# input:
#   - project.workspace.v1 JSON request
# output:
#   - project.workspace.v1 JSON result and independent project records
# depends_on:
#   - Python standard library
# used_by:
#   - workbench_capabilities_v0001
# ============================================================
import argparse
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_NAME = 'project_workspace_v0001'
SCRIPT_FAMILY = 'project_workspace'
SCRIPT_VERSION = 'v0001'
DEFAULT_ENCODING = 'utf-8'
SCHEMA = 'project.workspace.v1'


class ProjectError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise ProjectError(code)


def dispatch(root, operation, data):
    target = root / 'projects/projects.sqlite3'
    if operation == 'list' and not target.exists():
        return {'items': [], 'initialized': False}
    require(operation in {'list', 'save', 'archive'}, 'PROJECT_OPERATION')
    if operation != 'list':
        target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(target) if operation != 'list' else target.as_uri() + '?mode=ro', uri=operation == 'list', timeout=10)
    db.row_factory = sqlite3.Row
    try:
        if operation != 'list':
            db.executescript('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS revisions(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL,at TEXT NOT NULL,value TEXT NOT NULL);')
        if operation == 'list':
            return {'items': [json.loads(x['value']) for x in db.execute('SELECT value FROM projects ORDER BY rowid DESC')], 'initialized': True}
        id = data.get('id') or str(uuid.uuid4())
        require(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', id), 'PROJECT_ID')
        old = db.execute('SELECT value FROM projects WHERE id=?', (id,)).fetchone()
        previous = json.loads(old['value']) if old else {}
        if operation == 'archive':
            require(old, 'PROJECT_MISSING')
            value = {**previous, 'archived': data.get('archived') is not False}
        else:
            require(isinstance(data.get('name'), str) and 0 < len(data['name'].strip()) <= 120, 'PROJECT_NAME')
            folder = data.get('path', '')
            require(isinstance(folder, str) and len(folder) <= 2048, 'PROJECT_PATH')
            if folder:
                require(Path(folder).is_dir(), 'PROJECT_DIRECTORY_MISSING')
                folder = str(Path(folder).resolve())
            capability_ids = data.get('capability_ids', [])
            require(isinstance(capability_ids, list) and len(capability_ids) <= 300 and all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', x) for x in capability_ids), 'PROJECT_CAPABILITIES')
            agents = data.get('agent_ids', [])
            require(isinstance(agents, list) and len(agents) <= 100 and all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', x) for x in agents), 'PROJECT_AGENTS')
            value = {**previous, 'id': id, 'name': data['name'].strip(), 'path': folder, 'description': str(data.get('description', ''))[:1000], 'context': str(data.get('context', ''))[:20000], 'capability_ids': list(dict.fromkeys(capability_ids)), 'agent_ids': list(dict.fromkeys(agents)), 'archived': previous.get('archived', False), 'created_at': previous.get('created_at', datetime.now(timezone.utc).isoformat())}
        value['updated_at'] = datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO projects VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', (id, json.dumps(value, ensure_ascii=False)))
        db.execute('INSERT INTO revisions(id,at,value) VALUES(?,?,?)', (id, value['updated_at'], json.dumps(value, ensure_ascii=False)))
        db.commit()
        return value
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--response', required=True)
    args = parser.parse_args()
    raw = Path(args.request).read_bytes()
    request = json.loads(raw)
    run_id = request.get('run_id')
    try:
        require(len(raw) <= 262144 and request.get('schema_version') == SCHEMA and isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run_id), 'CONTRACT_INVALID')
        root = Path(args.root).resolve()
        require(request.get('root') == str(root) and request.get('producer', {}).get('script') in {'workbench_capabilities_v0001', 'standalone'}, 'CONTRACT_IDENTITY')
        result = dispatch(root, request['operation'], request.get('input', {}))
        output = {'status': 'completed', 'result': result}
        code = 0
    except Exception as error:
        output = {'status': 'failed', 'error': str(error) if isinstance(error, ProjectError) else type(error).__name__}
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
