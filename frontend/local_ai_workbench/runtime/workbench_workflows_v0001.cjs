// ============================================================
// 文件名: workbench_workflows_v0001.cjs
// 中文名: 通用节点编排与受管理接口协调
// 版本号: v0001
// 主层级: action
// 层级: workbench / platform
// 脚本定位: workflow_coordinator
// 职责说明:
// - 通用节点编排与受管理接口协调
// 本脚本做什么:
// - 复用现有契约与受管理接口，记录真实状态
// 本脚本不做什么:
// - 不改写业务事实，不自动授权、批准或调用模型
// 制度边界声明:
// - 仅管理工作台配置与派生展示，可回退
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_workflows_v0001
// family: workbench_workflows
// role: workflow_coordinator
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_workflows_v0001.cjs
// input:
//   - explicit versioned requests and existing metadata
// output:
//   - factual results and structured errors
// depends_on:
//   - existing workbench APIs and standard library
// used_by:
//   - workbench_platform
// ============================================================
const SCRIPT_NAME='workbench_workflows_v0001',SCRIPT_VERSION='v0001',SCRIPT_FAMILY='workbench_workflows',DEFAULT_ENCODING='utf8';

const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),{spawn}=require('node:child_process');
const {assert,identifier}=require('./workbench_contracts_v0001.cjs');
const NODES=[{"id":"standards","name":"查询适用规范","input":null,"output":"json","params":{"scope":"global"},"adapter":"governance.list","pure":false},{"id":"resources","name":"工作台资源目录","input":null,"output":"json","params":{},"adapter":"registry.list","pure":false},{"id":"text","name":"文本输入","input":null,"output":"string","params":{"text":""},"adapter":"transform","pure":true},{"id":"filter","name":"筛选记录","input":"json","output":"json","params":{"field":"name","contains":""},"adapter":"transform","pure":true},{"id":"template","name":"文本模板","input":"any","output":"string","params":{"template":"{{value}}"},"adapter":"transform","pure":true},{"id":"output","name":"结果输出","input":"any","output":"any","params":{},"adapter":"transform","pure":true},{"id":"workflow","name":"调用已保存工作流","input":null,"output":"json","params":{"workflow_id":""},"adapter":"workflow","pure":false}];
const hash=x=>crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');
class Workflows{
 constructor(root,store,registry,governance,capabilities){root=path.resolve(root);Object.assign(this,{root,store,registry,governance,capabilities});this.active=new Map();this.cacheLimit=100;const runs=this.store.get('workflow_runs',[]);let recovered=false;for(const run of runs)if(run.status==='running'){run.status='failed';run.interrupted=true;run.error='上次后端进程中断，未自动重放';recovered=true;}if(recovered)this.store.put('workflow_runs',runs);}
 catalog(){return {schema_version:'workbench.nodes.v1',items:NODES,unsupported:['AI/Agent：等待显式的受控调用契约','任意脚本：不允许通过参数执行代码','MCP：需要具体工具输入输出适配','任意SQL：当前仅使用已有只读浏览接口']};}
 list(){return {items:this.store.get('workflow_catalog',[]),runs:this.store.get('workflow_runs',[]).slice(-60).reverse()};}
 validate(graph){
  assert(graph?.schema_version==='workbench.workflow.v1','FLOW_SCHEMA','需要 workbench.workflow.v1');assert(typeof graph.id==='string'&&/^[a-z][a-z0-9_.-]{2,80}$/.test(graph.id),'FLOW_ID','工作流 ID 使用小写字母数字与 ._-');
  assert(typeof graph.name==='string'&&graph.name.trim()&&graph.name.length<160,'FLOW_NAME','工作流名称无效');
  assert(Array.isArray(graph.nodes)&&graph.nodes.length>0&&graph.nodes.length<=60&&Array.isArray(graph.edges)&&graph.edges.length<=120,'FLOW_LIMIT','节点或连线数量无效');
  assert(Buffer.byteLength(JSON.stringify(graph))<=200000,'FLOW_BUDGET','工作流定义超过预算');
  const ids=new Set();for(const n of graph.nodes){identifier(n.id);assert(!ids.has(n.id),'FLOW_DUPLICATE','节点 ID 重复');ids.add(n.id);assert(NODES.some(x=>x.id===n.type),'FLOW_NODE','节点类型没有执行适配');assert(n.params&&typeof n.params==='object'&&!Array.isArray(n.params),'FLOW_PARAMS','参数必须为对象');assert(Number.isFinite(n.x)&&Number.isFinite(n.y),'FLOW_POSITION','节点位置无效');}
  const incoming=new Map(graph.nodes.map(n=>[n.id,[]])),out=new Map(graph.nodes.map(n=>[n.id,[]]));
  for(const e of graph.edges){assert(ids.has(e.from)&&ids.has(e.to)&&e.from!==e.to,'FLOW_EDGE','连线端点无效');const from=NODES.find(x=>x.id===graph.nodes.find(n=>n.id===e.from).type),to=NODES.find(x=>x.id===graph.nodes.find(n=>n.id===e.to).type);assert(to.input&&(to.input==='any'||from.output==='any'||to.input===from.output),'FLOW_TYPES','端口类型不兼容');incoming.get(e.to).push(e.from);out.get(e.from).push(e.to);}
  for(const n of graph.nodes){const definition=NODES.find(x=>x.id===n.type);assert(incoming.get(n.id).length===(definition.input?1:0),'FLOW_INPUT','节点需要且只允许一个输入：'+n.id);}
  const queue=graph.nodes.filter(n=>!incoming.get(n.id).length).map(n=>n.id),order=[],counts=new Map([...incoming].map(([id,a])=>[id,a.length]));
  while(queue.length){const id=queue.shift();order.push(id);for(const next of out.get(id)){counts.set(next,counts.get(next)-1);if(counts.get(next)===0)queue.push(next);}}
  assert(order.length===graph.nodes.length,'FLOW_CYCLE','工作流存在循环');return {order,incoming};
 }
 save(input){
  const graph=JSON.parse(JSON.stringify(input.graph));this.validate(graph);const entries=this.store.get('workflow_catalog',[]),old=entries.find(x=>x.id===graph.id);
  assert((input.expected_version||0)===(old?.version||0),'FLOW_VERSION','工作流版本冲突');graph.version=(old?.version||0)+1;graph.updated_at=new Date().toISOString();
  const folder=path.join(this.root,'runtime/local_ai_workbench/workflows');fs.mkdirSync(folder,{recursive:true});const file=path.join(folder,graph.id+'.json');fs.writeFileSync(file+'.tmp',JSON.stringify(graph,null,2));fs.renameSync(file+'.tmp',file);
  this.store.put('workflow_definition:'+graph.id,graph);this.store.put('workflow_catalog',[...entries.filter(x=>x.id!==graph.id),{id:graph.id,name:graph.name,version:graph.version,updated_at:graph.updated_at}]);
  this.registry.import({schema_version:'workbench.resource.v1',resource:{id:'workflow:'+graph.id,type:'workflow',name:graph.name},adapter:'dataset',path:path.relative(this.root,file),capabilities:['inspect','health','read'],views:[{view_id:'workflow:'+graph.id+'.view',view_type:'custom',renderer:'DOM',data_mode:'snapshot'}],version:'v'+graph.version});
  this.store.event('workflow.saved',{id:graph.id,version:graph.version});return {item:graph};
 }
 get(id){identifier(id);const graph=this.store.get('workflow_definition:'+id);assert(graph,'FLOW_MISSING','工作流不存在');return {item:graph};}
 record(run){const all=this.store.get('workflow_runs',[]);this.store.put('workflow_runs',[...all.filter(x=>x.id!==run.id),run].slice(-100));this.store.event('workflow.run',{id:run.id,status:run.status,current:run.current});}
 async transform(type,value,params,id){
  const folder=path.join(this.root,'runtime/intermediate_tasks','workflow_'+crypto.randomUUID().replaceAll('-',''));fs.mkdirSync(folder,{recursive:true});const req=path.join(folder,'request.json'),res=path.join(folder,'response.json'),raw=JSON.stringify({schema_version:'workbench.workflow.transform.v1',root:this.root,run_id:id,producer:{script:SCRIPT_NAME,version:SCRIPT_VERSION},operation:type,value,params});fs.writeFileSync(req,raw);
  const code=await new Promise((resolve,reject)=>{const child=spawn(require('./release_paths_v0001.cjs').python(this.root),['-B',path.join(this.root,'scripts/action/infrastructures/workflow_transform_v0001.py'),'--root',this.root,'--request',req,'--response',res],{windowsHide:true,stdio:'ignore'});const timer=setTimeout(()=>{child.kill();reject(Error('变换子进程超时，失败交接保留'));},30000);child.once('error',error=>{clearTimeout(timer);reject(error);});child.once('close',code=>{clearTimeout(timer);resolve(code);});});
  assert(fs.existsSync(res)&&fs.statSync(res).size<4000000,'FLOW_RESPONSE','变换响应缺失或超限');const result=JSON.parse(fs.readFileSync(res,'utf8'));assert(result.run_id===id&&result.schema_version==='workbench.workflow.transform.v1'&&result.producer?.script==='workflow_transform_v0001'&&result.producer?.version==='v0001'&&result.source_sha256===crypto.createHash('sha256').update(raw).digest('hex'),'FLOW_HANDOFF','变换交接不匹配');assert(code===0&&result.status==='completed','FLOW_TRANSFORM',result.error||'变换失败');await this.capabilities.cleanup(folder);return result.result;
 }
 async execute(graph,run,ancestors=[]){
  assert(!ancestors.includes(graph.id)&&ancestors.length<5,'FLOW_RECURSION','嵌套工作流循环或超深');const plan=this.validate(graph),values=new Map(),cache=new Map();let totalBytes=0;
  for(const id of plan.order){
   if(run.cancel_requested){run.status='cancelled';break;}const node=graph.nodes.find(x=>x.id===id),def=NODES.find(x=>x.id===node.type),step={node:id,workflow_id:graph.id,workflow_version:graph.version,name:def.name,status:'running',at:new Date().toISOString()};run.current=id;run.steps.push(step);this.record(run);const value=values.get(plan.incoming.get(id)[0]);let result;
   try{
    const key=hash({type:node.type,value,params:node.params,version:SCRIPT_VERSION});if(def.pure&&cache.has(key)){result=cache.get(key);step.cached=true;}
    else if(node.type==='standards')result=await this.governance.operation('list',{scope:node.params.scope||'global'});
    else if(node.type==='resources')result={items:this.registry.list()};
    else if(node.type==='workflow'){const nested=this.get(node.params.workflow_id).item;result=await this.execute(nested,run,[...ancestors,graph.id]);}
    else result=await this.transform(node.type,value,node.params,run.id+'_'+id);
    totalBytes+=Buffer.byteLength(JSON.stringify(result??null));assert(totalBytes<=16000000,'FLOW_TOTAL_BUDGET','工作流累计输出超过预算');assert(Buffer.byteLength(JSON.stringify(result??null))<=2000000,'FLOW_RESULT_BUDGET','节点输出超过预算');values.set(id,result);if(def.pure&&cache.size<this.cacheLimit)cache.set(key,result);step.status='completed';step.output_hash=hash(result);step.finished_at=new Date().toISOString();this.record(run);
   }catch(error){step.status='failed';step.error=error.message;this.record(run);throw error;}
  }
  const outputs=Object.fromEntries([...values].filter(([id])=>graph.nodes.find(n=>n.id===id).type==='output'));assert(Buffer.byteLength(JSON.stringify(outputs))<=4000000,'FLOW_OUTPUT_BUDGET','最终输出超过预算');return outputs;
 }
 run(input){assert(this.active.size<4,'FLOW_BUSY','已有四个编排任务正在运行，请稍后重试');const graph=this.get(input.id).item;if(input.version!==undefined)assert(graph.version===input.version,'FLOW_VERSION','运行版本已经改变');const run={id:crypto.randomUUID(),workflow_id:graph.id,workflow_version:graph.version,definition_hash:hash(graph),status:'running',started_at:new Date().toISOString(),steps:[],cancel_requested:false};this.record(run);const work=this.execute(graph,run).then(outputs=>{if(run.status!=='cancelled'){run.outputs=outputs;run.status='completed';}}).catch(error=>{run.status='failed';run.error=error.message;}).finally(()=>{run.finished_at=new Date().toISOString();this.record(run);this.active.delete(run.id);});this.active.set(run.id,{run,work});return {run};}
 status(id){const run=this.active.get(id)?.run||this.store.get('workflow_runs',[]).find(x=>x.id===id);assert(run,'FLOW_RUN','运行记录不存在');return {run};}
 cancel(id){const entry=this.active.get(id);assert(entry,'FLOW_NOT_RUNNING','任务已经结束');entry.run.cancel_requested=true;this.record(entry.run);return {run:entry.run,message:'等待当前节点完成后取消后续节点'};}
 async close(){for(const entry of this.active.values())entry.run.cancel_requested=true;await Promise.allSettled([...this.active.values()].map(x=>x.work));}
}
module.exports={Workflows,NODES};
