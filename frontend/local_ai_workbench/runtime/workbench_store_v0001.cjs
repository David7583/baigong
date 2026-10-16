// ============================================================
// 文件名: workbench_store_v0001.cjs
// 中文名: 工作台索引持久化与事件恢复
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: 工作台索引持久化与事件恢复
//
// 职责说明:
// - 工作台索引持久化与事件恢复
//
// 本脚本做什么:
// - 持久化工作台快照、运行和事件序号，保留损坏缓存后恢复索引
//
// 本脚本不做什么:
// - 不替代独立业务资源，不自动授权或调用模型
//
// 制度边界声明:
// - 仅工作台配置和索引可写；测试写入独立临时目录，原始业务数据只读
// - 失败保留分类诊断，不返回伪成功；依赖由项目局部安装
//
// 可更新: True
// ============================================================

// ============================================================
// ALIAS_META
// ============================================================
// alias: workbench_store_v0001
// family: workbench_store
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_store_v0001.cjs
// input:
//   - private workbench index directory and versioned records
// output:
//   - persisted workspace, runs and replayable events
// depends_on:
//   - node:sqlite
//   - node:events
// used_by:
//   - workbench_registry_v0001
//   - workbench_actions_v0001
//   - workbench_server_v0001
// ============================================================

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { DatabaseSync } = require('node:sqlite');
const { EventEmitter } = require('node:events');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_store';
const SCRIPT_NAME = 'workbench_store_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// 核心组件：只保存工作台索引，不接管业务数据
// ============================================================
class Store extends EventEmitter {
  constructor(directory) {
    super();
    this.directory=path.resolve(directory);
    fs.mkdirSync(directory,{recursive:true});
    const target=path.join(directory,'workbench.sqlite');
    try{
      this.db = new DatabaseSync(target);
      this.db.exec("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=3000;");
    }catch(error){
      try{this.db?.close();}catch{}
      if(!/not a database|malformed/i.test(error.message))throw error;
      const suffix='.recovered-'+Date.now();
      for(const extension of ['','-wal','-shm'])if(fs.existsSync(target+extension))fs.renameSync(target+extension,target+extension+suffix);
      this.db=new DatabaseSync(target);this.db.exec("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=3000;");
      this.recovery='Corrupt workbench index preserved; registry will be rediscovered';
    }
    this.db.exec('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,value TEXT NOT NULL)');
    const schema=this.get('schema');
    if(schema && schema !== 'workbench.store.v1') {
      this.db.close();
      const suffix='.schema-backup-'+Date.now();
      for(const extension of ['','-wal','-shm'])if(fs.existsSync(target+extension))fs.renameSync(target+extension,target+extension+suffix);
      this.db=new DatabaseSync(target);
      this.db.exec('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=3000; CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL); CREATE TABLE runs(id TEXT PRIMARY KEY,value TEXT NOT NULL)');
      this.recovery='Unsupported cache schema preserved in backup; cold rediscovery required';
    }
    this.put('schema','workbench.store.v1');
  }
  get(key, fallback=null) { const row=this.db.prepare('SELECT value FROM metadata WHERE key=?').get(key);try{return row?JSON.parse(row.value):fallback;}catch{this.recovery='Invalid cached JSON ignored; original index record retained';return fallback;} }
  put(key,value) { this.db.prepare('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key,JSON.stringify(value)); if(this.assignmentRoot&&['studio_agents','studio_skills','studio_hidden'].includes(key))this.publishSkillAssignments(this.assignmentRoot); }
  publishSkillAssignments(root) {
    this.assignmentRoot=path.resolve(root);
    const hidden=new Set(this.get('studio_hidden',[]));
    const agents=this.get('studio_agents',[]).filter(x=>!hidden.has(x.id)).map(x=>({id:x.id,runtime_agent:x.runtime_agent||null,skill_ids:x.skill_ids||[]}));
    const skills=this.get('studio_skills',[]).filter(x=>!hidden.has(x.id)).map(x=>({id:x.id,name:x.name,path:x.path,enabled:x.enabled===true,sha256:x.sha256,bundle_sha256:x.bundle_sha256||null}));
    const target=path.join(this.assignmentRoot,'runtime/skill_assignments_v0001.json');
    const value={schema_version:'skill-assignments.v1',producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},revision:crypto.randomUUID(),published_at:new Date().toISOString(),market_root:path.join(this.directory,'skill-market'),agents,skills};
    fs.mkdirSync(path.dirname(target),{recursive:true});
    const temporary=target+'.tmp';fs.writeFileSync(temporary,JSON.stringify(value),{encoding:'utf8',mode:0o600});fs.renameSync(temporary,target);
  }
  event(type,payload) {
    const at=new Date().toISOString();
    const seq=Number(this.db.prepare('INSERT INTO events(type,at,payload) VALUES(?,?,?)').run(type,at,JSON.stringify(payload)).lastInsertRowid);
    const event={schema_version:'workbench.event.v1',event_id:String(seq),seq,type,at,payload};
    this.emit('event',event); return event;
  }
  cursor() { return this.db.prepare('SELECT COALESCE(MAX(seq),0) AS seq FROM events').get().seq; }
  events(after=0,limit=500) { return this.db.prepare('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?').all(after,limit).map(x=>({schema_version:'workbench.event.v1',event_id:String(x.seq),seq:x.seq,type:x.type,at:x.at,payload:JSON.parse(x.payload)})); }
  saveRun(run) { this.db.prepare('INSERT INTO runs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value').run(run.run_id,JSON.stringify(run)); }
  findRequest(id) {const row=this.db.prepare("SELECT value FROM runs WHERE json_extract(value,'$.request_id')=? LIMIT 1").get(id);return row?JSON.parse(row.value):null;}
  runs() { return this.db.prepare('SELECT value FROM runs ORDER BY rowid DESC LIMIT 200').all().map(x=>JSON.parse(x.value)); }
  close() { this.removeAllListeners(); this.db.close(); }
}
module.exports={Store};
