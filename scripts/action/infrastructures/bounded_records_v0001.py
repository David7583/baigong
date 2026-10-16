# ============================================================
# 文件名: bounded_records_v0001.py
# 中文名: 有界内存临时记录容器
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / bounded_storage
# 脚本定位: 向量批处理的磁盘辅助容器
# 职责说明: 提供可迭代日志及 SQLite 映射，避免全库对象驻留内存
# 本脚本做什么: 使用临时文件保存中间记录，严格解析 JSONL
# 本脚本不做什么: 不改变原始数据、身份和业务数据库
# 制度边界声明: 临时容器不是事务凭证；正式输出由调用方发布
# 可更新: True
# ============================================================
# ALIAS_META
# alias: bounded_records_v0001
# family: bounded_records
# role: disk_spool
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/bounded_records_v0001.py
# input: JSON serializable records
# output: bounded iterators and keyed lookups
# depends_on: Python stdlib
# used_by: embedding_lifecycle_guard_v0002, embedding_writer_v0002
# ============================================================
from __future__ import annotations

import json
import sqlite3
import tempfile
from itertools import islice
from collections.abc import MutableMapping
from pathlib import Path

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'bounded_records'
SCRIPT_NAME = 'bounded_records_v0001.py'
SCRIPT_VERSION = 'v0001'


def strict_jsonl(path):
    if not Path(path).exists():
        return
    with Path(path).open(encoding=DEFAULT_ENCODING) as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError('object required')
            except (ValueError, TypeError) as exc:
                raise ValueError(f'Invalid JSONL at {path}:{number}') from exc
            yield record


class DiskSequence:
    def __init__(self, records=()):
        self.stream = tempfile.TemporaryFile(mode='w+t', encoding=DEFAULT_ENCODING)
        self.count = 0
        for record in records:
            self.append(record)

    def append(self, record):
        self.stream.seek(0, 2)
        self.stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        self.count += 1

    def __len__(self):
        return self.count

    def __iter__(self):
        self.stream.flush()
        self.stream.seek(0)
        for line in self.stream:
            yield json.loads(line)

    def __getitem__(self, index):
        if isinstance(index,slice):
            if index.stop is None:raise IndexError('Unbounded materialization is not supported')
            return list(islice(iter(self),index.start or 0,index.stop,index.step or 1))
        if index<0:index+=self.count
        if not 0<=index<self.count:raise IndexError(index)
        return next(islice(iter(self),index,index+1))

    def close(self):
        self.stream.close()


class DiskMap(MutableMapping):
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='action_index_')
        self.connection = sqlite3.connect(str(Path(self.directory.name) / 'index.db'))
        self.connection.execute('PRAGMA cache_size=-4096')
        self.connection.execute('PRAGMA journal_mode=OFF')
        self.connection.execute('CREATE TABLE records(k TEXT PRIMARY KEY,v TEXT NOT NULL)')

    def __getitem__(self, key):
        row = self.connection.execute('SELECT v FROM records WHERE k=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def __setitem__(self, key, value):
        self.connection.execute('INSERT INTO records VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',
                                (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def __delitem__(self, key):
        if self.connection.execute('DELETE FROM records WHERE k=?', (key,)).rowcount == 0:
            raise KeyError(key)

    def __iter__(self):
        for row in self.connection.execute('SELECT k FROM records ORDER BY rowid'):
            yield row[0]

    def __len__(self):
        return self.connection.execute('SELECT COUNT(*) FROM records').fetchone()[0]

    def values(self):
        for row in self.connection.execute('SELECT v FROM records ORDER BY rowid'):
            yield json.loads(row[0])

    def sorted_values(self):
        for row in self.connection.execute('SELECT v FROM records ORDER BY k'):
            yield json.loads(row[0])

    def close(self):
        self.connection.close()
        self.directory.cleanup()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
