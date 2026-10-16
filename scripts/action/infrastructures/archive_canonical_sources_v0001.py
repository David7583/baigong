# ============================================================
# 文件名: archive_canonical_sources_v0001.py
# 中文名: 锚点规范化来源持久保存器
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / canonical_sources
# 脚本定位: 在正式发布前保存锚点恢复所需的唯一规范化原文
# 职责说明:
# - 将隔离准备来源保存到独立持久目录并核验哈希
# 本脚本做什么:
# - 读取人工/授权准备库的资产标识，保存对应规范化来源与出处
# 本脚本不做什么:
# - 不写事实库，不计算向量，不清理输入，不保存任意中间文件
# 制度边界声明:
# - 原始来源只读，已有同名不同内容拒绝；归档不属于清理产物
# 可更新: True
# ============================================================
# ============================================================
# ALIAS_META
# ============================================================
# alias: archive_canonical_sources_v0001
# family: archive_canonical_sources
# role: durable_anchor_canonical_source_preservation
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/archive_canonical_sources_v0001.py
# input:
#   - canonical-source-archive.v1 JSON request
# output:
#   - verified durable canonical sources and JSON response
# depends_on:
#   - Python stdlib
# used_by:
#   - data_action_chain_pipeline_v0021 JSON file handoff
# ============================================================
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
from pathlib import Path

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'archive_canonical_sources'
SCRIPT_NAME = 'archive_canonical_sources_v0001'
SCRIPT_VERSION = 'v0001'


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def stable_source_hash(path):
    """Ignore only the top-level timestamp in the gateway's small JSON header."""
    digest = hashlib.sha256()
    with path.open('rb') as source:
        header = source.read(65536)
        matches = []
        candidates = {match.start(): match.span(1) for match in re.finditer(rb'"canonicalized_at"\s*:\s*("[^"\\\r\n]*")', header)}
        depth, quoted, escaped = 0, False, False
        for index, value in enumerate(header):
            if index in candidates and depth == 1 and not quoted:
                matches.append(candidates[index])
            if quoted:
                if escaped: escaped = False
                elif value == 92: escaped = True
                elif value == 34: quoted = False
            elif value == 34: quoted = True
            elif value in (123, 91): depth += 1
            elif value in (125, 93): depth -= 1
        if len(matches) > 1:
            raise ValueError('duplicate_canonicalized_at_field')
        if matches:
            start, end = matches[0]
            header = header[:start] + b'"gateway_observational_timestamp"' + header[end:]
        digest.update(header)
        while chunk := source.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def checked(path, root):
    raw = Path(path).absolute()
    for part in [raw, *raw.parents]:
        if part == root:
            break
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('source_archive_link_forbidden')
    result = raw.resolve()
    if not result.is_relative_to(root):
        raise ValueError('source_archive_path_outside_root')
    return result


def archive(request):
    root = Path(request['root']).resolve()
    stage = checked(request['stage'], root)
    db_path = checked(stage / 'data.db', root)
    if sha(db_path) != request['source_db_sha256']:
        raise ValueError('prepared_source_database_changed')
    destination = checked(root / 'actioning/canonical_sources', root)
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as database:
        assets = [row[0] for row in database.execute('SELECT DISTINCT asset_id FROM data_text_units')]
    if len(assets) > 1000:
        raise ValueError('canonical_archive_asset_limit')
    for asset in assets:
        if not re.fullmatch(r'a_[0-9a-f]{20}', asset):
            raise ValueError('canonical_archive_asset_id_invalid')
        candidates = [checked(p, root) for p in stage.rglob(asset + '.json') if p.is_file()]
        if not candidates or len(candidates) > 100:
            raise ValueError('canonical_source_missing_or_ambiguous')
        hashes = {sha(path) for path in candidates}
        if len(hashes) != 1:
            raise ValueError('canonical_source_content_conflict')
        source, digest = candidates[0], hashes.pop()
        provenance = checked(source.with_suffix('.provenance.json'), root)
        evidence = json.loads(provenance.read_text(DEFAULT_ENCODING))
        if evidence.get('asset_id') != asset or evidence.get('canonical_sha256') != digest:
            raise ValueError('canonical_source_provenance_invalid')
        target = checked(destination / (asset + '.json'), root)
        input_digest = digest
        equivalent_replay = False
        if target.exists() and sha(target) != digest:
            if stable_source_hash(target) != stable_source_hash(source):
                raise ValueError('durable_canonical_source_conflict')
            digest = sha(target)
            equivalent_replay = True
        if not target.exists():
            task = checked(root / 'runtime/intermediate_tasks' / request['run_id'], root)
            temporary = task / (asset + '.json')
            with source.open('rb') as incoming, temporary.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush(); os.fsync(outgoing.fileno())
            if sha(temporary) != digest:
                raise ValueError('durable_source_copy_changed')
            temporary.replace(target)
        record = {'asset_id': asset, 'canonical_sha256': digest, 'source_sha256': evidence['source_sha256'],
                  'path': target.relative_to(root).as_posix(), 'protected': True, 'regenerable_intermediate': False}
        metadata = target.with_suffix('.provenance.json')
        if metadata.exists() and json.loads(metadata.read_text(DEFAULT_ENCODING)) != record:
            raise ValueError('durable_source_provenance_conflict')
        if not metadata.exists():
            temporary = metadata.with_suffix('.tmp')
            temporary.write_text(json.dumps(record, ensure_ascii=False), DEFAULT_ENCODING)
            temporary.replace(metadata)
        records.append({**record, 'input_canonical_sha256': input_digest,
                        'equivalent_replay': equivalent_replay,
                        'equivalence_policy': 'ignore_only_gateway_top_level_canonicalized_at_value'})
    return {'status': 'completed', 'sources': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(DEFAULT_ENCODING))
        if request.get('schema_version') != 'canonical-source-archive.v1':
            raise ValueError('canonical_archive_request_invalid')
        result = archive(request)
        response = {'schema_version': 'canonical-source-archive.v1', 'run_id': request['run_id'], 'status': 'completed',
                    'producer': SCRIPT_NAME, 'producer_version': SCRIPT_VERSION, 'result': result}
        temporary = args.response.with_suffix('.tmp')
        temporary.write_text(json.dumps(response, ensure_ascii=False), DEFAULT_ENCODING)
        temporary.replace(args.response)
        print(json.dumps({'status': 'completed', 'response': str(args.response)}))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__,
                          'detail': str(exc) if isinstance(exc, ValueError) else 'canonical_source_archive_failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
