// ============================================================
// 文件名: workbench_actions_v0001.cjs
// 中文名: 能力授权、异步运行与可取消操作编排
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: 能力授权、异步运行与可取消操作编排
//
// 职责说明:
// - 能力授权、异步运行与可取消操作编排
//
// 本脚本做什么:
// - 校验能力与确认，执行资源命令，记录幂等运行、超时、取消和进程输出
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
// alias: workbench_actions_v0001
// family: workbench_actions
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_actions_v0001.cjs
// input:
//   - resource action, request_id and explicit confirmation
// output:
//   - run state, result, classified errors and step events
// depends_on:
//   - workbench_contracts_v0001
//   - workbench_adapters_v0001
//   - workbench_store_v0001
//   - workbench_launcher_v0001
// used_by:
//   - workbench_server_v0001
// ============================================================

'use strict';
const crypto = require('node:crypto');
const path = require('node:path');
const fs = require('node:fs');
const {LauncherAdapter}=require('../adapters/workbench_launcher_v0001.cjs');
const {inside,assert,WorkbenchError} = require('./workbench_contracts_v0001.cjs');
const {fetchLocal} = require('../adapters/workbench_adapters_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_actions';
const SCRIPT_NAME = 'workbench_actions_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// 安全默认值与运行契约
// ============================================================
const SAFE=new Set(['inspect','health','logs','runs','attach','detach','open','open_folder','open_output']);
class Actions {
  constructor(registry,store,adapters,root,config={}) {
    this.registry=registry;this.store=store;this.adapters=adapters;this.root=root;this.timeout=config.command_timeout_ms || 15000;
    this.pending=new Map();this.closed=false;
    this.logDirectory=path.join(config.data_directory || path.join(root,'frontend/local_ai_workbench/.runtime'),'process-logs');
    fs.mkdirSync(this.logDirectory,{recursive:true});
    this.launcher=new LauncherAdapter(registry,store,root,this.logDirectory);this.owned=this.launcher.owned;
    for(const run of store.runs())if(['queued','running'].includes(run.state)){run.state='failed';run.error={code:'OBSERVER_RESTARTED',message:'Observer restarted; external outcome must be checked'};store.saveRun(run);}
  }
  submit(request) {
    const resource=this.registry.get(request.resource_id),action=request.action;
    assert(resource.capabilities.includes(action),'CAPABILITY_DENIED','Resource does not declare this capability');
    assert(SAFE.has(action)||request.confirmed===true,'CONFIRMATION_REQUIRED','This operation requires explicit confirmation');
    assert(typeof request.request_id==='string'&&request.request_id.length<=128&&request.request_id.length>0,'REQUEST_ID','Idempotency key required');
    const fingerprint=crypto.createHash('sha256').update(JSON.stringify([request.resource_id,action,request.input || {}])).digest('hex');
    const existing=this.store.findRequest(request.request_id);
    if(existing){assert(existing.fingerprint===fingerprint,'REQUEST_CONFLICT','Idempotency key reused with different input');return existing;}
    assert(this.pending.size<8,'BUSY','Too many concurrent operations');
    const run={schema_version:'workbench.run.v1',run_id:crypto.randomUUID(),request_id:request.request_id,fingerprint,
      resource_id:resource.resource_id,action,state:'queued',created_at:new Date().toISOString(),steps:[]};
    this.store.saveRun(run);this.store.event('run.queued',run);
    const controller=new AbortController();this.pending.set(run.run_id,{controller,run});
    setImmediate(()=>this.execute(run,resource,request,controller));
    return run;
  }
  update(run,state) {run.state=state;run.updated_at=new Date().toISOString();this.store.saveRun(run);this.store.event('run.'+state,run);}
  async execute(run,resource,request,controller) {
    this.update(run,'running');
    const timeout=setTimeout(()=>controller.abort(new WorkbenchError('COMMAND_TIMEOUT','Command timeout')),this.timeout);
    try {
      if(controller.signal.aborted)throw controller.signal.reason;
      run.steps.push({name:'adapter',state:'running',at:new Date().toISOString()});this.store.event('run.step_started',{run_id:run.run_id,step:'adapter'});
      const task=this.dispatch(resource,request,controller.signal);
      const aborted=new Promise((_,reject)=>controller.signal.addEventListener('abort',()=>reject(controller.signal.reason),{once:true}));
      const result=await Promise.race([task,aborted]);
      if(controller.signal.aborted)throw controller.signal.reason;
      run.result=result;run.steps[0].state='completed';this.store.event('run.step_completed',{run_id:run.run_id,step:'adapter'});
      this.update(run,'completed');
    }catch(error){run.error={code:error?.code || 'ACTION_FAILED',message:error?.code?error.message:'Resource action failed'};if(run.steps[0])run.steps[0].state='failed';this.update(run,controller.signal.aborted&&error?.code!=='COMMAND_TIMEOUT'?'cancelled':'failed');}
    finally{clearTimeout(timeout);this.pending.delete(run.run_id);}
  }
  async dispatch(resource,request,signal) {
    const id=resource.resource_id,m=resource.manifest,action=request.action;
    if(action==='inspect')return this.registry.public(resource);
    if(['health','attach','detach'].includes(action))return this.launcher.dispatch(resource,request,signal);
    if(action==='runs')return this.store.runs().filter(x=>x.resource_id===id);
    if(action==='logs')return this.processLogs(id);
    if(['open','open_folder','open_output'].includes(action)){
      if(action==='open'&&m.endpoint)return {open_url:new URL(m.endpoint).origin};
      const target=inside(this.root,action==='open_output'?(m.output_path || m.path):m.path);
      return {open_path:action==='open_folder'&&!fs.statSync(target).isDirectory()?path.dirname(target):target};
    }
    if(['launch','stop','restart'].includes(action))return this.launcher.dispatch(resource,request,signal);
    const command=m.commands?.[action];
    assert(command,'ACTION_UNIMPLEMENTED','No adapter command declared');
    return fetchLocal(command.url,{method:command.method || 'POST',headers:{'Content-Type':'application/json'},body:(command.method || 'POST')==='GET'?undefined:JSON.stringify(request.input || {})},signal,this.timeout);
  }
  cancel(id) {
    const pending=this.pending.get(id);assert(pending,'RUN_NOT_ACTIVE','Run is not active');
    pending.controller.abort(new WorkbenchError('CANCELLED','Observation/request cancelled; external effects may already exist',409));return {run_id:id,cancel_requested:true};
  }
  processLogs(id){
    this.registry.get(id);
    const log=this.store.get('process_logs',{})[id];
    if(!log||!fs.existsSync(log))return {resource_id:id,mode:'observer-events',items:this.store.events(Math.max(0,this.store.cursor()-100)).filter(x=>x.payload.resource_id===id)};
    assert(path.resolve(log).startsWith(path.resolve(this.logDirectory)+path.sep),'LOG_PATH','Invalid log index');
    const size=fs.statSync(log).size,buffer=Buffer.alloc(Math.min(size,65536)),fd=fs.openSync(log,'r');
    try{fs.readSync(fd,buffer,0,buffer.length,Math.max(0,size-buffer.length));}finally{fs.closeSync(fd);}
    const text=buffer.toString('utf8').replace(/(api[_-]?key|authorization|token)\s*[:=]\s*[^\s,;]+/gi,'$1=[REDACTED]');
    return {resource_id:id,mode:'process-output',text,truncated:size>buffer.length};
  }
  async close(){
    this.closed=true;
    this.launcher.close();
    for(const {controller} of this.pending.values())controller.abort(new WorkbenchError('OBSERVER_SHUTDOWN','Observer stopped'));
    while(this.pending.size)await new Promise(resolve=>setTimeout(resolve,10));
    // Independent resources deliberately survive observer shutdown.
  }
}
module.exports={Actions};
