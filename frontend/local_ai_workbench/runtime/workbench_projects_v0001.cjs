// ============================================================
// 文件名: workbench_projects_v0001.cjs
// 中文名: 使用者项目目录与数据绑定
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: 使用者项目目录与数据绑定
//
// 职责说明:
// - 提供按需功能列表、可信打开票据、多库接入和项目实际读取，不改变独立应用配置
//
// 本脚本做什么:
// - 提供按需功能列表、可信打开票据、多库接入和项目实际读取，不改变独立应用配置
//
// 本脚本不做什么:
// - 不改写原始库，不启动模型或更改 AI 许可
//
// 制度边界声明:
// - 用户操作显式触发；仅工作台私有状态可写；测试使用隔离目录
// - 失败分类返回；不把入口存在或配置保存冒充业务执行成功
//
// 可更新: True
// ============================================================
//
// ============================================================
// ALIAS_META
// ============================================================
// alias: workbench_projects_v0001
// family: workbench_projects
// role: workbench_projects
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_projects_v0001.cjs
// input:
//   - authenticated explicit user commands and local source facts
// output:
//   - versioned results and private workbench state
// depends_on:
//   - Node.js standard library
//   - workbench_contracts_v0001
//   - workbench_discovery_v0001
//   - workbench_database_adapter_v0001
//   - workbench_software_catalog_v0001
//   - config/workbench_user_projects_v0001.json
// used_by:
//   - workbench_server_v0001
// ============================================================

'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {inside,assert,identifier,workspace}=require('./workbench_contracts_v0001.cjs');
const {discover}=require('../adapters/workbench_discovery_v0001.cjs');
const {readDatabase}=require('../adapters/workbench_database_adapter_v0001.cjs');
const DEFAULT_ENCODING='utf8';
const SCRIPT_FAMILY='workbench_projects';
const SCRIPT_NAME='workbench_projects_v0001';
const SCRIPT_VERSION='v0001';
// ============================================================
// 版本化使用者状态与项目服务；不在构造时发现或加载业务
// ============================================================
const READER={id:'library.reader',name:'资料阅读',description:'接入资料库后直接阅读、搜索内容，不需要选择数据表。',kind:'builtin',bindable:true,available:true};
class Projects {
 constructor(root,registry,store,actions){this.root=root;this.registry=registry;this.store=store;this.actions=actions;this.tickets=new Map();this.attaching=new Map();this.revision=0;}
 state(){return this.store.get('user_state',{schema_version:'workbench.user.v1',mode:'user',selected:[],favorites:[],recent:[],bindings:{}});}
 save(state){this.store.put('user_state',state);return state;}
 mode(input){assert(['user','developer'].includes(input.mode),'USER_MODE','请选择使用者或开发者模式');const s=this.state();s.mode=input.mode;return this.save(s);}
 catalog(){
  const relative='scripts/action/development/functions/workbench_software_catalog_v0001.js';
  const entries=fs.existsSync(path.join(this.root,relative))?require(inside(this.root,relative)).scanSoftwareCatalog(this.root).filter(x=>x.registered&&x.status==='active'&&!x.software_id.startsWith('local_ai_workbench')):[];
  const names={independent_action_chat:'独立 AI 对话',action_chat:'Action 对话',regulation_gold_viewer:'规范资料阅读',two_dimensional_layout_solver_ui:'二维布局求解',regulation_artifact_viewer:'规范成果查看',data_action_chain_v0009_launcher:'数据处理工作台',data_action_chain_v0009_node_launcher:'数据处理节点编排'};
  const descriptions={independent_action_chat:'与 AI 对话，按需使用应用已有的检索能力。',action_chat:'打开现有对话应用，继续交流与检索。',regulation_gold_viewer:'阅读已经整理的规范资料，查看来源。',two_dimensional_layout_solver_ui:'打开二维布局工具，设置条件并查看求解结果。',regulation_artifact_viewer:'查看规范处理过程中的成果。',data_action_chain_v0009_launcher:'打开数据处理工具，选择并运行已有流程。',data_action_chain_v0009_node_launcher:'查看和配置数据处理流程的节点。'};
  const extra=JSON.parse(fs.readFileSync(path.join(__dirname,'../config/workbench_user_projects_v0001.json'),'utf8'));assert(extra.schema_version==='workbench.user-projects.v1'&&Array.isArray(extra.entries),'PROJECT_CONFIG','功能目录配置无效');
  const reviewed=extra.entries.map(x=>{identifier(x.id);assert(typeof x.entry==='string'&&/^software\/[a-zA-Z0-9_.-]+\.cmd$/.test(x.entry)&&Array.isArray(x.requires),'PROJECT_CONFIG','无效功能入口配置');let available=true;try{inside(this.root,x.entry);for(const file of x.requires)inside(this.root,file);}catch{available=false;}return {...x,kind:'external',bindable:false,available,data_note:'使用应用自身的数据设置；尚未接入工作台数据绑定'};});
  return [READER,...entries.map(x=>({id:'app.'+x.software_id,name:names[x.software_id]||x.display_name,description:descriptions[x.software_id]||x.description||'打开现有独立应用',kind:'external',bindable:false,available:x.launchable&&x.issues.length===0&&x.launcher_type==='cmd',entry:x.entry_point,issues:x.issues,data_note:'使用应用自身的数据设置；尚未接入工作台数据绑定'})),...reviewed.filter(x=>!entries.some(e=>e.entry_point===x.entry))];
 }
 get(id){identifier(id);const item=this.catalog().find(x=>x.id===id);assert(item,'PROJECT_MISSING','功能入口已变化，请重新添加');return item;}
 home(){const s=this.state(),profiles=this.store.get('user_source_profiles',{});return {...s,projects:s.selected.length?this.catalog().filter(x=>s.selected.includes(x.id)):[],sources:this.registry.list().filter(x=>x.database).map(x=>({...x,content_profile:profiles[x.resource_id]||null}))};}
 select(input){const p=this.get(input.project_id);assert(p.available,'PROJECT_UNAVAILABLE','该功能入口尚未接好，请在开发者模式检查');const s=this.state();if(!s.selected.includes(p.id))s.selected.push(p.id);this.save(s);return p;}
 favorite(input){const s=this.state();assert(s.selected.includes(input.project_id),'PROJECT_NOT_ADDED','请先添加功能');s.favorites=s.favorites.includes(input.project_id)?s.favorites.filter(x=>x!==input.project_id):[...s.favorites,input.project_id];return this.save(s);}
 remove(input){const s=this.state();s.selected=s.selected.filter(x=>x!==input.project_id);s.favorites=s.favorites.filter(x=>x!==input.project_id);s.recent=s.recent.filter(x=>x!==input.project_id);delete s.bindings[input.project_id];for(const [key,value] of this.tickets)if(value.project_id===input.project_id)this.tickets.delete(key);return this.save(s);}
 remember(id){const s=this.state();s.recent=[id,...s.recent.filter(x=>x!==id)].slice(0,10);this.save(s);}
 open(input){
  const p=this.get(input.project_id);assert(this.state().selected.includes(p.id),'PROJECT_NOT_ADDED','请先添加功能');assert(p.available,'PROJECT_UNAVAILABLE','入口暂不可用');
  if(p.kind==='builtin'){this.remember(p.id);return {kind:'builtin',project_id:p.id};}
  const full=inside(this.root,p.entry);assert(path.extname(full).toLowerCase()==='.cmd'&&path.relative(this.root,full).replaceAll('\\','/').startsWith('software/'),'PROJECT_ENTRY','只允许经过目录验证的项目启动器');
  const token=crypto.randomUUID();this.tickets.set(token,{project_id:p.id,path:full,hash:crypto.createHash('sha256').update(fs.readFileSync(full)).digest('hex'),expires:Date.now()+30000});
  for(const [key,ticket] of this.tickets)if(ticket.expires<Date.now())this.tickets.delete(key);
  assert(this.tickets.size<=30,'OPEN_BUSY','打开请求过多，请稍后重试');
  return {kind:'desktop',ticket:token,project_id:p.id};
 }
 consume(input){
  const value=this.tickets.get(input.ticket);assert(value&&value.expires>Date.now(),'OPEN_EXPIRED','打开请求已过期，请重试');this.tickets.delete(input.ticket);
  const p=this.get(value.project_id);assert(this.state().selected.includes(p.id)&&p.available&&inside(this.root,p.entry)===value.path,'PROJECT_CHANGED','功能入口已变化');
  assert(crypto.createHash('sha256').update(fs.readFileSync(value.path)).digest('hex')===value.hash,'PROJECT_CHANGED','启动器已修改，请重新打开');
  return {project_id:p.id,path:value.path};
 }
 opened(input){const s=this.state();assert(s.selected.includes(input.project_id),'PROJECT_NOT_ADDED','功能已卸载');this.remember(input.project_id);this.store.event('project.open_requested',{project_id:input.project_id});return {status:'handed_to_os',runtime:'unverified'};}
 async attach(input){
  assert(typeof input.path==='string'&&input.path.trim().length>0,'SOURCE_PATH','请选择数据库文件或文件夹');
  assert(!input.path.startsWith('\\\\')&&!input.path.startsWith('//'),'SOURCE_LOCAL','请选择本机数据库文件或文件夹');
  assert(fs.existsSync(path.resolve(this.root,input.path)),'SOURCE_MISSING','找不到该资料库，请确认文件位置后重试');
  const absolute=fs.realpathSync(path.resolve(this.root,input.path));
  if(this.attaching.has(absolute))return this.attaching.get(absolute);
  const task=this.attachSource(absolute,input);this.attaching.set(absolute,task);try{return await task;}finally{this.attaching.delete(absolute);}
 }
 async attachSource(absolute,input){
  const revision=this.revision;
  const found=await discover(this.root,{roots:[absolute]});const candidates=found.candidates.filter(x=>x.kind==='database');
  assert(candidates.length===1,'SOURCE_IDENTIFY','请选择一个数据库文件或 Chroma 文件夹；暂不支持该格式或目录中有多个库');
  const candidate=candidates[0],m=candidate.manifest;
  const profile=await readDatabase(this.root,m,{operation:'library',profile_only:true});
  assert(revision===this.revision,'WORKSPACE_CHANGED','工作台已清空，请重新接入');
  const existing=this.registry.list().find(x=>x.database&&fs.existsSync(path.resolve(this.root,x.entry.path))&&fs.realpathSync(path.resolve(this.root,x.entry.path))===fs.realpathSync(m.path));
  const id=existing?.resource_id||'source:'+crypto.createHash('sha256').update(fs.realpathSync(m.path).toLowerCase()).digest('hex').slice(0,24);
  if(!existing)this.registry.import({...m,resource:{...m.resource,id,name:typeof input.name==='string'&&input.name.trim()?input.name.trim().slice(0,120):path.basename(absolute)},views:[{view_id:id+'.data',view_type:'table',renderer:'DOM',data_mode:'page'}]});
  if(this.registry.detached.has(id))await this.registry.attach(id);
  const profiles=this.store.get('user_source_profiles',{});profiles[id]={supported:profile.library.supported,reason:profile.library.reason||null};this.store.put('user_source_profiles',profiles);
  return {resource_id:id,name:this.registry.public(this.registry.get(id)).display_name,driver:candidate.driver,usable:profile.library.supported,message:profile.library.supported?'已接入，可以分配给资料阅读':profile.library.reason==='documents_not_stored'?'已接入；这个向量库未保存正文。阅读内容请接入对应的 SQLite 或 DuckDB 资料库。':'已接入，但资料阅读暂不支持这种内容格式；可在开发者模式检查',profile:profile.library};
 }
 async bind(input){
  const revision=this.revision;
  const p=this.get(input.project_id);assert(p.bindable,'BINDING_UNSUPPORTED','该独立应用尚未支持工作台数据绑定，请使用应用自身设置');
  assert(Array.isArray(input.resource_ids)&&input.resource_ids.length<=20,'BINDING_INPUT','每个项目最多选择 20 个数据源');
  const ids=[...new Set(input.resource_ids)];
  for(const id of ids){const r=this.registry.get(id);assert(r.manifest.adapter==='database'&&!this.registry.detached.has(id),'SOURCE_UNAVAILABLE','数据已断开，请重新接入');const result=await readDatabase(this.root,r.manifest,{operation:'library',profile_only:true});assert(result.library.supported,'SOURCE_UNSUPPORTED','这种数据格式还不能供资料阅读使用');}
  assert(revision===this.revision,'WORKSPACE_CHANGED','工作台已清空，请重新选择');const s=this.state();assert(s.selected.includes(p.id),'PROJECT_NOT_ADDED','请先添加功能');s.bindings[p.id]=ids;this.save(s);return {status:'bound',project_id:p.id,resource_ids:ids,consumer:'library.reader'};
 }
 async read(input){
  const s=this.state();assert(s.selected.includes(input.project_id)&&input.project_id===READER.id,'PROJECT_NOT_ADDED','请先添加资料阅读');
  const ids=s.bindings[input.project_id]||[];assert(ids.includes(input.resource_id),'SOURCE_NOT_BOUND','请先为该功能选择资料库');
  const r=this.registry.get(input.resource_id);assert(!this.registry.detached.has(r.resource_id),'SOURCE_UNAVAILABLE','数据已断开，请重新接入');
  assert(Number.isInteger(input.offset??0)&&(input.offset??0)>=0&&(input.offset??0)<=1000000,'PAGE_INPUT','页码无效');
  assert(typeof (input.query??'')==='string'&&(input.query??'').length<=200,'QUERY_INPUT','搜索内容过长');
  return readDatabase(this.root,r.manifest,{operation:'library',offset:input.offset||0,query:input.query||'',limit:20});
 }
 async empty(){this.revision++;await this.registry.unload();const previous=this.store.get('workspace');this.store.put('business_previous_workspace',previous);this.store.put('workspace',workspace({tabs:['home'],active:'home',layout:previous?.layout}));const s=this.state();s.selected=[];s.favorites=[];s.recent=[];s.bindings={};this.tickets.clear();return this.save(s);}
}
module.exports={Projects};
