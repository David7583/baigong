// ============================================================
// 文件名: workbench_ai_inventory_v0001.cjs
// 中文名: 总控公开清单 JSON 适配
// 版本号: v0001
// 主层级: action
// 层级: workbench / integration
// 脚本定位: 总控公开清单 JSON 适配
// 职责说明: 通过已有索引和显式 JSON 契约提供真实信息
// 本脚本做什么: 总控公开清单 JSON 适配
// 本脚本不做什么: 不自动授权模型、不安装软件、不卸载系统应用
// 制度边界声明: 只在明确操作后执行；用户数据保留；错误可追溯
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_ai_inventory_v0001
// family: workbench_ai_inventory
// role: managed_integration
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_ai_inventory_v0001.cjs
// input: authenticated explicit operation or JSON request
// output: factual inventory or versioned result
// depends_on: standard library and existing managed APIs
// used_by: workbench_server_v0011
// ============================================================

'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {assert}=require('./workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_ai_inventory_v0001',SCRIPT_FAMILY='workbench_ai_inventory',SCRIPT_VERSION='v0001',DEFAULT_ENCODING='utf8';
class AIInventory{
 constructor(root,store,cleanup){Object.assign(this,{root,store,cleanup});this.pending=null;this.cache=null;}
 async snapshot(refresh=false){
  if(this.pending)return this.pending;
  if(!refresh&&this.cache&&Date.now()-this.cache.at<15000)return this.cache.value;
  this.pending=this.read().then(value=>{this.cache={at:Date.now(),value};return value;}).finally(()=>this.pending=null);
  return this.pending;
 }
 async read(){
  const config=JSON.parse(fs.readFileSync(path.join(__dirname,'../config/workbench_ai_inventory_v0001.json'),'utf8'));
  assert(config.schema_version==='workbench.ai-inventory-config.v1','AI_CONFIG','总控观察配置无效');
  const id='ai_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.root,'runtime/intermediate_tasks',id);
  fs.mkdirSync(folder,{recursive:true});const request=path.join(folder,'request.json'),response=path.join(folder,'response.json');
  const raw=JSON.stringify({schema_version:'workbench.ai-inventory.v1',run_id:id,producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},operation:'inventory',config:config.controller_config});
  fs.writeFileSync(request,raw);
  const code=await new Promise((resolve,reject)=>{const child=spawn(require('./release_paths_v0001.cjs').python(this.root),['-B',path.join(this.root,'scripts/action/infrastructures/action_ai_controller_v0003.py'),'--root',this.root,'--inventory-request',request,'--response',response],{windowsHide:true,stdio:'ignore'});const timer=setTimeout(()=>{child.kill();reject(Error('总控读取超时'));},30000);child.once('error',e=>{clearTimeout(timer);reject(e);});child.once('close',c=>{clearTimeout(timer);resolve(c);});});
  assert(fs.existsSync(response)&&fs.statSync(response).size<16*1024*1024,'AI_RESPONSE','总控没有返回清单');
  const value=JSON.parse(fs.readFileSync(response,'utf8'));
  assert(value.schema_version==='workbench.ai-inventory.v1'&&value.run_id===id&&value.producer?.script==='action_ai_controller_v0003'&&value.producer.version==='v0003'&&value.source_sha256===crypto.createHash('sha256').update(raw).digest('hex'),'AI_CONTRACT','总控响应来源不一致');
  assert(code===0&&value.status==='completed','AI_UNAVAILABLE',value.error||'总控清单读取失败');
  this.store.event('ai.inventory.observed',{run_id:id,status:value.result.status,providers:value.result.providers.length,applications:value.result.applications.length});
  await this.cleanup(folder);return {...value.result,controller_config:config.controller_config};
 }
 async close(){if(this.pending)await this.pending.catch(()=>{});}
}
module.exports={AIInventory};
