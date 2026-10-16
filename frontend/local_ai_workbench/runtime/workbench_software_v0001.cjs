// ============================================================
// 文件名: workbench_software_v0001.cjs
// 中文名: 本机软件登记、显式打开与 Blender 作业协调
// 版本号: v0001
// 主层级: action
// 层级: workbench / integration
// 脚本定位: 本机软件登记、显式打开与 Blender 作业协调
// 职责说明: 通过已有索引和显式 JSON 契约提供真实信息
// 本脚本做什么: 本机软件登记、显式打开与 Blender 作业协调
// 本脚本不做什么: 不自动授权模型、不安装软件、不卸载系统应用
// 制度边界声明: 只在明确操作后执行；用户数据保留；错误可追溯
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_software_v0001
// family: workbench_software
// role: managed_integration
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_software_v0001.cjs
// input: authenticated explicit operation or JSON request
// output: factual inventory or versioned result
// depends_on: standard library and existing managed APIs
// used_by: workbench_server_v0011
// ============================================================

'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {assert}=require('./workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_software_v0001',SCRIPT_FAMILY='workbench_software',SCRIPT_VERSION='v0001',DEFAULT_ENCODING='utf8';
const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const alive=pid=>{try{process.kill(pid,0);return true;}catch{return false;}};
class Software{
 constructor(root,store,cleanup){Object.assign(this,{root,store,cleanup});this.pending=null;this.closed=false;}
 snapshot(){const scan=this.store.get('software_inventory',{items:[],agents:[],errors:[],observed_at:null}),loaded=this.store.get('loaded_software',[]);
  return {...scan,items:scan.items.map(item=>{const own=loaded.find(x=>x.id===item.id);return {...item,loaded:!!own,missing:item.path?!fs.existsSync(item.path):false};}),loaded,semantics:'加载/卸载仅管理工作台接入，不安装或删除系统软件。'};}
 async scan(){if(this.pending)return this.pending;this.pending=this.discover().finally(()=>this.pending=null);return this.pending;}
 async discover(){
  const id='software_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.root,'runtime/intermediate_tasks',id);fs.mkdirSync(folder,{recursive:true});
  const request=path.join(folder,'request.json'),response=path.join(folder,'response.json'),raw=JSON.stringify({schema_version:'workbench.software-inventory.v1',run_id:id,operation:'scan',producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION}});
  fs.writeFileSync(request,raw);const code=await new Promise((resolve,reject)=>{const child=spawn(require('./release_paths_v0001.cjs').python(this.root),['-B',path.join(this.root,'scripts/action/infrastructures/local_software_inventory_v0001.py'),'--request',request,'--response',response],{windowsHide:true,stdio:'ignore'});const timer=setTimeout(()=>{child.kill();reject(Error('扫描超时'));},65000);child.once('error',e=>{clearTimeout(timer);reject(e);});child.once('close',c=>{clearTimeout(timer);resolve(c);});});
  assert(fs.existsSync(response)&&fs.statSync(response).size<16*1024*1024,'SOFTWARE_RESPONSE','软件扫描没有返回结果');const output=JSON.parse(fs.readFileSync(response,'utf8'));
  assert(output.schema_version==='workbench.software-inventory.v1'&&output.run_id===id&&output.producer?.script==='local_software_inventory_v0001'&&output.source_sha256===hash(request),'SOFTWARE_CONTRACT','扫描响应身份无效');
  assert(code===0&&output.status==='completed','SOFTWARE_SCAN',output.error||'扫描失败');
  const manual=this.store.get('software_manual',[]),result=output.result;
  result.items=[...new Map([...result.items,...manual].map(x=>[x.id,x])).values()];
  // Keep previously loaded items visible even when a shortcut or installed entry disappears.
  for(const item of this.store.get('loaded_software',[]))if(!result.items.some(x=>x.id===item.id))result.items.push({...item,discovery_missing:true});
  this.store.put('software_inventory',result);this.store.event('software.scanned',{run_id:id,items:result.items.length,agents:result.agents.length,errors:result.errors});await this.cleanup(folder);return this.snapshot();
 }
 register(input){
  assert(typeof input.path==='string'&&path.isAbsolute(input.path)&&path.extname(input.path).toLowerCase()==='.exe','SOFTWARE_PATH','请选择本机 EXE 入口');
  const file=fs.realpathSync(input.path);assert(fs.statSync(file).isFile(),'SOFTWARE_PATH','入口不是文件');
  assert(!/^(cmd|powershell|pwsh|pythonw?|node|wscript|cscript|mshta|rundll32|regsvr32|explorer|msiexec).exe$/i.test(path.basename(file))&&!/unins|uninstall|setup|update/i.test(path.basename(file)),'SOFTWARE_EXECUTABLE','命令解释器和安装/卸载程序不作为软件操作入口');
  const id='software.'+crypto.createHash('sha256').update(file.toLowerCase()).digest('hex').slice(0,24);
  const item={id,name:input.name||path.basename(file,'.exe'),path:file,launchable:true,adapter:path.basename(file).toLowerCase()==='blender.exe'?'blender':'native',source:'用户登记',sources:['用户登记'],version:'',status:'installed_entry'};
  const items=this.store.get('software_manual',[]);this.store.put('software_manual',[...items.filter(x=>x.id!==id),item]);const snap=this.snapshot();this.store.put('software_inventory',{...snap,items:[...snap.items.filter(x=>x.id!==id),item]});this.store.event('software.registered',{id,path:file});return {item};
 }
 load(input){
  const item=this.snapshot().items.find(x=>x.id===input.id);assert(item,'SOFTWARE_MISSING','请先扫描或登记软件');
  const all=this.store.get('loaded_software',[]);
  if(input.loaded===false){this.store.put('loaded_software',all.filter(x=>x.id!==input.id));this.store.put('studio_items',this.store.get('studio_items',[]).filter(x=>x.id!==input.id));this.store.event('software.unloaded',{id:input.id,preserved_files:true});return {loaded:false};}
  assert(item.launchable&&item.path&&fs.existsSync(item.path),'SOFTWARE_UNRESOLVED','未找到可靠的启动入口，请使用手动登记');
  const value={...item,sha256:hash(item.path),loaded_at:new Date().toISOString()};
  this.store.put('loaded_software',[...all.filter(x=>x.id!==item.id),value]);this.store.put('studio_items',[...this.store.get('studio_items',[]).filter(x=>x.id!==item.id),{...value,category:'software',native:true,managed_software:true,description:'本机软件入口 · '+value.adapter}]);this.store.event('software.loaded',{id:item.id,path:item.path,sha256:value.sha256});return {loaded:true,item:value};
 }
 resolve(id){
  const item=this.store.get('loaded_software',[]).find(x=>x.id===id);assert(item?.launchable,'SOFTWARE_NOT_LOADED','请先加载软件');
  assert(fs.existsSync(item.path),'SOFTWARE_MISSING','软件文件已被移动或删除');
  assert(hash(item.path)===item.sha256,'SOFTWARE_CHANGED','软件入口已更新，请重新加载以登记新版本');
  return item;
 }
 blendSource(value){assert(typeof value==='string'&&path.isAbsolute(value)&&path.extname(value).toLowerCase()==='.blend','BLEND_PATH','请选择现有 .blend 文件');const file=fs.realpathSync(value);assert(fs.statSync(file).isFile(),'BLEND_PATH','场景不是文件');return file;}
 async open(input){
  const item=this.resolve(input.id);const args=item.adapter==='blender'?['--disable-autoexec']:[];if(input.source){assert(item.adapter==='blender','SOFTWARE_ARGUMENT','只有 Blender 接受场景文件');args.push(this.blendSource(input.source));}
  const child=spawn(item.path,args,{cwd:path.dirname(item.path),windowsHide:true,detached:true,stdio:'ignore'});
  await new Promise((resolve,reject)=>{child.once('error',reject);child.once('spawn',resolve);});child.unref();
  this.store.event('software.opened',{id:item.id,pid:child.pid,source:input.source||null});return {status:'launch_requested',pid:child.pid,native_window:true};
 }
 jobs(){return this.store.get('blender_jobs',[]).map(job=>this.reconcile(job));}
 save(job){if(this.closed)return;const jobs=this.store.get('blender_jobs',[]);this.store.put('blender_jobs',[...jobs.filter(x=>x.id!==job.id),job]);}
 reconcile(job){
  if(job.status!=='running')return job;
  const response=path.join(job.directory,'response.json');
  if(fs.existsSync(response)){try{assert(fs.statSync(response).size<8*1024*1024,'BLENDER_SIZE','结果过大');const value=JSON.parse(fs.readFileSync(response,'utf8'));assert(value.schema_version==='workbench.blender-job.v1'&&value.run_id===job.id&&value.producer?.script==='blender_workspace_v0001'&&value.producer.version==='v0001'&&value.source_sha256===job.request_sha256,'BLENDER_CONTRACT','Blender 结果身份不一致');for(const file of [value.result?.blend,value.result?.render].filter(Boolean))assert(path.dirname(path.resolve(file))===path.resolve(job.directory),'BLENDER_OUTPUT','结果路径不属于此作业');job={...job,status:value.status,result:value.result||null,error:value.error||null,finished_at:new Date().toISOString()};}catch(error){job={...job,status:'failed',error:error.message};}this.save(job);}
  else if(job.pid&&!alive(job.pid)){job={...job,status:'interrupted',error:'进程已退出且未提交结果，请查看日志后重新运行；不自动恢复'};this.save(job);}
  return job;
 }
 job(id){const job=this.jobs().find(x=>x.id===id);assert(job,'BLENDER_JOB','作业不存在');return job;}
 async run(input){
  const item=this.resolve(input.id);assert(item.adapter==='blender','BLENDER_ADAPTER','此软件尚无工作台操作面');
  assert(['demo','inspect','render','transform'].includes(input.operation),'BLENDER_OPERATION','操作不受支持');
  assert(!this.jobs().some(x=>x.status==='running'),'BLENDER_BUSY','已有 Blender 后台作业，请等待完成');
  const source=input.operation==='demo'?null:this.blendSource(input.source);
  const id='blender_'+crypto.randomUUID().replaceAll('-',''),directory=path.join(this.root,'runtime/software/blender',id);fs.mkdirSync(directory,{recursive:true});
  const request=path.join(directory,'request.json'),response=path.join(directory,'response.json');
  const raw=JSON.stringify({schema_version:'workbench.blender-job.v1',run_id:id,producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},operation:input.operation,source,source_file_sha256:source?hash(source):null,object:input.object,location:input.location,rotation_degrees:input.rotation_degrees,scale:input.scale});
  fs.writeFileSync(request,raw);const fd=fs.openSync(path.join(directory,'process.log'),'a');
  let child;try{child=spawn(item.path,['--background','--factory-startup','--disable-autoexec','--python-exit-code','2','--python',path.join(this.root,'scripts/action/infrastructures/blender_workspace_v0001.py'),'--',request,response],{cwd:directory,windowsHide:true,detached:true,stdio:['ignore',fd,fd]});await new Promise((resolve,reject)=>{child.once('error',reject);child.once('spawn',resolve);});}finally{fs.closeSync(fd);}
  child.unref();const job={id,software_id:item.id,operation:input.operation,source,directory,request_sha256:hash(request),pid:child.pid,status:'running',started_at:new Date().toISOString()};
  this.save(job);this.store.event('blender.started',{id,software_id:item.id,operation:input.operation,pid:child.pid});
  child.once('exit',(code)=>{if(!this.closed){const final=this.reconcile(job);this.store.event('blender.exited',{id,code,status:final.status});}});return {job};
 }
 async close(){if(this.pending)await this.pending.catch(()=>{});this.closed=true;}
}
module.exports={Software};
