# ============================================================
# 文件名: append_verified_facts_v0001.py
# 中文名: 已验证事实增量发布器
# 版本号: v0001
# 主层级: action
# 层级: orchestration / fact_publication
# 脚本定位: 按 JSON 请求将隔离准备结果追加到已初始化的目标库
# 职责说明:
# - 保持事实冲突校验、身份时间旁表同步和批次恢复
# 本脚本做什么:
# - 只处理发布，提交后输出版本化 JSON 结果
# 本脚本不做什么:
# - 不准备来源，不计算向量，不修改已存在事实
# 制度边界声明:
# - 写入目标限制在请求项目根内；失败不伪报完成
# 可更新: True
# ============================================================
# ============================================================
# ALIAS_META
# ============================================================
# alias: append_verified_facts_v0001
# family: append_verified_facts
# role: append_verified_fact_publication
# version: v0001
# status: active
# entry_point: scripts/orchestration/action/append_verified_facts_v0001.py
# input:
#   - verified-facts-publication.v1 JSON request
# output:
#   - verified-facts-publication.v1 JSON response and publication receipt
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
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'append_verified_facts'
SCRIPT_NAME = 'append_verified_facts_v0001'
SCRIPT_VERSION = 'v0001'
ROOT = Path(__file__).resolve().parents[3]
KEYS = ('asset_id','path','value_index','segment_index','sentence_index','char_start','char_end')

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

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.publishing')
    with tmp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path)

def columns(db,table):return [row[1] for row in db.execute(f'PRAGMA table_info({table})')]

def append_row(db,table,fields,row,keys,ignored):
    mapping=dict(zip(fields,row));values=tuple(mapping[k] for k in keys)
    where=' AND '.join(k+' IS ?' for k in keys)
    old=db.execute(f'SELECT {",".join(fields)} FROM {table} WHERE {where}',values).fetchone()
    if old is not None:
        for name,a,b in zip(fields,old,row):
            if name in ignored:continue
            if name=='message_identity_evidence_json':
                # Generated canonical envelopes include run/path metadata. Original
                # source file/message hashes, coordinates and recorded facts must agree.
                a=json.loads(a);b=json.loads(b)
                for evidence in (a,b):
                    evidence.pop('source_access_id',None);evidence.pop('canonical_source_sha256',None)
            if a!=b:raise ValueError(f'Immutable fact conflict: {table}.{name} {values}')
        return False
    db.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})',row)
    return True

def publish(stage,config,receipt,*,fault_after_batches=0):
    """Data+both side tables commit together; Action visibility follows that commit."""
    batch_size=int(config.get('publish_batch_size',500))
    if not 1<=batch_size<=2000:raise ValueError('publish_batch_size out of bounds')
    root=Path(config.get('project_root',ROOT)).resolve()
    receipt=Path(receipt)
    binding={name:sha(stage/name) for name in ('data.db','action.db')}
    binding['targets']={name:str((root/config[name]).resolve()) for name in ('data_db','action_db')}
    state=json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else {'binding':binding,'tables':{},'status':'publishing','batches':0}
    if state['binding']!=binding:raise ValueError('Publication resume binding differs')
    started=time.monotonic()
    with exclusive_lease(root/config['state_dir']/'publish.lease'):
        for source_name,target_key,tables in (
            ('data.db','data_db',[('data_run_log',('run_id',),set()),('data_text_units',KEYS,{'ingested_at'})]),
            ('action.db','action_db',[
                ('ingestion_run_log',('run_id',),set()),
                ('concept_units',('unit_text_id',),{'created_at','run_id','first_seen_instance_id'}),
                ('instance_units',('instance_id',),{'created_at','run_id'}),
                ('unit_attributes',('attr_id',),{'created_at','run_id'})])):
            source=sqlite3.connect((stage/source_name).as_uri()+'?mode=ro',uri=True)
            target_path=(root/config[target_key]).resolve()
            if not target_path.is_file():raise ValueError(f'Initialize target schema first: {target_path}')
            target=sqlite3.connect(target_path,timeout=30)
            target.execute('PRAGMA journal_mode=WAL');target.execute('PRAGMA synchronous=FULL')
            target.execute('PRAGMA foreign_keys=ON');target.execute('PRAGMA cache_size=-8192')
            try:
                side_fields={}
                if source_name=='data.db':
                    for side in ('data_text_unit_source_timestamps','data_text_unit_source_identities'):
                        side_fields[side]=columns(source,side)
                        if side_fields[side]!=columns(target,side):raise ValueError(f'Schema mismatch: {side}')
                for table,keys,ignored in tables:
                    fields=columns(source,table)
                    if fields!=columns(target,table):raise ValueError(f'Schema mismatch: {table}')
                    progress=state['tables'].setdefault(table,{'cursor':0,'checked':0,'inserted':0})
                    scope=" AND script_name LIKE 'ingest_data_text_units_%'" if table=='data_run_log' else ''
                    cursor=source.execute(f'SELECT rowid,{",".join(fields)} FROM {table} WHERE rowid>?{scope} ORDER BY rowid',(progress['cursor'],))
                    while rows:=cursor.fetchmany(batch_size):
                        inserted=0
                        with target:
                            for rid,*row in rows:
                                inserted+=append_row(target,table,fields,tuple(row),keys,ignored)
                                if table=='data_text_units':
                                    values=tuple(dict(zip(fields,row))[k] for k in KEYS)
                                    for side in ('data_text_unit_source_timestamps','data_text_unit_source_identities'):
                                        sf=side_fields[side]
                                        sr=source.execute(f'SELECT {",".join(sf)} FROM {side} WHERE '+' AND '.join(k+' IS ?' for k in KEYS),values).fetchone()
                                        if sr is None:raise ValueError(f'Staged source evidence missing: {side}')
                                        append_row(target,side,sf,sr,KEYS,{'annotation_run_id','annotated_at','source_timestamp_annotation_id','source_identity_annotation_id'})
                            # Verify constraints inside this batch before commit.
                        progress['cursor']=rows[-1][0];progress['checked']+=len(rows);progress['inserted']+=inserted
                        state['batches']+=1;state['updated_at']=time.time();state['elapsed_seconds']=time.monotonic()-started
                        save(receipt,state)
                        if fault_after_batches and state['batches']>=fault_after_batches:raise RuntimeError('Injected publication interruption')
                    expected=source.execute(f'SELECT COUNT(*) FROM {table} WHERE 1=1{scope}').fetchone()[0]
                    if progress['checked']!=expected:raise ValueError(f'Publication conservation failed: {table}')
            finally:source.close();target.close()
        state.update(status='committed',elapsed_seconds=time.monotonic()-started,derivation='independent_followup',vectors='independent_followup')
        save(receipt,state)
    return state

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(DEFAULT_ENCODING))
        if request.get('schema_version') != 'verified-facts-publication.v1':
            raise ValueError('publication_request_invalid')
        config = request['config']
        root = Path(config['project_root']).resolve()
        stage, receipt = Path(request['stage']).resolve(), Path(request['receipt']).resolve()
        if not stage.is_relative_to(root) or not receipt.is_relative_to(root):
            raise ValueError('publication_input_outside_root')
        for key in ['data_db','action_db','state_dir']:
            if not (root/config[key]).resolve().is_relative_to(root):
                raise ValueError('publication_target_outside_root')
        fault = request.get('fault_after_batches',0)
        if fault and 'temp' not in root.parts:
            raise ValueError('fault_requires_isolated_temp_directory')
        result = publish(stage,config,receipt,fault_after_batches=fault)
        response = {'schema_version':'verified-facts-publication.v1','run_id':request['run_id'],
                    'producer':SCRIPT_NAME,'producer_version':SCRIPT_VERSION,'status':'completed','result':result}
        temporary = args.response.with_suffix('.tmp')
        temporary.write_text(json.dumps(response,ensure_ascii=False),DEFAULT_ENCODING)
        temporary.replace(args.response)
        print(json.dumps({'status':'completed','response':str(args.response)}))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'error','error_type':type(exc).__name__,
                          'detail':str(exc) if isinstance(exc,(ValueError,RuntimeError)) else 'publication_failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
