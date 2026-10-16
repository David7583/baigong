// ============================================================
// 文件名: workbench_projection_worker_v0001.cjs
// 中文名: 隔离执行磁盘投影与长查询
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / visualization
// 脚本定位: 隔离执行磁盘投影与长查询
//
// 职责说明:
// - 隔离执行磁盘投影与长查询
//
// 本脚本做什么:
// - 在工作线程中处理索引与投影请求并回传结果、事件和分类错误
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
// alias: workbench_projection_worker_v0001
// family: workbench_projection_worker
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/visualization/workbench_projection_worker_v0001.cjs
// input:
//   - worker query, detail and cancellation messages
// output:
//   - projection result or worker error message
// depends_on:
//   - node:worker_threads
//   - workbench_projection_v0001
// used_by:
//   - workbench_projection_proxy_v0001
// ============================================================

'use strict';
const {parentPort,workerData}=require('node:worker_threads');
const {ProjectionService}=require('./workbench_projection_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_projection_worker';
const SCRIPT_NAME = 'workbench_projection_worker_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// Worker 入口：计算不占用 HTTP / Event 主线程
// ============================================================
function main(){
  let resources=[];
  const registry={get:id=>{const item=resources.find(x=>x.resource_id===id);if(!item)throw new Error('RESOURCE_NOT_FOUND');return item;},list:()=>resources};
  const store={cursor:()=>0,event:(type,payload)=>parentPort.postMessage({event:{type,payload}})};
  const service=new ProjectionService(registry,workerData.root,store,workerData.config);
  const controllers=new Map();
  parentPort.on('message',async message=>{
    if(message.cancel){controllers.get(message.cancel)?.abort();return;}
    const controller=new AbortController();controllers.set(message.id,controller);
    resources=message.resources;
    try{
      const value=message.method==='detail'?await service.detail(message.resource_id,message.input,controller.signal):await service.query(message.resource_id,message.input,controller.signal);
      parentPort.postMessage({id:message.id,value});
    }catch(error){parentPort.postMessage({id:message.id,error:{code:error.code || 'PROJECTION_FAILED',message:error.code?error.message:'Projection failed'}});}
    finally{controllers.delete(message.id);}
  });
}
if(!require('node:worker_threads').isMainThread)main();
module.exports={main};
