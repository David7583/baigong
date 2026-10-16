# ============================================================
# 文件名: data_action_chain_pipeline_v0021.py
# 中文名: 隔离计算与增量发布总编排
# 版本号: v0021
# 主层级: action
# 层级: orchestration / concurrent_ingestion
# 脚本定位: 独立准备契约的隔离执行及正式库追加发布
# 职责说明: 避免生产整库快照回退与向量计算长事务
# 本脚本做什么: 持久绑定、阶段恢复、有限批次发布与逐行冲突校验
# 本脚本不做什么: 不修改既有事实、不删除原始文件、不等待向量计算
# 制度边界声明: 每批完整提交；中断重放核验已有事实；派生完成度单独展示
# 可更新: True
# ALIAS_META
# alias: data_action_chain_pipeline_v0021
# family: data_action_chain_pipeline
# role: isolated_ingestion_publisher
# version: v0021
# status: active
# entry_point: scripts/orchestration/action/data_action_chain_pipeline_v0021.py
# input: explicit source, run ID and local runtime config
# output: staged validated chain, append-only SQL publication receipts
# depends_on: isolated_data_preparation_v0001 JSON file contract, Python stdlib
# used_by: ingestion_jobs_v0002
# ============================================================
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/action/infrastructures'))
import ctypes
import subprocess
from ctypes import wintypes
from contextlib import contextmanager



# Local encoding/validation helpers; no imported business implementation.
class RetentionError(RuntimeError):
    pass

def seal(payload):
    result={**payload,'schema_version':'intermediate_retention_v0001'}
    result.pop('contract_sha256',None)
    raw=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    result['contract_sha256']=hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return result

def verify(payload,kind):
    if payload.get('schema_version')!='intermediate_retention_v0001' or payload.get('kind')!=kind or seal(payload)['contract_sha256']!=payload.get('contract_sha256'):
        raise RetentionError('Changed or invalid retention contract')
    return payload

def load(path):
    value=json.loads(Path(path).read_text('utf-8'))
    if not isinstance(value,dict):raise RetentionError('Retention JSON object required')
    return value

def now():
    from datetime import datetime,timezone
    return datetime.now(timezone.utc).isoformat()

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)

SCRIPT_NAME='data_action_chain_pipeline_v0021.py'
SCRIPT_VERSION='v0021'
SCRIPT_FAMILY='data_action_chain_pipeline'
DEFAULT_ENCODING='utf-8'
KEYS=('asset_id','path','value_index','segment_index','sentence_index','char_start','char_end')

def run_owned(command, *, capture_output=False, timeout=None, check=False, **kwargs):
    if os.name!='nt':
        raise RuntimeError('Owned ingestion process tree currently requires Windows')
    class Basic(ctypes.Structure):
        _fields_=[('per_process',ctypes.c_longlong),('per_job',ctypes.c_longlong),('flags',wintypes.DWORD),
                  ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',wintypes.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                  ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
    job=kernel.CreateJobObjectW(None,None)
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    limits=Extended();limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process=None
    try:
        if not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if capture_output:kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        kwargs['creationflags']=kwargs.get('creationflags',0)|subprocess.CREATE_NO_WINDOW|0x4
        process=subprocess.Popen(command,**kwargs)
        if not kernel.AssignProcessToJobObject(job,wintypes.HANDLE(int(process._handle))):
            process.kill();process.wait()
            raise ctypes.WinError(ctypes.get_last_error())
        ntdll=ctypes.WinDLL('ntdll')
        ntdll.NtResumeProcess.argtypes=[wintypes.HANDLE]
        if ntdll.NtResumeProcess(wintypes.HANDLE(int(process._handle)))!=0:
            raise RuntimeError('Unable to resume owned process')
        try:
            stdout,stderr=process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kernel.TerminateJobObject(job,124)
            stdout,stderr=process.communicate()
            raise subprocess.TimeoutExpired(command,timeout,output=stdout,stderr=stderr)
        result=subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
        measured=Extended()
        if kernel.QueryInformationJobObject(job,9,ctypes.byref(measured),ctypes.sizeof(measured),None):
            result.resource_usage={'peak_job_committed_bytes':measured.peak_job,
                                   'peak_process_committed_bytes':measured.peak_process,
                                   'read_bytes':measured.io.read_bytes,'write_bytes':measured.io.write_bytes}
        if check:result.check_returncode()
        return result
    finally:
        kernel.CloseHandle(job)
        if process is not None and process.poll() is None:process.wait()

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.publishing')
    with tmp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)


@contextmanager
def task_lease(path):
    with Path(path).open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

def json_file_step(script, root, schema, operation, payload):
    import uuid
    root=Path(root).resolve();run_id='handoff_'+uuid.uuid4().hex
    task=root/'runtime/intermediate_tasks'/run_id;task.mkdir(parents=True)
    request,response,manifest=task/'request.json',task/'response.json',task/'task.json'
    envelope={'schema_version':schema,'run_id':run_id,'operation':operation,'producer':SCRIPT_NAME,'producer_version':SCRIPT_VERSION,**payload}
    save(request,envelope)
    state={'schema_version':'intermediate-task.v1','run_id':run_id,'status':'running','active':True,
           'result_verified':False,'recovery_required':True,'consumers':[{'id':SCRIPT_NAME,'acknowledged':False}],
           'intermediates':[]}
    save(manifest,state)
    temporary=root/'temp';temporary.mkdir(parents=True,exist_ok=True)
    entry=Path(script) if Path(script).is_absolute() else Path(__file__).with_name(script)
    if not entry.resolve().is_relative_to(ROOT/'scripts'):
        raise ValueError('worker_entry_outside_source_root')
    command=[sys.executable,'-B',str(entry),'--request',str(request),'--response',str(response)]
    with task_lease(task/'task.lock'):
        try:
            result=run_owned(command,cwd=root,capture_output=True,text=True,encoding='utf-8',
                             env={**os.environ,'TMP':str(temporary),'TEMP':str(temporary)})
            if result.returncode:
                try:failure=json.loads(result.stdout)
                except Exception:failure={'error_type':'ChildProcessFailure','detail':'see failed task manifest'}
                message=failure.get('detail',failure.get('error_type','worker_failed'))
                if failure.get('error_type')=='RuntimeError':raise RuntimeError(message)
                raise ValueError(message)
            if response.is_symlink() or response.stat().st_size>16*1024*1024:
                raise ValueError('JSON handoff response ownership invalid')
            value=json.loads(response.read_text(encoding='utf-8'))
            expected_version='v'+entry.stem.rsplit('_v',1)[-1]
            if value.get('schema_version')!=schema or value.get('run_id')!=run_id or value.get('status')!='completed' or value.get('producer') not in {entry.stem,entry.name} or value.get('producer_version')!=expected_version:
                raise ValueError('JSON handoff response invalid')
            state.update(status='completed',active=False,result_verified=True,recovery_required=False,
                         consumers=[{'id':SCRIPT_NAME,'acknowledged':True}],
                         intermediates=[{'path':p.relative_to(root).as_posix(),'sha256':sha(p),'regenerable':True,'protected':False}
                                        for p in [request,response]])
            save(manifest,state)
        except Exception:
            state.update(status='failed',active=False,recovery_required=True);save(manifest,state)
            raise
    cleanup=run_owned([sys.executable,'-B',str(ROOT/'scripts/action/infrastructures/intermediate_cleanup_v0001.py'),
                       '--root',str(root),'--manifest',manifest.relative_to(root).as_posix()],
                      cwd=root,capture_output=True,text=True,encoding='utf-8')
    if cleanup.returncode:raise ValueError('completed_task_cleanup_pending')
    return value['result']


def preparation_request(stage, operation, options, run_id):
    root=ROOT
    for index,option in enumerate(options):
        if option=='--project-root':root=Path(options[index+1]);break
    return json_file_step('isolated_data_preparation_v0001.py',root,'isolated-preparation.v1',operation,{'options':options})


def publish(stage, config, receipt, *, fault_after_batches=0):
    payload={'config':config,'stage':str(stage),'receipt':str(receipt),'fault_after_batches':fault_after_batches}
    return json_file_step('append_verified_facts_v0001.py',Path(config.get('project_root',ROOT)),
                          'verified-facts-publication.v1','publish',payload)


def _execute(args):
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    root=Path(config.get('project_root',ROOT)).resolve()
    source=Path(args.source).resolve(strict=True)
    if not args.run_id.isalnum() or len(args.run_id)>40:raise ValueError('Use an alphanumeric run ID up to 40 characters')
    stage=root/'temp/c'/hashlib.sha256(args.run_id.encode()).hexdigest()[:10]
    stage.mkdir(parents=True,exist_ok=True)
    temporary_directory=stage/'tmp';temporary_directory.mkdir(exist_ok=True)
    child_environment={**os.environ,'TEMP':str(temporary_directory),'TMP':str(temporary_directory)}
    source_hash=sha(source)
    archived=root/config['data_root']/'data_raw/scanned'/source_hash/source.name
    archived.parent.mkdir(parents=True,exist_ok=True)
    if not archived.exists():
        temporary=archived.with_suffix(archived.suffix+'.publishing')
        shutil.copyfile(source,temporary)
        if sha(temporary)!=source_hash:raise ValueError('Source changed during durable copy')
        os.replace(temporary,archived)
    if sha(archived)!=source_hash:raise ValueError('Durable original hash mismatch')
    binding={'source':str(source),'sha256':source_hash,'run_id':args.run_id,'config_sha256':sha(args.config),'version':SCRIPT_VERSION}
    binding_path=stage/'binding.json'
    if binding_path.exists() and json.loads(binding_path.read_text(encoding='utf-8'))!=binding:raise ValueError('Run binding differs')
    if not binding_path.exists():save(binding_path,binding)
    manifest=stage/'completion.json'
    if manifest.exists():
        existing=json.loads(manifest.read_text(encoding='utf-8'))
        if existing.get('status')=='completed':return existing
    prepared=stage/'prepared.json'
    if not prepared.exists():
        original=stage/'data/data_raw'/source.name;original.parent.mkdir(parents=True,exist_ok=True)
        if not original.exists():shutil.copyfile(source,original)
        if sha(original)!=source_hash:raise ValueError('Staged original differs')
        action=stage/'action.db'
        if not action.is_file():
            run_owned([sys.executable,'-B',str(root/'scripts/action/tools/init_action_data_sql_schema_v0001.py'),'--init','--db-path',str(action)],capture_output=True,check=True,cwd=root)
        writer=yaml.safe_load((root/config['sql_writer_config']).read_text(encoding='utf-8'))
        writer['connection']['sqlite']['path']=str(action)
        sql_config=stage/'writer.yml';sql_config.write_text(yaml.safe_dump(writer,allow_unicode=True),encoding='utf-8')
        # A failed isolated attempt may be rolled back safely: it never points at production.
        attempt_file=stage/'attempt.json'
        previous=json.loads(attempt_file.read_text(encoding="utf-8")) if attempt_file.exists() else None
        if previous:
            journal=stage/'o/isolated_data_preparation/v0001'/previous['run_id']/'transaction_journal.json'
            legacy_journal=stage/'o/data_action_chain_pipeline/v0020'/previous['run_id']/'transaction_journal.json'
            if not journal.exists() and legacy_journal.exists():
                # Recover the old JSON journal with this self-contained worker;
                # the historical implementation is never loaded or launched.
                journal=legacy_journal
            if journal.exists():
                previous_journal=json.loads(journal.read_text(encoding="utf-8"))
                if previous_journal['state']=='COMMITTED':
                    evidence=journal.parent/'retention';outcome=evidence/'total_outcome.json'
                    completed=outcome.exists() and json.loads(outcome.read_text(encoding='utf-8')).get('overall_status')=='completed'
                    if not completed:
                        if not (evidence/'binding.json').exists():
                            saved_options=json.loads((stage/(previous['run_id']+'_options.json')).read_text(encoding='utf-8'))
                            evidence.mkdir(exist_ok=True)
                            recovered_binding=preparation_request(stage,'binding',saved_options,previous['run_id'])
                            if journal==legacy_journal:
                                recovered_binding=seal({**recovered_binding,'run_dir':str(journal.parent)})
                            write(evidence/'binding.json',recovered_binding)
                            shutil.copyfile(stage/(previous['run_id']+'_retention.json'),evidence/'confirmation.json')
                        result=run_owned([sys.executable,'-B',str(Path(__file__).with_name('intermediate_retention_pipeline_v0004.py')),'--binding',str(evidence/'binding.json'),'--receipt',str(evidence/'confirmation.json'),'--completion',str(journal.with_name('completion_manifest.json'))],cwd=root,capture_output=True,text=True,encoding='utf-8',check=True)
                        recovered=json.loads(result.stdout)
                        if recovered.get('status')!='completed':raise ValueError('Retention recovery incomplete')
                        save(stage/'retention_recovery.json',recovered)
                    save(prepared,{'completion':str(journal.with_name('completion_manifest.json'))})
                elif previous_journal['state'] in ('PENDING','PREPARING','ROLLING_BACK','ROLLBACK_FAILED'):
                    preparation_request(stage,'recover',['--project-root',str(root),'--recover-transaction',str(journal),'--confirm-interrupted-run'],previous['run_id'])
        if not prepared.exists():
            import uuid
            tokens=root/config['state_dir']/'run_tokens';tokens.mkdir(parents=True,exist_ok=True)
            while True:
                child='s'+uuid.uuid4().hex[:5]
                try:
                    with (tokens/(child+'.json')).open('x',encoding='utf-8') as token:
                        json.dump({'parent_run_id':args.run_id,'stage':str(stage),'source_sha256':source_hash},token)
                    break
                except FileExistsError:continue
            save(attempt_file,{'run_id':child})
            options=['--project-root',str(root),'--data-root',str(stage/'data'),'--target','data_raw/'+source.name,
                '--asset-version','v0001','--data-table','data_text_units','--data-db',str(stage/'data.db'),
                '--action-db',str(action),'--sql-writer-config',str(sql_config),'--duckdb-path',str(stage/'projection.duckdb'),
                '--derivation-target','duckdb','--vector-target','none','--retention-mode','keep','--run-id',child,
                '--test-mode','--confirm-execution','--confirm-database-write','--step-timeout',str(config.get('step_timeout',86400))]
            for option,sub in (('output-root','o'),('admission-target-root','a'),('registry-dir','r'),('structural-business-root','s'),('anchor-business-root','p'),('derivation-business-root','m'),('return-root','ret'),('vector-pipeline-root','v'),('chromadb-path','c'),('vector-state-index','i.jsonl'),('vector-replaced-archive','replaced')):
                options+=['--'+option,str(stage/sub)]
            for path in config['semantic_paths']:options+=['--semantic-path',path]
            receipt=stage/(child+'_retention.json');bound=preparation_request(stage,'binding',options,child)
            write(receipt,seal({'kind':'receipt','binding_sha256':bound['contract_sha256'],'run_id':child,'mode':'keep','destination':'','confirmed':True,'confirmed_at':now()}))
            options+=['--retention-receipt',str(receipt)]
            save(stage/(child+'_options.json'),options)
            with (stage/(child+'.stdout')).open('wb') as out,(stage/(child+'.stderr')).open('wb') as err:
                result=preparation_request(stage,'prepare',options,child)
                out.write(json.dumps(result,ensure_ascii=False).encode('utf-8'))
            completion=stage/'o/isolated_data_preparation/v0001'/child/'completion_manifest.json'
            if json.loads(completion.read_text(encoding="utf-8"))['status']!='completed':raise ValueError('Preparation receipt incomplete')
            save(prepared,{'completion':str(completion),'sha256':sha(completion)})
    if args.stage_only:return {'status':'prepared','stage':str(stage)}
    archived_sources=json_file_step(str(ROOT/'scripts/action/infrastructures/archive_canonical_sources_v0001.py'),root,
        'canonical-source-archive.v1','archive',{'root':str(root),'stage':str(stage),'source_db_sha256':sha(stage/'data.db')})
    publication=publish(stage,config,stage/'publication.json',fault_after_batches=args.fault_after_batches)
    result={'status':'completed','version':SCRIPT_VERSION,'binding':binding,'publication':publication,'stage':str(stage),'durable_original':str(archived),'durable_canonical_sources':archived_sources}
    save(manifest,result)
    return result


def execute(args):
    config=json.loads(Path(args.config).read_text(DEFAULT_ENCODING));root=Path(config.get('project_root',ROOT)).resolve()
    if not args.run_id.isalnum() or len(args.run_id)>40:raise ValueError('Use an alphanumeric run ID up to 40 characters')
    state=(root/config['state_dir']).resolve()
    if not state.is_relative_to(root):raise ValueError('ingestion_state_outside_root')
    locks=state/'run_locks';locks.mkdir(parents=True,exist_ok=True)
    binding={'source':str(Path(args.source).resolve(strict=True)),'sha256':sha(args.source),'run_id':args.run_id,'config_sha256':sha(args.config),'version':SCRIPT_VERSION}
    formal=state/'completions'/(args.run_id+'.json')
    with task_lease(locks/(args.run_id+'.lock')):
        if formal.exists():
            result=json.loads(formal.read_text(DEFAULT_ENCODING))
            if result.get('status')!='completed' or result.get('binding')!=binding:raise ValueError('Formal completion binding differs')
        else:
            result=_execute(args)
            if result.get('status')!='completed':return result
            if not result.get('durable_canonical_sources'):
                stage=Path(result['stage'])
                result['durable_canonical_sources']=json_file_step(str(ROOT/'scripts/action/infrastructures/archive_canonical_sources_v0001.py'),root,
                    'canonical-source-archive.v1','archive',{'root':str(root),'stage':str(stage),'source_db_sha256':sha(stage/'data.db')})
            result={**result,'schema_version':'verified-ingestion-completion.v1','producer':SCRIPT_NAME,'producer_version':SCRIPT_VERSION,
                    'preparation_consumers':[{'id':'fact_publisher','acknowledged':True},{'id':'canonical_archiver','acknowledged':True}]}
            save(formal,result)
        try:
            cleaned=json_file_step(str(ROOT/'scripts/action/infrastructures/purge_prepared_ingestion_v0001.py'),root,
                'prepared-ingestion-cleanup.v1','purge',{'root':str(root),'receipt':formal.relative_to(root).as_posix(),'receipt_sha256':sha(formal),'dry_run':False})
        except Exception as exc:
            # SQL publication remains complete. Keep a truthful cleanup pending
            # result and the recovery files; the same run retries cleanup only.
            cleaned={'status':'pending','error_type':type(exc).__name__,'detail':str(exc) if isinstance(exc,ValueError) else 'inspect_cleanup_task_log'}
        return {**result,'completion_receipt':str(formal),'completion_receipt_sha256':sha(formal),'preparation_cleanup':cleaned}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--config',default=str(ROOT/'config/action/config/concurrent_pipeline_v0001.json'))
    p.add_argument('--stage-only',action='store_true');p.add_argument('--fault-after-batches',type=int,default=0)
    args=p.parse_args()
    if args.fault_after_batches and not Path(args.config).resolve().is_relative_to(ROOT/'temp'):raise ValueError('Fault injection requires isolated configuration')
    print(json.dumps(execute(args),ensure_ascii=False))

if __name__=='__main__':main()
