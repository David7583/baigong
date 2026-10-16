// ============================================================
// File: workbench_capabilities_v0001.cjs
// 中文名: 独立能力与项目的工作台接入适配器
// Version: v0001
// Main Layer: action
// Layer: workbench / runtime
// Responsibilities: 通过 JSON 文件映射独立能力与项目后台。
// Does: 校验交接、展示目录、显式加载、分配和调用。
// Does not: 内嵌业务实现、授予 AI 权限或覆盖正式业务库。
// Boundary: 私有界面状态与能力 SQL 分开；成功才清理交接文件，失败留证。
// Updatable: True
// ============================================================
// ALIAS_META
// alias: workbench_capabilities_v0001
// family: workbench_capabilities
// role: capability_adapter
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_capabilities_v0001.cjs
// input:
//   - explicit authenticated operations
// output:
//   - validated capability/project results and handoff audit
// depends_on:
//   - capability_repository_v0001
//   - project_workspace_v0001
//   - capability_invoker_v0001
// used_by:
//   - workbench_server_v0003
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {assert,WorkbenchError}=require('./workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_capabilities_v0001',SCRIPT_FAMILY='workbench_capabilities',SCRIPT_VERSION='v0001',DEFAULT_ENCODING='utf8';
class Capabilities {
 constructor(root,store){this.root=path.resolve(root);this.store=store;this.python=require('./release_paths_v0001.cjs').python(this.root);this.pending=new Set();}
 async request(target,operation,input={}){
  assert(!this.closing,'CAPABILITY_CLOSING','工作台正在退出');
  const choices={repository:['capability_repository_v0001','capability.repository.v1'],projects:['project_workspace_v0001','project.workspace.v1'],invoke:['capability_invoker_v0001','capability.invoke.v1']};
  const [script,schema]=choices[target]||[];assert(script,'CAPABILITY_TARGET','未知后台');assert(fs.existsSync(this.python),'PYTHON_MISSING','本地区 Python 不可用');
  const runId='capability_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.root,'runtime/intermediate_tasks',runId);fs.mkdirSync(folder,{recursive:true});
  const request=path.join(folder,'request.json'),response=path.join(folder,'response.json'),raw=JSON.stringify({schema_version:schema,run_id:runId,producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},root:this.root,operation,input});fs.writeFileSync(request,raw,'utf8');
  const task=new Promise((resolve,reject)=>{const child=spawn(this.python,['-B',path.join(this.root,'scripts/action/infrastructures',script+'.py'),'--root',this.root,'--request',request,'--response',response],{windowsHide:true,stdio:'ignore'});child.once('error',reject);child.once('exit',code=>resolve(code));});this.pending.add(task);
  let state='failed';
  try{const code=await task;assert(fs.existsSync(response)&&fs.statSync(response).size<5*1024*1024,'CAPABILITY_RESPONSE','后台响应缺失或超出预算');const value=JSON.parse(fs.readFileSync(response,'utf8'));assert(value.schema_version===schema&&value.run_id===runId&&value.producer?.script===script&&value.producer?.version==='v0001'&&value.source_sha256===crypto.createHash('sha256').update(raw).digest('hex'),'CAPABILITY_CONTRACT','后台响应身份不匹配');if(code!==0||value.status!=='completed')throw new WorkbenchError('CAPABILITY_FAILED',value.error||'后台执行失败');if(target==='invoke'&&['call','discover'].includes(input.operation)){const outputFolder=path.join(this.root,'runtime/capability_results');fs.mkdirSync(outputFolder,{recursive:true});const outputPath=path.join(outputFolder,runId+'.json');fs.writeFileSync(outputPath,JSON.stringify({...value,capability_id:input.id,principal:input.principal,operation:input.operation,project_id:input.project_id||null}),'utf8');assert(JSON.parse(fs.readFileSync(outputPath,'utf8')).run_id===runId,'RESULT_VERIFY','正式结果核验失败');}state='completed';return value.result;}
  finally{this.pending.delete(task);const log=path.join(this.root,'logs/capability_workspace/handoffs.log');fs.mkdirSync(path.dirname(log),{recursive:true});fs.appendFileSync(log,JSON.stringify({run_id:runId,script,operation,status:state,at:new Date().toISOString(),principal:input.principal||'user'})+'\n');if(state==='completed')await this.cleanup(folder);}
 }
 async cleanup(folder){
  // The independent cleanup script consumes a precise successful handoff inventory.
  const script=path.join(this.root,'scripts/action/infrastructures/intermediate_cleanup_v0001.py');
  if(!fs.existsSync(script))return;
  const entries=fs.readdirSync(folder).filter(name=>name.endsWith('.json')).map(name=>path.join(folder,name));
  const manifest=path.join(folder,'task.json');
  fs.writeFileSync(manifest,JSON.stringify({schema_version:'intermediate-task.v1',run_id:path.basename(folder),status:'completed',active:false,result_verified:true,recovery_required:false,consumers:[{id:SCRIPT_NAME,acknowledged:true}],intermediates:entries.map(file=>({path:path.relative(this.root,file).replaceAll('\\','/'),sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),regenerable:true,protected:false}))}));
  // Cleanup API shape is validated by integration tests; any failure keeps files.
  const code=await new Promise(resolve=>{const child=spawn(this.python,['-B',script,'--root',this.root,'--manifest',path.relative(this.root,manifest)],{windowsHide:true,stdio:'ignore'});child.once('error',()=>resolve(-1));child.once('exit',resolve);});
  if(code!==0){const log=path.join(this.root,'logs/capability_workspace/handoffs.log');fs.appendFileSync(log,JSON.stringify({run_id:path.basename(folder),operation:'cleanup',status:'failed',at:new Date().toISOString()})+'\n');}
 }
 async list(principal){return this.request('repository','list',principal?{principal}:{});}
 async invoke(input,principal='user'){assert(['read','reference','discover','call'].includes(input.operation),'CAPABILITY_OPERATION','请选择读取、发现或调用');let projectContext=null;if(input.project_id){const project=(await this.projects()).items.find(x=>x.id===input.project_id&&!x.archived);assert(project&&project.capability_ids.includes(input.id),'PROJECT_BINDING','此能力未关联到所选项目');if(principal.startsWith('agent:'))assert(project.agent_ids.includes(principal.slice(6)),'PROJECT_AGENT','Agent 未分配到此项目');projectContext={id:project.id,name:project.name,path:project.path,context:project.context};}const result=await this.request('invoke','invoke',{...input,principal,project_context:projectContext});return result;}
 async collect(input){return this.request('repository',input.download===true?'download':'collect',input);}
 async importExisting(records){const results=[];for(const item of records){results.push(await this.collect(item));}return {collected:results.length,results};}
 async enable(input){return this.request('repository','enable',input);}
 async grant(input){return this.request('repository','grant',input);}
 async projects(){return this.request('projects','list');}
 async saveProject(input){const known=(await this.list()).items;assert((input.capability_ids||[]).every(id=>known.some(x=>x.id===id)),'PROJECT_CAPABILITY','项目引用了不存在的能力');return this.request('projects','save',input);}
 async archiveProject(input){return this.request('projects','archive',input);}
 async selectProject(input){if(input.id){const value=(await this.projects()).items.find(x=>x.id===input.id&&!x.archived);assert(value,'PROJECT_MISSING','项目不存在或已归档');}this.store.put('active_project',input.id||null);return {id:input.id||null};}
 async load(input){const values=this.store.get('loaded_capabilities',[]);assert((await this.list()).items.some(x=>x.id===input.id),'CAPABILITY_MISSING','能力不存在');this.store.put('loaded_capabilities',input.loaded===false?values.filter(x=>x!==input.id):[...new Set([...values,input.id])]);return {loaded:input.loaded!==false,preserved:true};}
 async close(){this.closing=true;await Promise.allSettled([...this.pending]);}
}
module.exports={Capabilities};
