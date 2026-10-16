// ============================================================
// 文件名: workbench_adapter_catalog_v0001.cjs
// 中文名: 后端适配器元数据目录与工作台接入
// 版本号: v0001
// 主层级: action
// 层级: workbench / adapters
// 脚本定位: 工作台适配器来源与版本契约目录
// 职责说明: 扫描真实适配源，管理显式加载身份和契约引用。
// 本脚本做什么: 有界读取 ALIAS_META、核对路径与哈希、维护独立接入状态。
// 本脚本不做什么: 不导入或执行发现的脚本，不修改原文件和运行许可。
// 制度边界声明: 加载是工作台接入；后台调用方和独立进程生命周期保持原样。
// 可更新: True
// ============================================================
// ALIAS_META
// alias: workbench_adapter_catalog_v0001
// family: workbench_adapter_catalog
// role: adapter_catalog
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_adapter_catalog_v0001.cjs
// input:
//   - adapter_sources_v0001 and explicit scan/load requests
// output:
//   - versioned metadata, dependency observations and pinned references
// depends_on:
//   - Node.js standard library
//   - workbench_store_v0001
// used_by:
//   - workbench_loading_v0003
//   - workbench_workflows_v0002
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {assert}=require('./workbench_contracts_v0001.cjs');
const SCRIPT_NAME='workbench_adapter_catalog_v0001',SCRIPT_VERSION='v0001',SCRIPT_FAMILY='workbench_adapter_catalog',DEFAULT_ENCODING='utf8';
const hash=x=>crypto.createHash('sha256').update(typeof x==='string'||Buffer.isBuffer(x)?x:JSON.stringify(x)).digest('hex');
const list=x=>Array.isArray(x)?x:typeof x==='string'&&x&&x!=='[]'?[x]:[];
function metadata(text){
 const lines=text.split(/\r?\n/).slice(0,350),meta=Object.create(null);let reading=false,started=false,key=null;
 for(const raw of lines){const line=raw.replace(/^\s*(?:#|\/\/)\s?/,''),comment=/^\s*(?:#|\/\/)/.test(raw);if(!reading){if(comment&&line.trim()==='ALIAS_META')reading=true;continue;}if(!comment){if(started)break;continue;}if(/^={3,}/.test(line.trim())){if(started)break;continue;}const match=line.match(/^([a-z_]+):\s*(.*)$/);if(match){key=match[1];if(['__proto__','constructor','prototype'].includes(key))continue;meta[key]=match[2].trim()||[];started=true;}else if(key&&/^\s*-\s+/.test(line)){if(!Array.isArray(meta[key]))meta[key]=list(meta[key]);meta[key].push(line.replace(/^\s*-\s+/,''));}}
 const heading=lines.map(x=>x.match(/^\s*(?:#|\/\/)\s*中文名:\s*(.+)$/)?.[1]).find(Boolean);
 return {meta,name:heading||meta.family||meta.alias};
}
class AdapterCatalog{
 constructor(root,store){this.root=fs.realpathSync(root);this.store=store;this.config=JSON.parse(fs.readFileSync(path.join(__dirname,'../config/adapter_sources_v0001.json'),'utf8'));assert(this.config.schema_version==='workbench.adapter-sources.v1','ADAPTER_CONFIG','适配器扫描配置无效');}
 records(){return this.store.get('adapter_catalog_v1',[]);}
 loaded(){return this.store.get('loaded_adapters',[]);}
 resolve(relative){
  assert(typeof relative==='string'&&!path.isAbsolute(relative)&&!relative.split(/[\\/]/).includes('..'),'ADAPTER_PATH','适配器来源越界');const target=path.resolve(this.root,relative);
  assert(target.startsWith(this.root+path.sep),'ADAPTER_PATH','适配器来源越界');let cursor=this.root;
  for(const part of path.relative(this.root,target).split(path.sep)){cursor=path.join(cursor,part);assert(!fs.lstatSync(cursor).isSymbolicLink(),'ADAPTER_LINK','不扫描链接或重解析来源');}
  const real=fs.realpathSync(target);assert(real===target||real.toLowerCase()===target.toLowerCase(),'ADAPTER_LINK','来源不是预期本地文件');return target;
 }
 read(relative){const file=this.resolve(relative),stat=fs.statSync(file);assert(stat.isFile()&&stat.size<=this.config.max_file_bytes,'ADAPTER_SIZE','适配器文件超过预算或不是文件');const bytes=fs.readFileSync(file);return {bytes,sha256:hash(bytes),size:bytes.length,modified_at:stat.mtime.toISOString()};}
 record(relative,source){
  const file=this.read(relative),parsed=metadata(file.bytes.toString('utf8')),m=parsed.meta,issues=[];
  for(const key of ['alias','family','version','entry_point'])if(typeof m[key]!=='string'||!m[key].trim())issues.push('缺少 '+key+' 声明');
  if(m.entry_point&&String(m.entry_point).replaceAll('\\','/')!==relative)issues.push('声明入口与实际文件路径不一致');
  return {id:'adapter:'+hash(relative).slice(0,24),name:String(parsed.name||path.basename(relative)).slice(0,160),path:relative,source:source.name,source_id:source.id,alias:m.alias||'',family:m.family||'',version:m.version||'',declared_status:m.status||'未声明',role:m.role||'',input:list(m.input),output:list(m.output),depends_on:list(m.depends_on),used_by:list(m.used_by),description:String(m.role||'后端适配器声明'),sha256:file.sha256,size:file.size,modified_at:file.modified_at,issues};
 }
 scan(){
  const found=[],errors=[];let count=0,bytes=0,truncated=false;
  const walk=(folder,source,depth)=>{if(depth>this.config.max_depth){truncated=true;return;}for(const entry of fs.readdirSync(folder,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){if(count>=this.config.max_files||bytes>=this.config.max_total_bytes){truncated=true;return;}const full=path.join(folder,entry.name),relative=path.relative(this.root,full).replaceAll('\\','/');if(entry.isSymbolicLink()){errors.push({path:relative,error:'跳过链接来源'});continue;}if(entry.isDirectory()){if(!this.config.exclude_directories.includes(entry.name)){try{walk(this.resolve(relative),source,depth+1);}catch(e){errors.push({path:relative,error:e.message});}}continue;}if(!this.config.extensions.includes(path.extname(entry.name)))continue;count++;try{const item=this.record(relative,source);bytes+=item.size;if(bytes>this.config.max_total_bytes){truncated=true;return;}found.push(item);}catch(e){errors.push({path:relative,error:e.message});}}};
  for(const source of this.config.sources){const file=path.join(this.root,source.path);if(!fs.existsSync(file)){errors.push({path:source.path,error:'扫描目录不存在'});continue;}try{walk(this.resolve(source.path),source,0);}catch(e){errors.push({path:source.path,error:e.message});}}
  const foundIds=new Set(found.map(x=>x.id));for(const previous of this.records())if(!foundIds.has(previous.id))found.push({...previous,not_seen:true});
  assert(found.length<=2000,'ADAPTER_LIMIT','适配器目录超过 2000 项，请整理配置');const scan={at:new Date().toISOString(),errors,truncated,files_examined:count,bytes_read:bytes};if(truncated)errors.push({path:'扫描预算',error:'扫描未完整结束，不能据此认定未见文件已删除'});
  this.store.put('adapter_catalog_v1',found);this.store.put('adapter_scan_v1',scan);this.store.event('adapters.scanned',{count:found.length,errors:errors.length,truncated});return this.snapshot();
 }
 present(item){
  let current=null,reason='';try{current=this.read(item.path);}catch(e){reason=e.message;}
  const changed=!!current&&current.sha256!==item.sha256,loaded=this.loaded().find(x=>x.id===item.id),pinnedChanged=!!loaded&&(!current||loaded.sha256!==current.sha256);
  if(!reason)reason=changed?'来源内容已变化，请重新扫描后核对版本':item.not_seen?'本次扫描未确认此来源，请检查扫描范围与预算':item.issues.join('；');
  const source=this.config.sources.find(x=>x.id===item.source_id),subdirectory=source?path.posix.dirname(item.path.slice(source.path.length+1)):'.',directory=[item.source,...(subdirectory==='.'?[]:subdirectory.split('/')),item.family||'声明待完善'];
  return {...item,directory,key:'adapter:'+item.id,kind:'adapter',scopes:['adapters'],loaded:!!loaded,available:!!current&&!changed&&!item.not_seen&&!item.issues.length,reason,source_changed:changed||pinnedChanged,pinned_sha256:loaded?.sha256||null,loaded_at:loaded?.loaded_at||null,runtime_state:'由原调用方管理，未探测进程',fingerprint:hash([item.id,current?.sha256,item.sha256,loaded?.sha256,item.issues,item.not_seen]),reference_ready:!!loaded&&!!current&&loaded.sha256===current.sha256&&!item.not_seen&&!item.issues.length};
 }
 snapshot(){const items=this.records().map(x=>this.present(x)),scan=this.store.get('adapter_scan_v1',null);return {schema_version:'workbench.adapter-catalog.v1',items,scan,sources:this.config.sources,revision:hash(items.map(x=>[x.id,x.fingerprint,x.loaded])),semantics:'加载是将适配器契约接入工作台；不执行脚本。卸载不停止后端调用或删除来源。'};}
 get(id){const item=this.records().find(x=>x.id===id);assert(item,'ADAPTER_MISSING','适配器不在已扫描目录中');return item;}
 change(id,load,expectedHash){
  const item=this.get(id),state=this.present(item);if(load){assert(state.available,'ADAPTER_UNAVAILABLE',state.reason||'适配器来源不可用');assert(!expectedHash||expectedHash===item.sha256,'ADAPTER_CHANGED','适配器版本已变化，不能恢复旧接入');this.store.put('loaded_adapters',[...this.loaded().filter(x=>x.id!==id),{id,sha256:item.sha256,version:item.version,loaded_at:new Date().toISOString()}]);}else this.store.put('loaded_adapters',this.loaded().filter(x=>x.id!==id));this.store.event('adapters.changed',{id,loaded:load,sha256:item.sha256});return this.present(item);
 }
 reference(id,sha256){const item=this.present(this.get(id));assert(item.reference_ready,'ADAPTER_NOT_LOADED','适配器未加载、来源缺失或内容已变化，请重新扫描并加载');assert(!sha256||sha256===item.pinned_sha256,'ADAPTER_CHANGED','引用与当前接入哈希不一致');return {schema_version:'workbench.adapter-reference.v1',id:item.id,name:item.name,family:item.family,version:item.version,source:item.path,sha256:item.pinned_sha256,input:item.input,output:item.output,depends_on:item.depends_on,used_by:item.used_by,execution:'metadata-reference-only'};}
 check(id){
  const item=this.present(this.get(id)),dependencies=item.depends_on.map(name=>{if(/^(?:Python|Node\.js|Browser) (?:standard library|DOM)|^node:/.test(name))return {name,status:'runtime',detail:'运行时或标准库声明，未安装或执行任何组件'};if(/[\\/]/.test(name)){try{const file=this.resolve(name);return {name,status:'found',path:path.relative(this.root,file).replaceAll('\\','/'),detail:'来源文件存在；未验证接口或执行可用性'};}catch{return {name,status:'unresolved',detail:'声明路径未找到或不在本项目范围'};}}const match=this.records().filter(x=>[x.alias,x.family,path.basename(x.path,path.extname(x.path))].includes(name));return match.length?{name,status:'declared',matches:match.map(x=>x.id),detail:'扫描目录存在匹配声明；未验证执行可用性'}:{name,status:'unresolved',detail:'未在本次扫描目录确认；可能由其他模块或第三方环境提供'};});
  const result={schema_version:'workbench.adapter-check.v1',id,at:new Date().toISOString(),source_available:item.available,source_changed:item.source_changed,loaded:item.loaded,reference_ready:item.reference_ready,sha256:item.sha256,issues:item.issues,reason:item.reason,dependencies,execution_tested:false,permissions_changed:false};this.store.put('adapter_check:'+id,result);this.store.event('adapters.checked',{id,available:item.available,unresolved:dependencies.filter(x=>x.status==='unresolved').length});return result;
 }
}
module.exports={AdapterCatalog,metadata};
