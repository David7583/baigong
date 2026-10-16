// ============================================================
// 文件名: workbench_tasks_v0002.cjs
// 中文名: 任务目录、用户控制与执行交接
// 版本号: v0002
// 主层级: action
// 层级: workbench / tasks
// 脚本定位: 任务目录、用户控制与执行交接
// 职责说明: 管理显式任务的输入、状态和结果
// 本脚本做什么: 任务目录、用户控制与执行交接
// 本脚本不做什么: 不绕过总控许可，不执行模型生成的命令
// 制度边界声明: 数据保留；失败如实记录；进程隐藏启动
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_tasks_v0002
// family: workbench_tasks
// role: managed_task_component
// version: v0002
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_tasks_v0002.cjs
// input: versioned JSON requests
// output: factual state and versioned results
// depends_on: existing Workbench and AI controller contracts
// used_by: workbench_tasks
// ============================================================
// 

'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {assert}=require('./workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_tasks_v0002',SCRIPT_VERSION='v0002';
const hash=x=>crypto.createHash('sha256').update(x).digest('hex');
const read=f=>JSON.parse(fs.readFileSync(f,'utf8'));
const write=(f,v)=>{fs.mkdirSync(path.dirname(f),{recursive:true});fs.writeFileSync(f+'.tmp',JSON.stringify(v,null,2));for(let attempt=0;;attempt++){try{fs.renameSync(f+'.tmp',f);break;}catch(error){if(!['EPERM','EACCES','EBUSY'].includes(error.code)||attempt>=30)throw error;Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,20);}}};
const live=pid=>{try{if(!Number.isInteger(pid)||pid<=0)return false;process.kill(pid,0);return true;}catch{return false;}};
const RUNNING=['starting','planning','running','synthesizing'];
class Tasks{
 constructor(root,store,inventory,software,governance){Object.assign(this,{root:path.resolve(root),store,inventory,software,governance});this.base=path.join(this.root,'runtime/workbench_tasks');fs.mkdirSync(this.base,{recursive:true});this.pending=false;this.config=JSON.parse(fs.readFileSync(path.join(__dirname,'../config/workbench_ai_inventory_v0001.json'),'utf8')).controller_config;}
 task(id){assert(/^task_[a-f0-9]{32}$/.test(id),'TASK_ID','任务 ID 无效');const file=path.join(this.base,id,'task.json');assert(fs.existsSync(file),'TASK_MISSING','任务不存在');return read(file);}
 state(task){if(!task.active_run)return null;const folder=path.join(this.base,task.id,'attempts',task.active_run),state=read(path.join(folder,'state.json'));assert(state.run_id===task.active_run,'TASK_STATE','运行身份不一致');const heartbeat=fs.existsSync(path.join(folder,'heartbeat.json'))?read(path.join(folder,'heartbeat.json')):null;
  if(RUNNING.includes(state.status)&&Date.now()-(heartbeat?.at||Date.parse(state.updated_at))>20000&&!live(heartbeat?.pid)){state.status='interrupted';state.error='执行进程已退出或启动失败；请检查调用记录后显式重试，未自动重放';write(path.join(folder,'state.json'),state);this.store.event('task.interrupted',{task_id:task.id,run_id:state.run_id});}
  const command=fs.existsSync(path.join(folder,'control.json'))?read(path.join(folder,'control.json')).operation:null;return {...state,...(RUNNING.includes(state.status)&&command?{display_status:command==='pause'?'pausing':'cancelling'}:{}),process_observed:live(heartbeat?.pid)};
 }
 list(){const items=fs.readdirSync(this.base,{withFileTypes:true}).filter(x=>x.isDirectory()&&/^task_[a-f0-9]{32}$/.test(x.name)).map(x=>this.task(x.name)).map(task=>{const state=this.state(task);return {id:task.id,title:task.title,goal:task.goal.slice(0,200),selection:task.selection,created_at:task.created_at,updated_at:state?.updated_at||task.created_at,status:state?.display_status||state?.status||'draft',completed_steps:state?.steps?.filter(s=>s.status==='completed').length||0,total_steps:state?.steps?.length||0,attempts:task.attempts.length};}).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));this.store.put('task_catalog',items);return {schema_version:'workbench.tasks.v1',items,scope:'真实文件与数据任务；代码执行逐次审批'};}
 detail(id){const task=this.task(id),state=this.state(task);let events=[];if(state){const file=path.join(this.base,id,'attempts',state.run_id,'events.jsonl');if(fs.existsSync(file))events=fs.readFileSync(file,'utf8').trim().split('\n').filter(Boolean).slice(-150).map(x=>JSON.parse(x));}return {task,state,events,artifact_directory:path.join(this.base,id,'attempts',task.active_run,'artifacts'),history:task.attempts.map(run=>({run_id:run,...read(path.join(this.base,id,'attempts',run,'state.json'))}))};}
 async executors(){const control=await this.inventory.snapshot(),items=[];for(const p of control.providers)for(const model of p.models){if(!(model.capabilities||[]).some(c=>['completion','chat','text_generation'].includes(c)))continue;items.push({id:p.provider+':'+model.model,provider:p.provider,model:model.model,name:model.display_name||model.model,profiles:p.profiles||['default'],kind:p.provider==='local_agents'?'agent':'ai',available:p.provider!=='local_agents'||model.available===true,status:model.message||'目录已登记；实际可用性由调用结果确认'});}
  const app=control.applications.find(a=>a.app_id==='workbench_tasks');return {items,unavailable_agents:(this.software.snapshot().agents||[]).map(x=>({name:x.name,status:'本机检测记录；需要受控执行适配，当前不可直接派发'})),application:app?{registered:true,allowed:!!app.allowed,retired:!!app.retired}: {registered:false,allowed:false},global_enabled:control.enabled,scope:'任务工作目录内自动执行；Python 代码须桌面用户单独确认。'};
 }
 async controller(operation,input={}){const id='task_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.base,'administration',id);fs.mkdirSync(folder,{recursive:true});const request=path.join(folder,'request.json'),response=path.join(folder,'response.json'),raw=JSON.stringify({schema_version:'workbench.task.ai.v1',run_id:id,producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},config:this.config,operation,...input});fs.writeFileSync(request,raw);
  const code=await new Promise((resolve,reject)=>{const child=spawn(require('./release_paths_v0001.cjs').python(this.root),['-B',path.join(this.root,'scripts/action/infrastructures/action_ai_controller_v0005.py'),'--root',this.root,'--task-request',request,'--response',response],{windowsHide:true,stdio:'ignore'});child.once('error',reject);child.once('close',resolve);});assert(fs.existsSync(response),'TASK_CONTROLLER','总控没有返回结果');const value=read(response);assert(value.schema_version==='workbench.task.ai.v1'&&value.run_id===id&&value.source_sha256===hash(raw)&&value.producer?.script==='action_ai_controller_v0005'&&value.producer.version==='v0005','TASK_CONTRACT','总控结果身份不一致');assert(code===0&&value.status==='completed','TASK_PERMISSION',value.error||'任务应用设置失败');await this.inventory.snapshot(true);this.store.event('task.application.'+operation,{...input,at:new Date().toISOString()});return value.result;
 }
 async rules(){await this.governance.ensure();const observe=()=>{const folder=path.join(this.root,'governance/rules'),files=fs.readdirSync(folder).filter(x=>x.endsWith('.json')).sort().map(name=>[name,hash(fs.readFileSync(path.join(folder,name)))]);files.push(['../levels.json',hash(fs.readFileSync(path.join(this.root,'governance/levels.json')))]);return hash(JSON.stringify(files));};const before=observe();const rules=(await this.governance.operation('list',{scope:'tasks'})).items.filter(x=>x.state==='active');for(const rule of rules)assert(!rule.requires_gate||rule.id==='gov.001','TASK_GOVERNANCE','任务执行器尚不能校验此强制规范：'+rule.id+'。需先配置对应校验器，不能自动绕过。');const full=[];for(const rule of rules)full.push(await this.governance.operation('get',{id:rule.id}));const fingerprint=observe();assert(fingerprint===before,'TASK_GOVERNANCE_CHANGED','规范在准入检查中发生变化，请重新提交');const text=JSON.stringify(full);assert(text.length<=80000,'TASK_GOVERNANCE_BUDGET','适用规范超过当前任务上下文预算，请先整理适用范围');return {text,hash:fingerprint};}
 lease(folder){const lock=path.join(folder,'worker.lock');fs.mkdirSync(folder,{recursive:true});if(fs.existsSync(lock)){const value=read(lock);assert(!live(value.pid)&&Date.now()-value.at>20000,'TASK_BUSY','本次尝试的执行进程尚未退出');fs.unlinkSync(lock);}return lock;}
 async start(input,retry=false){assert(!this.pending,'TASK_BUSY','正在处理另一项任务操作');this.pending=true;try{
  const requestId=String(input.request_id||'');assert(/^[a-zA-Z0-9_-]{8,100}$/.test(requestId),'TASK_REQUEST_ID','需要唯一提交标识');const requestHash=hash(JSON.stringify(input)),previous=this.store.get('task_request:'+requestId);if(previous){assert(previous.hash===requestHash,'TASK_REQUEST_CONFLICT','提交标识已用于不同任务');return this.detail(previous.id);}
  const prior=retry?this.task(input.id):null,goal=prior?.goal||String(input.goal||'').trim(),materials=prior?.materials||String(input.materials||''),selection=prior?.selection||input.selection,max_steps=prior?.max_steps??input.max_steps??4,max_output_tokens=prior?.max_output_tokens??input.max_output_tokens??2048;
  assert(goal.length>0&&goal.length<=12000&&materials.length<=40000,'TASK_INPUT','任务描述或材料长度无效');assert(Number.isInteger(max_steps)&&max_steps>=1&&max_steps<=8&&Number.isInteger(max_output_tokens)&&max_output_tokens>=256&&max_output_tokens<=8192,'TASK_BUDGET','步骤或输出预算无效');
  const catalog=await this.executors(),chosen=catalog.items.find(x=>x.provider===selection?.provider&&x.model===selection?.model&&x.profiles.includes(selection.profile||'default'));assert(chosen?.available,'TASK_EXECUTOR','请选择已接入且可用的 AI 或 Agent');assert(catalog.application.allowed&&!catalog.application.retired&&catalog.global_enabled===true,'TASK_PERMISSION','任务应用或总控许可尚未开启；请在任务设置和 AI 总控中确认');
  if(prior)assert(['failed','blocked','interrupted','cancelled','completed'].includes(this.state(prior)?.status),'TASK_RETRY','请先等待当前尝试结束；暂停的任务请续跑');
  const rules=await this.rules(),id=prior?.id||'task_'+crypto.randomUUID().replaceAll('-',''),run_id='run_'+crypto.randomUUID().replaceAll('-',''),folder=path.join(this.base,id,'attempts',run_id),at=new Date().toISOString(),lock=this.lease(folder);
  fs.writeFileSync(lock,JSON.stringify({run_id,pid:null,at:Date.now()}),{flag:'wx'});
  try{fs.mkdirSync(folder,{recursive:true});const task=prior||{schema_version:'workbench.task.v1',id,title:goal.split('\n')[0].slice(0,70),goal,materials,selection:{provider:chosen.provider,model:chosen.model,profile:selection.profile||'default'},max_steps,max_output_tokens,created_at:at,attempts:[]};
   const req={schema_version:'workbench.task.run.v2',producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},task_id:id,run_id,root:this.root,goal,materials,selection:task.selection,max_steps,max_output_tokens,controller_config:this.config,governance_text:rules.text,governance_hash:rules.hash,approval:{actor:'desktop_user',at,scope:'workspace_actions_with_explicit_code_approval'}};write(path.join(folder,'request.json'),req);write(path.join(folder,'state.json'),{schema_version:'workbench.task.state.v1',task_id:id,run_id,source_sha256:hash(fs.readFileSync(path.join(folder,'request.json'))),status:'starting',steps:[],calls:[],created_at:at,updated_at:at});task.active_run=run_id;task.attempts.push(run_id);write(path.join(this.base,id,'task.json'),task);this.store.put('task_request:'+requestId,{id,hash:requestHash,run_id});await this.governance.request('record_operation',{operation:'task.start',target:id,status:'user_confirmed'});await this.launch(folder,run_id);this.store.event('task.started',{task_id:id,run_id});return this.detail(id);
  }catch(error){try{if(read(lock).run_id===run_id)fs.unlinkSync(lock);}catch{}throw error;}
 }finally{this.pending=false;}}
 async launch(folder,run_id){const log=fs.openSync(path.join(folder,'process.log'),'a');try{await new Promise((resolve,reject)=>{const child=spawn(path.join(this.root,'.tools/node/node.exe'),[path.join(this.root,'scripts/action/development/scripts/workbench_tasks/workbench_task_worker_v0002.cjs'),path.join(folder,'request.json')],{cwd:this.root,windowsHide:true,detached:true,stdio:['ignore',log,log]});child.once('error',reject);child.once('spawn',()=>{write(path.join(folder,'worker.lock'),{run_id,pid:child.pid,at:Date.now()});child.unref();resolve();});});}finally{fs.closeSync(log);}}
 async control(operation,input){const task=this.task(input.id),state=this.state(task),folder=path.join(this.base,task.id,'attempts',task.active_run);if(operation==='resume'){assert(!this.pending,'TASK_BUSY','正在处理任务操作');this.pending=true;try{assert(state.status==='paused','TASK_RESUME','只有已保存检查点的暂停任务可以续跑');const rules=await this.rules(),req=read(path.join(folder,'request.json'));assert(req.schema_version==='workbench.task.run.v2','TASK_LEGACY','旧版暂停任务保留原记录；请取消后建立新尝试升级到当前执行器');assert(req.governance_hash===rules.hash,'TASK_GOVERNANCE_CHANGED','规范已变化，请先取消本次尝试，再建立新尝试');const lock=this.lease(folder);fs.writeFileSync(lock,JSON.stringify({run_id:task.active_run,pid:null,at:Date.now()}),{flag:'wx'});write(path.join(folder,'control.json'),{operation:null,at:new Date().toISOString()});state.status='starting';state.updated_at=new Date().toISOString();write(path.join(folder,'state.json'),state);await this.launch(folder,task.active_run);}finally{this.pending=false;}}else{assert(['pause','cancel'].includes(operation),'TASK_OPERATION','操作无效');assert(RUNNING.includes(state.status)||(operation==='cancel'&&['paused','awaiting_approval','awaiting_input'].includes(state.status)),'TASK_CONTROL','任务已经结束');write(path.join(folder,'control.json'),{operation,actor:'desktop_user',at:new Date().toISOString()});if(['paused','awaiting_approval','awaiting_input'].includes(state.status)){state.status='cancelled';write(path.join(folder,'state.json'),state);}}
  this.store.event('task.'+operation,{task_id:task.id,run_id:task.active_run});return this.detail(task.id);
 }
 async respond(input){
  assert(!this.pending,'TASK_BUSY','正在处理另一项操作');this.pending=true;try{
   const task=this.task(input.id),state=this.state(task),folder=path.join(this.base,task.id,'attempts',task.active_run),pending=state.pending;
   assert(['awaiting_approval','awaiting_input'].includes(state.status)&&pending,'TASK_DECISION','任务当前没有等待中的用户决定');
   assert(input.step_id===pending.step_id&&input.action_sha256===pending.action_sha256,'TASK_DECISION_STALE','操作已变化，请刷新后确认');
   const rules=await this.rules(),req=read(path.join(folder,'request.json'));assert(req.governance_hash===rules.hash,'TASK_GOVERNANCE_CHANGED','规范已变化，请取消并建立新尝试');
   const decision={actor:'desktop_user',at:new Date().toISOString(),step_id:pending.step_id,action_sha256:pending.action_sha256};
   if(pending.kind==='approval'){assert(typeof input.approved==='boolean','TASK_APPROVAL','必须明确同意或拒绝');decision.approved=input.approved;}else{assert(typeof input.text==='string'&&input.text.trim()&&input.text.length<=12000,'TASK_INPUT','请提供有效的补充说明');decision.text=input.text;}
   const lock=this.lease(folder);fs.writeFileSync(lock,JSON.stringify({run_id:task.active_run,pid:null,at:Date.now()}),{flag:'wx'});
   write(path.join(folder,'decision.json'),decision);await this.governance.request('record_operation',{operation:'task.'+(pending.kind==='approval'?'code_approval':'user_input'),target:task.id,status:decision.approved===false?'denied':'user_confirmed',detail:decision});this.store.event('task.decision',{task_id:task.id,run_id:task.active_run,...decision});
   state.status='starting';state.updated_at=new Date().toISOString();write(path.join(folder,'state.json'),state);await this.launch(folder,task.active_run);return this.detail(task.id);
  }finally{this.pending=false;}
 }
 async operation(op,input){if(op==='respond')return this.respond(input);if(op==='submit')return this.start(input);if(op==='retry')return this.start(input,true);if(['pause','resume','cancel'].includes(op))return this.control(op,input);if(op==='provision')return this.controller('provision');if(op==='permission'){assert(typeof input.allowed==='boolean','TASK_PERMISSION','许可必须为布尔值');return this.controller('permission',{allowed:input.allowed});}throw Error('Unknown task operation');}
}
module.exports={Tasks};
