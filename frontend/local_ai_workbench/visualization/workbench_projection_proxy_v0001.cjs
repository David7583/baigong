// ============================================================
// 文件名: workbench_projection_proxy_v0001.cjs
// 中文名: 投影请求隔离、取消与超时恢复
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / visualization
// 脚本定位: 投影请求隔离、取消与超时恢复
//
// 职责说明:
// - 投影请求隔离、取消与超时恢复
//
// 本脚本做什么:
// - 限制投影请求并发与时限，隔离失效工作线程并匹配请求响应
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
// alias: workbench_projection_proxy_v0001
// family: workbench_projection_proxy
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/visualization/workbench_projection_proxy_v0001.cjs
// input:
//   - resource identity, projection request and cancellation signal
// output:
//   - asynchronous bounded result or timeout error
// depends_on:
//   - node:worker_threads
//   - workbench_projection_worker_v0001
//   - workbench_contracts_v0001
// used_by:
//   - workbench_server_v0001
// ============================================================

'use strict';
const {Worker}=require('node:worker_threads');
const path=require('node:path');
const {WorkbenchError}=require('../runtime/workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_projection_proxy';
const SCRIPT_NAME = 'workbench_projection_proxy_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// 主线程代理：限并发、限时间，不阻塞事件与健康查询
// ============================================================
class ProjectionProxy{
  constructor(registry,root,store,config={}){
    this.registry=registry;this.root=root;this.store=store;this.config=config;this.pending=new Map();this.sequence=0;this.worker=null;this.closed=false;
  }
  start(){
    if(this.worker)return;
    this.worker=new Worker(path.join(__dirname,'workbench_projection_worker_v0001.cjs'),{workerData:{root:this.root,config:this.config}});
    this.worker.on('message',message=>{
      if(message.event){if(!this.closed)this.store.event(message.event.type,message.event.payload);return;}
      const request=this.pending.get(message.id);if(!request)return;
      request.cleanup();this.pending.delete(message.id);
      if(message.error)request.reject(new WorkbenchError(message.error.code,message.error.message,502));else request.resolve(message.value);
    });
    this.worker.on('error',()=>this.fail('Projection worker failed'));
    this.worker.on('exit',()=>{this.worker=null;if(this.pending.size)this.fail('Projection worker exited');});
  }
  fail(message){
    for(const item of this.pending.values()){item.cleanup();item.reject(new WorkbenchError('PROJECTION_WORKER',message,502));}
    this.pending.clear();
  }
  call(method,resourceId,input,signal){
    if(this.closed)return Promise.reject(new WorkbenchError('CLOSED','Projection service closed'));
    if(signal?.aborted)return Promise.reject(new WorkbenchError('CANCELLED','Projection cancelled'));
    if(this.pending.size>=4)return Promise.reject(new WorkbenchError('PROJECTION_BUSY','Projection queue full',429));
    this.start();
    return new Promise((resolve,reject)=>{
      const id=++this.sequence;
      const abort=()=>{this.worker?.postMessage({cancel:id});const item=this.pending.get(id);if(item){item.cleanup();this.pending.delete(id);reject(new WorkbenchError('CANCELLED','Projection cancelled',409));}};
      const timer=setTimeout(()=>{this.fail('Projection time budget exceeded; retry with a narrower view');this.worker?.terminate();},this.config.projection_timeout_ms || 60000);
      const cleanup=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);};
      this.pending.set(id,{resolve,reject,cleanup});signal?.addEventListener('abort',abort,{once:true});
      this.worker.postMessage({id,method,resource_id:resourceId,input,resources:[...this.registry.resources.values()]});
    });
  }
  query(id,input,signal){return this.call('query',id,input,signal);}
  detail(id,input,signal){return this.call('detail',id,input,signal);}
  async close(){this.closed=true;this.fail('Projection service closed');if(this.worker)await this.worker.terminate();}
}
module.exports={ProjectionProxy};
