// ============================================================
// 文件名: workbench_timeline_adapter_v0001.cjs
// 中文名: 独立只读时间适配进程桥
// 版本号: v0001
// 主层级: action
// 层级: workbench / adapters
// 脚本定位: isolated_timeline_reader
// 职责说明: 串行执行有界时间查询，隔离响应与超时
// 本脚本做什么: 传递固定根路径和私有缓存，管理自有子进程
// 本脚本不做什么: 不执行任意 SQL、不授权工具、不修改业务库
// 制度边界声明: 不接受客户端路径，进程仅按明确查询启动
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_timeline_adapter_v0001
// family: workbench_timeline_adapter
// role: isolated_timeline_reader
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/adapters/workbench_timeline_adapter_v0001.cjs
// input: trusted root/data directory and bounded request
// output: readonly timeline JSON
// depends_on: workbench_timeline_reader_v0001, node:child_process
// used_by: workbench_server_v0004
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const {WorkbenchError}=require('../runtime/workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_timeline_adapter_v0001',SCRIPT_FAMILY='workbench_timeline_adapter',SCRIPT_VERSION='v0001',DEFAULT_ENCODING='utf8';

function python(root){let at=__dirname;while(true){const candidate=path.join(at,'.venv/Scripts/python.exe');if(fs.existsSync(candidate))return candidate;const parent=path.dirname(at);if(parent===at)return require('../runtime/release_paths_v0001.cjs').python(root);at=parent;}}

class Timeline {
 constructor(root,directory){this.root=root;this.directory=path.join(directory,'timeline');this.queue=Promise.resolve();this.pending=0;this.closed=false;this.children=new Set();}
 read(input){
  if(this.closed)return Promise.reject(new WorkbenchError('CLOSED','Timeline reader closed',409));
  if(this.pending>=8)return Promise.reject(new WorkbenchError('TIMELINE_BUSY','时间查询繁忙，请稍后再试',429));
  if(!['status','summary','bucket','window','detail'].includes(input.operation))return Promise.reject(new WorkbenchError('TIMELINE_OPERATION','Unsupported timeline operation',400));
  this.pending++;const task=this.queue.then(()=>this.execute(input));this.queue=task.catch(()=>{});return task.finally(()=>this.pending--);
 }
 execute(input){return new Promise((resolve,reject)=>{
  if(this.closed)return reject(new WorkbenchError('CLOSED','Timeline reader closed',409));
  const safe={operation:input.operation,grain:input.grain,offset_minutes:input.offset_minutes,start:input.start,end:input.end,page:input.page,asset_id:input.asset_id,message_id:input.message_id,root:this.root,cache:this.directory};
  const child=spawn(python(this.root),[path.join(__dirname,'workbench_timeline_reader_v0001.py')],{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONIOENCODING:'utf-8'}});this.children.add(child);
  let output='',settled=false;const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);this.children.delete(child);if(error)reject(error);else resolve(value);};
  const timer=setTimeout(()=>{child.kill();finish(new WorkbenchError('TIMELINE_TIMEOUT','时间查询超时；保留业务库，请重试',504));},180000);
  child.on('error',()=>finish(new WorkbenchError('TIMELINE_PROCESS','时间读取进程未能启动',502)));child.stderr.resume();
  child.stdout.on('data',chunk=>{output+=chunk;if(output.length>2*1024*1024){child.kill();finish(new WorkbenchError('TIMELINE_BUDGET','Timeline response exceeds budget',502));}});
  child.on('close',code=>{let value;try{value=JSON.parse(output);}catch{return finish(new WorkbenchError('TIMELINE_RESPONSE','Invalid timeline response',502));}if(code!==0||value.error)return finish(new WorkbenchError(value.error?.code||'TIMELINE_QUERY',value.error?.message||'Timeline read failed',502));finish(null,value);});
  child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify(safe));
 });}
 async close(){this.closed=true;for(const child of this.children)child.kill();await this.queue;}
}
module.exports={Timeline};
