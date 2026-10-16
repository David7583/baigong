# ============================================================
# File: capability_repository_v0001.py
# 中文名: 独立能力资产收集与 SQL 目录
# Version: v0001
# Main Layer: action
# Layer: infrastructures
# Responsibilities: 保存独立能力资产、来源、校验与显式分配。
# Does: 按 JSON 请求收集、查询、启用、分配和解析能力。
# Does not: 执行第三方代码、安装依赖、调用模型或修改业务库。
# Boundary: 原始来源只读；能力副本按哈希保存；凭证只存环境变量名。
# Updatable: True
# ============================================================
# ALIAS_META
# alias: capability_repository_v0001
# family: capability_repository
# role: capability_catalog
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/capability_repository_v0001.py
# input:
#   - capability.repository.v1 JSON request
# output:
#   - capability.repository.v1 JSON result, assets and independent SQL catalog
# depends_on:
#   - Python standard library
# used_by:
#   - workbench_capabilities_v0001
#   - capability_invoker_v0001
# ============================================================
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_NAME = 'capability_repository_v0001'
SCRIPT_FAMILY = 'capability_repository'
SCRIPT_VERSION = 'v0001'
DEFAULT_ENCODING = 'utf-8'
SCHEMA = 'capability.repository.v1'
KINDS = {'skill', 'github', 'library', 'mcp', 'json_cli'}
MAX_BYTES = 100 * 1024 * 1024
EXCLUDED = {'.git', 'node_modules', '.venv', '__pycache__'}


class CatalogError(Exception):
    pass


def require(condition, code):
    if not condition:
        raise CatalogError(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(file):
    value = hashlib.sha256()
    with file.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def files(source):
    require(not source.is_symlink() and source.exists(), 'SOURCE_MISSING_OR_LINKED')
    paths = [source] if source.is_file() else sorted(source.rglob('*'))
    result, total = [], 0
    for file in paths:
        rel = Path(file.name) if source.is_file() else file.relative_to(source)
        if any(part in EXCLUDED for part in rel.parts):
            continue
        require(not file.is_symlink() and not file.is_junction(), 'SOURCE_LINKED')
        if not file.is_file():
            continue
        require(file.name not in {'.env', 'credentials.json'} and file.suffix.lower() not in {'.pem', '.key'}, 'SECRET_FILE_REFUSED')
        total += file.stat().st_size
        require(total <= MAX_BYTES and len(result) < 10000, 'ASSET_BUDGET')
        result.append((file, rel.as_posix(), digest(file)))
    require(result, 'EMPTY_ASSET')
    return result


def fingerprint(entries):
    return hashlib.sha256(json.dumps([(r, h) for _, r, h in entries], separators=(',', ':')).encode()).hexdigest()


def validate_descriptor(kind, value):
    require(isinstance(value, dict), 'DESCRIPTOR_INVALID')
    allowed = {'transport', 'command', 'args', 'url', 'token_env', 'env_names', 'entry'}
    require(set(value) <= allowed, 'DESCRIPTOR_UNKNOWN_OR_SECRET_FIELD')
    result = dict(value)
    if kind == 'mcp':
        require(value.get('transport') in {'stdio', 'http'}, 'MCP_TRANSPORT')
        if value['transport'] == 'http':
            url = urllib.parse.urlsplit(value.get('url', ''))
            require(url.scheme in {'http', 'https'} and url.hostname and not url.username and not url.password and not url.fragment and not url.query, 'MCP_URL')
            require(url.scheme == 'https' or url.hostname in {'localhost', '127.0.0.1', '::1'}, 'MCP_HTTP_LOCAL_ONLY')
        else:
            require(isinstance(value.get('command'), str) and value['command'], 'MCP_COMMAND')
            command = shutil.which(value['command']) or value['command']
            command = Path(command).resolve()
            require(command.is_file() and command.suffix.lower() not in {'.cmd', '.bat', '.ps1'}, 'MCP_EXECUTABLE')
            result['command'] = str(command)
        require(isinstance(value.get('args', []), list) and len(value.get('args', [])) <= 40 and all(isinstance(x, str) and len(x) < 4096 for x in value.get('args', [])), 'MCP_ARGS')
        for key in ['token_env']:
            require(not value.get(key) or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,100}', value[key]), 'ENV_NAME')
        require(isinstance(value.get('env_names', []), list) and len(value.get('env_names', [])) <= 20 and all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,100}', x) for x in value.get('env_names', [])), 'ENV_NAMES')
    if kind == 'json_cli':
        entry = Path(value.get('entry', ''))
        require(value.get('entry') and not entry.is_absolute() and '..' not in entry.parts and entry.suffix == '.py', 'CLI_ENTRY')
    return result


class Repository:
    def __init__(self, root):
        self.root = root
        self.base = root / 'capabilities'
        self.db_file = self.base / 'catalog.sqlite3'

    def connect(self, write=False):
        if not self.db_file.exists() and not write:
            return None
        if write:
            self.base.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.db_file) if write else self.db_file.as_uri() + '?mode=ro', uri=not write, timeout=10)
        db.row_factory = sqlite3.Row
        if write:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS capabilities(id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                    description TEXT NOT NULL, version TEXT NOT NULL, source TEXT NOT NULL, license TEXT NOT NULL,
                    asset_path TEXT, bundle_hash TEXT, descriptor TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS grants(principal TEXT NOT NULL, capability_id TEXT NOT NULL, PRIMARY KEY(principal, capability_id));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, operation TEXT NOT NULL, capability_id TEXT, details TEXT NOT NULL);
            ''')
        return db

    def event(self, db, operation, id, details):
        db.execute('INSERT INTO events(at,operation,capability_id,details) VALUES(?,?,?,?)', (now(), operation, id, json.dumps(details)))

    def list(self, principal=None):
        db = self.connect()
        if not db:
            return {'items': [], 'directory': str(self.base), 'initialized': False}
        try:
            rows = db.execute('SELECT c.*,COALESCE(s.enabled,0) enabled FROM capabilities c LEFT JOIN state s ON s.id=c.id ORDER BY c.created_at DESC').fetchall()
            permitted = {r[0] for r in db.execute('SELECT capability_id FROM grants WHERE principal=?', (principal,))} if principal else None
            result = []
            for row in rows:
                item = dict(row)
                if permitted is not None and (not item['enabled'] or item['id'] not in permitted):
                    continue
                item['enabled'] = bool(item['enabled'])
                item['descriptor'] = json.loads(item['descriptor'])
                item['available'] = not item['asset_path'] or (self.base / item['asset_path']).is_dir()
                item['principals'] = [r[0] for r in db.execute('SELECT principal FROM grants WHERE capability_id=?', (item['id'],))]
                result.append(item)
            return {'items': result, 'directory': str(self.base), 'initialized': True}
        finally:
            db.close()

    def collect(self, data):
        kind = data.get('kind')
        require(kind in KINDS, 'CAPABILITY_KIND')
        require(isinstance(data.get('name'), str) and 0 < len(data['name'].strip()) <= 120, 'CAPABILITY_NAME')
        descriptor = validate_descriptor(kind, data.get('descriptor', {}))
        source_text = str(data.get('source', ''))[:2048]
        if source_text.startswith(('http://', 'https://')):
            source_url = urllib.parse.urlsplit(source_text)
            require(not source_url.username and not source_url.password and not source_url.query, 'SOURCE_URL_CREDENTIALS')
        source = Path(data['path']).resolve() if data.get('path') else None
        require(kind not in {'skill', 'json_cli'} or source, 'ASSET_REQUIRED')
        entries = files(source) if source else []
        if kind == 'skill':
            require(any(r == 'SKILL.md' for _, r, _ in entries), 'SKILL_ROOT_REQUIRED')
        if kind == 'json_cli':
            require(any(r == descriptor['entry'] for _, r, _ in entries), 'CLI_ENTRY_MISSING')
        bundle = fingerprint(entries) if entries else None
        id = str(uuid.uuid4())
        asset = 'assets/' + id if entries else None
        db = self.connect(True)
        target = self.base / asset if asset else None
        try:
            existing = db.execute('SELECT id FROM capabilities WHERE kind=? AND name=? AND version=? AND source=? AND bundle_hash IS ? AND descriptor=?',
                (kind, data['name'].strip(), str(data.get('version', 'unversioned'))[:100], source_text or str(source or ''), bundle, json.dumps(descriptor, sort_keys=True))).fetchone()
            if existing:
                return {'id': existing['id'], 'existing': True}
            if target:
                target.mkdir(parents=True)
                for file, relative, sha in entries:
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, destination)
                    require(digest(destination) == sha, 'SOURCE_CHANGED_DURING_COPY')
            db.execute('INSERT INTO capabilities VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (id, kind, data['name'].strip(), str(data.get('description', ''))[:1000], str(data.get('version', 'unversioned'))[:100], source_text or str(source or ''), str(data.get('license', 'unknown'))[:200], asset, bundle, json.dumps(descriptor, sort_keys=True), now()))
            db.execute('INSERT INTO state VALUES(?,0)', (id,))
            self.event(db, 'collect', id, {'kind': kind, 'bundle_hash': bundle})
            db.commit()
            return {'id': id, 'collected': True, 'enabled': False, 'bundle_hash': bundle}
        except Exception:
            db.rollback()
            if target and target.exists():
                shutil.rmtree(target)
            raise
        finally:
            db.close()

    def download(self, data):
        url = urllib.parse.urlsplit(data.get('url', ''))
        parts = url.path.strip('/').split('/')
        require(url.scheme == 'https' and url.hostname == 'github.com' and not url.query and not url.fragment and not url.username and len(parts) == 2 and all(re.fullmatch(r'[\w.-]+', p) for p in parts), 'GITHUB_URL')
        ref = str(data.get('ref', '')).strip()
        require(ref and len(ref) <= 150 and re.fullmatch(r'[A-Za-z0-9_./-]+', ref), 'GITHUB_REF_REQUIRED')
        self.base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.download-', dir=self.base) as folder:
            folder = Path(folder)
            request = urllib.request.Request('https://codeload.github.com/' + '/'.join(parts) + '/zip/' + urllib.parse.quote(ref, safe=''), headers={'User-Agent': SCRIPT_NAME})
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read(MAX_BYTES + 1)
            require(len(content) <= MAX_BYTES, 'DOWNLOAD_BUDGET')
            archive = folder / 'archive.zip'
            archive.write_bytes(content)
            extracted = folder / 'extracted'
            total = 0
            with zipfile.ZipFile(archive) as zip:
                require(len(zip.infolist()) <= 10000, 'ARCHIVE_FILES')
                for item in zip.infolist():
                    relative = Path(item.filename.replace('\\', '/'))
                    require(not relative.is_absolute() and '..' not in relative.parts and ':' not in item.filename and not item.filename.startswith('/') and ((item.external_attr >> 16) & 0o170000) != 0o120000, 'ARCHIVE_PATH')
                    total += item.file_size
                    require(total <= MAX_BYTES, 'ARCHIVE_BUDGET')
                zip.extractall(extracted)
            folders = list(extracted.iterdir())
            require(len(folders) == 1 and folders[0].is_dir(), 'GITHUB_ARCHIVE_ROOT')
            return self.collect({**data, 'kind': 'github', 'path': str(folders[0]), 'source': data['url'], 'name': data.get('name') or parts[1], 'version': ref})

    def dispatch(self, operation, data):
        if operation == 'list':
            return self.list(data.get('principal'))
        if operation == 'collect':
            return self.collect(data)
        if operation == 'download':
            return self.download(data)
        if operation in {'enable', 'grant'}:
            db = self.connect(True)
            try:
                id = data.get('id')
                require(db.execute('SELECT 1 FROM capabilities WHERE id=?', (id,)).fetchone(), 'CAPABILITY_MISSING')
                if operation == 'enable':
                    require(isinstance(data.get('enabled'), bool), 'ENABLED_BOOLEAN_REQUIRED')
                    db.execute('UPDATE state SET enabled=? WHERE id=?', (int(data['enabled']), id))
                else:
                    principal = data.get('principal', '')
                    require(re.fullmatch(r'(agent|project):[A-Za-z0-9_.-]{1,100}|system', principal), 'PRINCIPAL_INVALID')
                    require(isinstance(data.get('allow'), bool), 'ALLOW_BOOLEAN_REQUIRED')
                    if data['allow']:
                        db.execute('INSERT OR IGNORE INTO grants VALUES(?,?)', (principal, id))
                    else:
                        db.execute('DELETE FROM grants WHERE principal=? AND capability_id=?', (principal, id))
                self.event(db, operation, id, {k: data[k] for k in ['enabled', 'principal', 'allow'] if k in data})
                db.commit()
                return {'id': id, 'updated': True}
            finally:
                db.close()
        if operation == 'resolve':
            values = self.list(data.get('principal') if data.get('principal') != 'user' else None)['items']
            item = next((x for x in values if x['id'] == data.get('id')), None)
            require(item and item['enabled'], 'CAPABILITY_DISABLED_OR_UNASSIGNED')
            require(item['available'], 'CAPABILITY_ASSET_MISSING')
            if item['asset_path']:
                asset = (self.base / item['asset_path']).resolve()
                require(asset.is_relative_to((self.base / 'assets').resolve()), 'ASSET_OUTSIDE_REPOSITORY')
                require(fingerprint(files(asset)) == item['bundle_hash'], 'ASSET_HASH_CHANGED')
                item['resolved_path'] = str(asset)
                if item['kind'] == 'skill':
                    document = asset / 'SKILL.md'
                    require(document.stat().st_size <= 262144, 'SKILL_CONTENT_BUDGET')
                    item['content'] = document.read_text(DEFAULT_ENCODING)
                    item['path'] = str(asset)
            return item
        raise CatalogError('OPERATION_UNKNOWN')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--response', required=True)
    args = parser.parse_args()
    raw = Path(args.request).read_bytes()
    request = json.loads(raw)
    run_id = request.get('run_id')
    response = Path(args.response)
    try:
        require(len(raw) <= 262144 and request.get('schema_version') == SCHEMA and isinstance(run_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run_id), 'CONTRACT_INVALID')
        root = Path(args.root).resolve()
        require(request.get('root') == str(root) and request.get('producer', {}).get('script') in {'workbench_capabilities_v0001', 'capability_invoker_v0001', 'standalone'}, 'CONTRACT_IDENTITY')
        result = Repository(root).dispatch(request['operation'], request.get('input', {}))
        output = {'schema_version': SCHEMA, 'run_id': run_id, 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION}, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'status': 'completed', 'result': result}
        code = 0
    except Exception as error:
        output = {'schema_version': SCHEMA, 'run_id': run_id, 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION}, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'status': 'failed', 'error': str(error) if isinstance(error, CatalogError) else type(error).__name__}
        code = 2
    response.parent.mkdir(parents=True, exist_ok=True)
    temporary = response.with_suffix('.tmp')
    temporary.write_text(json.dumps(output, ensure_ascii=False), encoding=DEFAULT_ENCODING)
    temporary.replace(response)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
