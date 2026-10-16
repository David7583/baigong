# ============================================================
# 文件名: repair_source_occurrences_v0001.py
# 中文名: 历史来源实例补录
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / recovery
# 脚本定位: 修复内容去重误删的独立来源
# 职责说明: 流式核对语言、语义记录及既有概念，追加来源与实例
# 本脚本做什么: 备份、分批提交、冲突拒绝、幂等恢复、逐项全量验证
# 本脚本不做什么: 不修改正文、既有身份、概念和原始导出，不调用模型
# 制度边界声明: 跨库批次可能中断，重放逐项校验并补齐；完成前不声称全库一致
# 可更新: True
# ALIAS_META
# alias: repair_source_occurrences_v0001
# family: source_occurrence_repair
# role: append_only_recovery
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/repair_source_occurrences_v0001.py
# input: retained semantic/language JSONL, explicit SQLite databases
# output: verified source rows, instance rows, backup and audit
# depends_on: original data and identity contracts, sql_writer_v0005
# used_by: authorized local recovery
# ============================================================
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import itertools
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
SCRIPT_NAME='repair_source_occurrences_v0001.py'
SCRIPT_VERSION='v0001'
DEFAULT_ENCODING='utf-8'
SCRIPT_FAMILY='source_occurrence_repair'
SEMANTIC_PATH='/conversations/*/nodes/*/message/content/parts/*/text'

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value)
    return value

sys.path.insert(0,str(ROOT/'scripts/action/native_plugin/supplemental_annotation/plugins/source_identity_annotation'))
from source_message_identity_contract_v0001 import KEY_FIELDS,DB_FIELDS,key,payload
from bounded_records_v0001 import strict_jsonl
from ingestion_jobs_v0001 import exclusive_lease
data=module('repair_data_contract','scripts/anchor/ingest_data_text_units_v0001.py')
stamp=module('repair_timestamp_contract','scripts/action/native_plugin/supplemental_annotation/plugins/source_timestamp_annotation/write_source_timestamp_annotations_v0001.py')
flt=module('repair_filter','scripts/action/pipelines/content_delta_filter_v0002.py')
writer_module=module('repair_writer','scripts/action/anchor/sql_writer_v0005.py')

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.publishing')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def pairs(semantic,language):
    original=(row for row in strict_jsonl(language) if row.get('path')==SEMANTIC_PATH and str(row.get('text','')).strip())
    for number,(normalized,row) in enumerate(itertools.zip_longest(strict_jsonl(semantic),original),1):
        if normalized is None or row is None or key(normalized)!=key(row):
            raise ValueError(f'Language/semantic coordinate correspondence failed at {number}')
        # Independent evidence must agree even though text normalization may differ.
        if payload(normalized)!=payload(row) or stamp._payload(normalized,number)!=stamp._payload(row,number):
            raise ValueError(f'Side-table source evidence differs at {number}')
        if data.validate_record(row):raise ValueError(f'Invalid original at {number}')
        yield number,normalized,row

def ensure_row(conn,table,keys,values,fields,expected,extra_fields=(),extra_values=(),*,write=True):
    where=' AND '.join(f'{field} IS ?' for field in keys)
    old=conn.execute(f'SELECT {",".join(fields)} FROM {table} WHERE {where}',values).fetchone()
    if old is not None:
        if old!=expected:raise ValueError(f'Existing payload conflict in {table}: {values}')
        return 0
    if not write:raise ValueError(f'Missing row in {table}: {values}')
    columns=keys+fields+extra_fields
    conn.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',values+expected+extra_values)
    return 1

def run(args):
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    semantic=Path(args.semantic).resolve();language=Path(args.language).resolve()
    dbpath=Path(args.data_db).resolve();config=Path(args.sql_config).resolve()
    import yaml
    action_path=Path(yaml.safe_load(config.read_text(encoding='utf-8'))['connection']['sqlite']['path'])
    if not action_path.is_absolute():action_path=ROOT/action_path
    action_path=action_path.resolve()
    if not dbpath.is_file() or not action_path.is_file():raise ValueError('Existing databases required')
    binding={'semantic':str(semantic),'semantic_sha256':sha(semantic),'language':str(language),'language_sha256':sha(language),
             'data_db':str(dbpath),'action_db':str(action_path),'sql_config_sha256':sha(config),'script_sha256':sha(__file__)}
    checkpoint=output/'checkpoint.json'
    state=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {'binding':binding,'last_record':0,'counts':{},'status':'prepared'}
    if state['binding']!=binding:raise ValueError('Resume binding differs')
    if args.write and not (output/'backups.json').exists():
        backups=[]
        for label,path in (('data',dbpath),('action',action_path)):
            target=output/(label+'.before.sqlite3')
            if target.exists():raise ValueError(f'Incomplete backup: {target}')
            src=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);dest=sqlite3.connect(target)
            try:src.backup(dest)
            finally:dest.close();src.close()
            backups.append({'source':str(path),'backup':str(target),'sha256':sha(target)})
        save(output/'backups.json',backups)
    engine=flt.ContentDeltaFilter(flt.load_filter_config(ROOT/'config/action/config/content_delta_filter_config_v0002.yml',ROOT))
    writer=writer_module.SqlWriter(config) if args.write else None
    conn=sqlite3.connect(dbpath.as_uri()+('?mode=rw' if args.write else '?mode=ro'),uri=True,timeout=30)
    conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA cache_size=-8192')
    action=sqlite3.connect(action_path.as_uri()+'?mode=ro',uri=True,timeout=30)
    run_id='source_repair_'+binding['semantic_sha256'][:16]
    counts=state['counts'];start=time.monotonic();batch=[];groups=set();checked=0
    stamp_fields=('event_time','event_time_raw_json','event_time_kind','event_time_source_path','event_time_status','event_time_rule_version','mapping_node_id','message_id','source_timestamp_annotation_id')
    def process(batch):
        nonlocal checked
        instance_args=[];delta={'data':0,'identity':0,'timestamp':0,'instance':0}
        now=data.utc_now_iso()
        try:
            if args.write:conn.execute('BEGIN IMMEDIATE')
            for number,normalized,row in batch:
                k=key(row);canonical,digest=engine.canonicalize_and_hash(normalized['unit_text'])
                iid=flt.occurrence_id(normalized,digest)
                concept=action.execute('SELECT unit_text,content_hash FROM concept_units WHERE unit_text_id=?',(digest,)).fetchone()
                if concept!=(canonical,digest):raise ValueError(f'Existing concept missing/conflicting at {number}')
                built=data.build_row(row,now)
                delta['data']+=ensure_row(conn,'data_text_units',KEY_FIELDS,k,data.INSERT_COLUMNS[7:11],built[7:11],('ingested_at',),(now,),write=args.write)
                delta['identity']+=ensure_row(conn,'data_text_unit_source_identities',KEY_FIELDS,k,DB_FIELDS,payload(row),('annotation_run_id','annotated_at'),(run_id,now),write=args.write)
                delta['timestamp']+=ensure_row(conn,'data_text_unit_source_timestamps',KEY_FIELDS,k,stamp_fields,stamp._payload(row,number),('annotation_run_id','annotated_at'),(run_id,now),write=args.write)
                fields=('unit_text_id',)+KEY_FIELDS+('content','content_hash','schema_version')
                expected=(digest,)+k+(canonical,digest,'v0001')
                old=action.execute('SELECT '+','.join(fields)+' FROM instance_units WHERE instance_id=?',(iid,)).fetchone()
                if old is not None and old!=expected:raise ValueError(f'Existing instance conflict at {number}')
                if old is None:
                    if not args.write:raise ValueError(f'Missing instance at {number}')
                    instance_args.append(dict(zip(fields,expected),instance_id=iid,run_id=run_id))
                source_path=row['message_identity_evidence']['source_ref']['source_path']
                groups.add(int(source_path.split('/')[1]));checked+=1
            if writer:
                for result in writer.write_many('instance',instance_args,batch_size=1000):
                    if result.status not in ('inserted','duplicate'):raise ValueError(str(result))
                    delta['instance']+=result.status=='inserted'
                conn.commit()
                for k,v in delta.items():counts[k]=counts.get(k,0)+v
                state.update(last_record=batch[-1][0],status='running',elapsed_seconds=time.monotonic()-start)
                save(checkpoint,state)
            if batch[-1][0]%10000==0:print(json.dumps({'record':batch[-1][0],'inserted':counts,'elapsed_seconds':round(time.monotonic()-start,2)}),flush=True)
        except Exception:
            conn.rollback();raise
    try:
        # Re-read and validate committed prefix on every replay; writes are idempotent.
        for item in pairs(semantic,language):
            batch.append(item)
            if len(batch)>=1000:process(batch);batch=[]
        if batch:process(batch)
        for database in (conn,action):
            integrity=database.execute('PRAGMA integrity_check').fetchall()
            if integrity!=[('ok',)] or database.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('SQLite integrity check failed')
        if sha(semantic)!=binding['semantic_sha256'] or sha(language)!=binding['language_sha256']:raise ValueError('Inputs changed while running')
        state.update(status='completed' if args.write else 'verified',verified_records=checked,source_conversation_groups=len(groups),source_indices=sorted(groups),elapsed_seconds=time.monotonic()-start)
        save(output/('checkpoint.json' if args.write else 'verification.json'),state)
        print(json.dumps({k:v for k,v in state.items() if k not in ('binding','source_indices')},ensure_ascii=False),flush=True)
    except Exception as exc:
        save(output/'failure.json',{'error':str(exc),'last_record':state['last_record'],'counts':counts,'resume':'re-run with identical inputs; existing facts are checked, never overwritten'})
        raise
    finally:
        conn.close();action.close()

def main():
    p=argparse.ArgumentParser()
    for field in ('semantic','language','data-db','sql-config','output'):p.add_argument('--'+field,required=True)
    p.add_argument('--write',action='store_true')
    args=p.parse_args()
    with exclusive_lease(ROOT/'runtime/concurrent_pipeline/publish.lease'):
        run(args)

if __name__=='__main__':main()
