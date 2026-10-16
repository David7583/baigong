# ============================================================
# 文件名: clean_completed_handoffs_v0001.py
# 中文名: 完成任务交接文件清理编排
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / intermediate_lifecycle
# 脚本定位: 工作台清理入口的独立 JSON 任务
# 职责说明:
# - 预览并清理已核验、已消费的明确任务清单
# 本脚本做什么:
# - 冻结清单哈希，逐任务通过 JSON 委托清理器，汇总真实结果
# 本脚本不做什么:
# - 不清空目录，不删除失败或在途任务，不把正式资料当中间文件
# 制度边界声明:
# - 无网络和模型调用；只接受项目内有归属的任务清单
# 可更新: True
# ============================================================
# ALIAS_META
# alias: clean_completed_handoffs_v0001
# family: clean_completed_handoffs
# role: verified_handoff_cleanup_coordinator
# version: v0001
# status: staging
# entry_point: scripts/action/infrastructures/clean_completed_handoffs_v0001.py
# input:
#   - completed-handoff-cleanup.v1 JSON request
# output:
#   - preview or actual per-task cleanup JSON response
# depends_on:
#   - intermediate_cleanup_v0001 JSON file interface
# used_by:
#   - workbench_runtime_control_v0001
# ============================================================
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ENCODING='utf-8'
SCRIPT_NAME='clean_completed_handoffs_v0001'
SCRIPT_FAMILY='clean_completed_handoffs'
SCRIPT_VERSION='v0001'


def write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False),DEFAULT_ENCODING)
    temporary.replace(path)


def checked(path,root):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or part.exists() and getattr(part.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('cleanup_link_forbidden')
        if part==root:break
    path=path.resolve()
    if not path.is_relative_to(root):raise ValueError('cleanup_path_outside_root')
    return path


def execute(request,owner):
    root=Path(request['root']).resolve();base=root/'runtime/intermediate_tasks'
    config_file=root/'config/action/config/concurrent_pipeline_v0001.json'
    state_dir='runtime/concurrent_pipeline'
    if config_file.exists():
        checked(config_file,root)
        if config_file.stat().st_size>1024*1024:raise ValueError('cleanup_config_too_large')
        state_dir=json.loads(config_file.read_text(DEFAULT_ENCODING))['state_dir']
    receipt_base=checked(root/state_dir/'completions',root)
    if not receipt_base.is_relative_to(root/'runtime'):raise ValueError('cleanup_receipt_directory_invalid')
    operation=request.get('operation')
    if operation not in {'preview','execute'}:raise ValueError('cleanup_operation_invalid')
    if operation=='preview':
        candidates=[]
        if base.exists():
            for folder in base.iterdir():
                if folder==owner:continue
                file=folder/'task.json'
                if file.is_file():
                    try:
                        checked(file,root)
                        if file.stat().st_size>4*1024*1024:raise ValueError('cleanup_manifest_too_large')
                        digest=hashlib.sha256(file.read_bytes()).hexdigest()
                    except Exception:
                        digest=None
                    candidates.append({'manifest':file.relative_to(root).as_posix(),'sha256':digest})
                    if len(candidates)>1000:raise ValueError('cleanup_inventory_limit')
        if receipt_base.exists():
            for file in receipt_base.glob('*.json'):
                checked(file,root)
                if file.stat().st_size>4*1024*1024:raise ValueError('cleanup_receipt_too_large')
                candidates.append({'kind':'prepared_ingestion','manifest':file.relative_to(root).as_posix(),
                                   'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
                if len(candidates)>1000:raise ValueError('cleanup_inventory_limit')
    else:
        candidates=request.get('tasks')
        if not isinstance(candidates,list) or len(candidates)>1000:raise ValueError('cleanup_selection_invalid')
    results=[]
    for index,item in enumerate(candidates):
        try:
            file=checked(root/item['manifest'],root)
            prepared=item.get('kind')=='prepared_ingestion'
            if prepared:
                if file.parent!=receipt_base or file.suffix!='.json':raise ValueError('cleanup_receipt_ownership_invalid')
            elif item.get('kind') not in (None,'handoff') or file.name!='task.json' or file.parent.parent!=base or file.parent==owner or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',file.parent.name):
                raise ValueError('cleanup_manifest_ownership_invalid')
            if file.stat().st_size>4*1024*1024:raise ValueError('cleanup_manifest_too_large')
            current=hashlib.sha256(file.read_bytes()).hexdigest()
            if current!=item['sha256']:raise ValueError('cleanup_plan_changed_preview_again')
            incoming,outgoing=owner/f'cleanup_{index}_request.json',owner/f'cleanup_{index}_response.json'
            schema='prepared-ingestion-cleanup.v1' if prepared else 'intermediate-cleanup.v1'
            worker='purge_prepared_ingestion_v0001' if prepared else 'intermediate_cleanup_v0001'
            payload={'receipt':item['manifest'],'receipt_sha256':current} if prepared else {'manifest':item['manifest'],'manifest_sha256':current}
            write(incoming,{'schema_version':schema,'run_id':request['run_id'],'root':str(root),
                            **payload,'dry_run':operation=='preview'})
            flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            child=subprocess.run([sys.executable,'-B',str(Path(__file__).with_name(worker+'.py')),
                                  '--request',str(incoming),'--response',str(outgoing)],capture_output=True,text=True,encoding=DEFAULT_ENCODING,timeout=30,**flags)
            if child.returncode:
                try:detail=json.loads(child.stdout).get('detail','cleanup_rejected')
                except Exception:detail='cleanup_rejected'
                results.append({**item,'status':'preserved','eligible':False,'reason':detail});continue
            value=json.loads(outgoing.read_text(DEFAULT_ENCODING))
            producer_valid=(value.get('producer')==worker and value.get('producer_version')=='v0001') if prepared else value.get('producer')=={'script':worker,'version':'v0001'}
            if value.get('schema_version')!=schema or value.get('run_id')!=request['run_id'] or not producer_valid or value.get('status')!='completed':
                raise ValueError('cleanup_response_contract_invalid')
            if prepared and value['result'].get('already_cleaned'):
                results.append({**item,'status':'preserved','eligible':False,'reason':'preparation_already_cleaned'});continue
            results.append({**item,'status':'eligible' if operation=='preview' else 'cleaned','eligible':True,'result':value['result']})
        except Exception as exc:
            results.append({**item,'status':'preserved','eligible':False,'reason':str(exc) if isinstance(exc,ValueError) else 'cleanup_operation_failed'})
    return {'status':'completed','operation':operation,'tasks':results,'eligible_count':sum(x['eligible'] for x in results),
            'cleaned_count':sum(x['status']=='cleaned' for x in results),'preserved_count':sum(x['status']=='preserved' for x in results)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True);parser.add_argument('--response',type=Path,required=True);args=parser.parse_args()
    try:
        request=json.loads(args.request.read_text(DEFAULT_ENCODING))
        if request.get('schema_version')!='completed-handoff-cleanup.v1' or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',request.get('run_id','')):
            raise ValueError('cleanup_request_invalid')
        root=Path(request['root']).resolve();owner=root/'runtime/intermediate_tasks'/request['run_id']
        if checked(args.request,root).parent!=owner or checked(args.response,root).parent!=owner or args.request.resolve()==args.response.resolve():raise ValueError('cleanup_task_ownership_invalid')
        result=execute(request,owner)
        write(args.response,{'schema_version':'completed-handoff-cleanup.v1','run_id':request['run_id'],'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'status':'completed','result':result})
        return 0
    except Exception as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'detail':str(exc) if isinstance(exc,ValueError) else 'cleanup_failed'}));return 2


if __name__=='__main__':raise SystemExit(main())
