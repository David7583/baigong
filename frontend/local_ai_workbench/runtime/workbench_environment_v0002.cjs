// ============================================================
// 文件名: workbench_environment_v0002.cjs
// 中文名: 实时加载依赖与当前环境清单认证适配
// 版本号: v0002
// 主层级: action
// 层级: workbench / integration
// 脚本定位: 实时加载依赖与当前环境清单认证适配
// 职责说明: 通过已有索引和显式 JSON 契约提供真实信息
// 本脚本做什么: 实时加载依赖与当前环境清单认证适配
// 本脚本不做什么: 不自动授权模型、不安装软件、不卸载系统应用
// 制度边界声明: 只在明确操作后执行；用户数据保留；错误可追溯
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_environment_v0002
// family: workbench_environment
// role: managed_integration
// version: v0002
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_environment_v0002.cjs
// input: authenticated explicit operation or JSON request
// output: factual inventory or versioned result
// depends_on: standard library and existing managed APIs
// used_by: workbench_server_v0011
// ============================================================
'use strict';
const SCRIPT_NAME='workbench_environment_v0002',SCRIPT_FAMILY='workbench_environment',SCRIPT_VERSION='v0002',DEFAULT_ENCODING='utf8';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {assert}=require('./workbench_contracts_v0001.cjs');
const publicSource=value=>{try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&!url.search&&!url.hash?url.href:'本地来源 / 未提供公开地址';}catch{return '本地来源 / 未提供公开地址';}};
class Environment{
 constructor(root,store,studio,capabilities){this.root=path.resolve(root);this.store=store;this.studio=studio;this.capabilities=capabilities;this.pending=new Set();this.inspection=null;this.closing=false;}
 async context(){
  const catalog=this.studio.toolHost.toolCatalog(),observed=await this.studio.toolHost.status(),states=new Map(observed.items.map(x=>[x.id,x]));
  const tools=catalog.map(x=>{const manifest=x.integration==='manifest'?this.studio.toolHost.manifests.read(x.manifest):null;return {id:x.id,name:x.name,runtime:manifest?.runtime||(x.builtin?'node':x.integration==='action-chat'?'python':'external'),version:x.version||'unversioned',status:states.get(x.id)?.status||'available',program_directory:manifest?path.relative(this.root,path.dirname(manifest.manifest)).replaceAll('\\','/'):null};});
  const loaded=new Set(this.store.get('loaded_capabilities',[])),caps=(await this.capabilities.list()).items.filter(x=>loaded.has(x.id)).map(x=>({id:x.id,kind:x.kind,name:x.name,version:x.version,source:publicSource(x.source),license:x.license,status:x.enabled?'enabled':'disabled',load_basis:'loaded capability ID'}));
  for(const skill of this.studio.skills().filter(x=>x.enabled))if(!caps.some(x=>x.id===skill.id))caps.push({id:skill.id,kind:'skill',name:skill.name,version:skill.version||skill.bundle_sha256||skill.sha256||'unversioned',source:publicSource(skill.source),license:skill.license||'未提供',status:'enabled',load_basis:'enabled Skill market entry'});
  for(const software of this.studio.items().filter(x=>x.managed_software))tools.push({id:software.id,name:software.name,runtime:'external',version:software.version||'unversioned',status:'loaded; process not probed',program_directory:null});
  return {tools,capabilities:caps};
 }
 async snapshot(){
  assert(!this.closing,'ENVIRONMENT_CLOSING','清单服务正在退出');if(this.inspection)return this.inspection;
  this.inspection=(async()=>{const context=await this.context(),id='env_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.root,'runtime/intermediate_tasks',id);fs.mkdirSync(folder,{recursive:true});const request=path.join(folder,'request.json'),response=path.join(folder,'response.json'),raw=JSON.stringify({schema_version:'environment.repository.v1',run_id:id,root:this.root,producer:{script:'workbench_environment_v0002',version:'v0002'},operation:'inspect',input:{context}});fs.writeFileSync(request,raw,'utf8');
   const pending=new Promise((resolve,reject)=>{const child=spawn(require('./release_paths_v0001.cjs').python(this.root),['-B',path.join(this.root,'scripts/action/infrastructures/environment_repository_v0002.py'),'--root',this.root,'--request',request,'--response',response],{windowsHide:true,stdio:'ignore'});child.once('error',reject);child.once('close',code=>{try{assert(fs.existsSync(response)&&fs.statSync(response).size<5*1024*1024,'ENVIRONMENT_RESPONSE','清单响应缺失或超预算');const value=JSON.parse(fs.readFileSync(response,'utf8'));assert(value.schema_version==='environment.repository.v1'&&value.run_id===id&&value.producer?.script==='environment_repository_v0002'&&value.producer?.version==='v0002'&&value.source_sha256===crypto.createHash('sha256').update(raw).digest('hex'),'ENVIRONMENT_CONTRACT','清单交接身份不匹配');assert(code===0&&value.status==='completed','ENVIRONMENT_FAILED',value.error||'清单观察失败');resolve(value.result);}catch(error){reject(error);}});});
   this.pending.add(pending);try{const result=await pending;await this.capabilities.cleanup(folder);return result;}finally{this.pending.delete(pending);}
  })().finally(()=>this.inspection=null);return this.inspection;
 }
 async close(){this.closing=true;if(this.inspection)await this.inspection.catch(()=>{});await Promise.allSettled([...this.pending]);}
}
module.exports={Environment};
