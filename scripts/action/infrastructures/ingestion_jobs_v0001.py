# ============================================================
# 文件名: ingestion_jobs_v0001.py
# 中文名: Data Action 持久任务队列
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / job_queue
# 脚本定位: 扫描、排队、恢复与状态展示；业务交由 v0020
# 职责说明: 串行执行已选择的新来源，保留内容身份及事务证据
# 本脚本做什么: 哈希扫描、追加原始副本、独立后台进程、检查已提交结果
# 本脚本不做什么: 不自行解析入库、不删除原始资料、不并发写业务库
# 制度边界声明: 失败不标记完成；恢复前核对事务；配置不含密钥
# 可更新: True
# ALIAS_META
# alias: ingestion_jobs_v0001
# family: ingestion_jobs
# role: durable_queue
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/ingestion_jobs_v0001.py
# input: selected local folder and source IDs
# output: queue SQLite, source provenance, v0020 receipts and status
# depends_on: Python stdlib, data_action_chain_pipeline_v0020
# used_by: action_chat_server_v0008
# ============================================================
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from owned_process_v0001 import run as run_owned

DEFAULT_ENCODING='utf-8'
SCRIPT_FAMILY='ingestion_jobs'
SCRIPT_NAME='ingestion_jobs_v0001.py'
SCRIPT_VERSION='v0001'
ROOT=Path(__file__).resolve().parents[3]
DEFAULT_CONFIG='config/action/config/ingestion_jobs_v0001.json'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def load_pipeline(root=ROOT):
    path=root/'scripts/orchestration/action/data_action_chain_pipeline_v0020.py'
    spec=importlib.util.spec_from_file_location('queued_data_action_v0020',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


@contextmanager
def exclusive_lease(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


class Jobs:
    def __init__(self, root=ROOT, config_path=None):
        self.root=Path(root).resolve()
        self.config_path=Path(config_path or self.root/DEFAULT_CONFIG).resolve()
        self.config=json.loads(self.config_path.read_text(encoding=DEFAULT_ENCODING))
        self.directory=(self.root/self.config['state_dir']).resolve()
        self.directory.relative_to(self.root)
        self.directory.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,path TEXT NOT NULL,sha TEXT NOT NULL,size INTEGER,status TEXT NOT NULL,staged TEXT,run_id TEXT,error TEXT,updated REAL,UNIQUE(path,sha));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,created REAL,kind TEXT,detail TEXT);
            CREATE TABLE IF NOT EXISTS control(k TEXT PRIMARY KEY,v TEXT);
            ''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(str(self.directory/'queue.sqlite3'),timeout=30)
        db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self,key,default=None):
        with self.db() as db:
            row=db.execute('SELECT v FROM control WHERE k=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self,key,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO control VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))

    def event(self,kind,detail):
        with self.db() as db:db.execute('INSERT INTO events(created,kind,detail) VALUES (?,?,?)',(time.time(),kind,json.dumps(detail,ensure_ascii=False)))

    def update(self,id,**values):
        values['updated']=time.time()
        if not set(values)<={'status','staged','run_id','error','updated'}:raise ValueError('Invalid queue fields')
        with self.db() as db:db.execute('UPDATE sources SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),id))

    def known_hashes(self):
        # Historical manifests remain the authority; failed/rolled-back runs are never imported as done.
        output=self.root/self.config['output_root']/'data_action_chain_pipeline'
        known=set()
        for path in output.glob('*/ */completion_manifest.json'.replace(' ','')):
            try:
                item=json.loads(path.read_text(encoding='utf-8'))
                journal=json.loads((path.parent/'transaction_journal.json').read_text(encoding='utf-8'))
                if item.get('status')!='completed' or journal.get('state')!='COMMITTED':continue
                returned=json.loads(Path(item['action_return_manifest']).read_text(encoding='utf-8'))
                if returned.get('status')=='ready_for_data_discovery':known.add(returned['source']['sha256'])
            except (OSError,ValueError,KeyError):continue
        with self.db() as db:
            known.update(row[0] for row in db.execute("SELECT sha FROM sources WHERE status='completed'"))
        return known

    def scan(self,folder):
        root=Path(folder).expanduser().resolve(strict=True)
        if not root.is_dir():raise ValueError('指定路径不是文件夹')
        known=self.known_hashes();count=0
        self.set('scan',{'status':'running','folder':str(root),'files':0})
        try:
            for parent,dirs,files in os.walk(root,followlinks=False):
                dirs[:]=[name for name in dirs if name not in {'.git','.venv','.python','node_modules','runtime','data_runtime','outputs','.deployment_backups'} and not Path(parent,name).is_symlink() and not Path(parent,name).is_junction()]
                for name in files:
                    path=Path(parent,name)
                    if path.suffix.lower()!='.json' or path.is_symlink():continue
                    count+=1
                    if count>self.config['scan_max_files']:raise ValueError('扫描文件数量超过配置上限，请选择更小的目录')
                    before=path.stat();checksum=sha(path);after=path.stat()
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError(f'扫描期间文件发生变化: {path}')
                    identifier=hashlib.sha256((str(path.resolve())+'\0'+checksum).encode()).hexdigest()[:32]
                    status='completed' if checksum in known else 'discovered'
                    with self.db() as db:
                        db.execute('INSERT OR IGNORE INTO sources(id,path,sha,size,status,updated) VALUES (?,?,?,?,?,?)',
                                   (identifier,str(path.resolve()),checksum,after.st_size,status,time.time()))
                    self.set('scan',{'status':'running','folder':str(root),'files':count})
            self.set('scan',{'status':'completed','folder':str(root),'files':count})
            self.event('scan_completed',{'folder':str(root),'files':count})
        except Exception as exc:
            self.set('scan',{'status':'failed','folder':str(root),'files':count,'error':str(exc)})
            raise

    def enqueue(self,ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=200 or any(not isinstance(x,str) for x in ids):raise ValueError('请选择 1 至 200 个来源')
        with self.db() as db:
            rows=[db.execute('SELECT status FROM sources WHERE id=?',(id,)).fetchone() for id in ids]
            if any(row is None or row[0] not in ('discovered','failed','interrupted','queued') for row in rows):raise ValueError('来源不存在、已完成或正在处理')
            for id in ids:db.execute("UPDATE sources SET status='queued',error=NULL,updated=? WHERE id=?",(time.time(),id))
        self.event('enqueued',{'ids':ids})
        return self.status()

    def spawn(self,mode,folder=None,vector='none'):
        if mode not in ('scan','run') or vector not in ('none','both'):raise ValueError('Invalid worker mode')
        args=[sys.executable,'-B',str(Path(__file__).resolve()),mode,'--config',str(self.config_path)]
        if mode=='scan':
            if not isinstance(folder,str) or not folder.strip():raise ValueError('请输入扫描目录')
            args+=['--folder',folder]
        else:
            args+=['--vector-target',vector]
            self.set('pause',False)
        with (self.directory/(mode+'.log')).open('ab') as log:
            process=subprocess.Popen(args,cwd=self.root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.event('worker_started',{'mode':mode,'pid':process.pid})
        return {'status':'started','pid':process.pid}

    def arguments(self,source,run_id,vector):
        config=self.config
        args=['--project-root',str(self.root),'--data-root',config['data_root'],'--target',source,
              '--asset-version','v0001','--data-table','data_text_units','--run-id',run_id,
              '--derivation-target','duckdb','--vector-target',vector,'--retention-mode','keep',
              '--confirm-database-write','--confirm-execution','--step-timeout',str(config['step_timeout'])]
        for key in ('data_db','action_db','duckdb_path','sql_writer_config','output_root','embed_config','chromadb_path','vector_state_index','vector_replaced_archive'):
            args+=['--'+key.replace('_','-'),config[key]]
        for path in config['semantic_paths']:args+=['--semantic-path',path]
        if vector!='none':args.append('--confirm-model-api')
        return args

    def recover(self,row,pipeline):
        if not row['run_id']:return False
        run=self.root/self.config['output_root']/'data_action_chain_pipeline'/'v0020'/row['run_id']
        journal=run/'transaction_journal.json'
        if not journal.exists():return False
        data=json.loads(journal.read_text(encoding='utf-8'))
        if data['state']=='COMMITTED':
            completion=json.loads((run/'completion_manifest.json').read_text(encoding='utf-8'))
            if completion['status']!='completed':raise ValueError('Committed transaction lacks complete receipt')
            self.update(row['id'],status='completed',error=None)
            return True
        if data['state'] in ('PENDING','PREPARING','ROLLING_BACK','ROLLBACK_FAILED'):
            pipeline.recover_interrupted_transaction(journal_path=journal,project_root=self.root)
            self.event('transaction_recovered',{'run_id':row['run_id']})
        elif data['state'] not in ('ROLLED_BACK','PREPARE_FAILED'):
            raise ValueError('Unknown transaction state; operator inspection required')
        return False

    def run(self,vector='none'):
        with exclusive_lease(self.directory/'runner.lease'):
            # A dead worker can leave a running item. The lease prevents two coordinators.
            pipeline=load_pipeline(self.root)
            self.set('worker',{'status':'running','pid':os.getpid(),'vector_target':vector})
            try:
                with self.db() as db:db.execute("UPDATE sources SET status='interrupted' WHERE status='running'")
                while not self.setting('pause',False):
                    with self.db() as db:row=db.execute("SELECT * FROM sources WHERE status IN ('queued','interrupted') ORDER BY updated LIMIT 1").fetchone()
                    if row is None:break
                    try:
                        if self.recover(row,pipeline):continue
                        if row['sha'] in self.known_hashes():
                            self.update(row['id'],status='completed',error=None);continue
                        original=Path(row['path'])
                        if sha(original)!=row['sha']:raise ValueError('来源已变化，请重新扫描；不会处理过期选择')
                        staged=self.root/self.config['data_root']/'data_raw'/'scanned'/row['sha']/original.name
                        staged.parent.mkdir(parents=True,exist_ok=True)
                        if not staged.exists():
                            temporary=staged.with_name(staged.name+'.'+uuid.uuid4().hex+'.partial')
                            shutil.copyfile(original,temporary)
                            if sha(temporary)!=row['sha']:raise ValueError('来源复制校验失败')
                            temporary.replace(staged)
                        if sha(staged)!=row['sha']:raise ValueError('已追加原始副本校验失败')
                        run_id='q'+uuid.uuid4().hex[:20]
                        args=self.arguments(str(staged.relative_to(self.root/self.config['data_root'])),run_id,vector)
                        options=pipeline.build_parser().parse_args(args)
                        binding=pipeline.retention_binding(options)
                        from intermediate_retention_contract_v0001 import seal,write,now
                        receipt=self.directory/(run_id+'_receipt.json')
                        write(receipt,seal({'kind':'receipt','binding_sha256':binding['contract_sha256'],'run_id':run_id,
                            'mode':'keep','destination':'','confirmed_at':now(),'confirmed':True}))
                        self.event('source_binding',{'id':row['id'],'original':str(original),'sha256':row['sha'],'staged':str(staged),'run_id':run_id})
                        self.update(row['id'],status='running',run_id=run_id,staged=str(staged))
                        args+=['--retention-receipt',str(receipt)]
                        # The pipeline handles its own transaction and rollback; output stays on disk.
                        with (self.directory/(run_id+'.stdout.json')).open('wb') as out,(self.directory/(run_id+'.stderr.log')).open('wb') as err:
                            code=run_owned([sys.executable,'-B',str(self.root/'scripts/orchestration/action/data_action_chain_pipeline_v0020.py'),*args],
                                cwd=self.root,stdin=subprocess.DEVNULL,stdout=out,stderr=err).returncode
                        if code!=0:raise ValueError(f'编排失败，退出码 {code}，详见 {run_id}.stderr.log')
                        with self.db() as db:latest=db.execute('SELECT * FROM sources WHERE id=?',(row['id'],)).fetchone()
                        if not self.recover(latest,pipeline):raise ValueError('未找到已提交事务凭证')
                        self.event('completed',{'id':row['id'],'run_id':run_id})
                    except Exception as exc:
                        self.update(row['id'],status='failed',error=str(exc))
                        self.event('failed',{'id':row['id'],'error':str(exc)})
                        self.set('worker',{'status':'failed','error':str(exc)})
                        return
                self.set('worker',{'status':'paused' if self.setting('pause',False) else 'idle'})
            finally:
                self.set('child',None)

    def status(self):
        with self.db() as db:
            sources=[dict(row) for row in db.execute("SELECT * FROM sources ORDER BY status='completed',updated DESC LIMIT 200")]
            counts={row[0]:row[1] for row in db.execute('SELECT status,COUNT(*) FROM sources GROUP BY status')}
            events=[dict(row) for row in db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 15')]
        progress=None
        for row in sources:
            if row['status']=='running' and row['run_id']:
                manifest=self.root/self.config['output_root']/'data_action_chain_pipeline'/'v0020'/row['run_id']/'run_manifest.json'
                if manifest.exists():
                    try:
                        data=json.loads(manifest.read_text(encoding='utf-8'))
                        progress={'run_id':row['run_id'],'status':data['status'],'completed_steps':len(data.get('steps',[])),
                                  'last_step':data.get('steps',[{}])[-1] if data.get('steps') else None,'transaction':data.get('transaction')}
                    except (OSError,ValueError):pass
                break
        return {'status':'ok','version':'v0020','sources':sources,'counts':counts,'events':events,
                'worker':self.setting('worker',{'status':'idle'}),'scan':self.setting('scan',{'status':'idle'}),
                'pause_requested':self.setting('pause',False),'progress':progress,'limit':200}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('scan','run','status'))
    parser.add_argument('--config',type=Path,default=ROOT/DEFAULT_CONFIG)
    parser.add_argument('--folder')
    parser.add_argument('--vector-target',choices=('none','both'),default='none')
    args=parser.parse_args();jobs=Jobs(config_path=args.config)
    if args.mode=='scan':
        with exclusive_lease(jobs.directory/'scanner.lease'):jobs.scan(args.folder)
    elif args.mode=='run':jobs.run(args.vector_target)
    else:print(json.dumps(jobs.status(),ensure_ascii=False))


if __name__=='__main__':
    main()
