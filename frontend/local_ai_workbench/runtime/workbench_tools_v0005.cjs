// ============================================================
// 文件名: workbench_tools_v0005.cjs
// 中文名: 独立工具接入与状态观察
// 版本号: v0005
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: independent_tool_host
// 职责说明:
// - 启动、复用并观察独立工具
// 本脚本做什么:
// - 启动、复用并观察独立工具
// 本脚本不做什么:
// - 不自动启动核心向量计算或调用模型
// 制度边界声明:
// - 业务事实、凭证、会话和独立工具生命周期分别保存
// - 失败明确报告；不自动授予 AI 权限或执行开发登记
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_tools_v0005
// family: workbench_tools
// role: independent_tool_host
// version: v0005
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_tools_v0005.cjs
// input:
//   - explicit operations and authenticated backend facts
// output:
//   - sanitized state and explicit operation results
// depends_on:
//   - existing workbench adapters and versioned contracts
// used_by:
//   - local_ai_workbench
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {ManifestTools}=require('./workbench_tool_manifest_v0003.cjs');

const {assert,localUrl,WorkbenchError}=require('./workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING='utf8';
const uid=()=>crypto.randomUUID();
const processAlive=state=>{if(!Number.isInteger(state?.pid)||state.pid<=0)return false;try{process.kill(state.pid,0);return true;}catch(error){return error.code!=='ESRCH';}};
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const safeName=value=>{assert(typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$/.test(value),'STUDIO_ID','ID格式无效');return value;};
function boundedText(value,label,max=300){assert(typeof value==='string'&&value.trim()&&value.length<=max,'STUDIO_INPUT',label+'无效');return value.trim();}
const SCRIPT_NAME='workbench_tools_v0005.cjs',SCRIPT_FAMILY='workbench_tools',SCRIPT_VERSION='v0005';
class Tools {
 constructor(root,store,directory){this.root=root;this.store=store;this.dataDirectory=directory;this.opening=new Map();this.manifests=new ManifestTools(root,store,directory);}
 items(){return this.store.get('studio_items',[]);}
 async stopChat(){const state=this.store.get('studio_chat_endpoint');assert(state,'CHAT_OFFLINE','聊天工具尚未启动');localUrl(state.url);const identity=await fetch(state.url+'api/workbench-identity',{signal:AbortSignal.timeout(1500)}).then(r=>r.json());assert(identity.instance===state.instance&&identity.root===this.root,'CHAT_OWNER','无法确认聊天实例归属');const saved=await fetch(state.url+'api/chat/save',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(10000)}).then(r=>r.json());assert(saved.verified===true,'CHAT_SAVE','会话未核验保存，保留服务');const result=await fetch(state.url+'api/admin/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(3000)}).then(r=>r.json());assert(result.status==='stopping','CHAT_STOP','聊天服务未接受停止请求');for(let n=0;n<100;n++){if(!processAlive(state)){this.store.put('studio_chat_endpoint',null);return {stopped:true,process_exited:true,sessions_preserved:true};}await new Promise(r=>setTimeout(r,100));}throw new WorkbenchError('CHAT_STOP_PENDING','已请求停止，进程尚未确认退出；未强制终止');}
 async status(){
  const catalog=this.toolCatalog(),known=new Map(catalog.map(x=>[x.id,x]));
  const keys=['studio_tool_endpoint','studio_chat_endpoint',...this.store.get('studio_known_tool_ids',[]).map(id=>'studio_manifest_tool:'+id)];
  const endpoints=keys.map(key=>({key,state:this.store.get(key)})).filter(x=>x.state);
  const result=await Promise.all(endpoints.map(async({key,state})=>{
   const id=key==='studio_tool_endpoint'?'tool.text-inspector':key==='studio_chat_endpoint'?'tool.action-chat':key.slice('studio_manifest_tool:'.length);
   let status='unavailable';
   try{localUrl(state.url);const route=id==='tool.action-chat'?'api/workbench-identity':'health';const r=await fetch(state.url+route,{signal:AbortSignal.timeout(1200)});const health=await r.json();assert(r.ok&&health.instance===state.instance&&(id==='tool.action-chat'?health.root===this.root:health.tool===(id==='tool.text-inspector'?'text-inspector':id)),'TOOL_IDENTITY','工具实例身份不一致');status='running';}
   catch{try{process.kill(state.pid,0);}catch(e){if(e.code==='ESRCH')status='stopped';}}
   return {id,name:known.get(id)?.name||(id==='tool.action-chat'?'独立 AI 对话':id),loaded:known.has(id),status,pid:state.pid,observation:'verified_health_or_process_exit'};
  }));
  for(const item of catalog)if(!result.some(x=>x.id===item.id))result.push({id:item.id,name:item.name,loaded:true,status:item.available===false?'unavailable':'not_observed',observation:'no_owned_instance'});
  return {observed_at:new Date().toISOString(),items:result};
 }
 discover(directory){const profiles=JSON.parse(fs.readFileSync(path.join(__dirname,"../config/workbench_chat_integrations_v0001.json"),"utf8")).profiles;const profile=profiles.find(p=>[p.launcher,p.config,"scripts/action/development/scripts/independent_action_chat/"+p.server+".py"].every(file=>fs.existsSync(path.join(this.root,file))));return [...this.manifests.scan(directory),...(profile?[{id:"tool.action-chat",category:"tools",name:"独立 AI 对话",description:"你已有的 Action Chat · 聊天、模型选择与检索",integration:"action-chat",path:profile.launcher,source:"local-scan",available:true,native:false}]:[])];}
 toolCatalog(){const declared=[];for(const name of ['scene_notes','data_sandbox']){const file=path.join(this.root,'frontend/local_ai_workbench/tools/landscape',name,'tool.manifest.json');if(fs.existsSync(file)){try{const value=this.manifests.read(file);if(!this.store.get('studio_hidden',[]).includes(value.id))declared.push({id:value.id,name:value.name,description:value.description,version:value.version,category:'tools',source:'local-scan',integration:'manifest',manifest:file,available:true,native:false});}catch{}}}const items=[...(this.store.get('studio_text_visible',true)?[{id:'tool.text-inspector',category:'tools',name:'文本检查器',description:'字数统计、JSON格式化与查找替换 · 独立本地工具',builtin:true}]:[]),...declared,...this.items().filter(x=>x.category==='tools')];return [...new Map(items.map(x=>[x.id,x])).values()];}
 async open(input){const item=this.toolCatalog().find(x=>x.id===input.id)||this.items().find(x=>x.id===input.id);assert(item,'TOOL_MISSING','工具不存在');assert(item.available!==false,'TOOL_UNAVAILABLE','资源尚不可用，请检查入口');if(item.native)throw new WorkbenchError('TOOL_NATIVE','此工具提供原生窗口，需先配置可嵌入的Web界面；工作台不会另开窗口');if(!item.builtin&&!item.integration){const url=localUrl(item.url);return {id:item.id,name:item.name,url:url.href,independent:true};}if(this.opening.has(item.id))return this.opening.get(item.id);const operation=(item.integration==='manifest'?this.manifests.open(item):item.integration==='action-chat'?this.launchChat(item):this.launchText(item)).then(result=>{this.store.put('studio_known_tool_ids',[...new Set([...this.store.get('studio_known_tool_ids',[]),item.id])]);return result;}).finally(()=>this.opening.delete(item.id));this.opening.set(item.id,operation);return operation;}
 async launchChat(item){assert(this.discover().some(x=>x.integration==='action-chat'),'CHAT_MISSING','本地区缺少聊天应用入口或配置');const prior=this.store.get('studio_chat_endpoint');if(prior){try{const response=await fetch(prior.url+'api/workbench-identity',{signal:AbortSignal.timeout(1000)});const identity=await response.json();if(identity.instance===prior.instance&&identity.root===this.root&&prior.host_version==='backend-monitor-1')return {...prior,id:item.id,name:item.name,independent:true};}catch{}}
  if(prior){try{const response=await fetch(prior.url+'api/workbench-identity',{signal:AbortSignal.timeout(1000)});const identity=await response.json();if(identity.instance===prior.instance&&identity.root===this.root){const headers={'Content-Type':'application/json'};const saved=await fetch(prior.url+'api/chat/save',{method:'POST',headers,body:'{}',signal:AbortSignal.timeout(10000)}).then(r=>r.json());assert(saved.verified===true,'CHAT_SAVE','旧聊天会话未核验保存，保留服务，请手动保存后重试');const stopped=await fetch(prior.url+'api/admin/shutdown',{method:'POST',headers,body:'{}',signal:AbortSignal.timeout(3000)}).then(r=>r.json());assert(stopped.status==='stopping','CHAT_STOP','旧服务未确认停止');await new Promise(r=>setTimeout(r,600));}}catch(error){if(error instanceof WorkbenchError)throw error;}}
  assert(!processAlive(prior),"CHAT_UNAVAILABLE","聊天实例尚未确认退出；保留会话与进程，避免重复启动");const instance=uid(),runDir=path.join(this.dataDirectory,'chat-launches',instance);fs.mkdirSync(runDir,{recursive:true});const ready=path.join(runDir,'ready.json'),log=fs.openSync(path.join(runDir,'process.log'),'a');const python=require('./release_paths_v0001.cjs').python(this.root);assert(fs.existsSync(python),'CHAT_PYTHON','聊天应用需要本地区自己的 Python 环境');const child=spawn(python,[path.join(__dirname,'../adapters/workbench_chat_host_v0002.py'),'--root',this.root,'--ready-file',ready,'--instance',instance],{cwd:this.root,detached:true,windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);let failure;child.once('error',e=>failure=e);child.unref();for(let i=0;i<300;i++){if(failure)throw new WorkbenchError('CHAT_START',failure.message);if(fs.existsSync(ready)){const state=JSON.parse(fs.readFileSync(ready,'utf8'));localUrl(state.url);this.store.put('studio_chat_endpoint',state);return {...state,id:item.id,name:item.name,independent:true};}if(child.exitCode!==null)throw new WorkbenchError('CHAT_START','聊天启动失败，请检查聊天权限配置和进程日志：'+path.join(runDir,'process.log'));await new Promise(r=>setTimeout(r,100));}throw new WorkbenchError('CHAT_TIMEOUT','聊天启动超时，请查看进程日志');
 }
 async launchText(item){const prior=this.store.get('studio_tool_endpoint');if(prior){try{const r=await fetch(prior.url+'health',{signal:AbortSignal.timeout(1000)});const body=await r.json();if(body.tool==='text-inspector'&&body.instance===prior.instance)return {...prior,id:item.id,name:item.name,independent:true};}catch{}}
  assert(!processAlive(prior),"TOOL_UNAVAILABLE","工具进程尚在但健康接口不可达；请先核查原实例，避免重复启动");const instance=uid();const entry=path.join(__dirname,'../tools/text_inspector/text_inspector_v0001.cjs');const runDir=path.join(this.dataDirectory,'tool-launches',instance);fs.mkdirSync(runDir,{recursive:true});const ready=path.join(runDir,'ready.json'),log=fs.openSync(path.join(runDir,'process.log'),'a');const child=spawn(process.execPath,[entry,'--ready-file',ready,'--instance',instance],{env:{...process.env,ELECTRON_RUN_AS_NODE:'1'},detached:true,windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);let failure;child.once('error',e=>failure=e);child.unref();for(let i=0;i<100;i++){if(failure)throw new WorkbenchError('TOOL_START',failure.message);if(fs.existsSync(ready)){const state=JSON.parse(fs.readFileSync(ready,'utf8'));localUrl(state.url);this.store.put('studio_tool_endpoint',state);return {...state,id:item.id,name:item.name,independent:true};}await new Promise(r=>setTimeout(r,100));}throw new WorkbenchError('TOOL_TIMEOUT','工具启动超时，请查看进程日志');
 }
 async stop(input){if(input.id==="tool.action-chat")return this.stopChat();const item=this.toolCatalog().find(x=>x.id===input.id);if(item?.integration==='manifest')return this.manifests.stop(item);assert(input.id==='tool.text-inspector','TOOL_STOP','外部工具由自身管理器关闭');const state=this.store.get('studio_tool_endpoint');assert(state,'TOOL_STOP','工具尚未启动');const r=await fetch(state.url+'shutdown',{method:'POST',headers:{'X-Tool-Instance':state.instance},signal:AbortSignal.timeout(2000)});assert(r.ok,'TOOL_STOP','工具拒绝关闭');this.store.put('studio_tool_endpoint',null);return {stopped:true};}
}
module.exports={Tools};
