// ============================================================
// 文件名: workbench_features_v0002.cjs
// 中文名: 现有功能描述与工作区模板接口
// 版本号: v0002
// 主层级: action
// 层级: workbench / platform
// 脚本定位: existing_feature_projection
// 职责说明:
// - 现有功能描述与工作区模板接口
// 本脚本做什么:
// - 复用现有契约与受管理接口，记录真实状态
// 本脚本不做什么:
// - 不改写业务事实，不自动授权、批准或调用模型
// 制度边界声明:
// - 仅管理工作台配置与派生展示，可回退
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_features_v0002
// family: workbench_features
// role: existing_feature_projection
// version: v0002
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_features_v0002.cjs
// input:
//   - explicit versioned requests and existing metadata
// output:
//   - factual results and structured errors
// depends_on:
//   - existing workbench APIs and standard library
// used_by:
//   - workbench_platform
// ============================================================
const SCRIPT_NAME='workbench_features_v0002',SCRIPT_VERSION='v0002',SCRIPT_FAMILY='workbench_features',DEFAULT_ENCODING='utf8';

const {assert,identifier}=require('./workbench_contracts_v0001.cjs');
class Features{
 constructor(store,studio,registry,capabilities,workflows,assets,adapterCatalog){Object.assign(this,{store,studio,registry,capabilities,workflows,assets,adapterCatalog});}
 async list(){
  const hub=await this.capabilities.list(),items=[];
  const add=(id,kind,name,version,entry,operations,panels=[],extra={})=>items.push({schema_version:'workbench.feature.v1',id,kind,name,version:version||'unversioned',description:extra.description||'',operations,ui_entry:entry,panels,runtime_dependencies:extra.runtime_dependencies||[],resource_dependencies:extra.resource_dependencies||[],permissions:extra.permissions||['existing-managed-policy'],lifecycle:extra.lifecycle||'observation-only',...extra});
  add('workspace:home','workspace','工作台主页','v1',{kind:'workspace',id:'home'},['open']);add('workspace:flow','workspace','通用能力编排','v1',{kind:'workspace',id:'flow'},['open'],['node-library','node-properties','run-log']);
  for(const a of this.adapterCatalog?.snapshot().items.filter(x=>x.loaded)||[])add(a.id,'adapter',a.name+' · '+a.version,a.version,{kind:'adapter',id:a.id},['inspect','check','reference','unload'],['library','properties'],{description:a.directory.join(' / '),lifecycle:'catalog-attachment; independent runtime unchanged'});
  for(const c of this.studio.categories())add('category:'+c.id,'category',c.name,'v1',{kind:'category',id:c.id},['open','search'],['library','properties'],{description:c.description});
  for(const t of this.studio.toolCatalog())add(t.id,'tool',t.name,t.version,{kind:'tool',id:t.id},['open','refresh','stop'],t.id==='tool.action-chat'?['history','session']:['tool-owned'],{description:t.description,lifecycle:'independent-process; close-view-is-not-stop',runtime_dependencies:[t.runtime||'declared-by-tool'],permissions:['tool.independence.v1']});
  for(const s of this.studio.items().filter(x=>x.managed_software))add(s.id,'software',s.name,s.version,{kind:'software',id:s.id},['inspect','open',...(s.adapter==='blender'?['blender-panel']:[])],['software-owned'],{description:s.path,lifecycle:'independent native process; unloading is not uninstalling'});
  for(const r of this.registry.list())add(r.resource_id,r.resource_type,r.display_name,r.version,{kind:'resource',id:r.resource_id},r.capabilities,['properties'],{description:r.detail,health:r.health?.status});
  for(const c of hub.items)add('capability:'+c.id,c.kind,c.name,c.version,{kind:'capability',id:c.id},['inspect','reference',...(c.enabled?['invoke']:[])],['properties'],{description:c.description,permissions:['existing grants',...(c.kind==='github'?['github.intake.v1']:[])]});
  for(const a of (await this.assets.request('list',{})).items)add('asset:'+a.id,'asset',a.name,a.version,{kind:'asset',id:a.id},['inspect','reference',...(a.format==='glb'||a.format==='gltf'?['preview']:[])],['asset-category','asset-properties']);
  for(const w of this.workflows.list().items)add('workflow:'+w.id,'workflow',w.name,w.version,{kind:'workflow',id:w.id},['open','run'],['node-library','node-properties','run-log']);
  return {items:[...new Map(items.map(x=>[x.id,x])).values()],generated_at:new Date().toISOString(),source:'existing registries; no duplicate registration database'};
 }
 templates(){return {items:this.store.get('workspace_templates',[])};}
 saveTemplate(value){assert(value?.schema_version==='workbench.workspace-template.v1','TEMPLATE_SCHEMA','模板契约错误');identifier(value.id);assert(typeof value.name==='string'&&value.name.length<120,'TEMPLATE_NAME','模板名称无效');assert(Array.isArray(value.features)&&value.features.length<40&&value.features.every(x=>typeof x==='string'&&x.length<180),'TEMPLATE_FEATURES','功能引用无效');assert(value.layout&&typeof value.layout==='object'&&Object.keys(value.layout).every(x=>['left','right','bottom','left_hidden','right_hidden','bottom_hidden','swapped','left_collapsed','right_collapsed','rail'].includes(x)),'TEMPLATE_LAYOUT','仅允许显示布局字段');for(const side of ['left','right','bottom'])assert(value.layout[side]===undefined||(Number.isFinite(value.layout[side])&&value.layout[side]>=0&&value.layout[side]<=600),'TEMPLATE_SIZE','布局宽度无效');assert(Buffer.byteLength(JSON.stringify(value))<20000,'TEMPLATE_BUDGET','模板超过预算');const items=this.templates().items,copy={...value,updated_at:new Date().toISOString()};this.store.put('workspace_templates',[...items.filter(x=>x.id!==value.id),copy]);return {item:copy};}
}
module.exports={Features};
