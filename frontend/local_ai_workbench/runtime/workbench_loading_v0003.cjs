// ============================================================
// 文件名: workbench_loading_v0003.cjs
// 中文名: 统一扫描与按范围加载编排
// 版本号: v0003
// 主层级: action
// 层级: workbench / loading
// 脚本定位: 统一扫描与按范围加载编排
// 职责说明: 显式管理工作台接入，保留原资源和独立生命周期
// 本脚本做什么: 统一扫描与按范围加载编排
// 本脚本不做什么: 不删除文件，不安装依赖，不自动授权或执行资源
// 制度边界声明: 扫描与加载分开；错误留证；只修改工作台接入
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_loading_v0003
// family: workbench_loading
// role: workbench_loading
// version: v0003
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_loading_v0003.cjs
// input:
//   - explicit scoped selection and existing resource inventories
// output:
//   - truthful loading state and per-resource results
// depends_on:
//   - existing resource catalogs and Connections operation lock
// used_by:
//   - workbench_server
// ============================================================

'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {assert}=require('./workbench_contracts_v0001.cjs');
const {discover}=require('../adapters/workbench_discovery_v0001.cjs');
const SCRIPT_NAME='workbench_loading_v0003',SCRIPT_VERSION='v0003';
const digest=x=>crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');
const DISABLED={growth:'经营中心使用业务数据集、导入和版本管理，不属于资源接入加载。',assets:'资产按稳定 ID 和项目引用管理，请使用资产导入与引用操作。',standards:'规范按审批、生效流程管理，不能通过解除加载停用。',tasks:'任务使用启动、暂停和取消管理，不属于资源加载。',environment:'环境是观察清单；依赖包安装与删除不属于接入加载。'};
const kindScopes=(kind,item)=>kind==='adapter'?['adapters']:kind==='resource'?['capabilities']:kind==='reader'?['tools']:kind==='capability'?['capabilities',...({mcp:['mcp'],github:['github'],library:['libraries'],skill:['skills']}[item.kind]||[])]:[kind==='item'?item.category:kind==='tool'?'tools':kind==='skill'?'skills':kind==='agent'||kind==='ai'?'agents':kind==='database'?'databases':kind==='project'?'projects':'software'];
class Loading{
 constructor(root,store,studio,registry,capabilities,software,inventory,connections,governance,adapterCatalog){Object.assign(this,{root,store,studio,registry,capabilities,software,inventory,connections,governance,adapterCatalog});}
 databaseGroups(){const config=JSON.parse(fs.readFileSync(path.join(__dirname,'../config/database_groups_v0001.json'),'utf8'));assert(config.schema_version==='workbench.database-groups.v1'&&Array.isArray(config.groups),'DATABASE_GROUP_CONFIG','数据库分组配置无效');return config;}
 databaseGroup(row){const cfg=this.databaseGroups(),overrides=this.store.get('database_group_overrides',{}),v=row.value,valid=x=>cfg.groups.some(g=>g.id===x),explicit=overrides[row.id]||v.metadata?.database_group;if(valid(explicit))return {database_group:explicit,group_source:overrides[row.id]?'用户指定':'资源声明'};const p=path.resolve(this.root,v.path).replaceAll('\\','/').toLowerCase(),local=path.relative(this.root,p).replaceAll('\\','/').toLowerCase();const exact=cfg.paths.find(x=>local===x.path.toLowerCase());if(exact)return {database_group:exact.group,group_source:'配置路径'};const prefix=(cfg.prefixes||[]).find(x=>local.startsWith(x.path.toLowerCase()));if(prefix)return {database_group:prefix.group,group_source:'配置目录'};const parts=p.split('/');for(const group of ['understand','data','action'])if(parts.includes(group))return {database_group:group,group_source:'目录归属'};return {database_group:'business',group_source:'默认业务，可调整'};}
 databaseType(type='all'){assert(type==='all'||this.databaseGroups().types.some(x=>x.id===type),'DATABASE_TYPE','数据库种类不存在');return type;}
 async registerDatabaseInfo(input){return this.connections.exclusive(async()=>{const type=this.databaseType(input.type);assert(type!=='all'&&!['sqlite','duckdb','chroma'].includes(type),'DATABASE_TYPE','支持的文件型数据库请使用扫描或添加数据库');this.group(input.group);assert(input.group!=='all','DATABASE_GROUP','请选择具体分组');assert(typeof input.name==='string'&&input.name.trim()&&input.name.length<160,'DATABASE_NAME','请输入名称');assert(typeof input.location==='string'&&input.location.trim()&&input.location.length<1024,'DATABASE_LOCATION','请输入不含凭证的位置');if(input.location.includes('://')){const url=new URL(input.location);assert(!url.username&&!url.password&&!url.search&&!url.hash,'DATABASE_CREDENTIAL','连接位置不能包含账号、密码、查询参数或令牌');}const location=input.location.trim(),id='database-info:'+digest([type,location]).slice(0,24);const row=this.row('database',{resource:{id,name:input.name.trim()},driver:type,path:location,metadata:{database_group:input.group,registration_only:true}});await this.governance.ensure();await this.governance.request('record_operation',{operation:'database.register_info',target:id,status:'recorded'});this.remember([row]);this.store.event('database.registered',{id,type,group:input.group});return {id,registration_only:true,connected:false};});}
 group(group='all'){assert(group==='all'||this.databaseGroups().groups.some(x=>x.id===group),'DATABASE_GROUP','数据库分组不存在');return group;}
 async setDatabaseGroup(input){return this.connections.exclusive(async()=>{this.group(input.group);assert(input.group!=='all','DATABASE_GROUP','请选择具体分组');const cat=await this.catalog('databases');assert(cat.revision===input.revision,'LOADING_CHANGED','清单已变化，请刷新');const row=cat.items.find(x=>x.key===input.key);assert(row,'DATABASE_GROUP_RESOURCE','数据库不在已发现目录中');await this.governance.ensure();await this.governance.request('record_operation',{operation:'database.group',target:row.id,status:'requested',detail:{before:row.database_group,after:input.group}});this.store.put('database_group_overrides',{...this.store.get('database_group_overrides',{}),[row.id]:input.group});this.store.event('database.group.changed',{id:row.id,before:row.database_group,after:input.group,at:new Date().toISOString()});return {id:row.id,group:input.group};});}
 scope(scope='all'){assert(scope==='all'||this.studio.categories().some(x=>x.id===scope),'LOADING_SCOPE','栏目不存在');return scope;}
 candidates(){return this.store.get('loading_candidates',[]);}
 remember(rows){const known=new Map(this.candidates().map(x=>[x.key,x]));for(const row of rows)known.set(row.key,row);assert(known.size<=20000,'LOADING_LIMIT','接入候选超过 20000 项，请缩小扫描范围');this.store.put('loading_candidates',[...known.values()]);}
 row(kind,value){const id=['database','resource'].includes(kind)?value.resource.id:value.id;return {key:kind+':'+id,kind,id,scopes:kindScopes(kind,value),value};}
 async entries(){
  const rows=new Map(this.candidates().filter(x=>x.kind!=='adapter').map(x=>[x.key,x])),errors=[];
  const add=(kind,value)=>{const row=this.row(kind,value);rows.set(row.key,row);};
  for(const value of [...this.store.get('studio_unloaded_items',[]),...this.studio.items()])if(!value.managed_software)add(value.category==='tools'?'tool':value.inventory_kind?'ai':'item',value);
  for(const value of this.studio.toolCatalog())add('tool',value);
  add('tool',{id:'tool.text-inspector',name:'文本检查器',category:'tools',builtin:true});
  for(const value of this.studio.skillMarket.skills())add('skill',value);
  for(const value of this.studio.skillMarket.agents())add('agent',value);
  for(const value of [...this.store.get('loaded_software',[]),...this.software.snapshot().items])add('software',value);
  for(const value of this.registry.resources.values())if(value.resource_id!=='workbench.system')add(value.manifest.adapter==='database'?'database':'resource',value.manifest);
  for(const value of this.studio.projects.catalog())if(this.studio.projects.state().selected.includes(value.id))add('reader',value);
  // Persisted manifests retain database identities after an all-connections detach.
  if(fs.existsSync(this.registry.directory))for(const name of fs.readdirSync(this.registry.directory).filter(x=>x.endsWith('.json'))){try{const value=JSON.parse(fs.readFileSync(path.join(this.registry.directory,name),'utf8'));const kind=value.adapter==='database'?'database':'resource';if(value.resource?.id!=='workbench.system'&&!rows.has(kind+':'+value.resource.id))add(kind,value);}catch(e){errors.push({scope:'databases',error:'资源声明不可读取：'+name});}}
  try{for(const value of (await this.capabilities.list()).items)add('capability',value);}catch(e){errors.push({scope:'capabilities',error:e.message});}
  try{for(const value of (await this.capabilities.projects()).items)add('project',value);}catch(e){errors.push({scope:'projects',error:e.message});}
  for(const value of this.adapterCatalog?.records()||[])add('adapter',value);
  return {rows:[...rows.values()],errors};
 }
 present(row){
  if(row.kind==='adapter')return this.adapterCatalog.present(row.value);
  const v=row.value,{kind,id}=row,items=this.studio.items(),hidden=this.studio.hidden();let loaded=false,available=true,reason='';
  const local=v.manifest||v.path||v.resolved_path;
  if(local&&!(kind==='software'&&!v.path))available=fs.existsSync(path.resolve(this.root,local));
  if(!available)reason='本地来源缺失，请重新扫描或修复路径';
  if(kind==='tool')loaded=this.studio.toolCatalog().some(x=>x.id===id);
  if(kind==='item'||kind==='ai')loaded=items.some(x=>x.id===id&&x.category===v.category);
  if(kind==='skill'){loaded=this.studio.skills().some(x=>x.id===id);available=v.available!==false&&fs.existsSync(path.join(v.path,'SKILL.md'));if(!available)reason='Skill 文件缺失';}
  if(kind==='agent')loaded=this.studio.agents().some(x=>x.id===id);
  if(kind==='software'){loaded=this.store.get('loaded_software',[]).some(x=>x.id===id);available=available&&!!v.launchable&&!!v.path;if(!available)reason='未找到可靠的软件启动入口';}
  if(kind==='capability'){loaded=this.store.get('loaded_capabilities',[]).includes(id);if(v.asset_state==='missing'){available=false;reason='能力文件缺失';}}
  if(kind==='reader'){loaded=this.studio.projects.state().selected.includes(id);available=v.available!==false;if(!available)reason='兼容功能入口不可用';}
  if(kind==='resource'){loaded=this.registry.resources.has(id)&&!this.registry.detached.has(id);}
  if(kind==='database'){loaded=this.registry.resources.has(id)&&!this.registry.detached.has(id);available=fs.existsSync(path.resolve(this.root,v.path));if(!available)reason='数据库路径缺失';if(!['sqlite','duckdb','chroma'].includes(v.driver)){available=false;reason='仅登记信息；尚无此类型的只读连接适配器';}}
  if(kind==='project'){loaded=this.store.get('active_project')===id;available=!v.archived;if(!available)reason='项目已归档';}
  const db=kind==='database'?{...this.databaseGroup(row),database_type:v.driver,preview_supported:['sqlite','duckdb','chroma'].includes(v.driver),driver:v.driver,connection:this.registry.resources.get(id)?.connection?.status||'disconnected',table_count:this.registry.resources.get(id)?.table_count??null}:{};
  return {...db,key:row.key,id,kind,scopes:row.scopes,name:v.name||v.resource?.name||id,description:v.description||v.inventory_kind||v.kind||'',path:local||'',loaded,available,reason,single:kind==='project',fingerprint:digest([id,kind,v.path,v.manifest,v.url,v.version,v.bundle_hash,v.provider,v.model])};
 }
 async catalog(scope='all',group='all',type='all'){
  this.databaseType(type);assert(type==='all'||scope==='databases','DATABASE_TYPE_SCOPE','种类筛选只用于数据库栏目');
  this.group(group);assert(group==='all'||scope==='databases','DATABASE_GROUP_SCOPE','分组只用于数据库栏目');
  this.scope(scope);const {rows,errors}=await this.entries();const selected=rows.filter(x=>scope==='all'||x.scopes.includes(scope)).map(x=>this.present(x)).filter(x=>(group==='all'||x.database_group===group)&&(type==='all'||x.database_type===type)).sort((a,b)=>a.name.localeCompare(b.name)||a.key.localeCompare(b.key));
  return {schema_version:'workbench.loading.catalog.v1',scope,group,type,groups:this.databaseGroups().groups,types:this.databaseGroups().types,items:selected,revision:digest([scope,group,type,selected.map(x=>[x.key,x.loaded,x.available,x.fingerprint,x.database_group])]),disabled_reason:DISABLED[scope]||null,errors,observed_at:this.store.get('loading_scan:'+scope)?.at||null,scan:this.store.get('loading_scan:'+scope)};
 }
 async scan(input={}){const scope=this.scope(input.scope);this.group(input.group||'all');assert(!DISABLED[scope],'LOADING_SCOPE',DISABLED[scope]);return this.connections.exclusive(async()=>{
  const rows=[],errors=[],wanted=x=>scope==='all'||scope===x,add=(kind,value)=>rows.push(this.row(kind,value));
  const attempt=async(scope,fn)=>{try{await fn();}catch(e){errors.push({scope,error:e.message});}};
  for(const c of ['tools','plugins','libraries','github'])if(wanted(c))await attempt(c,async()=>{const result=await this.studio.scan({category:c,discover_only:true});for(const value of result.found||[])add(c==='tools'?'tool':'item',value);});
  if(wanted('tools'))for(const name of ['scene_notes','data_sandbox'])await attempt('tools',async()=>{const file=path.join(this.root,'frontend/local_ai_workbench/tools/landscape',name,'tool.manifest.json');if(fs.existsSync(file)){const v=this.studio.toolHost.manifests.read(file);add('tool',{id:v.id,name:v.name,category:'tools',integration:'manifest',manifest:file,source:'local-scan',available:true});}});
  if(wanted('skills'))for(const value of this.studio.skillMarket.skills())await attempt('skills',async()=>{await this.studio.skillMarket.scan({path:value.path});add('skill',value);});
  if(wanted('software')||wanted('agents'))await attempt('software',()=>this.software.scan());
  if(wanted('agents')){
   await attempt('agents',async()=>{const value=await this.inventory.snapshot(true);for(const provider of value.providers||[])for(const model of provider.models||[])add('ai',{id:'ai:'+provider.provider+':'+model.model,category:'agents',name:model.display_name||model.model,provider:provider.provider,model:model.model,inventory_kind:provider.provider==='local_agents'?'总控 Agent':'AI 模型',source:'manual',description:'AI 总控目录；加载不授予调用许可'});for(const problem of value.problems||[])errors.push({scope:'agents',error:typeof problem==='string'?problem:JSON.stringify(problem)});});
   for(const value of this.software.snapshot().agents||[])add('ai',{id:'local-agent:'+digest([value.id,value.name,value.path]).slice(0,24),name:value.name,path:value.path||'',category:'agents',inventory_kind:'本机 Agent',source:'manual',description:value.evidence||value.source||''});
  }
  if(wanted('databases'))await attempt('databases',async()=>{const known=(await this.entries()).rows.filter(x=>x.kind==='database');const byPath=new Map(known.map(x=>[path.resolve(this.root,x.value.path).toLowerCase()+':'+x.value.driver,x.value]));const roots=input.roots||this.databaseGroups().scan_roots.filter(x=>fs.existsSync(path.resolve(this.root,x)));const result=roots.length?await discover(this.root,{roots}):{candidates:[],problems:[],truncated:false};for(const value of result.candidates.filter(x=>x.kind==='database'))add('database',byPath.get(path.resolve(this.root,value.path).toLowerCase()+':'+value.driver)||value.manifest);errors.push(...result.problems.map(x=>({scope:'databases',error:JSON.stringify(x)})));if(result.truncated)errors.push({scope:'databases',error:'扫描达到预算上限，结果不完整'});});
  if(wanted('adapters')&&this.adapterCatalog)await attempt('adapters',async()=>{const result=this.adapterCatalog.scan();errors.push(...(result.scan?.errors||[]).map(x=>({scope:'adapters',error:x.path+'：'+x.error})));});
  this.remember(rows);const catalog=await this.catalog(scope,input.group||'all',input.type||'all');errors.push(...catalog.errors);this.store.put('loading_scan:'+scope,{at:new Date().toISOString(),status:errors.length?'partial':'completed',errors});this.store.event('loading.changed',{operation:'scan',scope,count:catalog.items.length});return {...await this.catalog(scope,input.group||'all',input.type||'all'),errors,status:errors.length?'partial':'completed'};
 });}
 async apply(input){const scope=this.scope(input.scope);assert(!DISABLED[scope],'LOADING_SCOPE',DISABLED[scope]);assert(['load','unload'].includes(input.operation),'LOADING_OPERATION','请选择加载或解除加载');assert(['all','selected'].includes(input.mode),'LOADING_MODE','请选择全部或按需');assert(typeof input.request_id==='string'&&/^[a-zA-Z0-9_-]{8,160}$/.test(input.request_id),'LOADING_REQUEST','请求身份无效');
  return this.connections.exclusive(async()=>{
   const signature=digest(input),old=this.store.get('loading_request:'+input.request_id);if(old){assert(old.signature===signature,'LOADING_REPLAY','请求身份已被用于其他操作');return old.result;}
   const catalog=await this.catalog(scope,input.group||'all',input.type||'all');assert(catalog.revision===input.revision,'LOADING_CHANGED','清单已变化，请刷新后重新确认');const load=input.operation==='load';
   const selected=input.mode==='all'?catalog.items.filter(x=>x.loaded!==load&&(load?x.available&&!x.single:true)):(()=>{assert(Array.isArray(input.keys)&&input.keys.length&&input.keys.length<=2000&&new Set(input.keys).size===input.keys.length,'LOADING_SELECTION','请选择有效资源');return input.keys.map(key=>{const found=catalog.items.find(x=>x.key===key);assert(found,'LOADING_SELECTION','资源不属于当前栏目');return found;});})();
   assert(!(load&&selected.filter(x=>x.single).length>1),'LOADING_PROJECT','一次只能设定一个当前项目');assert(selected.every(x=>!load||x.available),'LOADING_MISSING','选择中包含不可加载的来源');
   const privateRows=(await this.entries()).rows;this.remember(privateRows);const byKey=new Map(privateRows.map(x=>[x.key,x]));const result={schema_version:'workbench.loading.result.v1',id:'loading_'+crypto.randomUUID().replaceAll('-',''),scope,group:input.group||'all',type:input.type||'all',operation:input.operation,status:'running',started_at:new Date().toISOString(),results:[],preserved:{files:true,permissions:true,independent_processes:true},skipped:input.mode==='all'&&load?catalog.items.filter(x=>!x.loaded&&(!x.available||x.single)).map(x=>({key:x.key,name:x.name,reason:x.reason||'当前项目需单项选择'})):[]};
   const file=path.join(this.connections.directory,result.id+'.json');const save=()=>{fs.mkdirSync(path.dirname(file),{recursive:true});const tmp=file+'.tmp';fs.writeFileSync(tmp,JSON.stringify(result,null,2));fs.renameSync(tmp,file);this.store.put('loading_request:'+input.request_id,{signature,result});this.store.put('loading_latest',result);};save();
   try{await this.governance.ensure();await this.governance.request('record_operation',{operation:'loading.'+input.operation,target:result.id,status:'user_confirmed'});}catch(error){result.status='failed';result.error=error.message;save();throw error;}
   for(const item of selected){try{if(item.loaded!==load)await this.change(byKey.get(item.key),load);const observed=this.present(byKey.get(item.key));assert(observed.loaded===load,'LOADING_VERIFY','执行后加载状态未达到目标');result.results.push({key:item.key,name:item.name,status:'completed',loaded:observed.loaded});}catch(e){result.results.push({key:item.key,name:item.name,status:'failed',error:e.message});}save();}
   result.status=result.results.some(x=>x.status==='failed')?'partial':'completed';result.finished_at=new Date().toISOString();save();await this.governance.request('record_operation',{operation:'loading.'+input.operation+'.result',target:result.id,status:result.status});this.store.event('loading.changed',{id:result.id,operation:input.operation,scope,status:result.status});return result;
  });
 }
 async change(row,load){const {kind,id,value:v}=row;
  if(kind==='adapter')return this.adapterCatalog.change(id,load);
  if(kind==='software')return this.software.load({id,loaded:load});
  if(kind==='capability')return this.capabilities.load({id,loaded:load});
  if(kind==='project')return this.capabilities.selectProject({id:load?id:null});
  if(kind==='reader'){if(load)this.studio.projects.select({project_id:id});else{const state=this.studio.projects.state();this.studio.projects.save({...state,selected:state.selected.filter(x=>x!==id)});}return;}
  if(kind==='database'||kind==='resource'){if(load){if(!this.registry.resources.has(id))this.registry.import(v);const observation=await this.registry.attach(id);if(kind==='database'&&observation.connection?.status!=='connected'){this.registry.detach(id);throw Error('数据库只读连接未成功：'+(observation.detail||observation.connection?.status||'unknown'));}}else this.registry.detach(id);return;}
  if(kind==='skill'||kind==='agent'){if(load)this.studio.reveal([id]);else this.store.put('studio_hidden',[...new Set([...this.studio.hidden(),id])]);return;}
  if(!load){this.studio.remove({id});return;}
  if(kind==='tool'&&id==='tool.text-inspector'){this.store.put('studio_text_visible',true);return;}
  if(kind==='tool'&&['tool.scene-notes','tool.data-sandbox'].includes(id)){this.studio.reveal([id]);return;}
  this.store.put('studio_items',[...this.studio.items().filter(x=>x.id!==id),{...v,category:v.category||row.scopes[0]}]);this.studio.reveal([id]);
 }
}
module.exports={Loading};
