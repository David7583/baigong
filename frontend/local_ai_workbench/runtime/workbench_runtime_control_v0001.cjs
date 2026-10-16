// ============================================================
// 文件名: workbench_runtime_control_v0001.cjs
// 中文名: 工作台运行观察与向量路径选择
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / runtime / operations
// 脚本定位: 工作台显式管理入口与独立服务适配层
//
// 职责说明:
// - 展示真实服务状态，选择新旧向量路径，执行有限后台任务
//
// 本脚本做什么:
// - 只读诊断、存储预览，按用户操作启动独立回环服务
//
// 本脚本不做什么:
// - 不修改旧向量脚本、配置或索引，不删除资料，不自动授权 AI
//
// 制度边界声明:
// - 本地进程使用无窗口启动；私有身份不进入公共响应或日志
// - 切换只保存选择；暂停后在途计算可能完成，不自动回退路径
//
// 可更新: True
// ============================================================

// ============================================================
// ALIAS_META
// ============================================================
// alias: workbench_runtime_control_v0001
// family: workbench_runtime_control
// role: runtime_observation_and_vector_path_selection
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_runtime_control_v0001.cjs
// input:
//   - root, private workbench directory, explicit control request
// output:
//   - sanitized status and independent service commands
// depends_on:
//   - workbench_system_probe_v0001
//   - anchor_vector_service_v0001
// used_by:
//   - workbench_server_v0003
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {assert,localUrl}=require('./workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING='utf8',SCRIPT_FAMILY='workbench_runtime_control',SCRIPT_NAME='workbench_runtime_control_v0001',SCRIPT_VERSION='v0001';

class RuntimeControl {
 constructor(root,directory){this.root=path.resolve(root);this.directory=directory;this.python=require('./release_paths_v0001.cjs').python(root);this.starting=null;this.selectionFile=path.join(root,'runtime/local_ai_workbench/vector_selection_v0001.json');this.ready=path.join(root,'runtime/anchor_vectors_v0001/service_ready.json');}
 selection(){let selected='paragraph';if(fs.existsSync(this.selectionFile)){const value=JSON.parse(fs.readFileSync(this.selectionFile,'utf8'));assert(['paragraph','legacy'].includes(value.selected),'VECTOR_SELECTION','向量路径配置损坏');selected=value.selected;}return {selected,choices:[{id:'paragraph',name:'锚点原段落 · 新流水线'},{id:'legacy',name:'概念 / 实例 · 原流水线'}],switch_semantics:'只切换后续查询与显式任务；不迁移、删除或自动恢复旧后台'};}
 select(input){assert(['paragraph','legacy'].includes(input.selected),'VECTOR_SELECTION','请选择已配置的向量路径');fs.mkdirSync(path.dirname(this.selectionFile),{recursive:true});const temporary=this.selectionFile+'.tmp';fs.writeFileSync(temporary,JSON.stringify({schema_version:1,selected:input.selected,updated_at:new Date().toISOString()}));fs.renameSync(temporary,this.selectionFile);return this.selection();}
 async probe(operation='status'){assert(['status','storage'].includes(operation),'OPERATION','无效观察操作');if(!fs.existsSync(this.python))return {status:'unavailable',error_type:'ProjectPythonMissing'};const script=path.join(__dirname,'../adapters/workbench_system_probe_v0001.py');return new Promise((resolve,reject)=>{const child=spawn(this.python,['-B',script,'--root',this.root,'--operation',operation],{cwd:this.root,windowsHide:true,stdio:['ignore','pipe','pipe']});let output='';const timer=setTimeout(()=>{child.kill();reject(new Error('只读诊断超时'));},35000);child.stdout.on('data',bytes=>{output+=bytes;if(output.length>1048576)child.kill();});child.once('error',error=>{clearTimeout(timer);reject(error);});child.once('exit',code=>{clearTimeout(timer);if(code!==0)return reject(new Error('只读诊断未完成'));try{resolve(JSON.parse(output));}catch{reject(new Error('诊断返回格式错误'));}});});}
 async paragraphState(){
  if(!fs.existsSync(this.ready))return {status:'offline'};
  const state=JSON.parse(fs.readFileSync(this.ready,'utf8'));
  assert(state.root===this.root&&Number.isInteger(state.pid)&&state.pid>0,'VECTOR_OWNER','服务身份记录无效');localUrl(state.url);
  try{const result=await fetch(state.url+'status',{signal:AbortSignal.timeout(1500)}).then(r=>r.json());assert(result.instance===state.instance&&result.root===this.root&&result.pid===state.pid,'VECTOR_OWNER','服务身份不一致');return {...result,url:state.url,gpu_released:false};}
  catch(error){if(error.code==='VECTOR_OWNER')throw error;let alive=true;try{process.kill(state.pid,0);}catch(probe){if(probe.code==='ESRCH')alive=false;}
   if(alive)return {status:state.lifecycle==='pausing'?'pausing':'unavailable',pid:state.pid,gpu_released:false,detail:'服务接口不可达，计算进程尚未确认退出'};
   return {status:'offline',pid:state.pid,gpu_released:true,pause_completed:state.lifecycle==='pausing'};
  }
 }
 legacyConfig(){const file=path.join(this.root,'config/action/config/concurrent_pipeline_v0001.json');if(!fs.existsSync(file))return null;const config=JSON.parse(fs.readFileSync(file,'utf8'));assert(Number.isInteger(config.service_port)&&config.service_port>0&&config.service_port<65536,'LEGACY_CONFIG','原路径端口无效');return {...config,url:'http://127.0.0.1:'+config.service_port+'/'};}
 async legacyState(){const config=this.legacyConfig();if(!config)return {status:'not_configured'};try{const result=await fetch(config.url+'status',{signal:AbortSignal.timeout(1200)}).then(r=>r.json());assert(typeof result.pid==='number'&&result.version,'LEGACY_STATUS','原服务状态无效');return {...result,url:config.url};}catch{return {status:'offline'};}}
 async status(){const [diagnostics,paragraph,legacy]=await Promise.all([this.probe(),this.paragraphState(),this.legacyState()]);return {selection:this.selection(),diagnostics,services:{paragraph,legacy},observed_at:new Date().toISOString()};}
 async cleanup(input={}){
  assert(['preview','execute'].includes(input.operation),'CLEANUP_OPERATION','请选择预览或清理');
  assert(fs.existsSync(this.python),'CLEANUP_PYTHON','本地区 Python 不可用');
  const runId='cleanup_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.root,'runtime/intermediate_tasks',runId);
  fs.mkdirSync(folder,{recursive:true});
  const request=path.join(folder,'request.json'),response=path.join(folder,'response.json'),manifest=path.join(folder,'task.json'),active=path.join(folder,'task.active');
  fs.writeFileSync(active,String(process.pid),{flag:'wx'});
  const state={schema_version:'intermediate-task.v1',run_id:runId,status:'running',active:true,result_verified:false,recovery_required:true,consumers:[{id:SCRIPT_NAME,acknowledged:false}],intermediates:[]};
  const publish=(file,value)=>{fs.writeFileSync(file+'.tmp',JSON.stringify(value));fs.renameSync(file+'.tmp',file);};publish(manifest,state);
  const installedSource=path.join(this.root,'scripts/action/infrastructures');
  const source=fs.existsSync(path.join(installedSource,'clean_completed_handoffs_v0001.py'))?installedSource:path.resolve(__dirname,'../../../scripts/action/infrastructures');
  const run=(entry,args)=>new Promise((resolve,reject)=>{const child=spawn(this.python,['-B',path.join(source,entry),...args],{cwd:this.root,windowsHide:true,stdio:['ignore','pipe','ignore']});let output='';child.stdout.on('data',bytes=>{if(output.length<65536)output+=bytes;});child.once('error',reject);child.once('close',code=>{if(code===0)resolve();else{let detail='清理任务失败，已保留恢复文件';try{detail=JSON.parse(output).detail||detail;}catch{}reject(new Error(detail));}});});
  let result;
  try{
   publish(request,{schema_version:'completed-handoff-cleanup.v1',run_id:runId,producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},root:this.root,operation:input.operation,tasks:input.tasks||[]});
   await run('clean_completed_handoffs_v0001.py',['--request',request,'--response',response]);
   const envelope=JSON.parse(fs.readFileSync(response,'utf8'));
   assert(envelope.schema_version==='completed-handoff-cleanup.v1'&&envelope.run_id===runId&&envelope.status==='completed'&&envelope.producer?.script==='clean_completed_handoffs_v0001'&&envelope.producer?.version==='v0001','CLEANUP_CONTRACT','清理返回身份不一致');
   result=envelope.result;
   Object.assign(state,{status:'completed',active:false,result_verified:true,recovery_required:false,consumers:[{id:SCRIPT_NAME,acknowledged:true}],intermediates:fs.readdirSync(folder).filter(x=>x.endsWith('.json')&&x!=='task.json').map(name=>{const file=path.join(folder,name);assert(fs.lstatSync(file).isFile()&&!fs.lstatSync(file).isSymbolicLink(),'CLEANUP_FILE','交接文件归属无效');return {path:path.relative(this.root,file).replaceAll('\\','/'),sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),regenerable:true,protected:false};})});publish(manifest,state);
  }catch(error){Object.assign(state,{status:'failed',active:false,recovery_required:true});publish(manifest,state);throw error;}
  finally{fs.unlinkSync(active);}
  await run('intermediate_cleanup_v0001.py',['--root',this.root,'--manifest',path.relative(this.root,manifest)]);
  return result;
 }
 async ensureParagraph(){if(this.starting)return this.starting;const operation=async()=>{if((await this.paragraphState()).status!=='offline')return JSON.parse(fs.readFileSync(this.ready,'utf8'));assert(fs.existsSync(this.python),'VECTOR_PYTHON','本地区 Python 不可用');const instance=crypto.randomUUID(),pythonw=require('./release_paths_v0001.cjs').python(this.root),entry=path.join(__dirname,'../tools/anchor_vectors/anchor_vector_service_v0001.py');fs.mkdirSync(path.dirname(this.ready),{recursive:true});const log=fs.openSync(path.join(path.dirname(this.ready),'service.log'),'a');const child=spawn(fs.existsSync(pythonw)?pythonw:this.python,['-B',entry,'--root',this.root,'--ready-file',this.ready,'--instance',instance],{cwd:this.root,detached:true,windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);let error;child.once('error',value=>error=value);child.unref();for(let n=0;n<300;n++){if(error||child.exitCode!==null)throw new Error('新向量服务启动失败，请检查 service.log');if(fs.existsSync(this.ready)){const state=JSON.parse(fs.readFileSync(this.ready,'utf8'));if(state.instance===instance&&state.pid===child.pid&&(await this.paragraphState()).status!=='offline')return state;}await new Promise(resolve=>setTimeout(resolve,100));}throw new Error('新向量服务启动超时');};this.starting=operation().finally(()=>this.starting=null);return this.starting;}
 async control(input){assert(['start','pause','retry_rejected','shutdown'].includes(input.operation),'VECTOR_OPERATION','无效向量操作');const selected=input.path||this.selection().selected;assert(['paragraph','legacy'].includes(selected),'VECTOR_SELECTION','无效向量路径');if(selected==='paragraph'){if(input.operation==='start'){const legacy=await this.legacyState();assert(legacy.status==='offline'||legacy.status==='not_configured','VECTOR_GPU_BUSY','原向量服务仍在运行；请先在原路径暂停并停止服务，释放显存后再启动新路径');}if(input.operation!=='start')assert(fs.existsSync(this.ready),'VECTOR_OFFLINE','新服务未运行');const state=input.operation==='start'?await this.ensureParagraph():JSON.parse(fs.readFileSync(this.ready,'utf8'));localUrl(state.url);assert((await this.paragraphState()).status!=='offline','VECTOR_OFFLINE','新服务未运行');const response=await fetch(state.url+'control',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+state.token},body:JSON.stringify({operation:input.operation,settings:input.settings||{}}),signal:AbortSignal.timeout(5000)});const result=await response.json();assert(response.ok,'VECTOR_CONTROL',result.detail||result.error_type||'向量操作失败');return result;}
 const config=this.legacyConfig();assert(config,'LEGACY_CONFIG','原路径没有配置');let live=await this.legacyState();assert(live.status!=='offline','LEGACY_OFFLINE','原服务未运行；请使用原流水线入口启动，旧路径保持原样');assert(input.operation!=='retry_rejected','LEGACY_OPERATION','此操作仅用于新路径');const tokenFile=path.resolve(this.root,config.state_dir,'service_token');assert(tokenFile.startsWith(this.root+path.sep),'LEGACY_TOKEN','原身份路径无效');const token=fs.readFileSync(tokenFile,'utf8').trim();const route=input.operation==='shutdown'?'shutdown':'vectors';const response=await fetch(config.url+route,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify(route==='vectors'?{enabled:input.operation==='start'}:{}),signal:AbortSignal.timeout(5000)});assert(response.ok,'LEGACY_CONTROL','原服务拒绝操作');return response.json();}
}
module.exports={RuntimeControl};
