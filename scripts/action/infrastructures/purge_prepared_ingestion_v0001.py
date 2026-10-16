# ============================================================
# 文件名: purge_prepared_ingestion_v0001.py
# 中文名: 已发布入库任务准备产物清理
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / intermediate_lifecycle
# 脚本定位: 依据正式发布凭证清理隔离准备副本
# 职责说明:
# - 正式结果、原始来源和规范化来源核验后清理精确准备清单
# 本脚本做什么:
# - 保存日志，冻结文件哈希，逐项核验删除并记录审计
# 本脚本不做什么:
# - 不修改正式库，不删除唯一来源，不清理失败或尚待发布任务
# 制度边界声明:
# - 仅清理本次 run 绑定的 temp/c 目录中的独占普通文件
# 可更新: True
# ============================================================
# ALIAS_META
# alias: purge_prepared_ingestion_v0001
# family: purge_prepared_ingestion
# role: verified_ingestion_preparation_cleanup
# version: v0001
# status: staging
# entry_point: scripts/action/infrastructures/purge_prepared_ingestion_v0001.py
# input:
#   - prepared-ingestion-cleanup.v1 request and formal receipt SHA256
# output:
#   - atomic deletion result and retained cleanup logs
# depends_on:
#   - Python stdlib
# used_by:
#   - data_action_chain_pipeline_v0021
# ============================================================
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime,timezone
from pathlib import Path

DEFAULT_ENCODING='utf-8'
SCRIPT_NAME='purge_prepared_ingestion_v0001'
SCRIPT_FAMILY='purge_prepared_ingestion'
SCRIPT_VERSION='v0001'


def checked(path,root):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or part.exists() and getattr(part.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('preparation_cleanup_link_forbidden')
        if part==root:break
    value=path.resolve()
    if not value.is_relative_to(root):raise ValueError('preparation_cleanup_path_outside_root')
    return value


def sha(file):
    with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write(file,value):
    file.parent.mkdir(parents=True,exist_ok=True);temporary=file.with_suffix('.tmp')
    with temporary.open('w',encoding=DEFAULT_ENCODING) as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    temporary.replace(file)


@contextmanager
def lease(file):
    with file.open('a+b') as stream:
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


def purge(request):
    root=Path(request['root']).resolve(strict=True)
    receipt=checked(root/request['receipt'],root)
    if not receipt.is_relative_to(root/'runtime') or sha(receipt)!=request['receipt_sha256']:
        raise ValueError('formal_ingestion_receipt_changed')
    value=json.loads(receipt.read_text(DEFAULT_ENCODING));binding=value['binding'];parent=binding['run_id']
    if value.get('schema_version')!='verified-ingestion-completion.v1' or value.get('producer') not in {'data_action_chain_pipeline_v0021','data_action_chain_pipeline_v0021.py'} or value.get('producer_version')!='v0021' or not re.fullmatch(r'[a-zA-Z0-9]{1,40}',parent) or value.get('status')!='completed' or value.get('publication',{}).get('status')!='committed':
        raise ValueError('ingestion_not_verified_complete')
    consumers=value.get('preparation_consumers',[])
    if {x.get('id') for x in consumers}!={'fact_publisher','canonical_archiver'} or any(x.get('acknowledged') is not True for x in consumers):
        raise ValueError('preparation_consumers_not_acknowledged')
    original=checked(value['durable_original'],root)
    if not original.is_relative_to(root/'data/data_raw') or sha(original)!=binding['sha256']:
        raise ValueError('durable_original_missing_or_changed')
    sources=value.get('durable_canonical_sources',{})
    if sources.get('status')!='completed':raise ValueError('durable_canonical_sources_not_complete')
    for item in sources['sources']:
        file=checked(root/item['path'],root)
        if not file.is_relative_to(root/'actioning/canonical_sources') or sha(file)!=item['canonical_sha256'] or item.get('source_sha256')!=binding['sha256']:
            raise ValueError('durable_canonical_source_missing_or_changed')
    stage=checked(value['stage'],root)
    expected=root/'temp/c'/hashlib.sha256(parent.encode()).hexdigest()[:10]
    if stage!=expected:raise ValueError('preparation_stage_identity_invalid')
    targets=value['publication']['binding']['targets']
    required={'data_db':{'data_run_log','data_text_units','data_text_unit_source_identities','data_text_unit_source_timestamps'},
              'action_db':{'ingestion_run_log','concept_units','instance_units','unit_attributes'}}
    if set(targets)!=set(required):raise ValueError('formal_target_binding_invalid')
    for key,path in targets.items():
        file=checked(path,root)
        if file.is_relative_to(stage):raise ValueError('formal_target_points_into_preparation')
        if not file.is_file() or file.stat().st_size==0 or file.name.lower()=='action.db':raise ValueError('formal_database_missing_or_registry')
        with closing(sqlite3.connect(file.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            db.execute('PRAGMA query_only=ON')
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required[key]<=tables:raise ValueError('formal_database_schema_missing_preserve_preparation')
    if not stage.exists():return {'status':'completed','already_cleaned':True,'deleted_count':0}
    audit=root/'logs/ingestion_cleanup'/parent;audit.mkdir(parents=True,exist_ok=True)
    plan_path=audit/'cleanup_plan.log';lock=audit/'cleanup.lock'
    if (stage/'binding.json').exists():
        file=checked(stage/'binding.json',root)
        if file.stat().st_size>4*1024*1024 or json.loads(file.read_text(DEFAULT_ENCODING))!=binding:raise ValueError('preparation_binding_changed')
    elif not plan_path.exists():raise ValueError('preparation_binding_missing')
    if (stage/'completion.json').exists():
        file=checked(stage/'completion.json',root)
        if file.stat().st_size>4*1024*1024:raise ValueError('preparation_completion_too_large')
        completion=json.loads(file.read_text(DEFAULT_ENCODING))
        if completion.get('status')!='completed' or completion.get('binding')!=binding:raise ValueError('preparation_completion_not_verified')
    elif not plan_path.exists():raise ValueError('preparation_completion_missing')
    with lease(lock):
        if plan_path.exists():
            plan=json.loads(plan_path.read_text(DEFAULT_ENCODING))
            if plan.get('receipt_sha256')!=request['receipt_sha256'] or plan.get('stage')!=str(stage):raise ValueError('preparation_cleanup_plan_changed')
        else:
            files=[]
            for file in stage.rglob('*'):
                checked(file,root)
                if file.is_dir():continue
                if not file.is_file() or file.stat().st_nlink!=1:raise ValueError('preparation_file_not_exclusive')
                if len(files)>=50000:raise ValueError('preparation_cleanup_inventory_limit')
                files.append({'path':file.relative_to(stage).as_posix(),'sha256':sha(file)})
            plan={'schema_version':'prepared-ingestion-cleanup-plan.v1','stage':str(stage),'receipt_sha256':request['receipt_sha256'],
                  'run_id':parent,'files':files,'created_at':datetime.now(timezone.utc).isoformat()}
            write(plan_path,plan)
        if request.get('dry_run') is True:return {'status':'dry_run','files':plan['files'],'deleted_count':0}
        # Reject changes before deleting the first item; retries accept missing
        # listed files, but never acquire ownership of newly appearing files.
        for item in plan['files']:
            file=checked(stage/item['path'],root)
            if not file.is_relative_to(stage) or file==stage:raise ValueError('preparation_cleanup_plan_path_invalid')
            if file.exists() and (not file.is_file() or file.stat().st_nlink!=1 or sha(file)!=item['sha256']):raise ValueError('preparation_file_changed')
        deleted=0
        for item in plan['files']:
            file=checked(stage/item['path'],root)
            if not file.exists():continue
            if sha(file)!=item['sha256']:raise ValueError('preparation_file_changed_before_delete')
            if file.suffix in {'.log','.stdout','.stderr'} or file.name in {'pipeline_stdout.txt','pipeline_stderr.txt'}:
                saved=audit/'worker_logs'/item['path'];saved.parent.mkdir(parents=True,exist_ok=True)
                if saved.exists() and sha(saved)!=item['sha256']:raise ValueError('retained_worker_log_conflict')
                if not saved.exists():shutil.copyfile(file,saved)
                if sha(saved)!=item['sha256']:raise ValueError('retained_worker_log_verification_failed')
            file.unlink();deleted+=1
            with (audit/'cleanup.log').open('a',encoding=DEFAULT_ENCODING) as stream:
                stream.write(json.dumps({'event':'deleted','path':item['path'],'sha256':item['sha256'],'time':datetime.now(timezone.utc).isoformat()})+'\n')
        directories=[p for p in stage.rglob('*') if p.is_dir()]
        for directory in sorted(directories,key=lambda p:len(p.parts),reverse=True):
            checked(directory,root)
            try:directory.rmdir()
            except OSError:pass
        try:stage.rmdir()
        except OSError:pass
        result={'status':'completed','deleted_count':deleted,'remaining_unlisted_files':stage.exists(),'log_directory':audit.relative_to(root).as_posix()}
        with (audit/'cleanup.log').open('a',encoding=DEFAULT_ENCODING) as stream:stream.write(json.dumps({'event':'completed',**result})+'\n')
        return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True);parser.add_argument('--response',type=Path,required=True);args=parser.parse_args()
    try:
        request=json.loads(args.request.read_text(DEFAULT_ENCODING))
        if request.get('schema_version')!='prepared-ingestion-cleanup.v1' or type(request.get('dry_run')) is not bool or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',request.get('run_id','')):
            raise ValueError('preparation_cleanup_request_invalid')
        root=Path(request['root']).resolve();owner=root/'runtime/intermediate_tasks'/request['run_id']
        if checked(args.request,root).parent!=owner or checked(args.response,root).parent!=owner or args.request.resolve()==args.response.resolve():raise ValueError('preparation_cleanup_request_owner_invalid')
        result=purge(request)
        write(args.response,{'schema_version':'prepared-ingestion-cleanup.v1','run_id':request['run_id'],'producer':SCRIPT_NAME,'producer_version':SCRIPT_VERSION,'status':'completed','result':result})
        return 0
    except Exception as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'detail':str(exc) if isinstance(exc,ValueError) else 'preparation_cleanup_failed'}));return 2


if __name__=='__main__':raise SystemExit(main())
