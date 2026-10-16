// ============================================================
// 文件名: workflow_graph_v0001.mjs
// 中文名: 工作流结构契约与编辑变换
// 版本号: v0001
// 主层级: action
// 层级: workbench / workflow / contract
// 脚本定位: 纯图结构校验与编辑操作
// 职责说明: 在浏览器与后端使用相同的端口、Schema、组合和迁移规则。
// 本脚本做什么: 校验数据，生成可审阅的图变更，不产生执行副作用。
// 本脚本不做什么: 不调用业务能力，不授予权限，不访问存储。
// 制度边界声明: 定义与实例分离；组合保留接口映射和内部身份。
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workflow_graph_v0001
// family: workflow_graph
// role: workflow_contract
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/workspace/workflow_graph_v0001.mjs
// input:
//   - versioned workflow JSON and capability definitions
// output:
//   - normalized graphs, diagnostics and immutable edits
// depends_on: []
// used_by:
//   - platform_workflow_v0002
//   - workbench_workflows_v0002
// ============================================================
export const SCRIPT_NAME='workflow_graph_v0001', SCRIPT_VERSION='v0001', SCRIPT_FAMILY='workflow_graph', DEFAULT_ENCODING='utf8';
export const SCHEMA='workbench.workflow.v2';
const ID=/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const dangerous=new Set(['__proto__','prototype','constructor']);
export const clone=value=>structuredClone(value);
export const uid=prefix=>prefix+'-'+crypto.randomUUID().replaceAll('-','').slice(0,12);
export class GraphError extends Error {constructor(code,message){super(message);this.code=code;}}
export function normalize(raw){
 const graph=clone(raw);
 if(!graph||!['workbench.workflow.v1',SCHEMA].includes(graph.schema_version))throw new GraphError('FLOW_SCHEMA','不支持的工作流版本');
 if(graph.schema_version==='workbench.workflow.v1'){
  graph.schema_version=SCHEMA;graph.migrated_from='workbench.workflow.v1';
  graph.edges=(graph.edges||[]).map((e,i)=>({...e,id:e.id||'legacy-'+i,fromPort:e.fromPort||'value',toPort:e.toPort||'value'}));
 }
 graph.goal??='';graph.interface??={inputs:[],outputs:[]};graph.viewport??={x:0,y:0,zoom:1};
 return graph;
}
export function definition(node,definitions){
 if(node.type==='composite'&&node.subgraph){
  return {id:'composite',name:node.label||'组合节点',description:'当前实例中的组合快照',category:['自定义节点'],source:node.definition_ref?.id||'当前工作流',params:{},parameter_schema:{type:'object',properties:{}},inputs:node.subgraph.interface?.inputs||[],outputs:node.subgraph.interface?.outputs||[],adapter:'composite'};
 }
 return definitions.find(d=>d.id===node.type);
}
export function compatible(from,to){
 const source=Array.isArray(from)?from:[from||'any'],target=Array.isArray(to)?to:[to||'any'];
 return target.includes('any')||source.every(t=>target.includes(t)||(target.includes('json')&&['object','array','string','number','integer','boolean','null','json'].includes(t))||(t==='integer'&&target.includes('number')));
}
export function schemaErrors(value,schema={},path='参数',depth=0){
 const errors=[];if(depth>20)return [path+' Schema 嵌套过深'];
 if(schema===true)return [];if(schema===false)return [path+' 不允许此值'];
 if(schema.$ref)return [path+' 含未解析的 $ref，需注册完整内联契约'];
 if(schema.const!==undefined&&JSON.stringify(value)!==JSON.stringify(schema.const))errors.push(path+' 不符合固定值');
 if(schema.enum&&!schema.enum.some(x=>JSON.stringify(x)===JSON.stringify(value)))errors.push(path+' 不在允许值中');
 for(const key of ['allOf','anyOf','oneOf'])if(schema[key]){const results=schema[key].map(s=>schemaErrors(value,s,path,depth+1));const ok=results.filter(x=>!x.length).length;if(key==='allOf')errors.push(...results.flat());else if(key==='anyOf'?!ok:ok!==1)errors.push(path+' 不符合 '+key+' 契约');}
 if(schema.not&&!schemaErrors(value,schema.not,path,depth+1).length)errors.push(path+' 不符合排除规则');
 const type=value===null?'null':Array.isArray(value)?'array':typeof value;
 const types=Array.isArray(schema.type)?schema.type:schema.type?[schema.type]:[];
 if(types.length&&!types.some(t=>t===type||t==='integer'&&Number.isInteger(value)||t==='json'||t==='any'))return [...errors,path+' 需要 '+types.join(' / ')];
 if(type==='string'){
  if(schema.minLength!==undefined&&value.length<schema.minLength)errors.push(path+' 长度不足');
  if(schema.maxLength!==undefined&&value.length>schema.maxLength)errors.push(path+' 超过长度限制');
  if(schema.pattern){try{if(!new RegExp(schema.pattern,'u').test(value))errors.push(path+' 格式无效');}catch{errors.push(path+' Schema 正则无效');}}
 }
 if(type==='number'){
  if(!Number.isFinite(value))errors.push(path+' 不是有限数字');
  if(schema.minimum!==undefined&&value<schema.minimum||schema.maximum!==undefined&&value>schema.maximum)errors.push(path+' 超出范围');
  if(schema.exclusiveMinimum!==undefined&&value<=schema.exclusiveMinimum||schema.exclusiveMaximum!==undefined&&value>=schema.exclusiveMaximum)errors.push(path+' 超出开区间范围');
 }
 if(type==='array'){
  if(schema.minItems!==undefined&&value.length<schema.minItems||schema.maxItems!==undefined&&value.length>schema.maxItems)errors.push(path+' 数量不符合契约');
  if(schema.uniqueItems&&new Set(value.map(x=>JSON.stringify(x))).size!==value.length)errors.push(path+' 不允许重复项');
  value.forEach((v,i)=>errors.push(...schemaErrors(v,schema.items||{},path+'['+i+']',depth+1)));
 }
 if(type==='object'){
  for(const key of schema.required||[])if(value[key]===undefined)errors.push(path+'.'+key+' 必填');
  for(const [key,v] of Object.entries(value)){
   if(dangerous.has(key)){errors.push(path+'.'+key+' 是保留字段');continue;}
   const s=schema.properties?.[key];
   if(!s&&schema.additionalProperties===false)errors.push(path+'.'+key+' 未在契约中声明');
   else errors.push(...schemaErrors(v,s||schema.additionalProperties||{},path+'.'+key,depth+1));
  }
 }
 return errors;
}
export function validate(raw,definitions,{depth=0,externalInputs=[]}={}){
 const errors=[],warnings=[],add=(code,message,node,edge)=>errors.push({code,message,node,edge,severity:'error',kind:'deterministic'});
 let graph;try{graph=normalize(raw);}catch(error){add(error.code,error.message);return {valid:false,errors,warnings,order:[]};}
 if(depth>8){add('FLOW_DEPTH','组合嵌套超过 8 层');return {valid:false,errors,warnings,order:[]};}
 if(!/^[a-z][a-z0-9_.-]{2,80}$/.test(graph.id||''))add('FLOW_ID','工作流标识无效');
 if(typeof graph.name!=='string'||!graph.name.trim()||graph.name.length>159)add('FLOW_NAME','名称需要 1–159 字符');
 if(!Array.isArray(graph.nodes)||!Array.isArray(graph.edges)){add('FLOW_STRUCTURE','节点和连线必须为数组');return {valid:false,errors,warnings,order:[]};}
 if(graph.nodes.length>500||graph.edges.length>2000||JSON.stringify(graph).length>2000000)add('FLOW_LIMIT','图超过 500 节点、2000 连线或 2 MB 限制');
 const nodes=new Map(),defs=new Map(),incoming=new Map(),outgoing=new Map();
 for(const node of graph.nodes){
  if(!ID.test(node.id||'')||dangerous.has(node.id)||nodes.has(node.id))add('FLOW_DUPLICATE','节点标识无效或重复',node.id);
  nodes.set(node.id,node);incoming.set(node.id,[]);outgoing.set(node.id,[]);
  if(!Number.isFinite(node.x)||!Number.isFinite(node.y))add('FLOW_POSITION','节点坐标无效',node.id);
  const def=definition(node,definitions);defs.set(node.id,def);
  if(!def){add('FLOW_NODE','能力未注册或已不可用：'+node.type,node.id);continue;}
  if(node.capability_ref&&def.capability_ref&&JSON.stringify(node.capability_ref)!==JSON.stringify(def.capability_ref))add('FLOW_CAPABILITY_VERSION','能力版本已变化，请显式更新引用或重新添加节点',node.id);
  for(const direction of ['inputs','outputs']){const portIds=new Set();for(const p of def[direction]||[]){if(!ID.test(p.id||'')||dangerous.has(p.id)||portIds.has(p.id))add('FLOW_PORT_SCHEMA','能力端口标识无效或重复',node.id);portIds.add(p.id);}}
  if(def.available===false)add('FLOW_UNAVAILABLE',def.unavailable_reason||'能力不可用',node.id);
  if(!node.params||typeof node.params!=='object'||Array.isArray(node.params))add('FLOW_PARAMS','参数必须为对象',node.id);
  if(node.type==='composite'){
   const nested=validate(node.subgraph,definitions,{depth:depth+1,externalInputs:node.subgraph.interface?.inputs||[]});
   errors.push(...nested.errors.map(e=>({...e,node:node.id,inner_node:e.node,message:'组合内部：'+e.message})));
  }
 }
 const edgeIds=new Set();
 for(const edge of graph.edges){
  if(!ID.test(edge.id||'')||edgeIds.has(edge.id))add('FLOW_EDGE_ID','连线标识无效或重复',undefined,edge.id);edgeIds.add(edge.id);
  if(!nodes.has(edge.from)||!nodes.has(edge.to)||edge.from===edge.to){add('FLOW_EDGE','连线端点无效',undefined,edge.id);continue;}
  const from=defs.get(edge.from)?.outputs?.find(p=>p.id===edge.fromPort),to=defs.get(edge.to)?.inputs?.find(p=>p.id===edge.toPort);
  if((!from||!to)&&defs.get(edge.from)&&defs.get(edge.to))add('FLOW_PORT','端口不存在',edge.to,edge.id);
  else if(from&&to&&!compatible(from.type,to.type))add('FLOW_TYPES','端口类型不兼容：'+from.type+' → '+to.type,edge.to,edge.id);
  incoming.get(edge.to).push(edge);outgoing.get(edge.from).push(edge);
 }
 const exposed=[...externalInputs,...(graph.interface?.inputs||[])];
 for(const node of graph.nodes){
  const def=defs.get(node.id);if(!def)continue;
  const params=clone(node.params||{});
  for(const port of def.inputs||[]){
   const wires=incoming.get(node.id).filter(e=>e.toPort===port.id),bound=exposed.some(p=>p.node===node.id&&p.port===port.id);
   if(wires.length>1&&!port.multiple)add('FLOW_MULTIPLE','端口只允许一个来源：'+port.name,node.id);
   if(bound&&wires.length)add('FLOW_INTERFACE','外部输入与内部连线重复：'+port.id,node.id);
   if(port.required&&!wires.length&&!bound&&params[port.id]===undefined)add('FLOW_INPUT','缺少输入：'+(port.name||port.id),node.id);
   if(wires.length||bound)delete params[port.id];
  }
  const schema=clone(def.parameter_schema||{type:'object'});
  schema.required=(schema.required||[]).filter(key=>!incoming.get(node.id).some(e=>e.toPort===key)&&!exposed.some(p=>p.node===node.id&&p.port===key));
  for(const message of schemaErrors(params,schema))add('FLOW_PARAMS',message,node.id);
  if(!outgoing.get(node.id).length&&def.role!=='output'&&node.type!=='output'&&!(graph.interface?.outputs||[]).some(p=>p.node===node.id))warnings.push({code:'FLOW_UNUSED',message:'节点结果尚未连接输出：'+(node.label||def.name),node:node.id,severity:'warning',kind:'deterministic'});
 }
 for(const direction of ['inputs','outputs']){
  const seen=new Set();for(const port of graph.interface?.[direction]||[]){
   const actual=defs.get(port.node)?.[direction]?.find(p=>p.id===port.port);
   if(!ID.test(port.id||'')||seen.has(port.id)||!actual||JSON.stringify(actual.type)!==JSON.stringify(port.type))add('FLOW_INTERFACE','组合接口失效：'+port.id,port.node);
   seen.add(port.id);
  }
 }
 const counts=new Map([...incoming].map(([id,edges])=>[id,edges.length])),queue=[...counts].filter(([,c])=>!c).map(([id])=>id),order=[];
 while(queue.length){const id=queue.shift();order.push(id);for(const e of outgoing.get(id)){counts.set(e.to,counts.get(e.to)-1);if(!counts.get(e.to))queue.push(e.to);}}
 if(order.length!==nodes.size)add('FLOW_CYCLE','存在非法循环依赖');
 if(!graph.nodes.length)add('FLOW_EMPTY','工作流尚无节点');
 if(graph.nodes.length&&!graph.nodes.some(n=>n.type==='output')&&!(graph.interface?.outputs||[]).length)warnings.push({code:'FLOW_NO_OUTPUT',message:'尚未配置结果输出节点或对外输出接口',severity:'warning',kind:'deterministic'});
 return {valid:!errors.length,errors,warnings,order};
}
export function connect(graph,edge,definitions,replaceId){
 const next=clone(graph);next.edges=next.edges.filter(e=>e.id!==replaceId);next.edges.push({...edge,id:edge.id||uid('edge')});
 const fatal=validate(next,definitions).errors.filter(e=>['FLOW_EDGE','FLOW_PORT','FLOW_TYPES','FLOW_MULTIPLE','FLOW_CYCLE','FLOW_INTERFACE'].includes(e.code));
 if(fatal.length)throw new GraphError(fatal[0].code,fatal[0].message);return next;
}
export function createNode(def,x=40,y=40){
 if(def.graph)return {id:uid('node'),type:'composite',label:def.name,x,y,params:{},subgraph:clone(def.graph),definition_ref:{id:def.id,version:def.version}};
 return {id:uid('node'),type:def.id,label:def.name,x,y,params:clone(def.params||{}),capability_ref:clone(def.capability_ref||null)};
}
export function copySelection(graph,ids){return clone({nodes:graph.nodes.filter(n=>ids.has(n.id)),edges:graph.edges.filter(e=>ids.has(e.from)&&ids.has(e.to))});}
export function pasteSelection(graph,clipboard,offset={x:35,y:35}){
 const next=clone(graph),mapping=new Map(clipboard.nodes.map(n=>[n.id,uid('node')]));
 const nodes=clipboard.nodes.map(n=>({...clone(n),id:mapping.get(n.id),x:n.x+offset.x,y:n.y+offset.y}));
 next.nodes.push(...nodes);next.edges.push(...clipboard.edges.map(e=>({...e,id:uid('edge'),from:mapping.get(e.from),to:mapping.get(e.to)})));
 return {graph:next,ids:nodes.map(n=>n.id)};
}
export function groupSelection(graph,ids,definitions,name='组合节点'){
 if(!ids.size)throw new GraphError('FLOW_SELECTION','先选择节点');
 const next=clone(graph),chosen=next.nodes.filter(n=>ids.has(n.id)),x=Math.min(...chosen.map(n=>n.x)),y=Math.min(...chosen.map(n=>n.y));
 const internal=next.edges.filter(e=>ids.has(e.from)&&ids.has(e.to)),inputs=[],outputs=[];
 const expose=(direction,node,port)=>{
  const list=direction==='inputs'?inputs:outputs;let exposed=list.find(p=>p.node===node.id&&p.port===port.id);
  if(!exposed){exposed={...clone(port),id:(direction==='inputs'?'in':'out')+'_'+list.length,node:node.id,port:port.id,name:(node.label||node.id)+' · '+(port.name||port.id)};list.push(exposed);}return exposed.id;
 };
 for(const node of chosen){const def=definition(node,definitions);if(!def)throw new GraphError('FLOW_NODE','无法封装缺失能力');
  for(const p of def.inputs||[])if(!internal.some(e=>e.to===node.id&&e.toPort===p.id))expose('inputs',node,p);
  for(const p of def.outputs||[])if(!internal.some(e=>e.from===node.id&&e.fromPort===p.id)||next.edges.some(e=>e.from===node.id&&e.fromPort===p.id&&!ids.has(e.to)))expose('outputs',node,p);
 }
 const node={id:uid('group'),type:'composite',label:name,x,y,params:{},subgraph:{schema_version:SCHEMA,id:uid('group'),name,nodes:chosen.map(n=>({...n,x:n.x-x+40,y:n.y-y+40})),edges:internal,interface:{inputs,outputs}}};
 next.edges=next.edges.filter(e=>!internal.some(i=>i.id===e.id)).map(e=>({...e,...(ids.has(e.from)?{from:node.id,fromPort:expose('outputs',chosen.find(n=>n.id===e.from),definition(chosen.find(n=>n.id===e.from),definitions).outputs.find(p=>p.id===e.fromPort))}:{}),...(ids.has(e.to)?{to:node.id,toPort:expose('inputs',chosen.find(n=>n.id===e.to),definition(chosen.find(n=>n.id===e.to),definitions).inputs.find(p=>p.id===e.toPort))}:{})}));
 // Existing enclosing interfaces must continue pointing to the same data paths.
 for(const direction of ['inputs','outputs'])next.interface[direction]=next.interface[direction].map(p=>ids.has(p.node)?{...p,node:node.id,port:expose(direction,chosen.find(n=>n.id===p.node),definition(chosen.find(n=>n.id===p.node),definitions)[direction].find(q=>q.id===p.port))}:p);
 next.nodes=next.nodes.filter(n=>!ids.has(n.id));next.nodes.push(node);return {graph:next,id:node.id};
}
export function ungroup(graph,id){
 const next=clone(graph),group=next.nodes.find(n=>n.id===id);if(!group?.subgraph)throw new GraphError('FLOW_GROUP','请选择组合节点');
 const mapping=new Map(group.subgraph.nodes.map(n=>[n.id,uid('node')]));
 const resolve=(direction,port)=>{const p=group.subgraph.interface[direction].find(p=>p.id===port);if(!p)throw new GraphError('FLOW_INTERFACE','组合接口已失效');return {node:mapping.get(p.node),port:p.port};};
 next.edges=next.edges.map(e=>{const a=e.from===id?resolve('outputs',e.fromPort):null,b=e.to===id?resolve('inputs',e.toPort):null;return {...e,...(a?{from:a.node,fromPort:a.port}:{}),...(b?{to:b.node,toPort:b.port}:{})};});
 next.edges.push(...group.subgraph.edges.map(e=>({...e,id:uid('edge'),from:mapping.get(e.from),to:mapping.get(e.to)})));
 for(const direction of ['inputs','outputs'])next.interface[direction]=next.interface[direction].map(p=>p.node===id?{...p,...resolve(direction,p.port)}:p);
 const nodes=group.subgraph.nodes.map(n=>({...n,id:mapping.get(n.id),x:n.x+group.x,y:n.y+group.y}));
 next.nodes=next.nodes.filter(n=>n.id!==id).concat(nodes);return {graph:next,ids:nodes.map(n=>n.id)};
}
export function semanticKey(graph){
 const clean=g=>({id:g.id,goal:g.goal||'',interface:g.interface,nodes:g.nodes.map(n=>({id:n.id,type:n.type,params:n.params,definition_ref:n.definition_ref,subgraph:n.subgraph?clean(n.subgraph):undefined})).sort((a,b)=>a.id.localeCompare(b.id)),edges:g.edges.map(e=>({from:e.from,to:e.to,fromPort:e.fromPort,toPort:e.toPort})).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)))});
 return JSON.stringify(clean(graph));
}
export function applyProposal(graph,changes,definitions){
 if(!Array.isArray(changes)||changes.length>100)throw new GraphError('FLOW_PROPOSAL','建议变更数量无效');
 let next=clone(graph);
 for(const change of changes){
  if(change.op==='add_node'){
   const def=definitions.find(d=>d.id===change.type);if(!def)throw new GraphError('FLOW_PROPOSAL_NODE','建议引用了不存在的能力：'+change.type);
   const node=createNode(def,change.x??60,change.y??60);node.id=change.id||node.id;node.params={...node.params,...(change.params||{})};next.nodes.push(node);
  }else if(change.op==='connect')next.edges.push({id:uid('edge'),from:change.from,to:change.to,fromPort:change.fromPort,toPort:change.toPort});
  else if(change.op==='set_params'){const n=next.nodes.find(n=>n.id===change.id);if(!n)throw new GraphError('FLOW_PROPOSAL_NODE','待调整节点不存在');n.params={...n.params,...change.params};}
  else if(change.op==='remove_edge')next.edges=next.edges.filter(e=>e.id!==change.id);
  else if(change.op==='remove_node'){next.nodes=next.nodes.filter(n=>n.id!==change.id);next.edges=next.edges.filter(e=>e.from!==change.id&&e.to!==change.id);}
  else throw new GraphError('FLOW_PROPOSAL_OPERATION','不支持的建议操作');
 }
 const structural=validate(next,definitions).errors.filter(e=>!['FLOW_INPUT','FLOW_PARAMS','FLOW_EMPTY','FLOW_UNAVAILABLE'].includes(e.code));
 if(structural.length)throw new GraphError('FLOW_PROPOSAL_INVALID',structural[0].message);return next;
}
