// ============================================================
// 文件名: workbench_connections_v0001.cjs
// 中文名: 工作台接入解除加载、扫描恢复与核验编排
// 版本号: v0001
// 主层级: action
// 层级: workbench / connections
// 脚本定位: 工作台接入解除加载、扫描恢复与核验编排
// 职责说明:
//   - 管理可追溯的接入状态变更及结果核验
// 本脚本做什么:
//   - 工作台接入解除加载、扫描恢复与核验编排
// 本脚本不做什么:
//   - 不删除业务数据，不安装软件，不授予 AI 或资源执行许可
// 制度边界声明:
//   - 显式授权、恢复清单、失败留证；不停止独立工具进程
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_connections_v0001
// family: workbench_connections
// role: managed_connections
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_connections_v0001.cjs
// input:
//   - authenticated explicit operation and versioned JSON handoff
// output:
//   - persisted state, comparison and audit
// depends_on:
//   - existing Studio, Registry, Software and Capabilities adapters
//   - Node standard library
// used_by:
//   - workbench_server
// ============================================================

'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {assert}=require('./workbench_contracts_v0001.cjs');
const {discover}=require('../adapters/workbench_discovery_v0001.cjs');
const SCRIPT_NAME='workbench_connections_v0001',SCRIPT_VERSION='v0001';
const hash=x=>crypto.createHash('sha256').update(x).digest('hex'),read=f=>JSON.parse(fs.readFileSync(f,'utf8'));
const write=(f,v)=>{fs.mkdirSync(path.dirname(f),{recursive:true});const tmp=f+'.'+process.pid+'.tmp';fs.writeFileSync(tmp,JSON.stringify(v,null,2));fs.renameSync(tmp,f);};
const ids=a=>[...new Set(a)].sort();
const stable=v=>Array.isArray(v)?v.map(stable):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,stable(v[k])])):v;
const same=(a,b)=>JSON.stringify(stable(a))===JSON.stringify(stable(b));
class Connections {
 constructor(root,store,studio,registry,capabilities,software,governance,adapterCatalog){Object.assign(this,{root,store,studio,registry,capabilities,software,governance,adapterCatalog});this.directory=path.join(store.directory,'connections');this.busy=null;this.closed=false;}
 capture(){return {adapters:this.adapterCatalog?.loaded()||[],items:this.studio.items(),tools:this.studio.toolCatalog(),skills:this.studio.skills(),agents:this.studio.agents(),software:this.store.get('loaded_software',[]),capabilities:this.store.get('loaded_capabilities',[]),project:this.store.get('active_project'),resources:[...this.registry.resources.values()].filter(x=>x.resource_id!=='workbench.system').map(x=>({id:x.resource_id,manifest:x.manifest,detached:this.registry.detached.has(x.resource_id)})),user:this.studio.projects.state()};}
 identity(s){return {adapters:(s.adapters||[]).map(x=>({id:x.id,sha256:x.sha256})).sort((a,b)=>a.id.localeCompare(b.id)),items:ids(s.items.map(x=>x.category+':'+x.id)),tools:ids(s.tools.map(x=>x.id)),skills:ids(s.skills.map(x=>x.id)),agents:ids(s.agents.map(x=>x.id)),skill_states:s.skills.map(x=>({id:x.id,enabled:x.enabled})).sort((a,b)=>a.id.localeCompare(b.id)),resource_connections:s.resources.map(x=>({id:x.id,detached:x.detached})).sort((a,b)=>a.id.localeCompare(b.id)),software:ids(s.software.map(x=>x.id)),capabilities:ids(s.capabilities),project:s.project||null,resources:ids(s.resources.map(x=>x.id)),bindings:s.user.bindings,selected:ids(s.user.selected),agent_bindings:s.agents.map(x=>({id:x.id,skills:ids(x.skill_ids)})).sort((a,b)=>a.id.localeCompare(b.id))};}
 summary(s){const v=this.identity(s);return {...v,counts:Object.fromEntries(Object.entries(v).filter(([,x])=>Array.isArray(x)).map(([k,x])=>[k,x.length]))};}
 save(run){run.updated_at=new Date().toISOString();write(path.join(this.directory,run.id+'.json'),run);this.store.put('connections_latest',run.id);this.store.event('connections.changed',{id:run.id,status:run.status,phase:run.phase});}
 get(id=this.store.get('connections_latest')){if(!id)return null;assert(/^connection_[a-f0-9]{32}$/.test(id),'CONNECTION_ID','恢复记录无效');return read(path.join(this.directory,id+'.json'));}
 public(run){if(!run)return null;return {id:run.id,status:run.status,phase:run.phase,created_at:run.created_at,updated_at:run.updated_at,before:this.summary(run.before),after:run.after,checks:run.checks,errors:run.errors,events:run.events};}
 status(){return {current:this.summary(this.capture()),latest:this.public(this.get()),busy:!!this.busy,semantics:'解除加载接入不删除文件或登记，不停止工具；恢复原清单，不自动新增扫描发现的资源或授权。'};}
 async exclusive(fn){assert(!this.busy,'CONNECTION_BUSY','接入维护正在执行，请稍后再试');const lock=path.join(this.directory,'operation.lock');fs.mkdirSync(this.directory,{recursive:true});if(fs.existsSync(lock)){const old=read(lock);let alive=false;try{process.kill(old.pid,0);alive=true;}catch{}assert(!alive,'CONNECTION_BUSY','另一个工作台进程正在维护相同接入目录');fs.unlinkSync(lock);}fs.writeFileSync(lock,JSON.stringify({pid:process.pid}),{flag:'wx'});this.busy=Promise.resolve().then(fn);try{return await this.busy;}finally{this.busy=null;fs.unlinkSync(lock);}}
 async operation(op,input={}){assert(['reset','restore','cycle'].includes(op),'CONNECTION_OPERATION','不支持的接入操作');return this.exclusive(async()=>{
  if(input.request_id){assert(/^[a-zA-Z0-9_-]{8,160}$/.test(input.request_id),'CONNECTION_REQUEST','提交身份无效');const old=this.store.get('connection_request:'+input.request_id);if(old){assert(old.operation===op,'CONNECTION_REQUEST','提交标识已用于其他操作');return this.public(this.get(old.id));}}
  let run;if(op==='restore'){run=this.get(input.id);assert(run,'CONNECTION_MISSING','没有可以恢复的解除加载清单');assert(hash(JSON.stringify(run.before))===run.before_sha256,'CONNECTION_SNAPSHOT','恢复清单指纹不一致');if(run.status==='completed')return this.public(run);if(run.after)assert(same({...run.after,adapters:run.after.adapters||[],counts:{...run.after.counts,adapters:(run.after.adapters||[]).length}},this.summary(this.capture())),'CONNECTION_CHANGED','接入状态已被其他操作改变，请先核对恢复记录，不能覆盖新的加载选择');}else{const latest=this.get();assert(!latest||latest.status==='completed','CONNECTION_PENDING','上次解除加载尚未完整恢复，请先扫描恢复该清单');const before=this.capture();run={schema_version:'workbench.connections.v1',id:'connection_'+crypto.randomUUID().replaceAll('-',''),created_at:new Date().toISOString(),before,before_sha256:hash(JSON.stringify(before)),status:'running',phase:'snapshot',events:[],errors:[],checks:[]};this.save(run);}
  if(input.request_id)this.store.put('connection_request:'+input.request_id,{operation:op,id:run.id});
  const event=(phase,detail)=>{run.phase=phase;run.events.push({at:new Date().toISOString(),phase,...detail});this.save(run);};
  try{await this.governance.request('record_operation',{operation:'connections.'+op,target:run.id,status:'user_confirmed'});
   if(op!=='restore'){event('unloading',{});await this.studio.reset();this.store.put('loaded_adapters',[]);this.store.put('loaded_software',[]);this.store.put('loaded_capabilities',[]);this.store.put('active_project',null);const empty=this.capture();const clean=['items','tools','skills','agents','software','capabilities','resources','adapters'].every(k=>empty[k].length===0)&&empty.user.selected.length===0;run.checks=[{check:'unloaded_connections_empty',passed:clean}];event('unloaded',{counts:this.summary(empty).counts});assert(clean,'CONNECTION_NOT_EMPTY','解除加载后仍存在加载接入');run.status='unloaded';run.after=this.summary(empty);this.save(run);}
   if(op==='reset')return this.public(run);
   run.status='restoring';run.errors=[];this.save(run);
   const attempt=async(name,fn)=>{event(name,{});try{await fn();}catch(e){run.errors.push({phase:name,error:e.message});this.save(run);}};
   const before=run.before;
   for(const entry of before.adapters||[])await attempt('restore_adapter:'+entry.id,async()=>{assert(this.adapterCatalog,'ADAPTER_CATALOG','适配器目录不可用');this.adapterCatalog.change(entry.id,true,entry.sha256);});
   await attempt('scan_tools',async()=>{await this.studio.scan({category:'tools'});for(const tool of before.tools.filter(x=>x.integration==='manifest'&&x.manifest)){await this.studio.scan({category:'tools',path:path.dirname(tool.manifest)});}const found=this.studio.toolCatalog();for(const t of before.tools)assert(found.some(x=>x.id===t.id),'CONNECTION_TOOL','扫描未发现原工具：'+t.id);});
   for(const category of ids(before.items.map(x=>x.category)).filter(x=>['libraries','github','plugins'].includes(x)))await attempt('scan_'+category,()=>this.studio.scan({category}));
   for(const skill of before.skills)await attempt('scan_skill:'+skill.id,async()=>{assert(fs.existsSync(path.join(skill.path,'SKILL.md')),'CONNECTION_SKILL','Skill 文件缺失');await this.studio.scan({category:'skills',path:skill.path});assert(this.studio.skills().some(x=>x.id===skill.id),'CONNECTION_SKILL','Skill 未能重新发现');});
   await attempt('restore_agent_connections',async()=>{const known=this.studio.skillMarket.agents();assert(before.agents.every(x=>known.some(y=>x.id===y.id)),'CONNECTION_AGENT','原 Agent 登记缺失');this.studio.reveal(before.agents.map(x=>x.id));});
   if(before.software.length)await attempt('scan_software',async()=>{await this.software.scan();for(const old of before.software){const found=this.software.snapshot().items.find(x=>x.id===old.id);assert(found?.path&&fs.existsSync(found.path),'CONNECTION_SOFTWARE','软件已缺失：'+old.name);assert(hash(fs.readFileSync(found.path))===old.sha256,'CONNECTION_SOFTWARE_CHANGED','软件文件已变化：'+old.name+'，需用户单独重新加载');this.software.load({id:old.id});}});
   if(before.capabilities.length)await attempt('reload_capabilities',async()=>{const list=(await this.capabilities.list()).items;for(const id of before.capabilities){assert(list.some(x=>x.id===id),'CONNECTION_CAPABILITY','能力登记缺失：'+id);await this.capabilities.load({id});}});
   for(const source of before.resources)await attempt('discover_source:'+source.id,async()=>{const m=source.manifest;if(m.path){assert(fs.existsSync(path.resolve(this.root,m.path)),'CONNECTION_SOURCE','原资源路径缺失：'+source.id);}if(m.adapter==='database'){const discovered=await discover(this.root,{roots:[path.resolve(this.root,m.path)]});assert(discovered.candidates.some(x=>x.kind==='database'&&x.driver===m.driver),'CONNECTION_DATABASE','数据库扫描未识别原类型：'+source.id);}this.registry.import(m);if(source.detached)this.registry.detach(source.id);else await this.registry.attach(source.id);});
   await attempt('restore_project',()=>this.capabilities.selectProject({id:before.project}));
   await attempt('restore_reader_bindings',async()=>{const p=this.studio.projects;for(const id of before.user.selected)if(!p.state().selected.includes(id))p.select({project_id:id});for(const [id,resource_ids] of Object.entries(before.user.bindings))await p.bind({project_id:id,resource_ids});p.save({...p.state(),favorites:before.user.favorites,recent:before.user.recent});});
   // Restore only the previously loaded set; scan discoveries remain available through each catalog.
   await attempt('reconcile_original_set',async()=>{const desired=new Set(before.items.map(x=>x.category+':'+x.id));for(const item of this.studio.items())if(!desired.has(item.category+':'+item.id)&&!before.tools.some(x=>x.id===item.id))this.studio.remove({id:item.id});const found=this.studio.items();for(const original of before.items){assert(!original.path||fs.existsSync(path.resolve(this.root,original.path)),'CONNECTION_FILE','登记文件缺失：'+original.name);if(!found.some(x=>x.category===original.category&&x.id===original.id)){assert(original.source==='manual','CONNECTION_DISCOVERY','扫描无法恢复：'+original.name);assert(!original.path||fs.existsSync(path.resolve(this.root,original.path)),'CONNECTION_FILE','登记文件缺失：'+original.name);found.push(original);}}
    // Built-ins need no duplicate studio row. Keep original IDs, metadata and references after rediscovery.
    const foundMap=new Map(found.map(x=>[x.category+':'+x.id,x]));this.store.put('studio_items',before.items.filter(x=>foundMap.has(x.category+':'+x.id)).map(x=>({...foundMap.get(x.category+':'+x.id),...x})));
    const wantedTools=new Set(before.tools.map(x=>x.id));this.store.put('studio_text_visible',wantedTools.has('tool.text-inspector'));for(const id of ['tool.scene-notes','tool.data-sandbox']){if(wantedTools.has(id))this.studio.reveal([id]);else this.store.put('studio_hidden',ids([...this.studio.hidden(),id]));}
   });
   const after=this.capture();run.after=this.summary(after);const equivalent=same(this.identity(before),this.identity(after));run.checks=run.checks.filter(x=>x.check!=='restored_identity_and_bindings').concat({check:'restored_identity_and_bindings',passed:equivalent});run.status=equivalent&&!run.errors.length?'completed':'partial';event('verified',{equivalent});await this.governance.request('record_operation',{operation:'connections.'+op+'.result',target:run.id,status:run.status});return this.public(run);
  }catch(error){run.status='failed';run.errors.push({phase:run.phase,error:error.message});this.save(run);throw error;}
 });}
 async close(){this.closed=true;if(this.busy)await this.busy;}
}
module.exports={Connections};
