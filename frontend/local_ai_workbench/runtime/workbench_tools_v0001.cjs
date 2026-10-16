// ============================================================
// File: workbench_tools_v0001.cjs
// 中文名: independent_tool_host
// Version: v0001
// Main Layer: action
// Layer: workbench / runtime
// Updatable: True
// Purpose: 按显式操作管理资源、Skill来源及Agent接取关系。
// Boundary: 不执行Skill内容，不调用AI，不代替业务应用，不覆盖原始库。
// ============================================================
// ALIAS_META
// alias: workbench_tools
// family: workbench_tools
// role: independent_tool_host
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_tools_v0001.cjs
// input:
//   - explicit authenticated category, import, scan and open requests
// output:
//   - private catalog, managed skills, agent feeds and embedded tool endpoints
// depends_on:
//   - workbench_store_v0001
//   - workbench_contracts_v0001
//   - workbench_skill_archive_v0001
// used_by:
//   - workbench_studio_v0001
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {assert,localUrl,WorkbenchError}=require('./workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING='utf8';
const uid=()=>crypto.randomUUID();
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const safeName=value=>{assert(typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$/.test(value),'STUDIO_ID','ID格式无效');return value;};
function boundedText(value,label,max=300){assert(typeof value==='string'&&value.trim()&&value.length<=max,'STUDIO_INPUT',label+'无效');return value.trim();}
const SCRIPT_NAME='workbench_tools_v0001.cjs',SCRIPT_FAMILY='workbench_tools',SCRIPT_VERSION='v0001';
class Tools {
 constructor(root,store,directory){this.root=root;this.store=store;this.dataDirectory=directory;this.opening=new Map();}
 items(){return this.store.get('studio_items',[]);}
 discover(){const profiles=JSON.parse(fs.readFileSync(path.join(__dirname,"../config/workbench_chat_integrations_v0001.json"),"utf8")).profiles;const profile=profiles.find(p=>[p.launcher,p.config,"scripts/action/development/scripts/independent_action_chat/"+p.server+".py"].every(file=>fs.existsSync(path.join(this.root,file))));return profile?[{id:"tool.action-chat",category:"tools",name:"独立 AI 对话",description:"你已有的 Action Chat · 聊天、模型选择与检索",integration:"action-chat",path:profile.launcher,source:"local-scan",available:true,native:false}]:[];}
 toolCatalog(){return [...(this.store.get('studio_text_visible',true)?[{id:'tool.text-inspector',category:'tools',name:'文本检查器',description:'字数统计、JSON格式化与查找替换 · 独立本地工具',builtin:true}]:[]),...this.items().filter(x=>x.category==='tools')];}
 async open(input){const item=this.toolCatalog().find(x=>x.id===input.id)||this.items().find(x=>x.id===input.id);assert(item,'TOOL_MISSING','工具不存在');if(item.native)throw new WorkbenchError('TOOL_NATIVE','此工具提供原生窗口，需先配置可嵌入的Web界面；工作台不会另开窗口');if(!item.builtin&&!item.integration){const url=localUrl(item.url);return {id:item.id,name:item.name,url:url.href,independent:true};}if(this.opening.has(item.id))return this.opening.get(item.id);const operation=(item.integration==='action-chat'?this.launchChat(item):this.launchText(item)).finally(()=>this.opening.delete(item.id));this.opening.set(item.id,operation);return operation;}
 async launchChat(item){assert(this.discover().length,'CHAT_MISSING','本地区缺少聊天应用入口或配置');const prior=this.store.get('studio_chat_endpoint');if(prior){try{const response=await fetch(prior.url+'api/workbench-identity',{signal:AbortSignal.timeout(1000)});const identity=await response.json();if(identity.instance===prior.instance&&identity.root===this.root&&prior.host_version==='chat-upgrade-1')return {...prior,id:item.id,name:item.name,independent:true};}catch{}}
  if(prior){try{const response=await fetch(prior.url+'api/workbench-identity',{signal:AbortSignal.timeout(1000)});const identity=await response.json();if(identity.instance===prior.instance&&identity.root===this.root){const headers={'Content-Type':'application/json'};const saved=await fetch(prior.url+'api/chat/save',{method:'POST',headers,body:'{}',signal:AbortSignal.timeout(10000)}).then(r=>r.json());assert(saved.verified===true,'CHAT_SAVE','旧聊天会话未核验保存，保留服务，请手动保存后重试');const stopped=await fetch(prior.url+'api/admin/shutdown',{method:'POST',headers,body:'{}',signal:AbortSignal.timeout(3000)}).then(r=>r.json());assert(stopped.status==='stopping','CHAT_STOP','旧服务未确认停止');await new Promise(r=>setTimeout(r,600));}}catch(error){if(error instanceof WorkbenchError)throw error;}}
  const instance=uid(),runDir=path.join(this.dataDirectory,'chat-launches',instance);fs.mkdirSync(runDir,{recursive:true});const ready=path.join(runDir,'ready.json'),log=fs.openSync(path.join(runDir,'process.log'),'a');const python=require('./release_paths_v0001.cjs').python(this.root);assert(fs.existsSync(python),'CHAT_PYTHON','聊天应用需要本地区自己的 Python 环境');const child=spawn(python,[path.join(__dirname,'../adapters/workbench_chat_host_v0001.py'),'--root',this.root,'--ready-file',ready,'--instance',instance],{cwd:this.root,detached:true,windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);let failure;child.once('error',e=>failure=e);child.unref();for(let i=0;i<300;i++){if(failure)throw new WorkbenchError('CHAT_START',failure.message);if(fs.existsSync(ready)){const state=JSON.parse(fs.readFileSync(ready,'utf8'));localUrl(state.url);this.store.put('studio_chat_endpoint',state);return {...state,id:item.id,name:item.name,independent:true};}if(child.exitCode!==null)throw new WorkbenchError('CHAT_START','聊天启动失败，请检查聊天权限配置和进程日志：'+path.join(runDir,'process.log'));await new Promise(r=>setTimeout(r,100));}throw new WorkbenchError('CHAT_TIMEOUT','聊天启动超时，请查看进程日志');
 }
 async launchText(item){const prior=this.store.get('studio_tool_endpoint');if(prior){try{const r=await fetch(prior.url+'health',{signal:AbortSignal.timeout(1000)});const body=await r.json();if(body.tool==='text-inspector'&&body.instance===prior.instance)return {...prior,id:item.id,name:item.name,independent:true};}catch{}}
  const instance=uid();const entry=path.join(__dirname,'../tools/text_inspector/text_inspector_v0001.cjs');const runDir=path.join(this.dataDirectory,'tool-launches',instance);fs.mkdirSync(runDir,{recursive:true});const ready=path.join(runDir,'ready.json'),log=fs.openSync(path.join(runDir,'process.log'),'a');const child=spawn(process.execPath,[entry,'--ready-file',ready,'--instance',instance],{env:{...process.env,ELECTRON_RUN_AS_NODE:'1'},detached:true,windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);let failure;child.once('error',e=>failure=e);child.unref();for(let i=0;i<100;i++){if(failure)throw new WorkbenchError('TOOL_START',failure.message);if(fs.existsSync(ready)){const state=JSON.parse(fs.readFileSync(ready,'utf8'));localUrl(state.url);this.store.put('studio_tool_endpoint',state);return {...state,id:item.id,name:item.name,independent:true};}await new Promise(r=>setTimeout(r,100));}throw new WorkbenchError('TOOL_TIMEOUT','工具启动超时，请查看进程日志');
 }
 async stop(input){assert(input.id==='tool.text-inspector','TOOL_STOP','外部工具由自身管理器关闭');const state=this.store.get('studio_tool_endpoint');assert(state,'TOOL_STOP','工具尚未启动');const r=await fetch(state.url+'shutdown',{method:'POST',headers:{'X-Tool-Instance':state.instance},signal:AbortSignal.timeout(2000)});assert(r.ok,'TOOL_STOP','工具拒绝关闭');this.store.put('studio_tool_endpoint',null);return {stopped:true};}
}
module.exports={Tools};
