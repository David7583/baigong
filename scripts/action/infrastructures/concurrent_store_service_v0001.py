# ============================================================
# 文件名: concurrent_store_service_v0001.py
# 中文名: 持续派生与向量后台服务
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / concurrent_stores
# 脚本定位: 单进程拥有 DuckDB 与 Chroma，计算和查询独立线程
# 职责说明: 跟进已提交 SQLite；有界批次、可恢复游标、查询截止
# 本脚本做什么: 向量缓存、增量派生、短提交、受限本机查询
# 本脚本不做什么: 不修改权威 SQLite、不访问外部模型、不接受任意写 SQL
# 制度边界声明: 派生滞后显式展示；签名核验后才推进游标；失败保留待恢复批次
# 可更新: True
# ALIAS_META
# alias: concurrent_store_service_v0001
# family: concurrent_store_service
# role: derived_store_owner
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/concurrent_store_service_v0001.py
# input: committed SQLite rows, local model and bounded retrieval requests
# output: incremental DuckDB/Chroma and durable checkpoints
# depends_on: embedding_generator_v0007, embedding_writer_v0002, chromadb, duckdb
# used_by: ingestion_jobs_v0002, retrieval_executor_v0003
# ============================================================
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
import os
import queue
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[3]
SCRIPT_NAME='concurrent_store_service_v0001.py'
SCRIPT_VERSION='v0001'
SCRIPT_FAMILY='concurrent_store_service'
DEFAULT_ENCODING='utf-8'
from ingestion_jobs_v0001 import exclusive_lease

def module(name,path):
    path=ROOT/path;sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result;spec.loader.exec_module(result);return result

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

class Store:
    def __init__(self,config_path):
        self.config_path=Path(config_path).resolve();self.config=json.loads(self.config_path.read_text(encoding='utf-8'))
        if any(key.startswith('test_') for key in self.config) and not self.config_path.is_relative_to(ROOT/'temp'):
            raise ValueError('Test controls require isolated configuration')
        self.root=Path(self.config.get('project_root',ROOT)).resolve()
        self.directory=self.root/self.config['state_dir'];self.directory.mkdir(parents=True,exist_ok=True)
        self.stop=threading.Event();self.vector_lock=threading.Lock();self.duck=None;self.collection=None
        self.state_path=self.directory/'derived.sqlite3'
        with self.state() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS control(k TEXT PRIMARY KEY,v TEXT); CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,payload TEXT,status TEXT); CREATE TABLE IF NOT EXISTS vector_index(object_type TEXT,object_id TEXT,signature TEXT UNIQUE,payload TEXT,PRIMARY KEY(object_type,object_id));')
        token=self.directory/'service_token'
        if not token.exists():
            with token.open('x') as stream:stream.write(secrets.token_urlsafe(32))
        self.token=token.read_text(encoding="utf-8").strip()
        self.vector_status={'status':'paused'};self.duck_status={'status':'starting'}
        self.control=None
        self.embedding_requests=queue.Queue(maxsize=16)
        self.vector_thread=None
        self.worker_lock=threading.Lock()

    def ensure_vector_worker(self):
        with self.worker_lock:
            if self.vector_thread is None or not self.vector_thread.is_alive():
                self.vector_thread=threading.Thread(target=self.vector_loop,daemon=True)
                self.vector_thread.start()

    def embed(self,body):
        text=body.get('text');timeout=body.get('timeout_ms',15000)
        config=yaml.safe_load((self.root/self.config['embed_config']).read_text(encoding='utf-8'))
        if not isinstance(text,str) or not 1<=len(text)<=8000 or not 1<=timeout<=30000:raise ValueError('Invalid embedding request')
        if body.get('model')!=config['model']['name'] or body.get('dimension')!=config['model']['dimension']:raise ValueError('Query/index model mismatch')
        item={'text':text,'deadline':time.monotonic()+timeout/1000,'done':threading.Event()}
        try:self.embedding_requests.put_nowait(item)
        except queue.Full:raise ValueError('Embedding query queue busy')
        self.ensure_vector_worker()
        if not item['done'].wait(timeout/1000):raise TimeoutError('Embedding query deadline reached')
        if 'error' in item:raise ValueError(item['error'])
        return {'status':'completed','vectors':[item['vector']],'model':config['model']['name'],'dimension':config['model']['dimension'],'shared_model_runtime':True}

    def ai_allowed(self):
        connection=yaml.safe_load((self.root/self.config.get('ai_gate_chat_config','config/action/config/independent_action_chat_config_v0003.local.yml')).read_text(encoding='utf-8'))['ai_controller']
        if self.control is None:
            controller=module('continuous_ai_gate',connection['script'])
            self.control=controller.AIController(self.root/connection['config'],root=self.root)
        token=os.environ.get(connection['token_env'],'')
        if not token:
            access=json.loads((self.root/connection['token_file']).read_text(encoding='utf-8'))
            if access.get('app_id')!=connection['app_id']:raise ValueError('AI application credential identity differs')
            token=access.get('token','')
        try:
            with self.control._db() as db:self.control._admit(db,connection['app_id'],token)
            return True
        except Exception as exc:
            if type(exc).__name__=='PermissionDenied':return False
            raise

    @contextmanager
    def state(self):
        db=sqlite3.connect(self.state_path,timeout=5);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def get(self,key,default=None):
        with self.state() as db:row=db.execute('SELECT v FROM control WHERE k=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self,key,value):
        with self.state() as db:db.execute('INSERT OR REPLACE INTO control VALUES (?,?)',(key,canonical(value)))

    def status(self):
        with self.state() as db:
            indexed=db.execute('SELECT COUNT(*) FROM vector_index').fetchone()[0]
            pending=db.execute("SELECT COUNT(*) FROM batches WHERE status='pending'").fetchone()[0]
        return {'status':'ok','version':SCRIPT_VERSION,'pid':os.getpid(),'vector':dict(self.vector_status),
                'duckdb':dict(self.duck_status),'indexed_vectors':indexed,'pending_batches':pending,
                'vector_enabled':self.get('vector_enabled',False),'last_vector_batch':self.get('last_vector_batch'),
                'derived_visibility':'committed batches; may lag authoritative SQLite'}

    @contextmanager
    def source(self):
        db=sqlite3.connect((self.root/self.config['action_db']).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        db.row_factory=sqlite3.Row
        try:yield db
        finally:db.close()

    def duck_loop(self):
        try:
            import duckdb
            self.duck=duckdb.connect(str(self.root/self.config['duckdb_path']))
            self.duck.execute("SET threads=2");self.duck.execute("SET memory_limit='512MB'")
            self.duck.execute("SET enable_external_access=false")
            self.duck.execute("SET autoload_known_extensions=false")
            self.duck.execute("SET autoinstall_known_extensions=false")
            # Only views change; source fact rows and history remain append-only.
            for label,key in (('concept','unit_text_id'),('instance','instance_id'),('attribute','attr_id')):
                table=label+'_units_sync'
                if not self.duck.execute('SELECT 1 FROM information_schema.tables WHERE table_name=?',[table]).fetchone():raise ValueError(f'Initialize DuckDB schema first: {table}')
                self.duck.execute(f'CREATE OR REPLACE VIEW {label}_units_latest AS SELECT * FROM {table} QUALIFY ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY synced_at DESC,sync_id DESC)=1')
                self.duck.execute(f'CREATE INDEX IF NOT EXISTS idx_continuous_{label}_id ON {table} ({key})')
            connection=self.duck.cursor()
            self.duck_status={'status':'running'}
            while not self.stop.is_set():
                changed=False
                for src,label,key in (('concept_units','concept','unit_text_id'),('instance_units','instance','instance_id'),('unit_attributes','attribute','attr_id')):
                    position=self.get('duck_cursor_'+src,0)
                    with self.source() as source:
                        fields=[row[1] for row in source.execute(f'PRAGMA table_info({src})')]
                        rows=source.execute(f'SELECT rowid AS source_rowid,* FROM {src} WHERE rowid>? ORDER BY rowid LIMIT ?',(position,self.config.get('duckdb_batch_size',1000))).fetchall()
                    if not rows:continue
                    changed=True;table=label+'_units_sync'
                    run='delta_'+digest([src,position,[(row[key],dict(row)) for row in rows]])[:24]
                    schema={r[1]:r[2] for r in connection.execute(f"PRAGMA table_info('{table}')").fetchall()}
                    columns=['sync_id','synced_at']+fields
                    timestamp=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime())
                    payload=[dict(zip(columns,[run,timestamp]+[row[f] for f in fields])) for row in rows]
                    try:
                        connection.execute('BEGIN TRANSACTION')
                        connection.execute('CREATE TEMP TABLE delta AS SELECT unnest(json_transform_strict(?,?),recursive:=true)',[canonical(payload),canonical([{f:schema[f] for f in columns}])])
                        stable=[f for f in fields if f not in ('created_at','run_id','first_seen_instance_id')]
                        mismatch=' OR '.join(f'd.{f} IS DISTINCT FROM t.{f}' for f in stable)
                        if connection.execute(f'SELECT 1 FROM delta d JOIN {table} t ON d.{key}=t.{key} WHERE {mismatch} LIMIT 1').fetchone():raise ValueError(f'DuckDB fact conflict: {src}')
                        inserted=connection.execute(f'SELECT COUNT(*) FROM delta d WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{key}=d.{key})').fetchone()[0]
                        connection.execute(f'INSERT INTO {table} ({",".join(columns)}) SELECT {",".join("d."+f for f in columns)} FROM delta d WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{key}=d.{key})')
                        if inserted and not connection.execute('SELECT 1 FROM _sync_log WHERE sync_id=?',[run]).fetchone():
                            connection.execute('INSERT INTO _sync_log (sync_id,source_db,source_table,record_count,started_at,finished_at,script_name,script_version,source_db_hash) VALUES (?,?,?,?,?,?,?,?,?)',
                                [run,str(self.root/self.config['action_db']),src,inserted,timestamp,timestamp,SCRIPT_NAME,SCRIPT_VERSION,None])
                        connection.execute('DROP TABLE delta');connection.execute('COMMIT')
                    except Exception:
                        connection.execute('ROLLBACK');raise
                    # A crash here replays the same immutable rows and writes zero duplicates.
                    self.set('duck_cursor_'+src,rows[-1]['source_rowid'])
                    self.duck_status={'status':'running','table':src,'source_rowid':rows[-1]['source_rowid'],'last_batch_inserted':inserted,'updated_at':time.time()}
                if not changed:
                    self.duck_status={**self.duck_status,'status':'caught_up'}
                    self.stop.wait(self.config.get('poll_seconds',2))
            connection.close()
        except Exception as exc:
            self.duck_status={'status':'failed','error':str(exc)};self.set('duck_error',{'error':str(exc),'traceback':traceback.format_exc()})

    def commit_vectors(self,records,batch_id,cursor_key,cursor_value,writer):
        payload={'records':records,'cursor_key':cursor_key,'cursor_value':cursor_value}
        with self.state() as db:
            old=db.execute('SELECT payload FROM batches WHERE id=?',(batch_id,)).fetchone()
            if old and old[0]!=canonical(payload):raise ValueError('Vector batch conflict')
            db.execute('INSERT OR IGNORE INTO batches VALUES (?,?,?)',(batch_id,canonical(payload),'pending'))
        built=[writer._build_chroma_record(r) for r in records]
        with self.vector_lock:
            self.collection.upsert(ids=[r['id'] for r in built],embeddings=[r['embedding'] for r in built],metadatas=[r['metadata'] for r in built])
            if self.config.get('test_fault_after_vector_upsert'):os._exit(97)
            actual=self.collection.get(ids=[r['id'] for r in built],include=['metadatas','embeddings'])
            by_id={oid:(actual['metadatas'][i],actual['embeddings'][i]) for i,oid in enumerate(actual['ids'])}
            for r in built:
                if r['id'] not in by_id:raise ValueError('Chroma write verification missing ID')
                meta,vector=by_id[r['id']]
                if meta!=r['metadata'] or len(vector)!=len(r['embedding']) or any(abs(float(a)-float(b))>1e-6 for a,b in zip(vector,r['embedding'])):raise ValueError('Chroma write verification mismatch')
        with self.state() as db:
            for record in records:
                stored={k:v for k,v in record.items() if k!='embedding_vector'}
                old=db.execute('SELECT signature FROM vector_index WHERE object_type=? AND object_id=?',(record['object_type'],record['object_id'])).fetchone()
                if old and old[0]!=record['embedding_signature']:raise ValueError('Existing vector encoding context conflict; use new collection')
                db.execute('INSERT OR IGNORE INTO vector_index VALUES (?,?,?,?)',(record['object_type'],record['object_id'],record['embedding_signature'],canonical(stored)))
            receipt={**payload,'records':[{k:v for k,v in row.items() if k!='embedding_vector'} for row in records],
                     'vector_checksums':[digest(row['embedding_vector']) for row in records]}
            db.execute("UPDATE batches SET status='committed',payload=? WHERE id=?",(canonical(receipt),batch_id))
            db.execute('INSERT OR REPLACE INTO control VALUES (?,?)',(cursor_key,canonical(cursor_value)))

    def vector_loop(self):
        cache=None
        try:
            generator=module('concurrent_generator','scripts/action/vector/embedding_generator_v0007.py')
            writer=module('concurrent_vector_writer','scripts/action/vector/embedding_writer_v0002.py')
            config=yaml.safe_load((self.root/self.config['embed_config']).read_text(encoding='utf-8'))
            if config.get('backend',{}).get('type')!='local':raise ValueError('Background computation requires explicit local model configuration')
            context=None
            while not self.stop.is_set():
                if not self.get('vector_enabled',False) and self.embedding_requests.empty():
                    self.vector_status={'status':'paused'};self.stop.wait(.25);continue
                if not self.ai_allowed():
                    while not self.embedding_requests.empty():
                        item=self.embedding_requests.get_nowait();item['error']='AI permission is paused';item['done'].set()
                    self.vector_status={'status':'permission_paused'};self.stop.wait(.5);continue
                if context is None:
                    self.vector_status={'status':'initializing'}
                    context=generator._reuse_context(config,'continuous',False)
                    if not context:raise ValueError('Cannot fingerprint local model')
                    old_context=self.get('encoding_context')
                    if old_context and old_context!=context:raise ValueError('Encoding context changed; use new vector store')
                    self.set('encoding_context',context)
                    from embedding_cache_v0001 import PersistentVectorCache
                    cache=PersistentVectorCache(self.directory/'vector_cache',context,int(config['model']['dimension']))
                    _,self.collection=writer._get_or_create_collection(self.root/self.config['chromadb_path'])
                    import torch
                    torch.set_num_threads(2)
                    with self.state() as db:pending=db.execute("SELECT id,payload FROM batches WHERE status='pending'").fetchall()
                    for bid,payload in pending:
                        item=json.loads(payload);self.commit_vectors(item['records'],bid,item['cursor_key'],item['cursor_value'],writer)
                    # Warm the sole model before reporting batch progress. Cache-only
                    # catch-up must not misleadingly imply that inference is ready.
                    self.vector_status={'status':'initializing','phase':'loading_shared_model'}
                    generator._local_model_call([''],config)
                # Query requests share the loaded model and take priority between
                # bounded background batches; never load a second GPU copy in chat.
                for _ in range(2):
                    try:item=self.embedding_requests.get_nowait()
                    except queue.Empty:break
                    try:
                        if time.monotonic()>item['deadline']:raise TimeoutError('Embedding query expired')
                        vector=cache.get(item['text'])
                        if vector is None:
                            vector=generator._local_model_call([item['text']],config)[0]
                            cache.put_batch([item['text']],[vector])
                        item['vector']=vector
                    except Exception as exc:item['error']=str(exc)
                    finally:item['done'].set()
                if not self.get('vector_enabled',False):continue
                if self.config.get('test_vector_limit') and self.status()['indexed_vectors']>=self.config['test_vector_limit']:
                    self.set('vector_enabled',False);continue
                changed=False
                for target,table,key,text in (('concept','concept_units','unit_text_id','unit_text'),('instance','instance_units','instance_id','content')):
                    cursor_key='vector_cursor_'+target;position=self.get(cursor_key,0)
                    with self.source() as source:
                        rows=source.execute(f'SELECT rowid AS cursor,{key} AS id,{text} AS content,content_hash AS hash FROM {table} WHERE rowid>? ORDER BY rowid LIMIT ?',(position,self.config.get('vector_batch_size',4))).fetchall()
                    if not rows:continue
                    changed=True;self.vector_status={'status':'computing','target':target,'source_rowid':position,'batch_size':len(rows),'updated_at':time.time()}
                    bid='v'+digest([context,target,[(r['id'],r['hash']) for r in rows]])[:28]
                    output=self.directory/'vector_batches'/f'{bid}.jsonl';output.parent.mkdir(parents=True,exist_ok=True)
                    # Incomplete generated file is disposable; cache is independently transactional.
                    if output.exists():output.unlink()
                    stats={'computed':0,'reused':0}
                    generator._generate_and_write_batch(rows,target,config,bid,output,False,cache,stats)
                    records=[json.loads(line) for line in output.read_text(encoding='utf-8').splitlines() if line.strip()]
                    if len(records)!=len(rows):raise ValueError('Vector count conservation failed')
                    self.commit_vectors(records,bid,cursor_key,rows[-1]['cursor'],writer)
                    output.unlink()  # Verified metadata, vector checksums and durable cache retain provenance.
                    self.vector_status={'status':'running','target':target,'source_rowid':rows[-1]['cursor'],'last_batch':stats,'updated_at':time.time(),
                                        'gpu_peak_allocated_bytes':torch.cuda.max_memory_allocated() if torch.cuda.is_available() else 0}
                    self.set('last_vector_batch',self.vector_status)
                    # Test-only limit is rejected outside the isolated test configuration.
                    limit=self.config.get('test_vector_limit')
                    if limit and self.status()['indexed_vectors']>=limit:self.set('vector_enabled',False);break
                    if not self.embedding_requests.empty():break
                if not changed:
                    self.vector_status={**self.vector_status,'status':'caught_up'};self.stop.wait(self.config.get('poll_seconds',2))
        except Exception as exc:
            self.vector_status={'status':'failed','error':str(exc)};self.set('vector_error',{'error':str(exc),'traceback':traceback.format_exc()});self.set('vector_enabled',False)
        finally:
            if cache:cache.close()
            if self.control:self.control.close()
            self.control=None

    def query(self,body):
        limits=body.get('limits',{});max_rows=limits.get('max_rows',100);max_bytes=limits.get('max_bytes',262144);timeout=limits.get('timeout_ms',2000)
        if not 1<=max_rows<=100 or not 1024<=max_bytes<=1048576 or not 1<=timeout<=30000:raise ValueError('Invalid query limits')
        if body.get('backend')=='chroma':
            p=body.get('parameters',{});vector=p.get('query_embedding');count=p.get('n_results',10)
            dimension=int(yaml.safe_load((self.root/self.config['embed_config']).read_text(encoding="utf-8"))['model']['dimension'])
            if not isinstance(vector,list) or len(vector)!=dimension or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector) or not 1<=count<=max_rows:raise ValueError('Invalid vector request')
            if self.collection is None:raise ValueError('Vector index not ready')
            if not self.vector_lock.acquire(timeout=.1):raise ValueError('Vector index committing; retry')
            try:
                raw=self.collection.query(query_embeddings=[vector],n_results=count,where=p.get('where'),include=['metadatas','distances'])
                rows=[{'object_id':oid,'metadata':raw['metadatas'][0][i],'distance':raw['distances'][0][i]} for i,oid in enumerate(raw['ids'][0])]
            finally:self.vector_lock.release()
        elif body.get('backend')=='duckdb':
            import duckdb
            if self.duck is None:raise ValueError('DuckDB not ready')
            connection=self.duck.cursor();timer=threading.Timer(timeout/1000,connection.interrupt)
            try:
                query=body.get('query','');statements=connection.extract_statements(query)
                if len(statements)!=1 or statements[0].type!=duckdb.StatementType.SELECT:raise ValueError('Read-only SELECT required')
                # Trusted projected schema is fixed; SQL cannot access filesystem or extensions.
                if any(word in query.lower() for word in ('read_','sqlite_','postgres','http','attach','pragma','duckdb_','information_schema','pg_','query_table','getenv')):raise ValueError('External/catalog access denied')
                timer.start();cursor=connection.execute(query,body.get('parameters',[]))
                names=[col[0] for col in cursor.description]
                if len(names)!=len(set(names)):raise ValueError('Duplicate result columns')
                rows=[dict(zip(names,row)) for row in cursor.fetchmany(max_rows+1)]
                from datetime import datetime,date,time as daytime
                from decimal import Decimal
                rows=[{k:(v.isoformat() if isinstance(v,(datetime,date,daytime)) else str(v) if isinstance(v,Decimal) else v) for k,v in row.items()} for row in rows]
            finally:timer.cancel();connection.close()
        else:raise ValueError('Unsupported backend')
        kept=[];size=0
        for row in rows[:max_rows]:
            size+=len(canonical(row).encode())
            if size>max_bytes:break
            kept.append(row)
        return {'status':'completed','rows':kept,'truncated':len(kept)!=len(rows),'evidence_status':'requires_source_verification','derived_state':self.status()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',default=str(ROOT/'config/action/config/concurrent_pipeline_v0001.json'));args=p.parse_args()
    store=Store(args.config)
    if store.config.get('test_vector_limit') and not Path(args.config).resolve().is_relative_to(ROOT/'temp'):raise ValueError('Test limit outside test configuration')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,value):
            body=canonical(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path=='/status':self.send(200,store.status())
            else:self.send(404,{'status':'not_found'})
        def do_POST(self):
            if self.headers.get('Authorization')!='Bearer '+store.token:self.send(403,{'status':'denied'});return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=131072:raise ValueError('Invalid body length')
                body=json.loads(self.rfile.read(length))
                if self.path=='/query':result=store.query(body)
                elif self.path=='/embed':result=store.embed(body)
                elif self.path=='/vectors':
                    if type(body.get('enabled')) is not bool:raise ValueError('Boolean enabled required')
                    store.set('vector_enabled',body['enabled']);result={'status':'accepted'}
                    if body['enabled']:store.ensure_vector_worker()
                elif self.path=='/shutdown':
                    store.stop.set();threading.Thread(target=self.server.shutdown,daemon=True).start();result={'status':'stopping'}
                else:raise ValueError('Unknown endpoint')
                self.send(200,result)
            except Exception as exc:self.send(400,{'status':'failed','error':str(exc)})
    with exclusive_lease(store.directory/'store_owner.lease'):
        server=ThreadingHTTPServer(('127.0.0.1',int(store.config['service_port'])),Handler)
        threading.Thread(target=store.duck_loop,daemon=True).start()
        store.ensure_vector_worker()
        server.serve_forever();server.server_close()

if __name__=='__main__':main()
