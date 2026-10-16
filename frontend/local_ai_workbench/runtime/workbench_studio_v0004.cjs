// ============================================================
// File: workbench_studio_v0004.cjs
// 中文名: 栏目目录、独立工具承载与本地Skill市场服务
// Version: v0003
// Main Layer: action
// Layer: workbench / runtime
// Updatable: True
// Responsibilities: 展示独立资源并保持真实状态和来源。
// Does: 按显式操作承载工具、栏目和已授权的接取关系。
// Does not: 删除原始资料、在启动时登记开发对象或自动授权 AI。
// Purpose: 按显式操作管理资源、Skill来源及Agent接取关系。
// Boundary: 不执行Skill内容，不调用AI，不代替业务应用，不覆盖原始库。
// ============================================================
// ALIAS_META
// alias: workbench_studio
// family: workbench_studio
// role: resource_and_skill_market
// version: v0004
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_studio_v0004.cjs
// input:
//   - explicit authenticated category, import, scan and open requests
// output:
//   - private catalog, managed skills, agent feeds and embedded tool endpoints
// depends_on:
//   - workbench_store_v0001
//   - workbench_contracts_v0001
//   - workbench_skill_archive_v0001
// used_by:
//   - workbench_server_v0001
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {Skills,githubInfo}=require('./workbench_skills_v0002.cjs');
const {Tools}=require('./workbench_tools_v0005.cjs');
const {assert,localUrl,WorkbenchError}=require('./workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_studio_v0004.cjs';
const SCRIPT_FAMILY='workbench_studio';
const SCRIPT_VERSION='v0004';
const DEFAULT_ENCODING='utf8';
const CATEGORIES=[['agents','AI / Agent','连接 Agent，分配可用 Skill'],['tools','工具','独立执行，嵌入工作区'],['databases','数据库','挂载与浏览本地资料'],['plugins','插件','管理扩展与接入信息'],['skills','Skill','统一保管，按需交给 Agent'],['software','软件','管理本机软件入口'],['github','第三方 GitHub','收集仓库与能力来源']].map(([id,name,description])=>({id,name,description}));
const uid=()=>crypto.randomUUID();
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const safeName=value=>{assert(typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$/.test(value),'STUDIO_ID','名称仅支持字母、数字、点、横线和下划线');return value;};
function boundedText(value,label,max=300){assert(typeof value==='string'&&value.trim()&&value.length<=max,'STUDIO_INPUT',label+'无效');return value.trim();}
// ============================================================
// 可扩展栏目和本地目录；启动不扫描、不下载、不执行
// ============================================================
class Studio {
 constructor(root,store,projects,directory){this.root=root;this.store=store;this.projects=projects;this.dataDirectory=directory;this.skillMarket=new Skills(root,store,directory);this.toolHost=new Tools(root,store,directory);this.directory=this.skillMarket.directory;store.publishSkillAssignments(root);}
 categories(){const base=path.join(__dirname,'../config/categories');const defaults=fs.readdirSync(base).filter(x=>x.endsWith('.json')).map(x=>JSON.parse(fs.readFileSync(path.join(base,x),'utf8'))).sort((a,b)=>a.order-b.order);return [...defaults,...this.store.get('studio_categories',[]).filter(x=>!defaults.some(y=>y.id===x.id))];}
 items(){return this.store.get('studio_items',[]);}
 hidden(){return this.store.get('studio_hidden',[]);}
 reveal(ids){this.store.put('studio_hidden',this.hidden().filter(id=>!ids.includes(id)));}
 archive(values){const previous=this.store.get('studio_unloaded_items',[]);this.store.put('studio_unloaded_items',[...previous.filter(x=>!values.some(y=>y.id===x.id)),...values]);}
 skills(){return this.skillMarket.skills().filter(x=>!this.hidden().includes(x.id));}
 agents(){return this.skillMarket.agents().filter(x=>!this.hidden().includes(x.id));}
 snapshot(){return {categories:this.categories(),items:this.items(),skills:this.skills(),agents:this.agents(),layout:this.store.get('studio_layout',{}),databases:this.projects.home().sources.filter(x=>!this.projects.registry.detached.has(x.resource_id)).map(x=>({...x,id:x.resource_id,name:x.display_name,description:'只读挂载 · '+x.database.driver,path:x.entry.path})),skill_directory:this.directory};}
 layout(input){const next={};for(const [key,min,max] of [['left',180,460],['right',220,520],['bottom',80,400],['cards',180,400],['header',42,100]]){const v=Number(input[key]);if(Number.isFinite(v))next[key]=Math.max(min,Math.min(max,v));}for(const key of ['left_hidden','right_hidden','bottom_hidden'])next[key]=input[key]===true;if(input.focus_previous&&typeof input.focus_previous==='object')next.focus_previous=Object.fromEntries(['left','right','bottom'].map(side=>[side,input.focus_previous[side]===true]));this.store.put('studio_layout',next);return next;}
 category(input){const id=safeName(input.id);assert(!this.categories().some(x=>x.id===id),'CATEGORY_DUPLICATE','栏目已存在');const value={id,name:boundedText(input.name,'栏目名称',40),description:input.description||'自定义资源栏目'};this.store.put('studio_categories',[...this.store.get('studio_categories',[]),value]);return value;}
 add(input){assert(this.categories().some(x=>x.id===input.category),'CATEGORY','栏目不存在');const item={id:uid(),category:input.category,name:boundedText(input.name,'名称',120),description:String(input.description||'').slice(0,1000),url:String(input.url||''),path:String(input.path||''),created_at:new Date().toISOString(),source:'manual'};if(item.url){const u=new URL(item.url);assert(['http:','https:'].includes(u.protocol)&&!u.username&&!u.password,'RESOURCE_URL','仅接受无凭证的HTTP地址');}this.store.put('studio_items',[...this.items(),item]);this.store.event('studio.added',{id:item.id,category:item.category});return item;}
 remove(input){this.archive(this.items().filter(x=>x.id===input.id));assert(typeof input.id==='string'&&input.id,'STUDIO_ID','请选择资源');this.store.put('studio_items',this.items().filter(x=>x.id!==input.id));if(input.id==='tool.text-inspector')this.store.put('studio_text_visible',false);if(['tool.scene-notes','tool.data-sandbox'].includes(input.id))this.store.put('studio_hidden',[...new Set([...this.hidden(),input.id])]);if(this.skillMarket.skills().some(x=>x.id===input.id)||this.skillMarket.agents().some(x=>x.id===input.id))this.store.put('studio_hidden',[...new Set([...this.hidden(),input.id])]);if(this.projects.registry.resources.has(input.id))this.projects.registry.detach(input.id);this.store.put('studio_agents',this.store.get('studio_agents',[]).map(x=>({...x,skill_ids:x.skill_ids.filter(id=>id!==input.id)})));this.store.event('studio.unloaded',{id:input.id});return {removed:true,preserved:true};}
 async reset(){this.archive(this.items());this.store.put('studio_items',[]);this.store.put('studio_text_visible',false);this.store.put('studio_hidden',[...new Set([...this.hidden(),...this.skillMarket.skills().map(x=>x.id),...this.skillMarket.agents().map(x=>x.id)])]);this.store.put('studio_agents',this.store.get('studio_agents',[]).map(x=>({...x,skill_ids:[]})));await this.projects.empty();this.store.event('studio.emptied',{preserved:true});return {empty:true,preserved:true};}
 async scan(input){
  if(input.category==='libraries'){const found=[];const python=path.join(this.root,'.venv/Lib/site-packages');if(fs.existsSync(python))for(const entry of fs.readdirSync(python).filter(x=>x.endsWith('.dist-info'))){const metadata=path.join(python,entry,'METADATA');if(!fs.existsSync(metadata))continue;const text=fs.readFileSync(metadata,'utf8'),name=text.match(/^Name: (.+)$/m)?.[1]||entry,version=text.match(/^Version: (.+)$/m)?.[1]||'';found.push({id:'library:python:'+name,category:'libraries',name,description:'Python · '+version,path:metadata,version,source:'library-scan'});}const modules=path.join(__dirname,'../node_modules');if(fs.existsSync(modules)){const paths=fs.readdirSync(modules).filter(x=>!x.startsWith('.')).flatMap(x=>x.startsWith('@')?fs.readdirSync(path.join(modules,x)).map(y=>path.join(modules,x,y)):path.join(modules,x));for(const folder of paths){const file=path.join(folder,'package.json');if(!fs.existsSync(file))continue;try{const meta=JSON.parse(fs.readFileSync(file,'utf8'));found.push({id:'library:node:'+meta.name,category:'libraries',name:meta.name,description:'Node · '+meta.version,path:file,version:meta.version,source:'library-scan'});}catch{}}}found.push(...this.store.get('studio_unloaded_items',[]).filter(x=>x.category==='libraries'&&x.source==='manual'&&!this.items().some(y=>y.id===x.id)));this.store.put('studio_items',[...this.items().filter(x=>!(x.category==='libraries'&&x.source==='library-scan')),...found]);return {found};}
  if(input.category==='skills'){const result=this.skillMarket.scan(input);const matches=this.skillMarket.skills().filter(x=>result.found.some(f=>f.path===x.path||f.path===x.source));this.reveal(matches.map(x=>x.id));return result;}
  if(input.category==='github'){if(!input.url){const found=this.store.get('studio_unloaded_items',[]).filter(x=>x.category==='github');this.store.put('studio_items',[...this.items().filter(x=>!found.some(y=>y.id===x.id)),...found]);return {found};}const info=await githubInfo(input.url);return this.add({category:'github',name:info.name,description:info.description,url:info.url});}
  assert(['tools','software','plugins'].includes(input.category),'SCAN_UNSUPPORTED','此栏目使用手动添加或连接');
  const found=this.projects.catalog().filter(x=>x.kind==='external'&&x.available&&(input.category!=='tools'||x.id!=='app.independent_action_chat')).map(x=>({id:'native:'+x.id,category:input.category,name:x.name,description:x.description,path:x.entry,source:'local-scan',available:x.available,native:true}));if(input.category==='tools')found.push(...this.toolHost.discover(input.path));for(const item of this.store.get('studio_unloaded_items',[]).filter(x=>x.category===input.category&&x.source==='manual'&&!this.items().some(y=>y.id===x.id)))if(!found.some(x=>x.id===item.id))found.push(item);
  const previous=this.items().filter(x=>!(x.source==='local-scan'&&x.category===input.category));this.store.put('studio_items',[...previous,...found]);if(input.category==='tools'){this.store.put('studio_text_visible',true);this.reveal(found.map(x=>x.id));}return {found};
 }
 importSkills(...args){const result=this.skillMarket.importSkills(...args);this.reveal(result.installed.map(x=>x.id));return result;}
 async download(...args){const result=await this.skillMarket.download(...args);this.reveal(result.installed.map(x=>x.id));return result;}
 readSkill(...args){return this.skillMarket.readSkill(...args);}
 toggle(...args){return this.skillMarket.toggle(...args);}
 connect(...args){const result=this.skillMarket.connect(...args);this.reveal([result.id]);return result;}
 bind(...args){return this.skillMarket.bind(...args);}
 feed(...args){assert(!this.hidden().includes(args[0]),'AGENT_AUTH','Agent已从工作台卸载');const result=this.skillMarket.feed(...args);result.skills=result.skills.filter(x=>!this.hidden().includes(x.id));return result;}
 toolCatalog(...args){return this.toolHost.toolCatalog(...args);}
 open(...args){return this.toolHost.open(...args);}
 stop(...args){return this.toolHost.stop(...args);}
}
module.exports={Studio,CATEGORIES};
