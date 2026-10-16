# ============================================================
# 文件名: workbench_timeline_reader_v0001.py
# 中文名: 原消息时间分布只读适配
# 版本号: v0001
# 主层级: action
# 层级: workbench / adapters
# 脚本定位: source_message_timeline
# 职责说明: 去重消息、分桶、来源会话与证据查询
# 本脚本做什么: 只读时间和身份旁表，生成可重建私有消息索引
# 本脚本不做什么: 不修改业务库、不生成标题或主题、不调用模型
# 制度边界声明: 原时间与来源身份保留；无主题明确返回缺失
# 可更新: True
# ============================================================
# ALIAS_META
# alias: workbench_timeline_reader_v0001
# family: workbench_timeline_reader
# role: readonly_source_message_timeline
# version: v0001
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_timeline_reader_v0001.py
# input: trusted root/cache and bounded timeline request JSON
# output: timeline summary, bucket conversations, window nodes or evidence
# depends_on: Python standard library, SQLite timestamp and identity side tables
# used_by: workbench_timeline_adapter_v0001
# ============================================================
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

DEFAULT_ENCODING = 'utf-8'
SCRIPT_NAME = 'workbench_timeline_reader_v0001'
SCRIPT_FAMILY = 'workbench_timeline_reader'
SCRIPT_VERSION = 'v0001'
KEYS = ('asset_id', 'path', 'value_index', 'segment_index', 'sentence_index', 'char_start', 'char_end')
STAMP = 'data_text_unit_source_timestamps'
IDENTITY = 'data_text_unit_source_identities'


def fingerprint(source):
    return [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in (source, Path(str(source)+'-wal')) if p.exists()]


def source_connection(source):
    connection = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=2)
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    return connection


def epoch(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError('Event time must have UTC timezone')
    return parsed.timestamp()


# ============================================================
# 原库只读，索引只写工作台私有缓存；按消息去重而非按句子计聊天
# ============================================================
def build_index(source, cache):
    signature = fingerprint(source)
    if cache.exists():
        with closing(sqlite3.connect(cache.as_uri()+'?mode=ro', uri=True)) as c:
            row = c.execute('SELECT value FROM info WHERE key=?', ('source_signature',)).fetchone()
            if row and json.loads(row[0]) == signature:
                return
    temporary = cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.building')
    cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(source_connection(source)) as original, closing(sqlite3.connect(temporary)) as derived:
            original.execute('SELECT 1 FROM '+IDENTITY+' LIMIT 1')
            derived.executescript('CREATE TABLE info(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE messages(asset TEXT,message TEXT,epoch REAL,conversation TEXT,conversation_index INTEGER,source_hash TEXT,source_path TEXT,role TEXT,coordinates TEXT,unit_count INTEGER,ordinal INTEGER,PRIMARY KEY(asset,message)); CREATE INDEX messages_time ON messages(epoch); CREATE INDEX messages_conversation ON messages(conversation,epoch);')
            grouped = {}
            statuses = {}
            for row in original.execute('SELECT '+','.join(KEYS)+',message_id,event_time,event_time_status FROM '+STAMP):
                status = row[9]
                statuses[status] = statuses.get(status, 0)+1
                if status != 'resolved' or not row[7] or not row[8]:
                    continue
                try:
                    stamp = epoch(row[8])
                except (ValueError, OverflowError):
                    statuses['unparseable_resolved'] = statuses.get('unparseable_resolved', 0)+1
                    continue
                key = (row[0], row[7])
                existing = grouped.get(key)
                if existing:
                    existing['count'] += 1
                    existing['conflict'] |= existing['epoch'] != stamp
                else:
                    grouped[key] = {'epoch': stamp, 'coordinates': list(row[:7]), 'count': 1, 'conflict': False}
            missing_identity = 0
            ambiguous = 0
            for ordinal, ((asset, message), fact) in enumerate(sorted(grouped.items(), key=lambda item: (item[1]['epoch'], item[0])), 1):
                if fact['conflict']:
                    ambiguous += 1
                    continue
                identity = original.execute('SELECT message_identity_evidence_json,message_role FROM '+IDENTITY+' WHERE '+ ' AND '.join(k+'=?' for k in KEYS), fact['coordinates']).fetchone()
                evidence = json.loads(identity[0]) if identity and identity[0] else {}
                canonical = evidence.get('fields', {}).get('role', {}).get('canonical_path', '')
                match = re.match(r'^/conversations/(\d+)/nodes/', canonical)
                index = int(match.group(1)) if match else None
                source_hash = evidence.get('canonical_source_sha256')
                conversation = json.dumps([asset, source_hash, index], separators=(',', ':')) if source_hash and index is not None else None
                missing_identity += conversation is None
                source_path = evidence.get('source_ref', {}).get('source_path')
                derived.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)', (asset, message, fact['epoch'], conversation, index, source_hash, source_path, identity[1] if identity else None, json.dumps(fact['coordinates']), fact['count'], ordinal))
            meta = {'source_signature': signature, 'statuses': statuses, 'ambiguous_messages': ambiguous, 'messages_without_conversation': missing_identity, 'built_at': datetime.now(timezone.utc).isoformat(), 'topic_status': 'not_persisted', 'conversation_identity': 'asset plus canonical source hash and original conversation index; cross-source conversations not merged'}
            derived.executemany('INSERT INTO info VALUES(?,?)', [(k, json.dumps(v)) for k, v in meta.items()])
            derived.commit()
            if fingerprint(source) != signature:
                raise ValueError('Source changed while building; retry after ingestion is stable')
        os.replace(temporary, cache)
    finally:
        if temporary.exists():
            temporary.unlink()


def bucket_start(stamp, grain, offset):
    value = datetime.fromtimestamp(stamp, timezone.utc)+timedelta(minutes=offset)
    if grain == 'day':
        value = value.replace(hour=0, minute=0, second=0, microsecond=0)
    elif grain == 'week':
        value = (value-timedelta(days=value.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif grain == 'month':
        value = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        value = value.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return (value-timedelta(minutes=offset)).timestamp()


def next_bucket(stamp, grain, offset):
    value = datetime.fromtimestamp(stamp, timezone.utc)+timedelta(minutes=offset)
    if grain in ('day', 'week'):
        value += timedelta(days=1 if grain == 'day' else 7)
    elif grain == 'month':
        value = value.replace(year=value.year+(value.month == 12), month=value.month % 12+1)
    else:
        value = value.replace(year=value.year+1)
    return (value-timedelta(minutes=offset)).timestamp()


def query(request):
    source = Path(request['root']).resolve()/'sql/action/data.db'
    cache = Path(request['cache']).resolve()/'source_message_timeline_v0001.sqlite3'
    operation = request.get('operation', 'status')
    if operation not in ('status', 'summary', 'bucket', 'window', 'detail'):
        raise ValueError('Unsupported timeline operation')
    if not source.is_file():
        return {'available': False, 'reason': '原消息数据库未配置', 'items': [], 'bins': []}
    with closing(source_connection(source)) as c:
        names = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {STAMP, IDENTITY}.issubset(names):
            return {'available': False, 'reason': '来源缺少时间或身份旁表', 'items': [], 'bins': []}
    if operation == 'status':
        return {'available': True, 'topic_status': 'not_persisted', 'source': str(source), 'cache_ready': cache.exists()}
    if operation == 'summary' or not cache.exists():
        build_index(source, cache)
    with closing(sqlite3.connect(cache.as_uri()+'?mode=ro', uri=True)) as c:
        c.row_factory = sqlite3.Row
        meta = {row[0]: json.loads(row[1]) for row in c.execute('SELECT key,value FROM info')}
        if fingerprint(source) != meta['source_signature']:
            raise ValueError('源数据已更新，请重新读取时间分布')
        first, last, total = c.execute('SELECT min(epoch),max(epoch),count(*) FROM messages').fetchone()
        result = {'schema_version': 'workbench.timeline.v1', 'available': True, 'read_only_source': True, 'source': str(source), 'meta': meta, 'total_messages': total, 'time_range': [first, last], 'count_semantics': 'deduplicated asset/message IDs; conversations are active in a half-open bucket; text units are not chats'}
        if operation == 'summary':
            grain = request.get('grain', 'auto')
            if grain == 'auto':
                grain = 'day' if not total or last-first < 720*86400 else 'month' if last-first < 720*28*86400 else 'year'
            if grain not in ('day', 'week', 'month', 'year'):
                raise ValueError('Unsupported time grain')
            offset = int(request.get('offset_minutes', 480))
            if abs(offset) > 840:
                raise ValueError('Invalid timezone offset')
            buckets = {}
            if total:
                cursor = bucket_start(first, grain, offset)
                while cursor <= last:
                    if len(buckets) >= 720:
                        raise ValueError('时间分桶超过 720 个，请选择更粗的粒度')
                    buckets[cursor] = {'start': cursor, 'end': next_bucket(cursor, grain, offset), 'messages': 0, 'units': 0, 'conversations': set()}
                    cursor = buckets[cursor]['end']
                for row in c.execute('SELECT epoch,conversation,unit_count FROM messages'):
                    bucket = buckets[bucket_start(row[0], grain, offset)]
                    bucket['messages'] += 1
                    bucket['units'] += row[2]
                    if row[1]:
                        bucket['conversations'].add(row[1])
            bins = [{**value, 'conversations': len(value['conversations'])} for value in buckets.values()]
            result.update({'bins': bins, 'grain': grain, 'offset_minutes': offset, 'total_conversations': c.execute('SELECT count(DISTINCT conversation) FROM messages').fetchone()[0]})
            return result
        if operation in ('bucket', 'window'):
            start, end = float(request['start']), float(request['end'])
            if not start < end or not all(abs(v) < 1e12 for v in (start, end)):
                raise ValueError('Invalid half-open time window')
            parameters = (start, end)
            count = c.execute('SELECT count(*) FROM messages WHERE epoch>=? AND epoch<?', parameters).fetchone()[0]
            if operation == 'bucket':
                page = max(0, int(request.get('page', 0)))
                groups = c.execute('SELECT conversation,conversation_index,source_hash,asset,count(*) AS messages,sum(unit_count) AS units,min(epoch) AS first,max(epoch) AS last,min(message) AS sample_message FROM messages WHERE epoch>=? AND epoch<? GROUP BY conversation,asset ORDER BY messages DESC,asset,conversation LIMIT 21 OFFSET ?', parameters+(page*20,)).fetchall()
                result.update({'message_count': count, 'items': [{**dict(row), 'topic': None, 'topic_status': 'not_persisted'} for row in groups[:20]], 'has_more': len(groups)>20, 'page': page, 'start': start, 'end': end})
            else:
                rows = c.execute('SELECT * FROM messages WHERE epoch>=? AND epoch<? ORDER BY epoch,asset,message LIMIT 601', parameters).fetchall()
                result.update({'source_count': total, 'visible_count': count, 'nodes': [{'id': json.dumps([row['asset'], row['message']], separators=(',', ':')), 'x': row['ordinal'] % 32*12, 'y': row['ordinal']//32*12, 'source_line': row['ordinal'], 'event_epoch': row['epoch'], 'kind': 'source_message', 'label': ('来源会话 #'+str(row['conversation_index']+1) if row['conversation_index'] is not None else '会话身份未关联')+' · 主题未入库', 'asset_id': row['asset'], 'message_id': row['message']} for row in rows[:600]], 'edges': [], 'layout': 'source-message-record-grid', 'truncated': count>600, 'budgets': {'nodes': 600, 'edges': 0}, 'start': start, 'end': end})
            return result
        asset, message = request.get('asset_id'), request.get('message_id')
        row = c.execute('SELECT * FROM messages WHERE asset=? AND message=?', (asset, message)).fetchone()
        if row is None:
            raise ValueError('Original message not found')
        coordinates = json.loads(row['coordinates'])
        with closing(source_connection(source)) as original:
            where = ' AND '.join(k+'=?' for k in KEYS)
            text = original.execute('SELECT text FROM data_text_units WHERE '+where, coordinates).fetchone()
            identity = original.execute('SELECT message_identity_evidence_json FROM '+IDENTITY+' WHERE '+where, coordinates).fetchone()
        result.update({'record': dict(row), 'original_text_unit_excerpt': text[0][:1600] if text else None, 'excerpt_semantics': 'first persisted text unit; not a chat title, summary or complete message', 'identity_evidence': json.loads(identity[0]) if identity else None, 'coordinates': dict(zip(KEYS, coordinates)), 'topic': None, 'topic_status': 'not_persisted'})
        return result


# ============================================================
# JSON CLI；只在明确请求时执行，不在导入时读写
# ============================================================
def main():
    started = time.perf_counter()
    try:
        request = json.loads(sys.stdin.read(262145))
        result = query(request)
        result['elapsed_ms'] = round((time.perf_counter()-started)*1000, 2)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as error:
        print(json.dumps({'error': {'code': type(error).__name__, 'message': str(error)}}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
