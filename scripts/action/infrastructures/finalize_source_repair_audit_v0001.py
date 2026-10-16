# 文件名: finalize_source_repair_audit_v0001.py
# 中文名: 来源补录验收登记
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / recovery_audit
# 脚本定位: 将已独立验证的修复凭证关联到 SQL 运行日志
# 职责说明: 核对实际实例数量后追加审计，不改事实
# 本脚本做什么: 验证 run_id、数量、库绑定并登记凭证哈希
# 本脚本不做什么: 不补造源时间、不重写修复前日志、不覆盖事实
# 制度边界声明: 时间表示验收登记时间；原始修复证据原样保留
# 可更新: True
# ALIAS_META
# alias: finalize_source_repair_audit_v0001
# family: source_repair_audit
# role: verified_receipt_registration
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/finalize_source_repair_audit_v0001.py
# input: verified repair report and matching SQL configuration
# output: append-only SQL audit references
# depends_on: sql_writer_v0005
# used_by: authorized local recovery
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/action/anchor'))
from sql_writer_v0005 import SqlWriter
SCRIPT_NAME='finalize_source_repair_audit_v0001.py'
SCRIPT_FAMILY='source_repair_audit'
SCRIPT_VERSION='v0001'
DEFAULT_ENCODING='utf-8'

def main():
    p=argparse.ArgumentParser();p.add_argument('--report',required=True);p.add_argument('--sql-config',required=True);a=p.parse_args()
    report=Path(a.report).resolve();raw=report.read_bytes();value=json.loads(raw)
    if value['status']!='verified' or value['verified_records']!=value['last_record']:raise ValueError('Independent full verification required')
    binding=value['binding'];run_id='source_repair_'+binding['semantic_sha256'][:16]
    digest=hashlib.sha256(raw).hexdigest();writer=SqlWriter(a.sql_config)
    configured=writer.config.sqlite_path
    if not configured.is_absolute():configured=ROOT/configured
    if configured.resolve()!=Path(binding['action_db']).resolve():raise ValueError('Database binding differs')
    conn=writer.connect()
    try:
        count=conn.execute('SELECT COUNT(*) FROM instance_units WHERE run_id=?',(run_id,)).fetchone()[0]
        if count!=value['counts']['instance']:raise ValueError('Repair instance count differs')
        old=conn.execute('SELECT input_hash,output_hash FROM ingestion_run_log WHERE run_id=?',(run_id,)).fetchone()
    finally:conn.close()
    if old is None:
        writer.record_run_event(run_id=run_id,script_name=SCRIPT_FAMILY,script_version=SCRIPT_VERSION,status='started',input_hash=binding['semantic_sha256'],error_summary=None)
    if old is None or tuple(old)==(binding['semantic_sha256'],None):
        writer.record_run_event(run_id=run_id,script_name=SCRIPT_FAMILY,script_version=SCRIPT_VERSION,status='finished',input_hash=binding['semantic_sha256'],output_hash=digest,error_summary=None)
    elif tuple(old)!=(binding['semantic_sha256'],digest):raise ValueError('Existing audit differs')
    db=sqlite3.connect(binding['data_db'])
    try:
        previous=db.execute('SELECT input_hash,record_count FROM data_run_log WHERE run_id=?',(run_id,)).fetchone()
        if previous and previous!=(binding['language_sha256'],value['counts']['data']):raise ValueError('Data audit differs')
        if not previous:
            now=datetime.now(timezone.utc).isoformat()
            with db:db.execute('INSERT INTO data_run_log(run_id,script_name,script_version,started_at,finished_at,input_hash,record_count) VALUES (?,?,?,?,?,?,?)',(run_id,SCRIPT_FAMILY,SCRIPT_VERSION,now,now,binding['language_sha256'],value['counts']['data']))
    finally:db.close()
    print(json.dumps({'status':'verified_audit_registered','run_id':run_id,'verification_sha256':digest,'records':count,'timestamp_semantics':'verification_registration_time_not_source_event_time'}))

if __name__=='__main__':main()
