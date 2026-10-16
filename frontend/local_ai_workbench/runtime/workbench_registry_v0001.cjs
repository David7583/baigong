// ============================================================
// 文件名: workbench_registry_v0001.cjs
// 中文名: 资源发现、快照与状态增量同步
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: 资源发现、快照与状态增量同步
//
// 职责说明:
// - 资源发现、快照与状态增量同步
//
// 本脚本做什么:
// - 扫描并验证资源声明、去重身份、保存快照并调度有限并发健康观测
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
// alias: workbench_registry_v0001
// family: workbench_registry
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_registry_v0001.cjs
// input:
//   - manifest directory, discovery config and adapter observations
// output:
//   - normalized resources, problems and registry events
// depends_on:
//   - workbench_contracts_v0001
//   - workbench_adapters_v0001
//   - workbench_store_v0001
// used_by:
//   - workbench_server_v0001
// ============================================================

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {manifest,identifier,WorkbenchError} = require('./workbench_contracts_v0001.cjs');
const {state} = require('../adapters/workbench_adapters_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_registry';
const SCRIPT_NAME = 'workbench_registry_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// 核心组件：来源明确的观测注册表
// ============================================================
class Registry {
  constructor(store,adapters,directory,config={}) {
    this.store=store; this.adapters=adapters; this.directory=directory; this.config=config;
    this.resources=new Map(); this.problems=[]; this.busy=false; this.closed=false;
    this.enabled=store.get('business_enabled',config.empty_start!==true);
    this.selectedIds=this.enabled?store.get('business_resource_ids',null):[];
    this.detached=new Set(store.get('detached',[]));
    const snapshot=store.get('snapshot',[]);
    for(const item of Array.isArray(snapshot)?snapshot:[]) {
      if(item.resource_id!=='workbench.system'&&(!this.enabled||(this.selectedIds&&!this.selectedIds.includes(item.resource_id))))continue;
      try {
        item.manifest=manifest(item.manifest);
        this.resources.set(item.resource_id,{...item,stale:true,health:{status:'unknown'},connection:{status:'disconnected'}});
      }catch{this.problem('SNAPSHOT_INVALID','Invalid cached resource');}
    }
    this.upsert({schema_version:'workbench.resource.v1',resource:{id:'workbench.system',type:'system',name:'Workbench System'},adapter:'system',capabilities:['inspect','health','logs','runs'],views:[{view_id:'system.overview',view_type:'dashboard',renderer:'DOM',data_mode:'snapshot'},{view_id:'system.graph',view_type:'graph',renderer:'Canvas',data_mode:'viewport',lod:true}]},'builtin');
    if(store.recovery)this.problem('INDEX_RECOVERED',store.recovery);
  }
  problem(code,message,source='registry') {
    if(this.problems.some(x=>x.code===code&&x.message===message&&x.source===source))return;
    const problem={code,message,source,at:new Date().toISOString()};
    this.problems.push(problem);this.problems=this.problems.slice(-100);
    this.store.event('system.warning',problem);
  }
  upsert(raw,source) {
    const m=manifest(raw), id=m.resource.id, previous=this.resources.get(id);
    if(previous && previous.source!==source)throw new WorkbenchError('RESOURCE_CONFLICT','Resource identity belongs to another source');
    for(const item of this.resources.values())if(item.resource_id!==id && item.views.some(v=>m.views.some(next=>next.view_id===v.view_id)))throw new WorkbenchError('VIEW_CONFLICT','View identity belongs to another resource');
    const hash=crypto.createHash('sha256').update(JSON.stringify(m)).digest('hex');
    if(previous && previous.source_hash===hash) {previous.stale=this.detached.has(id);return previous;}
    const next={resource_id:id,resource_type:m.resource.type,display_name:m.resource.name,capabilities:m.capabilities,views:m.views,
      ...state(),manifest:m,source,source_hash:hash,stale:false,updated_at:new Date().toISOString()};
    if(previous&&previous.manifest.adapter===m.adapter&&previous.manifest.path===m.path&&previous.manifest.endpoint===m.endpoint){for(const key of ['health','runtime','connection','detail','observed_at','observed_source'])if(previous[key]!==undefined)next[key]=previous[key];}
    this.resources.set(id,next);this.store.event(previous?'resource.changed':'resource.discovered',{resource_id:id});
    return next;
  }
  public(item) {
    const {manifest:m,...data}=item;
    return {...data,version:m.metadata?.version || 'v1',description:m.description || '',
      metadata:m.metadata || {},database:m.adapter==='database'?{driver:m.driver,read_only:true}:null,entry:{path:m.path || null,endpoint:m.endpoint || null}};
  }
  list() { return [...this.resources.values()].map(x=>this.public(x)); }
  get(id) {identifier(id);const value=this.resources.get(id);if(!value)throw new WorkbenchError('RESOURCE_NOT_FOUND','Resource not found',404);return value;}
  persist() {this.store.put('snapshot',[...this.resources.values()]);}
  async refresh() {
    if(this.busy || this.closed)return;
    if(!this.enabled){await this.probe('workbench.system');return;}
    this.busy=true;
    const seen=new Set(['workbench.system']); const invalidSources=new Set();
    try {
      fs.mkdirSync(this.directory,{recursive:true});
      for(const file of fs.readdirSync(this.directory).filter(x=>x.endsWith('.json'))) {
        const source='manifest:'+file;
        try {
          const full=path.join(this.directory,file);
          if(fs.lstatSync(full).isSymbolicLink() || fs.statSync(full).size>256*1024)throw new WorkbenchError('MANIFEST_SIZE','Manifest rejected');
          const raw=JSON.parse(fs.readFileSync(full,'utf8'));
          if(this.selectedIds&&!this.selectedIds.includes(raw.resource?.id))continue;
          const item=this.upsert(raw,source); seen.add(item.resource_id);
        }catch(error){invalidSources.add(source);this.problem(error.code || 'INVALID_MANIFEST','Invalid manifest: '+file,source);}
      }
      if(this.config.legacy&&!this.selectedIds) {
        try{for(const raw of this.adapters.legacyCatalog()) {
          const item=this.upsert(raw,'legacy:'+raw.resource.id);seen.add(item.resource_id);
        }}catch{invalidSources.add('legacy');this.problem('LEGACY_DISCOVERY','Legacy catalog unavailable');}
      }
      for(const [id,item] of this.resources) {
        if(!seen.has(id)) {
          if(invalidSources.has(item.source)||(invalidSources.has('legacy')&&item.source.startsWith('legacy:'))) {
            item.stale=true;item.health={status:'degraded'};this.store.event('resource.health_changed',{resource_id:id});
          }else{this.resources.delete(id);this.store.event('resource.removed',{resource_id:id});}
        }
      }
      const pending=[...this.resources.values()];
      // Bounded concurrency prevents one resource set from exhausting sockets.
      let index=0;
      await Promise.all(Array.from({length:Math.min(4,pending.length)},async()=>{
        while(index<pending.length && !this.closed){const resource=pending[index++];if(!resource.stale)await this.probe(resource.resource_id);}
      }));
      this.persist(); this.store.event('registry.changed',{count:this.resources.size});
    }finally{this.busy=false;}
  }
  async probe(id,signal) {
    const item=this.get(id);
    if(this.detached.has(id))return this.public(item);
    const old=JSON.stringify([item.health,item.runtime,item.connection,item.detail,item.metrics,item.bytes,item.source_mtime_ms]);
    const observation=await this.adapters.probe(item,signal);
    if(this.closed)return this.public(item);
    const {source:observedSource,...observedState}=observation;
    Object.assign(item,observedState,{stale:false,observed_source:observedSource || item.observed_source});
    const changed=old!==JSON.stringify([item.health,item.runtime,item.connection,item.detail,item.metrics,item.bytes,item.source_mtime_ms]);
    if(changed)this.store.event('resource.state_changed',{resource_id:id,health:item.health,runtime:item.runtime,connection:item.connection});
    return this.public(item);
  }
  detach(id) {
    const item=this.get(id);this.detached.add(id);item.connection={status:'disconnected'};item.stale=true;
    this.store.put('detached',[...this.detached]);this.persist();this.store.event('connection.disconnected',{resource_id:id});
    return this.public(item);
  }
  async attach(id,signal) {
    this.get(id);this.detached.delete(id);this.store.put('detached',[...this.detached]);
    const result=await this.probe(id,signal);this.persist();this.store.event('connection.changed',{resource_id:id,connection:result.connection});return result;
  }
  import(raw) {
    const m=manifest(raw); const source='manifest:'+m.resource.id.replace(/:/g,'_')+'.json';
    const previous=this.resources.get(m.resource.id);
    if(previous && previous.source!==source)throw new WorkbenchError('RESOURCE_CONFLICT','Cannot overwrite another resource');
    for(const item of this.resources.values())if(item.resource_id!==m.resource.id && item.views.some(v=>m.views.some(next=>next.view_id===v.view_id)))throw new WorkbenchError('VIEW_CONFLICT','View identity belongs to another resource');
    const target=path.join(this.directory,source.slice(9));
    fs.mkdirSync(this.directory,{recursive:true});
    const temp=target+'.tmp';fs.writeFileSync(temp,JSON.stringify(m,null,2));fs.renameSync(temp,target);
    this.enabled=true;this.store.put('business_enabled',true);
    if(this.selectedIds&&!this.selectedIds.includes(m.resource.id)){this.selectedIds.push(m.resource.id);this.store.put('business_resource_ids',this.selectedIds);}
    const item=this.upsert(m,source);this.persist();return this.public(item);
  }
  async unload(){
    while(this.busy)await new Promise(resolve=>setTimeout(resolve,20));
    this.persist();this.enabled=false;this.store.put('business_enabled',false);
    this.store.put('business_previous_resource_ids',this.selectedIds);this.selectedIds=[];this.store.put('business_resource_ids',[]);
    for(const id of this.resources.keys())if(id!=='workbench.system')this.resources.delete(id);
    this.store.event('business.unloaded',{});
  }
  async close() {
    this.closed=true;
    while(this.busy)await new Promise(resolve=>setTimeout(resolve,20));
  }
}
module.exports={Registry};
