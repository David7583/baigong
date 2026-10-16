# 文件名: ingestion_jobs_v0004.py
# 中文名: 并行摄取与向量任务控制
# 版本号: v0004
# 主层级: action
# 层级: infrastructures / jobs
# 脚本定位: 持久队列、隔离编排和独立向量服务
# 职责说明: 分离长计算与入库、查询生命周期
# 本脚本做什么: 扫描、排队、恢复、服务状态与启停
# 本脚本不做什么: 不直接计算模型、不回退生产库
# 制度边界声明: 入库完成与向量追赶分别展示
# 可更新: True
# ALIAS_META
# alias: ingestion_jobs_v0004
# family: ingestion_jobs
# role: concurrent_runtime_controls
# version: v0004
# status: active
# entry_point: scripts/action/infrastructures/ingestion_jobs_v0004.py
# input:
#   - backend.jobs.v1 JSON handoff or explicit CLI operation
# output:
#   - versioned response JSON and durable queue state
# depends_on:
#   - owned_process_v0001
#   - data_action_chain_pipeline_v0021
#   - concurrent_store_service_v0002
# used_by:
#   - workbench_runtime_control_v0002
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
# ============================================================
# 本版本包含的完整实现；不依赖同名旧版本文件
# ============================================================
import argparse as _contained_0001_argparse
import hashlib as _contained_0001_hashlib
import importlib.util as _contained_0001_importlib
import json as _contained_0001_json
import os as _contained_0001_os
import shutil as _contained_0001_shutil
import sqlite3 as _contained_0001_sqlite3
import subprocess as _contained_0001_subprocess
import sys as _contained_0001_sys
import time as _contained_0001_time
import uuid as _contained_0001_uuid
from contextlib import contextmanager as _contained_0001_contextmanager
from pathlib import Path as _contained_0001_Path
from owned_process_v0001 import run as _contained_0001_run_owned
_contained_0001_DEFAULT_ENCODING = 'utf-8'
_contained_0001_SCRIPT_FAMILY = 'ingestion_jobs'
_contained_0001_SCRIPT_NAME = 'ingestion_jobs_v0001.py'
_contained_0001_SCRIPT_VERSION = 'v0001'
_contained_0001_ROOT = _contained_0001_Path(__file__).resolve().parents[3]
_contained_0001_DEFAULT_CONFIG = 'config/action/config/ingestion_jobs_v0001.json'

def _contained_0001_sha(path):
    with _contained_0001_Path(path).open('rb') as stream:
        return _contained_0001_hashlib.file_digest(stream, 'sha256').hexdigest()

def _contained_0001_load_pipeline(root=_contained_0001_ROOT):
    path = root / 'scripts/orchestration/action/data_action_chain_pipeline_v0020.py'
    spec = _contained_0001_importlib.util.spec_from_file_location('queued_data_action_v0020', path)
    module = _contained_0001_importlib.util.module_from_spec(spec)
    _contained_0001_sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

@_contained_0001_contextmanager
def _contained_0001_exclusive_lease(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0)
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        if _contained_0001_os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if _contained_0001_os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)

class _contained_0001_Jobs:

    def __init__(self, root=_contained_0001_ROOT, config_path=None):
        self.root = _contained_0001_Path(root).resolve()
        self.config_path = _contained_0001_Path(config_path or self.root / _contained_0001_DEFAULT_CONFIG).resolve()
        self.config = _contained_0001_json.loads(self.config_path.read_text(encoding=_contained_0001_DEFAULT_ENCODING))
        self.directory = (self.root / self.config['state_dir']).resolve()
        self.directory.relative_to(self.root)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('\n            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,path TEXT NOT NULL,sha TEXT NOT NULL,size INTEGER,status TEXT NOT NULL,staged TEXT,run_id TEXT,error TEXT,updated REAL,UNIQUE(path,sha));\n            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,created REAL,kind TEXT,detail TEXT);\n            CREATE TABLE IF NOT EXISTS control(k TEXT PRIMARY KEY,v TEXT);\n            ')

    @_contained_0001_contextmanager
    def db(self):
        db = _contained_0001_sqlite3.connect(str(self.directory / 'queue.sqlite3'), timeout=30)
        db.row_factory = _contained_0001_sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT v FROM control WHERE k=?', (key,)).fetchone()
            return _contained_0001_json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO control VALUES (?,?)', (key, _contained_0001_json.dumps(value, ensure_ascii=False)))

    def event(self, kind, detail):
        with self.db() as db:
            db.execute('INSERT INTO events(created,kind,detail) VALUES (?,?,?)', (_contained_0001_time.time(), kind, _contained_0001_json.dumps(detail, ensure_ascii=False)))

    def update(self, id, **values):
        values['updated'] = _contained_0001_time.time()
        if not set(values) <= {'status', 'staged', 'run_id', 'error', 'updated'}:
            raise ValueError('Invalid queue fields')
        with self.db() as db:
            db.execute('UPDATE sources SET ' + ','.join((k + '=?' for k in values)) + ' WHERE id=?', (*values.values(), id))

    def known_hashes(self):
        output = self.root / self.config['output_root'] / 'data_action_chain_pipeline'
        known = set()
        for path in output.glob('*/ */completion_manifest.json'.replace(' ', '')):
            try:
                item = _contained_0001_json.loads(path.read_text(encoding='utf-8'))
                journal = _contained_0001_json.loads((path.parent / 'transaction_journal.json').read_text(encoding='utf-8'))
                if item.get('status') != 'completed' or journal.get('state') != 'COMMITTED':
                    continue
                returned = _contained_0001_json.loads(_contained_0001_Path(item['action_return_manifest']).read_text(encoding='utf-8'))
                if returned.get('status') == 'ready_for_data_discovery':
                    known.add(returned['source']['sha256'])
            except (OSError, ValueError, KeyError):
                continue
        for file in (self.directory/'completions').glob('*.json'):
            try:
                receipt=json.loads(file.read_text('utf-8'))
                if receipt.get('status')=='completed':known.add(receipt['binding']['sha256'])
            except (OSError,ValueError,KeyError):continue
        with self.db() as db:
            known.update((row[0] for row in db.execute("SELECT sha FROM sources WHERE status='completed'")))
        return known

    def scan(self, folder):
        root = _contained_0001_Path(folder).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('指定路径不是文件夹')
        known = self.known_hashes()
        count = 0
        self.set('scan', {'status': 'running', 'folder': str(root), 'files': 0})
        try:
            for parent, dirs, files in _contained_0001_os.walk(root, followlinks=False):
                dirs[:] = [name for name in dirs if name not in {'.git', '.venv', '.python', 'node_modules', 'runtime', 'data_runtime', 'outputs', '.deployment_backups'} and (not _contained_0001_Path(parent, name).is_symlink()) and (not _contained_0001_Path(parent, name).is_junction())]
                for name in files:
                    path = _contained_0001_Path(parent, name)
                    if path.suffix.lower() != '.json' or path.is_symlink():
                        continue
                    count += 1
                    if count > self.config['scan_max_files']:
                        raise ValueError('扫描文件数量超过配置上限，请选择更小的目录')
                    before = path.stat()
                    checksum = _contained_0001_sha(path)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError(f'扫描期间文件发生变化: {path}')
                    identifier = _contained_0001_hashlib.sha256((str(path.resolve()) + '\x00' + checksum).encode()).hexdigest()[:32]
                    status = 'completed' if checksum in known else 'discovered'
                    with self.db() as db:
                        db.execute('INSERT OR IGNORE INTO sources(id,path,sha,size,status,updated) VALUES (?,?,?,?,?,?)', (identifier, str(path.resolve()), checksum, after.st_size, status, _contained_0001_time.time()))
                    self.set('scan', {'status': 'running', 'folder': str(root), 'files': count})
            self.set('scan', {'status': 'completed', 'folder': str(root), 'files': count})
            self.event('scan_completed', {'folder': str(root), 'files': count})
        except Exception as exc:
            self.set('scan', {'status': 'failed', 'folder': str(root), 'files': count, 'error': str(exc)})
            raise

    def enqueue(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any((not isinstance(x, str) for x in ids)):
            raise ValueError('请选择 1 至 200 个来源')
        with self.db() as db:
            rows = [db.execute('SELECT status FROM sources WHERE id=?', (id,)).fetchone() for id in ids]
            if any((row is None or row[0] not in ('discovered', 'failed', 'interrupted', 'queued') for row in rows)):
                raise ValueError('来源不存在、已完成或正在处理')
            for id in ids:
                db.execute("UPDATE sources SET status='queued',error=NULL,updated=? WHERE id=?", (_contained_0001_time.time(), id))
        self.event('enqueued', {'ids': ids})
        return self.status()

    def spawn(self, mode, folder=None, vector='none'):
        if mode not in ('scan', 'run') or vector not in ('none', 'both'):
            raise ValueError('Invalid worker mode')
        args = [_contained_0001_sys.executable, '-B', str(_contained_0001_Path(__file__).resolve()), mode, '--config', str(self.config_path)]
        if mode == 'scan':
            if not isinstance(folder, str) or not folder.strip():
                raise ValueError('请输入扫描目录')
            args += ['--folder', folder]
        else:
            args += ['--vector-target', vector]
            self.set('pause', False)
        with (self.directory / (mode + '.log')).open('ab') as log:
            process = _contained_0001_subprocess.Popen(args, cwd=self.root, stdin=_contained_0001_subprocess.DEVNULL, stdout=log, stderr=log, creationflags=_contained_0001_subprocess.CREATE_NO_WINDOW if _contained_0001_os.name == 'nt' else 0)
        self.event('worker_started', {'mode': mode, 'pid': process.pid})
        return {'status': 'started', 'pid': process.pid}

    def arguments(self, source, run_id, vector):
        config = self.config
        args = ['--project-root', str(self.root), '--data-root', config['data_root'], '--target', source, '--asset-version', 'v0001', '--data-table', 'data_text_units', '--run-id', run_id, '--derivation-target', 'duckdb', '--vector-target', vector, '--retention-mode', 'keep', '--confirm-database-write', '--confirm-execution', '--step-timeout', str(config['step_timeout'])]
        for key in ('data_db', 'action_db', 'duckdb_path', 'sql_writer_config', 'output_root', 'embed_config', 'chromadb_path', 'vector_state_index', 'vector_replaced_archive'):
            args += ['--' + key.replace('_', '-'), config[key]]
        for path in config['semantic_paths']:
            args += ['--semantic-path', path]
        if vector != 'none':
            args.append('--confirm-model-api')
        return args

    def recover(self, row, pipeline):
        if not row['run_id']:
            return False
        run = self.root / self.config['output_root'] / 'data_action_chain_pipeline' / 'v0020' / row['run_id']
        journal = run / 'transaction_journal.json'
        if not journal.exists():
            return False
        data = _contained_0001_json.loads(journal.read_text(encoding='utf-8'))
        if data['state'] == 'COMMITTED':
            completion = _contained_0001_json.loads((run / 'completion_manifest.json').read_text(encoding='utf-8'))
            if completion['status'] != 'completed':
                raise ValueError('Committed transaction lacks complete receipt')
            self.update(row['id'], status='completed', error=None)
            return True
        if data['state'] in ('PENDING', 'PREPARING', 'ROLLING_BACK', 'ROLLBACK_FAILED'):
            pipeline.recover_interrupted_transaction(journal_path=journal, project_root=self.root)
            self.event('transaction_recovered', {'run_id': row['run_id']})
        elif data['state'] not in ('ROLLED_BACK', 'PREPARE_FAILED'):
            raise ValueError('Unknown transaction state; operator inspection required')
        return False

    def run(self, vector='none'):
        with _contained_0001_exclusive_lease(self.directory / 'runner.lease'):
            pipeline = _contained_0001_load_pipeline(self.root)
            self.set('worker', {'status': 'running', 'pid': _contained_0001_os.getpid(), 'vector_target': vector})
            try:
                with self.db() as db:
                    db.execute("UPDATE sources SET status='interrupted' WHERE status='running'")
                while not self.setting('pause', False):
                    with self.db() as db:
                        row = db.execute("SELECT * FROM sources WHERE status IN ('queued','interrupted') ORDER BY updated LIMIT 1").fetchone()
                    if row is None:
                        break
                    try:
                        if self.recover(row, pipeline):
                            continue
                        if row['sha'] in self.known_hashes():
                            self.update(row['id'], status='completed', error=None)
                            continue
                        original = _contained_0001_Path(row['path'])
                        if _contained_0001_sha(original) != row['sha']:
                            raise ValueError('来源已变化，请重新扫描；不会处理过期选择')
                        staged = self.root / self.config['data_root'] / 'data_raw' / 'scanned' / row['sha'] / original.name
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        if not staged.exists():
                            temporary = staged.with_name(staged.name + '.' + _contained_0001_uuid.uuid4().hex + '.partial')
                            _contained_0001_shutil.copyfile(original, temporary)
                            if _contained_0001_sha(temporary) != row['sha']:
                                raise ValueError('来源复制校验失败')
                            temporary.replace(staged)
                        if _contained_0001_sha(staged) != row['sha']:
                            raise ValueError('已追加原始副本校验失败')
                        run_id = 'q' + _contained_0001_uuid.uuid4().hex[:20]
                        args = self.arguments(str(staged.relative_to(self.root / self.config['data_root'])), run_id, vector)
                        options = pipeline.build_parser().parse_args(args)
                        binding = pipeline.retention_binding(options)
                        from intermediate_retention_contract_v0001 import seal, write, now
                        receipt = self.directory / (run_id + '_receipt.json')
                        write(receipt, seal({'kind': 'receipt', 'binding_sha256': binding['contract_sha256'], 'run_id': run_id, 'mode': 'keep', 'destination': '', 'confirmed_at': now(), 'confirmed': True}))
                        self.event('source_binding', {'id': row['id'], 'original': str(original), 'sha256': row['sha'], 'staged': str(staged), 'run_id': run_id})
                        self.update(row['id'], status='running', run_id=run_id, staged=str(staged))
                        args += ['--retention-receipt', str(receipt)]
                        with (self.directory / (run_id + '.stdout.json')).open('wb') as out, (self.directory / (run_id + '.stderr.log')).open('wb') as err:
                            code = _contained_0001_run_owned([_contained_0001_sys.executable, '-B', str(self.root / 'scripts/orchestration/action/data_action_chain_pipeline_v0020.py'), *args], cwd=self.root, stdin=_contained_0001_subprocess.DEVNULL, stdout=out, stderr=err).returncode
                        if code != 0:
                            raise ValueError(f'编排失败，退出码 {code}，详见 {run_id}.stderr.log')
                        with self.db() as db:
                            latest = db.execute('SELECT * FROM sources WHERE id=?', (row['id'],)).fetchone()
                        if not self.recover(latest, pipeline):
                            raise ValueError('未找到已提交事务凭证')
                        self.event('completed', {'id': row['id'], 'run_id': run_id})
                    except Exception as exc:
                        self.update(row['id'], status='failed', error=str(exc))
                        self.event('failed', {'id': row['id'], 'error': str(exc)})
                        self.set('worker', {'status': 'failed', 'error': str(exc)})
                        return
                self.set('worker', {'status': 'paused' if self.setting('pause', False) else 'idle'})
            finally:
                self.set('child', None)

    def status(self):
        with self.db() as db:
            sources = [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY status='completed',updated DESC LIMIT 200")]
            counts = {row[0]: row[1] for row in db.execute('SELECT status,COUNT(*) FROM sources GROUP BY status')}
            events = [dict(row) for row in db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 15')]
        progress = None
        for row in sources:
            if row['status'] == 'running' and row['run_id']:
                manifest = self.root / self.config['output_root'] / 'data_action_chain_pipeline' / 'v0020' / row['run_id'] / 'run_manifest.json'
                if manifest.exists():
                    try:
                        data = _contained_0001_json.loads(manifest.read_text(encoding='utf-8'))
                        progress = {'run_id': row['run_id'], 'status': data['status'], 'completed_steps': len(data.get('steps', [])), 'last_step': data.get('steps', [{}])[-1] if data.get('steps') else None, 'transaction': data.get('transaction')}
                    except (OSError, ValueError):
                        pass
                break
        return {'status': 'ok', 'version': 'v0020', 'sources': sources, 'counts': counts, 'events': events, 'worker': self.setting('worker', {'status': 'idle'}), 'scan': self.setting('scan', {'status': 'idle'}), 'pause_requested': self.setting('pause', False), 'progress': progress, 'limit': 200}

def _contained_0001_main():
    parser = _contained_0001_argparse.ArgumentParser()
    parser.add_argument('mode', choices=('scan', 'run', 'status'))
    parser.add_argument('--config', type=_contained_0001_Path, default=_contained_0001_ROOT / _contained_0001_DEFAULT_CONFIG)
    parser.add_argument('--folder')
    parser.add_argument('--vector-target', choices=('none', 'both'), default='none')
    args = parser.parse_args()
    jobs = _contained_0001_Jobs(config_path=args.config)
    if args.mode == 'scan':
        with _contained_0001_exclusive_lease(jobs.directory / 'scanner.lease'):
            jobs.scan(args.folder)
    elif args.mode == 'run':
        jobs.run(args.vector_target)
    else:
        print(_contained_0001_json.dumps(jobs.status(), ensure_ascii=False))

BaseJobs = _contained_0001_Jobs
exclusive_lease = _contained_0001_exclusive_lease
ROOT = _contained_0001_ROOT
sha = _contained_0001_sha
from owned_process_v0001 import run as run_owned

SCRIPT_NAME='ingestion_jobs_v0004.py'
SCRIPT_VERSION='v0004'
DEFAULT_ENCODING='utf-8'
SCRIPT_FAMILY='ingestion_jobs'
DEFAULT_CONFIG='config/action/config/concurrent_pipeline_v0001.json'

def store_request(config,directory,path,body=None):
    request=urllib.request.Request(f"http://127.0.0.1:{config['service_port']}/{path}",
        data=None if body is None else json.dumps(body).encode(),headers={} if body is None else {
            'Content-Type':'application/json','Authorization':'Bearer '+(directory/'service_token').read_text(encoding="utf-8").strip()})
    with urllib.request.urlopen(request,timeout=2) as response:return json.load(response)

def alive(pid):
    if not isinstance(pid,int):return False
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    try:
        code=ctypes.c_ulong();kernel.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code));return code.value==259
    finally:kernel.CloseHandle(ctypes.c_void_p(handle))

class Jobs(BaseJobs):
    def __init__(self,root=ROOT,config_path=None):super().__init__(root,config_path or Path(root)/DEFAULT_CONFIG)

    def ensure_store(self):
        try:return store_request(self.config,self.directory,'status')
        except (OSError,ValueError):pass
        with exclusive_lease(self.directory/'store_start.lease'):
            try:return store_request(self.config,self.directory,'status')
            except (OSError,ValueError):pass
            with (self.directory/'store_service.log').open('ab') as log:
                process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('concurrent_store_service_v0002.py')),'--config',str(self.config_path)],cwd=self.root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            self.set('store_process',{'pid':process.pid,'started_at':time.time()})
            return {'status':'starting','pid':process.pid}

    def vectors(self,enabled):
        self.ensure_store()
        deadline=time.monotonic()+10
        while True:
            try:return store_request(self.config,self.directory,'vectors',{'enabled':enabled})
            except OSError as exc:
                if time.monotonic()>=deadline:raise ValueError('后台服务启动失败，请查看 store_service.log') from exc
                time.sleep(.2)

    def spawn(self,mode,folder=None,vector='none'):
        if mode not in ('scan','run'):raise ValueError('Invalid worker mode')
        lease='scanner.lease' if mode=='scan' else 'runner.lease'
        with exclusive_lease(self.directory/(mode+'_start.lease')):
            prior=self.setting(mode+'_process',{})
            if alive(prior.get('pid')):return {'status':'already_running','pid':prior['pid']}
            if mode=='run':
                self.ensure_store();self.set('pause',False)
            args=[sys.executable,'-B',str(Path(__file__).resolve()),mode,'--config',str(self.config_path)]
            if mode=='scan':
                if not folder or not Path(folder).is_dir():raise ValueError('请选择存在的文件夹')
                args+=['--folder',folder]
            with (self.directory/(mode+'.log')).open('ab') as log:
                process=subprocess.Popen(args,cwd=self.root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            self.set(mode+'_process',{'pid':process.pid,'started_at':time.time()})
            self.event('worker_started',{'mode':mode,'pid':process.pid,'vectors_independent':True})
            return {'status':'started','pid':process.pid}

    def run(self,vector='none'):
        with exclusive_lease(self.directory/'runner.lease'):
            self.set('worker',{'status':'running','pid':os.getpid(),'version':'v0021'})
            with self.db() as db:db.execute("UPDATE sources SET status='interrupted' WHERE status='running'")
            while not self.setting('pause',False):
                with self.db() as db:row=db.execute("SELECT * FROM sources WHERE status IN ('queued','interrupted') ORDER BY updated LIMIT 1").fetchone()
                if row is None:
                    self.set('worker',{'status':'idle','pid':os.getpid(),'version':'v0021'})
                    time.sleep(1)
                    continue
                try:
                    self.set('worker',{'status':'running','pid':os.getpid(),'version':'v0021'})
                    if row['sha'] in self.known_hashes():
                        self.update(row['id'],status='completed',error=None)
                        self.event('already_ingested',{'id':row['id'],'sha256':row['sha']})
                        continue
                    if sha(row['path'])!=row['sha']:raise ValueError('原始文件已变化，请重新扫描')
                    run_id=row['run_id'] or ('q'+uuid.uuid4().hex[:20])
                    self.update(row['id'],status='running',run_id=run_id,error=None)
                    script=self.root/'scripts/orchestration/action/data_action_chain_pipeline_v0021.py'
                    with (self.directory/(run_id+'.stdout')).open('ab') as out,(self.directory/(run_id+'.stderr')).open('ab') as err:
                        result=run_owned([sys.executable,'-B',str(script),'--config',str(self.config_path),'--source',row['path'],'--run-id',run_id],cwd=self.root,stdout=out,stderr=err)
                    if result.returncode:raise ValueError(f'摄取失败，可恢复同一任务，日志 {run_id}.stderr')
                    completion=self.directory/'completions'/(run_id+'.json')
                    if not completion.exists():completion=self.root/'temp/c'/hashlib.sha256(run_id.encode()).hexdigest()[:10]/'completion.json'
                    receipt=json.loads(completion.read_text(encoding='utf-8'))
                    if receipt['status']!='completed' or receipt['binding']['sha256']!=row['sha']:raise ValueError('提交凭证与来源不一致')
                    self.update(row['id'],status='completed',error=None)
                    self.event('sql_committed',{'id':row['id'],'run_id':run_id,'receipt':str(completion),'sha256':sha(completion)})
                except Exception as exc:
                    self.update(row['id'],status='failed',error=str(exc));self.set('worker',{'status':'failed','error':str(exc)});return
            self.set('worker',{'status':'paused' if self.setting('pause',False) else 'idle'})

    def status(self):
        value=super().status();value['version']='v0021';value['controller_version']=SCRIPT_VERSION;value['progress']=None
        worker=value['worker']
        if worker.get('status')=='running' and not alive(worker.get('pid')):
            value['worker']={**worker,'status':'interrupted'}
        for row in value['sources']:
            if row['status']=='running' and row['run_id']:
                stage=self.root/'temp/c'/hashlib.sha256(row['run_id'].encode()).hexdigest()[:10]
                pub=stage/'publication.json'
                if pub.exists():value['progress']={'run_id':row['run_id'],'phase':'增量发布','publication':json.loads(pub.read_text(encoding='utf-8'))}
                else:value['progress']={'run_id':row['run_id'],'phase':'隔离解析与校验'}
                break
        try:value['stores']=store_request(self.config,self.directory,'status')
        except (OSError,ValueError):
            value['stores']={'status':'offline','vector':{'status':'offline'},'duckdb':{'status':'offline'}}
            ledger=self.directory/'derived.sqlite3'
            if ledger.exists():
                import sqlite3
                db=sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True,timeout=2)
                try:
                    row=db.execute("SELECT v FROM control WHERE k='vector_error'").fetchone()
                    if row:
                        error=json.loads(row[0]);value['stores']['vector']={'status':'failed','error':error['error']+'；请点击启动 / 继续本地向量计算，重启后台恢复。'}
                    value['stores']['indexed_vectors']=db.execute('SELECT COUNT(*) FROM vector_index').fetchone()[0]
                finally:db.close()
        return value

def dispatch(root, request, response, dry_run=False):
    root = Path(root).resolve()
    request, response = Path(request).resolve(), Path(response).resolve()
    request.relative_to(root / 'runtime/intermediate_tasks')
    response.relative_to(request.parent)
    if request.is_symlink() or response.is_symlink() or request.stat().st_size > 262144:
        raise ValueError('handoff_path_invalid')
    raw = request.read_bytes()
    value = json.loads(raw)
    if value.get('schema_version') != 'backend.jobs.v1' or value.get('run_id') != request.parent.name or value.get('root') != str(root):
        raise ValueError('jobs_contract_invalid')
    if value.get('producer') != {'script': 'workbench_runtime_control_v0002', 'version': 'v0002'}:
        raise ValueError('jobs_producer_invalid')
    operation, params = value.get('operation'), value.get('params', {})
    if operation not in {'scan', 'enqueue', 'start', 'pause', 'stores_start'} or not isinstance(params, dict):
        raise ValueError('jobs_operation_invalid')
    if operation == 'scan' and (not isinstance(params.get('folder'), str) or not Path(params['folder']).is_dir()):
        raise ValueError('scan_directory_invalid')
    if operation == 'enqueue' and (not isinstance(params.get('ids'), list) or not 1 <= len(params['ids']) <= 200 or any(not isinstance(x, str) for x in params['ids'])):
        raise ValueError('queue_selection_invalid')
    if operation == 'start' and params.get('confirm_write') is not True:
        raise ValueError('database_write_confirmation_required')
    config = root / DEFAULT_CONFIG
    settings = json.loads(config.read_text(DEFAULT_ENCODING))
    for key in ('state_dir', 'data_db', 'action_db', 'data_root', 'output_root'):
        (root / settings[key]).resolve().relative_to(root)
    if dry_run:
        return {'status': 'dry_run', 'operation': operation}
    jobs = Jobs(root=root, config_path=config)
    if operation == 'scan':
        result = jobs.spawn('scan', folder=params['folder'])
    elif operation == 'enqueue':
        jobs.enqueue(params['ids'])
        result = {'status': 'queued', 'selected': len(set(params['ids']))}
    elif operation == 'start':
        result = jobs.spawn('run', vector='none')
    elif operation == 'pause':
        jobs.set('pause', True)
        result = {'status': 'pause_requested', 'finish_current_source': True}
    else:
        result = jobs.ensure_store()
    envelope = {'schema_version': 'backend.jobs.v1', 'run_id': value['run_id'], 'producer': {'script': 'ingestion_jobs_v0004', 'version': SCRIPT_VERSION},
                'source_sha256': hashlib.sha256(raw).hexdigest(), 'status': 'completed', 'result': result}
    temporary = response.with_suffix('.tmp')
    temporary.write_text(json.dumps(envelope, ensure_ascii=False), DEFAULT_ENCODING)
    temporary.replace(response)
    return {'status': 'completed', 'operation': operation}


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('scan','run','status','dispatch'));p.add_argument('--config');p.add_argument('--folder')
    p.add_argument('--root', default=str(ROOT));p.add_argument('--request');p.add_argument('--response');p.add_argument('--dry-run', action='store_true')
    a=p.parse_args()
    try:
        if a.mode == 'dispatch':
            result = dispatch(a.root, a.request, a.response, a.dry_run)
            print(json.dumps(result, ensure_ascii=False)); return 0
        jobs=Jobs(root=Path(a.root),config_path=a.config)
        if a.mode=='run':jobs.run()
        elif a.mode=='scan':
            with exclusive_lease(jobs.directory/'scanner.lease'):jobs.scan(a.folder)
        else:print(json.dumps(jobs.status(),ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}, ensure_ascii=False)); return 2

if __name__=='__main__':raise SystemExit(main())
