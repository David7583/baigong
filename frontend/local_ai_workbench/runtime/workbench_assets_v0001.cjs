// ============================================================
// File: workbench_assets_v0001.cjs
// 中文名: 工作台资产 JSON 交接适配器
// Version: v0001
// Main Layer: action
// Layer: workbench / runtime
// Responsibilities: 认证资产操作、校验独立 SQLite 后台交接、复用成功清理。
// Does not: 渲染、AI 调用、LOD 调度或修改能力库。
// Boundary: 仅已登记包内文件可读取；失败留证；资源请求不触发模型。
// Updatable: True
// ALIAS_META
// alias: workbench_assets_v0001
// family: workbench_assets
// role: asset_adapter
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_assets_v0001.cjs
// input: authenticated asset operations
// output: asset.repository.v1 verified results
// depends_on: content_asset_repository_v0001, workbench_capabilities_v0001
// used_by: workbench_server_v0006
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {assert,WorkbenchError}=require('./workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_assets_v0001',SCRIPT_FAMILY='workbench_assets',SCRIPT_VERSION='v0001',DEFAULT_ENCODING='utf8';
class Assets{
 constructor(root,capabilities){this.root=path.resolve(root);this.capabilities=capabilities;this.pending=new Set();}
 async request(operation,input={}){
  assert(!this.closing,'ASSET_CLOSING','资产服务正在关闭');
  const id='asset_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.root,'runtime/intermediate_tasks',id);fs.mkdirSync(folder,{recursive:true});
  const raw=JSON.stringify({schema_version:'asset.repository.v1',run_id:id,root:this.root,producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},operation,input}),req=path.join(folder,'request.json'),res=path.join(folder,'response.json');fs.writeFileSync(req,raw,'utf8');
  const task=new Promise((resolve,reject)=>{const child=spawn(require('./release_paths_v0001.cjs').python(this.root),['-B',path.join(this.root,'scripts/action/infrastructures/content_asset_repository_v0001.py'),'--root',this.root,'--request',req,'--response',res],{windowsHide:true,stdio:'ignore'});child.once('error',reject);child.once('exit',resolve);});this.pending.add(task);let completed=false;
  try{const code=await task;assert(fs.existsSync(res)&&fs.statSync(res).size<12*1024*1024,'ASSET_RESPONSE','资产后台响应缺失或过大');const value=JSON.parse(fs.readFileSync(res,'utf8'));assert(value.schema_version==='asset.repository.v1'&&value.run_id===id&&value.producer?.script==='content_asset_repository_v0001'&&value.source_sha256===crypto.createHash('sha256').update(raw).digest('hex'),'ASSET_CONTRACT','资产交接校验失败');if(code!==0||value.status!=='completed')throw new WorkbenchError('ASSET_FAILED',value.error||'资产操作失败');completed=true;if(value.event_warning)value.result.event_warning=value.event_warning;return value.result;}
  finally{this.pending.delete(task);const log=path.join(this.root,'logs/content_assets/handoffs.log');fs.mkdirSync(path.dirname(log),{recursive:true});fs.appendFileSync(log,JSON.stringify({run_id:id,operation,status:completed?'completed':'failed',at:new Date().toISOString()})+'\n');if(completed)await this.capabilities.cleanup(folder);}
 }
 async file(id,version,relative){
  const {item}=await this.request('resolve',{id,version});assert(!item.archived,'ASSET_ARCHIVED','资产已归档');
  const rel=relative||path.relative(path.resolve(this.root,item.base_path),item.path).replaceAll('\\','/');
  assert(item.files.some(f=>f.relative===rel),'ASSET_FILE','文件不在登记的资源包内');
  const home=fs.realpathSync(path.join(this.root,'assets')),file=fs.realpathSync(path.resolve(this.root,item.base_path,rel));assert(file.startsWith(home+path.sep)&&fs.statSync(file).isFile(),'ASSET_PATH','资产路径越界');return {file,item};
 }
 async close(){this.closing=true;await Promise.allSettled([...this.pending]);}
}
module.exports={Assets};
