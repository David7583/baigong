// ============================================================
// 文件名: workbench_launcher_v0001.cjs
// 中文名: 独立进程启动与观察连接适配器
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / adapters
// 脚本定位: 独立进程启动与观察连接适配器
//
// 职责说明:
// - 独立进程启动与观察连接适配器
//
// 本脚本做什么:
// - 启动声明的独立进程，维护当前观察者的 PID 所有权，适配连接与停止
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
// alias: workbench_launcher_v0001
// family: workbench_launcher
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/adapters/workbench_launcher_v0001.cjs
// input:
//   - validated resource launch or connection action
// output:
//   - independent process handle or connection observation
// depends_on:
//   - workbench_contracts_v0001
//   - workbench_store_v0001
// used_by:
//   - workbench_actions_v0001
// ============================================================

'use strict';
const crypto=require('node:crypto');
const path=require('node:path');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {inside,assert}=require('../runtime/workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING='utf8';
const SCRIPT_FAMILY='workbench_launcher';
const SCRIPT_NAME='workbench_launcher_v0001';
const SCRIPT_VERSION='v0001';

// ============================================================
// 资源生命周期适配：关闭观察者不关闭独立资源
// ============================================================
class LauncherAdapter {
  constructor(registry,store,root,logDirectory){
    this.registry=registry;this.store=store;this.root=root;this.logDirectory=logDirectory;
    this.owned=new Map();this.closed=false;
  }
  async dispatch(resource,request,signal){
    const id=resource.resource_id,m=resource.manifest,action=request.action;
    if(signal?.aborted)throw signal.reason;
    if(action==='health')return this.registry.probe(id,signal);
    if(action==='attach')return this.registry.attach(id,signal);
    if(action==='detach')return this.registry.detach(id);
    if(action==='launch'){
      assert(m.launch && ['node','python'].includes(m.launch.runtime),'LAUNCH_CONTRACT','A supported launcher is required');
      const entry=inside(this.root,m.launch.script);
      assert(!this.owned.has(id),'ALREADY_LAUNCHED','A launched process is already tracked');
      const executable=m.launch.runtime==='node'?process.execPath:inside(this.root,'.venv/Scripts/python.exe');
      const args=m.launch.args || [];assert(Array.isArray(args)&&args.every(x=>typeof x==='string'),'LAUNCH_ARGUMENTS','Invalid arguments');
      const log=path.join(this.logDirectory,crypto.randomUUID()+'.log'),descriptor=fs.openSync(log,'a');
      let child;
      try{child=spawn(executable,[entry,...args],{cwd:this.root,detached:true,stdio:['ignore',descriptor,descriptor],windowsHide:true,shell:false});}
      finally{fs.closeSync(descriptor);}
      await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);});
      child.unref();this.owned.set(id,child);resource.runtime={status:'running',pid:child.pid};this.store.event('resource.state_changed',{resource_id:id,runtime:resource.runtime});
      const logs=this.store.get('process_logs',{});logs[id]=log;this.store.put('process_logs',logs);
      child.once('exit',()=>{this.owned.delete(id);resource.runtime={status:'stopped'};if(!this.closed)this.store.event('resource.state_changed',{resource_id:id,runtime:resource.runtime});});
      return {pid:child.pid,ownership:'started_by_this_observer',independent:true};
    }
    if(action==='stop'||action==='restart'){
      const child=this.owned.get(id);assert(child,'PROCESS_OWNERSHIP','Cannot stop an attached or unverified process');
      await new Promise((resolve,reject)=>{child.once('exit',resolve);child.once('error',reject);child.kill();});
      this.owned.delete(id);
      return action==='restart'?this.dispatch(resource,{...request,action:'launch'},signal):{stopped:true};
    }
    assert(false,'LAUNCH_ACTION','Unsupported launcher operation');
  }
  close(){this.closed=true;}
}
module.exports={LauncherAdapter};

