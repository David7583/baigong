// ============================================================
// 文件名: workbench_database_adapter_v0001.cjs
// 中文名: 多数据库只读挂载进程桥
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / adapters
// 脚本定位: 多数据库只读挂载进程桥
//
// 职责说明:
// - 多数据库只读挂载进程桥
//
// 本脚本做什么:
// - 有界排队启动只读数据库子进程，执行超时、取消及返回体预算
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
// alias: workbench_database_adapter_v0001
// family: workbench_database_adapter
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/adapters/workbench_database_adapter_v0001.cjs
// input:
//   - database driver, existing source path and read operation
// output:
//   - bounded database result or classified process failure
// depends_on:
//   - workbench_database_reader_v0001
//   - workbench_contracts_v0001
// used_by:
//   - workbench_adapters_v0001
//   - workbench_server_v0001
// ============================================================

'use strict';
const path=require('node:path');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {WorkbenchError,assert}=require('../runtime/workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_database_adapter';
const SCRIPT_NAME = 'workbench_database_adapter_v0001';
const SCRIPT_VERSION = 'v0001';
let activeReaders=0;
const waitingReaders=[];

function releaseReader(){activeReaders--;waitingReaders.shift()?.();}
function acquireReader(signal,timeout){
  if(signal?.aborted)return Promise.reject(new WorkbenchError('CANCELLED','Database read cancelled',409));
  if(activeReaders<4){activeReaders++;return Promise.resolve();}
  assert(waitingReaders.length<16,'DATABASE_BUSY','Database read queue is full; retry later');
  return new Promise((resolve,reject)=>{
    const cleanup=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);};
    const remove=()=>{const index=waitingReaders.indexOf(grant);if(index>=0)waitingReaders.splice(index,1);};
    const abort=()=>{remove();cleanup();reject(new WorkbenchError('CANCELLED','Queued database read cancelled',409));};
    const grant=()=>{cleanup();activeReaders++;resolve();};
    const timer=setTimeout(()=>{remove();cleanup();reject(new WorkbenchError('DATABASE_TIMEOUT','Queued database read timed out',504));},timeout);
    signal?.addEventListener('abort',abort,{once:true});waitingReaders.push(grant);
  });
}

// ============================================================
// 输入校验与进程隔离
// ============================================================
function python(){
  let root=__dirname;
  while(!fs.existsSync(require('../runtime/release_paths_v0001.cjs').python(root))){
    const parent=path.dirname(root);if(parent===root)throw new WorkbenchError('PYTHON_MISSING','Project Python environment not found');root=parent;
  }
  return require('../runtime/release_paths_v0001.cjs').python(root);
}
function databasePath(root,value){
  assert(typeof value==='string'&&value.length>0,'DATABASE_PATH','Database path required');
  const full=path.isAbsolute(value)?path.resolve(value):path.resolve(root,value);
  assert(fs.existsSync(full),'DATABASE_MISSING','Database does not exist');
  return fs.realpathSync(full);
}
async function runReader(root,manifest,input={},signal,timeout=10000,script='workbench_database_reader_v0001.py'){
  const source=databasePath(root,manifest.path);
  assert(['sqlite','duckdb','chroma'].includes(manifest.driver),'DATABASE_DRIVER','Unsupported database driver');
  const executable=python();
  const started=Date.now();await acquireReader(signal,timeout);
  timeout=Math.max(1,timeout-(Date.now()-started));
  return new Promise((resolve,reject)=>{
    let child;
    try{child=spawn(executable,[path.join(__dirname,script)],{stdio:['pipe','pipe','pipe'],windowsHide:true,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONIOENCODING:'utf-8'}});}
    catch(error){releaseReader();reject(error);return;}
    let text='',size=0,finished=false;
    const finish=(error,value)=>{if(finished)return;finished=true;releaseReader();clearTimeout(timer);signal?.removeEventListener('abort',abort);if(error)reject(error);else resolve(value);};
    const abort=()=>{child.kill();finish(new WorkbenchError('CANCELLED','Database read cancelled',409));};
    const timer=setTimeout(()=>{child.kill();finish(new WorkbenchError('DATABASE_TIMEOUT','Database read timed out',504));},timeout);
    signal?.addEventListener('abort',abort,{once:true});
    child.on('error',()=>finish(new WorkbenchError('DATABASE_PROCESS','Database reader could not start',502)));
    child.stdout.on('data',chunk=>{size+=chunk.length;if(size>2*1024*1024){child.kill();finish(new WorkbenchError('DATABASE_BUDGET','Database response exceeds budget'));}else text+=chunk;});
    child.stderr.resume();
    child.on('close',code=>{
      let result;try{result=JSON.parse(text);}catch{return finish(new WorkbenchError('DATABASE_RESPONSE','Invalid database response',502));}
      if(code!==0||result.error)return finish(new WorkbenchError(result.error?.code || 'DATABASE_READ',result.error?.message || 'Database read failed',502));
      finish(null,result);
    });
    child.stdin.on('error',()=>{});
    child.stdin.end(JSON.stringify({...input,path:source,driver:manifest.driver}));
    if(signal?.aborted)abort();
  });
}
const readDatabase=(root,manifest,input,signal,timeout)=>runReader(root,manifest,input,signal,timeout);
const exportDatabase=(root,manifest,selection,output,signal)=>runReader(root,manifest,{...selection,output},signal,55000,'workbench_database_projection_v0001.py');
module.exports={readDatabase,exportDatabase,databasePath};
