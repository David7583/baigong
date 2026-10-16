# ============================================================
# 文件名: vector_integrity_check_v0004.py
# 中文名: 有界内存全量向量完整性审计
# 版本号: v0004
# 主层级: action
# 层级: vector / audit
# 脚本定位: 使用磁盘索引完成 SQL、Chroma、状态索引的全量核验
# 职责说明: 保持六项审计，错误明示，不使用抽样替代检查
# 本脚本做什么: 分页读取并在临时 SQLite 上执行集合核验
# 本脚本不做什么: 不生成向量、不修复或修改业务数据库
# 制度边界声明: 样例仅用于报告展示，所有异常逐条落盘
# 可更新: True
# ============================================================
# ALIAS_META
# alias: vector_integrity_check
# family: vector_audit
# role: integrity_checker
# version: v0004
# status: active
# entry_point: scripts/action/vector/vector_integrity_check_v0004.py
# input: SQL database, Chroma collection, JSONL state index
# output: complete audit counts, bounded samples, full issue JSONL
# depends_on: Python stdlib, chromadb
# used_by: vector_embedding_pipeline_v0008
# ============================================================
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'infrastructures'))
from bounded_records_v0001 import strict_jsonl

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'vector_audit'
SCRIPT_NAME = 'vector_integrity_check_v0004.py'
SCRIPT_VERSION = 'v0004'
COLLECTION_NAME = 'action_data_embeddings'
BATCH_SIZE = 1000


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _connect_chromadb(path):
    import chromadb
    from chromadb.config import Settings
    if not Path(path).is_dir():
        raise ValueError('Chroma database does not exist')
    client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
    return client, client.get_collection(COLLECTION_NAME)


def audit_collection(sql_db_path, collection, state_index_path, output_dir=None, target='all', quick_mode=False):
    if target not in ('all', 'concept', 'instance'):
        raise ValueError('Invalid audit target')
    result = {'status':'ok', 'timestamp':utc_now(), 'audit_target':target,
              'scope':'global' if target == 'all' else 'single_object_type',
              'summary':{'audit_target':target}, 'checks':{}, 'details':{}, 'errors':[],
              'complete_issue_files':{}, 'report_sample_limit':10}
    root = Path(output_dir) if output_dir else None
    if root:
        root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='action_audit_') as scratch:
        db = sqlite3.connect(str(Path(scratch) / 'audit.db'))
        db.execute('PRAGMA cache_size=-4096')
        db.execute('PRAGMA temp_store=FILE')
        db.executescript('''
            CREATE TABLE s(k TEXT PRIMARY KEY, t TEXT, id TEXT, h TEXT);
            CREATE TABLE c(sig TEXT PRIMARY KEY, k TEXT, t TEXT, id TEXT, h TEXT, state TEXT, seq INTEGER);
            CREATE TABLE i(k TEXT PRIMARY KEY,sig TEXT);
        ''')
        try:
            source = sqlite3.connect(Path(sql_db_path).resolve().as_uri() + '?mode=ro', uri=True)
            try:
                source.execute('BEGIN')
                for kind, table, column in (('instance','instance_units','instance_id'), ('concept','concept_units','unit_text_id')):
                    cursor = source.execute(f'SELECT {column},content_hash FROM {table}')
                    for rows in iter(lambda:cursor.fetchmany(BATCH_SIZE), []):
                        db.executemany('INSERT INTO s VALUES (?,?,?,?)',
                            ((f'{kind}:{row[0]}',kind,str(row[0]),str(row[1]).lower() if row[1] else '') for row in rows))
                source.commit()
            finally:
                source.close()
            total = collection.count()
            offset = 0
            while offset < total:
                page = collection.get(limit=min(BATCH_SIZE,total-offset), offset=offset, include=['metadatas'])
                ids, metas = page.get('ids',[]), page.get('metadatas',[])
                if not ids or len(ids) != len(metas):
                    raise ValueError('Incomplete Chroma audit page')
                rows = []
                for number, (signature, meta) in enumerate(zip(ids,metas), offset):
                    if not isinstance(meta,dict) or not meta.get('object_type') or not meta.get('object_id'):
                        raise ValueError('Invalid Chroma identity metadata')
                    kind, oid = str(meta['object_type']),str(meta['object_id'])
                    rows.append((signature,f'{kind}:{oid}',kind,oid,str(meta.get('source_hash') or '').lower(),meta.get('state','active'),number))
                db.executemany('INSERT INTO c VALUES (?,?,?,?,?,?,?)',rows)
                offset += len(ids)
            if collection.count() != total:
                raise ValueError('Chroma changed during audit; retry with writer lease')
            for rec in strict_jsonl(state_index_path):
                if not all(rec.get(k) for k in ('object_type','object_id','embedding_signature')):
                    raise ValueError('Invalid state-index identity')
                db.execute('INSERT INTO i VALUES (?,?) ON CONFLICT(k) DO UPDATE SET sig=excluded.sig',
                           (f"{rec['object_type']}:{rec['object_id']}",rec['embedding_signature']))
            db.commit()
            summary = result['summary']
            for prefix, table in (('sql','s'),('chromadb','c')):
                for kind in ('instance','concept'):
                    summary[f'{prefix}_{kind}_count'] = db.execute(f'SELECT COUNT(*) FROM {table} WHERE t=?',(kind,)).fetchone()[0]
                summary[f'{prefix}_total'] = sum(summary[f'{prefix}_{k}_count'] for k in ('instance','concept'))
            summary['index_count'] = db.execute('SELECT COUNT(*) FROM i').fetchone()[0]
            matched = {kind:summary[f'sql_{kind}_count'] == summary[f'chromadb_{kind}_count'] for kind in ('instance','concept')}
            ok = all(matched.values()) if target == 'all' else matched[target]
            result['checks']['count_alignment'] = 'pass' if ok else 'fail'
            result['details']['count_alignment'] = {k:v for k,v in summary.items() if k.endswith('_count') and k != 'index_count'}
            result['details']['count_alignment'].update(status=result['checks']['count_alignment'],
                audit_target=target,scope_match=ok,instance_match=matched['instance'],concept_match=matched['concept'])
            if target != 'all':
                db.execute('DELETE FROM s WHERE t<>?',(target,))
                db.execute('DELETE FROM c WHERE t<>?',(target,))
                db.execute('DELETE FROM i WHERE substr(k,1,?)<>?',(len(target)+1,target+':'))
            db.execute('CREATE INDEX ck ON c(k)')
            db.execute('CREATE TABLE a AS SELECT k,sig FROM c WHERE state="active" GROUP BY k HAVING seq=MAX(seq)')
            db.execute('CREATE UNIQUE INDEX ak ON a(k)')
            for key,table in (('sql','s'),('chromadb','c'),('index','i')):
                summary[f'scoped_{key}_count'] = db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            checks = {
                'orphan_vectors':('SELECT c.t,c.id,c.sig FROM c LEFT JOIN s ON s.k=c.k WHERE s.k IS NULL',('object_type','object_id','embedding_signature'),'orphan_count'),
                'missing_vectors':('SELECT s.t,s.id FROM s WHERE NOT EXISTS(SELECT 1 FROM c WHERE c.k=s.k)',('object_type','object_id'),'missing_count'),
                'signature_drift':('SELECT c.t,c.id,s.h,c.h FROM c JOIN s ON s.k=c.k WHERE s.h<>"" AND c.h<>"" AND s.h<>c.h',('object_type','object_id','sql_content_hash','chromadb_source_hash'),'signature_drift_count'),
                'multi_active':('SELECT id,COUNT(*),json_group_array(sig) FROM c WHERE state="active" GROUP BY k HAVING COUNT(*)>1',('object_id','active_count','signatures'),'multi_active_count'),
            }
            for name,(query,fields,count_key) in checks.items():
                if quick_mode:
                    result['checks'][name] = 'skipped'
                    continue
                count,samples = 0,[]
                path = root / (name + '.jsonl') if root else None
                stream = path.open('w',encoding=DEFAULT_ENCODING) if path else None
                try:
                    for row in db.execute(query):
                        record = dict(zip(fields,row))
                        if name == 'multi_active':
                            record['signatures'] = json.loads(record['signatures'])
                        count += 1
                        if len(samples)<10:
                            samples.append(record)
                        if stream:
                            stream.write(json.dumps(record,ensure_ascii=False)+'\n')
                finally:
                    if stream: stream.close()
                summary[count_key] = count
                result['checks'][name] = 'fail' if count else 'pass'
                if count:
                    result['details'][name] = samples
                    if path: result['complete_issue_files'][name] = str(path)
            if quick_mode:
                result['checks']['index_consistency'] = 'skipped'
            else:
                details = {}
                for label,query in (
                    ('index_orphans','SELECT i.k FROM i LEFT JOIN a ON a.k=i.k WHERE a.sig IS NULL OR a.sig<>i.sig'),
                    ('index_missing','SELECT a.k FROM a LEFT JOIN i ON a.k=i.k WHERE i.sig IS NULL OR a.sig<>i.sig')):
                    details[label+'_count'] = db.execute(f'SELECT COUNT(*) FROM ({query})').fetchone()[0]
                    details[label+'_sample'] = [row[0] for row in db.execute(query+' LIMIT 10')]
                    if root:
                        path=root/(label+'.jsonl')
                        with path.open('w',encoding=DEFAULT_ENCODING) as stream:
                            for row in db.execute(query):stream.write(json.dumps({'key':row[0]})+'\n')
                        result['complete_issue_files'][label]=str(path)
                result['details']['index_consistency']=details
                result['checks']['index_consistency']='fail' if details['index_orphans_count'] or details['index_missing_count'] else 'pass'
        finally:
            db.close()
    result['status']='issues_found' if 'fail' in result['checks'].values() else 'ok'
    result['finished_at']=utc_now()
    return result


def run_integrity_check(sql_db_path, chromadb_path, state_index_path, output_dir, target='all', quick_mode=False, dry_run=False):
    try:
        client,collection = _connect_chromadb(chromadb_path)
        result=audit_collection(sql_db_path,collection,state_index_path,None if dry_run else output_dir,target,quick_mode)
    except Exception as exc:
        result={'status':'error','checks':{},'errors':[f'{type(exc).__name__}: {exc}']}
    if output_dir and not dry_run:
        root=Path(output_dir);root.mkdir(parents=True,exist_ok=True)
        result['output_dir']=str(root)
        (root/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding=DEFAULT_ENCODING)
        (root/'run_meta.json').write_text(json.dumps({'script_name':SCRIPT_NAME,'script_version':SCRIPT_VERSION,
            'status':result['status'],'audit_target':target,'sql_db_path':str(sql_db_path),'chromadb_path':str(chromadb_path)},indent=2),encoding=DEFAULT_ENCODING)
    return result


def main():
    parser=argparse.ArgumentParser(description='Bounded full vector integrity audit')
    parser.add_argument('--sql-db',required=True,type=Path)
    parser.add_argument('--chromadb-path',required=True,type=Path)
    parser.add_argument('--state-index',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--target',choices=('all','concept','instance'),default=os.environ.get('ACTION_VECTOR_INTEGRITY_TARGET','all'))
    parser.add_argument('--quick',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    result=run_integrity_check(args.sql_db,args.chromadb_path,args.state_index,args.output_dir,args.target,args.quick,args.dry_run)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status']=='ok' else 2


if __name__ == '__main__':
    raise SystemExit(main())
