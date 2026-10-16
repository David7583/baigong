// ============================================================
// 文件名: workbench_adapters_v0001.cjs
// 中文名: 独立资源只读探测与旧版接入
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / adapters
// 脚本定位: 独立资源只读探测与旧版接入
//
// 职责说明:
// - 独立资源只读探测与旧版接入
//
// 本脚本做什么:
// - 观测独立资源健康状态并将旧版目录、状态和日志转为工作台读取结果
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
// alias: workbench_adapters_v0001
// family: workbench_adapters
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/adapters/workbench_adapters_v0001.cjs
// input:
//   - normalized resource manifest and legacy project root
// output:
//   - health, runtime, connection and legacy read-only records
// depends_on:
//   - Node.js standard library
//   - workbench_contracts_v0001
// used_by:
//   - workbench_registry_v0001
//   - workbench_actions_v0001
// ============================================================

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { DatabaseSync } = require('node:sqlite');
const {inside,localUrl,WorkbenchError} = require('../runtime/workbench_contracts_v0001.cjs');
const {readDatabase}=require('./workbench_database_adapter_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_adapters';
const SCRIPT_NAME = 'workbench_adapters_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// 工具函数与状态观测契约
// ============================================================
function state(health='unknown',runtime='unknown',connection='not_applicable',detail='') {
  return {health:{status:health},runtime:{status:runtime},connection:{status:connection},observed_at:new Date().toISOString(),detail};
}
async function fetchLocal(url, options={}, signal, timeout=1800) {
  localUrl(url);
  const combined=signal?AbortSignal.any([signal,AbortSignal.timeout(timeout)]):AbortSignal.timeout(timeout);
  const response=await fetch(url,{...options,redirect:'error',signal:combined});
  if(!response.ok) throw new WorkbenchError('RESOURCE_HTTP','Resource returned HTTP '+response.status,502);
  const reader=response.body.getReader(); let size=0; const chunks=[];
  try {
    while(true) { const {done,value}=await reader.read(); if(done)break; size+=value.length; if(size>2*1024*1024)throw new WorkbenchError('RESOURCE_SIZE','Resource response exceeds budget',502); chunks.push(Buffer.from(value)); }
  } finally { await reader.cancel().catch(()=>{}); }
  const text=Buffer.concat(chunks).toString('utf8');
  try{return JSON.parse(text);}catch{ return {message:text.slice(0,2000)}; }
}
class Adapters {
  constructor(root,options={}) { this.root=root; this.timeout=options.health_timeout_ms || 1800; }
  async probe(resource,signal) {
    const m=resource.manifest;
    if(m.adapter==='database'){
      try{const result=await readDatabase(this.root,m,{},signal,10000);return {...state('available','not_applicable','connected'),driver:m.driver,read_only:true,table_count:result.tables.length,source:m.path};}
      catch(error){if(signal?.aborted)throw error;return {...state('unavailable','not_applicable','error',error.message),driver:m.driver,read_only:true};}
    }
    if(m.adapter==='system') return {...state('available','running','connected'),metrics:{cpu_count:os.cpus().length,load:os.loadavg(),memory_total:os.totalmem(),memory_free:os.freemem(),uptime:os.uptime()},source:'node:os'};
    if(m.adapter==='sqlite') {
      let db;
      try { db=new DatabaseSync(inside(this.root,m.path),{readOnly:true}); db.exec('PRAGMA query_only=ON'); const tables=db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 100").all(); return {...state('available','not_applicable','connected'),tables,source:m.path}; }
      catch {return state('unavailable','not_applicable','error','Read-only database connection failed');}
      finally{db?.close();}
    }
    if(m.adapter==='dataset') {
      try { const stats=fs.statSync(inside(this.root,m.path)); return {...state('available','not_applicable','connected'),source:m.path,bytes:stats.size,source_mtime_ms:stats.mtimeMs}; } catch {return state('unavailable','not_applicable','error','Dataset source missing');}
    }
    if(m.adapter==='process') {
      if(!m.pid) return state('unknown','unknown','not_applicable','No verified process identity');
      try{process.kill(m.pid,0);return state('unknown','running','not_applicable','PID observed; health not inferred');}catch{return state('unknown','unknown','not_applicable','PID not observable');}
    }
    if(m.endpoint) {
      try {
        const body=await fetchLocal(m.endpoint,{},signal,this.timeout);
        const failed=body.status==='error'||body.can_proceed===false||body.status==='degraded';
        return {...state(failed?'degraded':'available','unknown','connected','HTTP endpoint observed; process state not inferred'),source:m.endpoint};
      }catch(error) {
        if(signal?.aborted)throw error;
        return state('unknown','unknown','error',error.name==='TimeoutError'?'Health timeout':'Endpoint unavailable');
      }
    }
    return state('unknown','unknown','not_applicable','No health contract declared');
  }
  legacyManifest(item) {
    return {schema_version:'workbench.resource.v1',resource:{id:'software:'+item.software_id,type:'application',name:item.display_name},adapter:'http',
      capabilities:['inspect','health','attach','detach','open_folder'],endpoint:item.health_target || undefined,
      path:item.entry_point,legacy_id:item.software_id,metadata:{version:item.version,registered:item.registered,issues:item.issues},views:[]};
  }
  legacyCatalog() {
    const file=path.join(this.root,'scripts/action/development/functions/workbench_software_catalog_v0001.js');
    if(!fs.existsSync(file))return [];
    return require(file).scanSoftwareCatalog(this.root).map(x=>this.legacyManifest(x));
  }
  legacyData(kind) {
    const base=path.join(this.root,'scripts/action/development/functions');
    const database=path.join(this.root,'sql/action.db');
    if(kind==='functions'||kind==='tasks') {
      const reader=require(path.join(base,'workbench_action_catalog_v0001.js'));
      return kind==='functions'?reader.listFunctions(database):reader.listTasks(database);
    }
    if(kind==='system')return require(path.join(base,'workbench_system_status_v0001.js')).collectSystemStatus(this.root);
    if(kind==='events')return require(path.join(base,'workbench_event_reader_v0001.js')).readRecentEvents(path.join(this.root,'logs/local_ai_workbench_events.jsonl'));
    if(kind==='usage')return require(path.join(base,'workbench_token_usage_v0001.js')).aggregateTokenUsage(path.join(this.root,'actioning/ai_adapter/call_logs'),12);
    throw new WorkbenchError('LEGACY_KIND','Unknown legacy view');
  }
}
module.exports={Adapters,state,fetchLocal};
