# ============================================================
# 文件名: embedding_cache_v0001.py
# 中文名: 可恢复本地向量批次缓存
# 版本号: v0001
# 主层级: action
# 层级: vector / checkpoint
# 脚本定位: 持久保存已验证的本地模型计算结果
# 职责说明: 以内容哈希和完整编码上下文隔离恢复数据
# 本脚本做什么: 事务提交向量，读取时验证维度、数值与校验和
# 本脚本不做什么: 不复用 HTTP 或模拟向量，不替代业务审计
# 制度边界声明: 新运行仍生成自己的业务身份和 provenance
# 可更新: True
# ALIAS_META
# alias: embedding_cache_v0001
# family: embedding_cache
# role: durable_computation_checkpoint
# version: v0001
# status: active
# entry_point: scripts/action/vector/embedding_cache_v0001.py
# input: validated local model context and vectors
# output: checked cached vectors
# depends_on: Python stdlib
# used_by: embedding_generator_v0007
# ============================================================
import hashlib
import json
import math
import sqlite3
from pathlib import Path

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'embedding_cache'
SCRIPT_NAME = 'embedding_cache_v0001.py'
SCRIPT_VERSION = 'v0001'


def digest(value):
    return hashlib.sha256(value.encode(DEFAULT_ENCODING)).hexdigest()


class PersistentVectorCache:
    def __init__(self, directory, context, dimension, fallback=None):
        self.dimension = dimension
        self.fallback = fallback
        context = {key:value for key,value in context.items() if key != 'ingest_run_id'}
        self.context = json.dumps(context, sort_keys=True, separators=(',',':'))
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(Path(directory)/(digest(self.context)+'.sqlite3')))
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.connection.execute('PRAGMA cache_size=-4096')
        self.connection.execute('CREATE TABLE IF NOT EXISTS vectors(text_hash TEXT PRIMARY KEY, vector TEXT, checksum TEXT)')
        self.connection.execute('CREATE TABLE IF NOT EXISTS context(value TEXT PRIMARY KEY)')
        rows = self.connection.execute('SELECT value FROM context').fetchall()
        if rows and rows != [(self.context,)]:
            raise ValueError('Embedding checkpoint context mismatch')
        self.connection.execute('INSERT OR IGNORE INTO context VALUES (?)',(self.context,))
        self.connection.commit()

    def validate(self, vector):
        if not isinstance(vector,list) or len(vector) != self.dimension or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector):
            raise ValueError('Invalid cached vector')
        return vector

    def get(self, text):
        row = self.connection.execute('SELECT vector,checksum FROM vectors WHERE text_hash=?',(digest(text),)).fetchone()
        if row:
            if digest(row[0]) != row[1]:
                raise ValueError('Embedding checkpoint checksum mismatch')
            return self.validate(json.loads(row[0]))
        return self.fallback.get(text) if self.fallback else None

    def put_batch(self, texts, vectors):
        with self.connection:
            for text,vector in zip(texts,vectors):
                data = json.dumps(self.validate(vector),separators=(',',':'),allow_nan=False)
                self.connection.execute('INSERT OR IGNORE INTO vectors VALUES (?,?,?)',(digest(text),data,digest(data)))

    def close(self):
        self.connection.close()
        if self.fallback:
            self.fallback.close()
