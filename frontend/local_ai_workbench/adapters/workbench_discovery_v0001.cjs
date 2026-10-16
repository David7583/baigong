// ============================================================
// 文件名: workbench_discovery_v0001.cjs
// 中文名: 本地业务候选与登记库只读识别
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / adapters
// 脚本定位: 本地业务候选与登记库只读识别
//
// 职责说明:
// - 有界扫描指定目录，识别数据库和 CMD 元数据，读取登记数量与少量名称
//
// 本脚本做什么:
// - 有界扫描指定目录，识别数据库和 CMD 元数据，读取登记数量与少量名称
//
// 本脚本不做什么:
// - 不自动执行业务，不发起模型调用，不改写原始数据库
//
// 制度边界声明:
// - 业务来源只读；工作台状态写入私有索引；测试产物仅在临时目录
// - 错误保留分类信息，缺失语义不推断为运行成功
//
// 可更新: True
// ============================================================

// ============================================================
// ALIAS_META
// ============================================================
// alias: workbench_discovery_v0001
// family: workbench_discovery
// role: workbench_discovery
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/adapters/workbench_discovery_v0001.cjs
// input:
//   - explicit local roots and optional action registry path
// output:
//   - candidate manifests, evidence paths, registry facts and isolated problems
// depends_on:
//   - node:sqlite
//   - workbench_contracts_v0001
// used_by:
//   - workbench_server_v0001
// ============================================================

'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {DatabaseSync}=require('node:sqlite');
const {assert}=require('../runtime/workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING='utf8';
const SCRIPT_FAMILY='workbench_discovery';
const SCRIPT_NAME='workbench_discovery_v0001';
const SCRIPT_VERSION='v0001';
// ============================================================
// 来源识别与登记事实读取；不执行扫描到的程序
// ============================================================
function registryFacts(source){
 let db;
 try{
  db=new DatabaseSync(source,{readOnly:true});db.exec('PRAGMA query_only=ON; PRAGMA busy_timeout=1000');
  const names=new Set(db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map(x=>x.name));
  if(!names.has('script_registry')||!names.has('function_registry'))return null;
  const tables=[];
  for(const name of ['script_registry','function_registry','task_registry','function_block_registry','flow_definition']){
   if(!names.has(name))continue;
   const columns=db.prepare('PRAGMA table_info("'+name+'")').all().map(x=>x.name);
   const wanted=columns.filter(x=>/^(script_id|script_name|family|function_id|function_name|task_id|task_name|block_id|block_name|status|scope|scope_id|element_id|element_type|order_index)$/.test(x));
   tables.push({name,count:Number(db.prepare('SELECT COUNT(*) AS n FROM "'+name+'"').get().n),sample:wanted.length?db.prepare('SELECT '+wanted.map(x=>'"'+x+'"').join(',')+' FROM "'+name+'" LIMIT 12').all():[]});
  }
  return {source,read_only:true,observed_at:new Date().toISOString(),tables,semantics:'Registration facts do not prove availability, execution or stable reuse'};
 }finally{db?.close();}
}
async function discover(root,input={}){
 const scopes=input.roots || ['sql','software'];assert(Array.isArray(scopes)&&scopes.length>0&&scopes.length<=12&&scopes.every(x=>typeof x==='string'&&x.length<1024),'DISCOVERY_ROOTS','Provide 1 to 12 local directories');
 const candidates=[],problems=[],seen=new Set();let visited=0,registry=null;
 const add=(full,kind,driver,metadata={})=>{
  const canonical=fs.realpathSync(full);if(seen.has(canonical.toLowerCase()))return;seen.add(canonical.toLowerCase());
  const id='discovered:'+crypto.createHash('sha256').update(kind+':'+canonical.toLowerCase()).digest('hex').slice(0,24);
  const relative=path.relative(root,canonical),local=!relative.startsWith('..')&&!path.isAbsolute(relative);
  const name=metadata.display_name || path.basename(full);
  const m={schema_version:'workbench.resource.v1',resource:{id,type:kind==='launcher'?'application':'datasource',name},adapter:driver?'database':'dataset',path:driver?canonical:(local?relative:canonical),...(driver?{driver}:{}),capabilities:['inspect','health',...(local&&kind==='launcher'?['open_folder']:[]),'attach','detach'],metadata:{...metadata,discovered_path:canonical,identified_as:kind,runtime_verified:false},views:[{view_id:id+'.overview',view_type:'table',renderer:'DOM',data_mode:'snapshot'}]};
  candidates.push({id,name,kind,driver:driver || null,path:canonical,exists:true,bytes:fs.statSync(full).size,runtime:'unknown',manifest:m});
 };
 const visit=async(full,depth)=>{
  if(visited>=5000)return;visited++;
  try{
   const stat=await fs.promises.lstat(full);if(stat.isSymbolicLink())return;
   if(stat.isDirectory()){
    if(['node_modules','.git','.venv','.runtime'].includes(path.basename(full)))return;
    if(fs.existsSync(path.join(full,'chroma.sqlite3'))){add(full,'database','chroma');return;}
    if(depth>=4)return;
    for(const entry of await fs.promises.readdir(full))await visit(path.join(full,entry),depth+1);
   }else if(stat.isFile()){
    if(/\.cmd$/i.test(full)){
     if(stat.size>262144){problems.push({path:full,code:'CMD_SIZE'});return;}
     const content=await fs.promises.readFile(full,'utf8'),metadata={};
     const section=content.match(/REM SOFTWARE_META\r?\n([\s\S]*?)REM SOFTWARE_META_END/i)?.[1] || '';
     for(const line of section.split(/\r?\n/)){const match=line.match(/^REM\s+(software_id|display_name|version|status):\s*(.*)$/i);if(match)metadata[match[1].toLowerCase()]=match[2];}
     add(full,'launcher',null,{...metadata,metadata_present:Boolean(section)});return;
    }
    if(!/\.(db|sqlite|sqlite3|duckdb)$/i.test(full))return;
    const fd=await fs.promises.open(full,'r'),buffer=Buffer.alloc(16);try{await fd.read(buffer,0,16,0);}finally{await fd.close();}
    if(buffer.toString('ascii',0,15)==='SQLite format 3'){
     add(full,'database','sqlite');
     if(path.basename(full).toLowerCase()==='action.db')try{registry=registryFacts(full);}catch{problems.push({path:full,code:'REGISTRY_UNREADABLE'});}
    }else if(buffer.toString('ascii',8,12)==='DUCK')add(full,'database','duckdb');
    else problems.push({path:full,code:'DATABASE_SIGNATURE_UNKNOWN'});
   }
  }catch(error){problems.push({path:full,code:error.code || 'DISCOVERY_READ'});}
 };
 for(const scope of scopes){assert(!scope.startsWith('\\\\')&&!scope.startsWith('//'),'DISCOVERY_LOCAL','Only local filesystem roots supported');await visit(path.resolve(root,scope),0);}
 return {schema_version:'workbench.discovery.v1',observed_at:new Date().toISOString(),candidates,registry,problems,visited,truncated:visited>=5000,depth_limit:4,activated:false};
}
module.exports={discover,registryFacts};
