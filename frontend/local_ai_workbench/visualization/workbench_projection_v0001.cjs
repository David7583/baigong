// ============================================================
// 文件名: workbench_projection_v0001.cjs
// 中文名: 有界数据投影、聚类、视口与对象溯源
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / visualization
// 脚本定位: 有界数据投影、聚类、视口与对象溯源
//
// 职责说明:
// - 有界数据投影、聚类、视口与对象溯源
//
// 本脚本做什么:
// - 流式建立派生空间索引，执行视口裁剪、分层聚合和独立边预算
//
// 本脚本不做什么:
// - 不替代独立业务资源，不自动授权或调用模型
//
// 制度边界声明:
// - 仅工作台配置和索引可写；测试写入独立临时目录，原始业务数据只读
// - 失败保留分类诊断，不返回伪成功；依赖由项目局部安装
//
// 可更新: True
// ============================================================

// ============================================================
// ALIAS_META
// ============================================================
// alias: workbench_projection_v0001
// family: workbench_projection
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/visualization/workbench_projection_v0001.cjs
// input:
//   - resource JSONL or registry source, viewport, zoom and filter
// output:
//   - bounded projection and original-object provenance
// depends_on:
//   - Node.js standard library
//   - workbench_contracts_v0001
//   - workbench_database_adapter_v0001
// used_by:
//   - workbench_server_v0001
// ============================================================

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');
const {DatabaseSync}=require('node:sqlite');
const {inside,assert,WorkbenchError} = require('../runtime/workbench_contracts_v0001.cjs');
const {exportDatabase,readDatabase,databasePath}=require('../adapters/workbench_database_adapter_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_projection';
const SCRIPT_NAME = 'workbench_projection_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// 磁盘投影索引：无应用级总节点数上限，内存和响应量有预算
// ============================================================
class ProjectionService {
  constructor(registry,root,store,config={}) {
    this.registry=registry;this.root=root;this.store=store;this.cache=new Map();this.builds=new Map();
    this.exports=new Map();
    this.nodeBudget=config.node_budget || 600;this.edgeBudget=config.edge_budget || 300;this.closed=false;
    this.directory=path.join(config.data_directory || path.join(root,'frontend/local_ai_workbench/.runtime'),'projections');
    fs.mkdirSync(this.directory,{recursive:true});
  }
  async source(id,signal) {
    const resource=this.registry.get(id);
    if(resource.manifest.adapter==='database')return this.databaseSource(resource,signal);
    if(resource.manifest.adapter!=='dataset')return this.registrySource();
    const file=inside(this.root,resource.manifest.path),stat=fs.statSync(file);
    const revision=stat.size+':'+stat.mtimeMs;
    const key=crypto.createHash('sha256').update(id+':'+revision).digest('hex');
    if(this.cache.has(key))return this.cache.get(key);
    if(this.builds.has(key))return this.builds.get(key);
    const promise=this.build(resource,file,key,revision,signal);
    this.builds.set(key,promise);
    try{return await promise;}finally{this.builds.delete(key);}
  }
  schema(db) {
    db.exec('PRAGMA cache_size=-8192; PRAGMA temp_store=FILE; CREATE TABLE IF NOT EXISTS nodes(row_id INTEGER PRIMARY KEY,id TEXT UNIQUE,x REAL,y REAL,label TEXT,kind TEXT,source_line INTEGER); CREATE VIRTUAL TABLE IF NOT EXISTS spatial USING rtree(row_id,x0,x1,y0,y1); CREATE TABLE IF NOT EXISTS edges(source TEXT,target TEXT); CREATE INDEX IF NOT EXISTS edges_source ON edges(source); CREATE INDEX IF NOT EXISTS edges_target ON edges(target); CREATE TABLE IF NOT EXISTS info(key TEXT PRIMARY KEY,value TEXT)');
    db.exec('CREATE TABLE IF NOT EXISTS provenance(id TEXT PRIMARY KEY,value TEXT NOT NULL)');
  }
  async databaseSource(resource,signal){
    const m=resource.manifest,selection=m.projection;assert(selection?.table,'DATABASE_VIEW','Select a table before opening its record map');
    let source=databasePath(this.root,m.path);if(m.driver==='chroma'&&fs.statSync(source).isDirectory())source=path.join(source,'chroma.sqlite3');
    const revisionOf=()=>[source,selection,...[source,source+'-wal'].map(file=>fs.existsSync(file)?[fs.statSync(file).size,fs.statSync(file).mtimeMs]:null)];
    const revision=JSON.stringify(revisionOf()),key=crypto.createHash('sha256').update(revision).digest('hex');
    if(this.cache.has(key))return this.cache.get(key);
    if(this.exports.has(key))return this.exports.get(key);
    const build=(async()=>{
      const file=path.join(this.directory,key+'.jsonl');
      if(!fs.existsSync(file))await exportDatabase(this.root,m,selection,file,signal);
      assert(JSON.stringify(revisionOf())===revision,'SOURCE_CHANGED','Source changed during export; retry');
      const stat=fs.statSync(file);return this.build(resource,file,key,stat.size+':'+stat.mtimeMs,signal);
    })();this.exports.set(key,build);
    try{return await build;}finally{this.exports.delete(key);}
  }
  registrySource(){
    const db=new DatabaseSync(':memory:');this.schema(db);
    const insert=db.prepare('INSERT INTO nodes(id,x,y,label,kind,source_line) VALUES(?,?,?,?,?,?)');
    for(const [i,item] of this.registry.list().entries()){const row=insert.run(item.resource_id,(i%20)*40,Math.floor(i/20)*40,item.display_name,item.resource_type,0);db.prepare('INSERT INTO spatial VALUES(?,?,?,?,?)').run(row.lastInsertRowid,(i%20)*40,(i%20)*40,Math.floor(i/20)*40,Math.floor(i/20)*40);}
    return {db,revision:this.store.cursor(),source:'resource-registry',transient:true};
  }
  async build(resource,file,key,revision,signal){
    const target=path.join(this.directory,key+'.sqlite');
    let db=new DatabaseSync(target);this.schema(db);
    if(db.prepare("SELECT value FROM info WHERE key='complete'").get()?.value==='yes')return this.keep(key,{db,revision,source:resource.manifest.path});
    // Incomplete derived indexes are rebuilt transactionally; raw JSONL never changes.
    db.exec('DELETE FROM nodes; DELETE FROM spatial; DELETE FROM edges; DELETE FROM provenance; BEGIN');
    const insert=db.prepare('INSERT INTO nodes(id,x,y,label,kind,source_line) VALUES(?,?,?,?,?,?)');
    const position=db.prepare('INSERT INTO spatial VALUES(?,?,?,?,?)');
    const edge=db.prepare('INSERT INTO edges VALUES(?,?)');
    const stream=fs.createReadStream(file,{encoding:'utf8'});
    const lines=readline.createInterface({input:stream,crlfDelay:Infinity});
    let line=0;
    try{
      for await(const text of lines){
        if(signal?.aborted||this.closed)throw new WorkbenchError('CANCELLED','Projection indexing cancelled',409);
        line++;if(!text.trim())continue;
        assert(text.length<65536,'DATASET_LINE','Dataset line exceeds budget');
        let value;try{value=JSON.parse(text);}catch{throw new WorkbenchError('DATASET_JSON','Invalid JSON at line '+line);}
        if(value.type==='edge')edge.run(String(value.source),String(value.target));
        else{
          assert(typeof value.id==='string'&&value.id.length<=128&&Number.isFinite(value.x)&&Number.isFinite(value.y)&&Math.abs(value.x)<1e30&&Math.abs(value.y)<1e30,'DATASET_NODE','Nodes require stable id and finite coordinates');
          const row=insert.run(value.id,value.x,value.y,String(value.label || value.id).slice(0,200),String(value.kind || 'record'),line);
          position.run(row.lastInsertRowid,value.x,value.x,value.y,value.y);
          if(value.provenance)db.prepare('INSERT INTO provenance VALUES(?,?)').run(value.id,JSON.stringify(value.provenance));
        }
        if(line%2000===0){db.exec('COMMIT; BEGIN');await new Promise(resolve=>setImmediate(resolve));}
      }
      const after=fs.statSync(file);
      assert(after.size+':'+after.mtimeMs===revision,'SOURCE_CHANGED','Source changed during indexing; retry required');
      db.prepare("INSERT OR REPLACE INTO info VALUES('complete','yes')").run();db.exec('COMMIT');
      this.store.event('projection.updated',{resource_id:resource.resource_id,revision,count:db.prepare('SELECT COUNT(*) AS n FROM nodes').get().n});
      return this.keep(key,{db,revision,source:resource.manifest.path});
    }catch(error){try{db.exec('ROLLBACK');}catch{}db.close();throw error;}
    finally{lines.close();stream.destroy();}
  }
  keep(key,value){
    if(this.cache.size>=3){const first=this.cache.keys().next().value;this.cache.get(first).db.close();this.cache.delete(first);}
    this.cache.set(key,value);return value;
  }
  async query(id,query={},signal) {
    const start=performance.now(),data=await this.source(id,signal),db=data.db;
    try{
      const zoom=Math.max(.01,Math.min(20,Number(query.zoom)||.25));
      const lod=zoom<.12?0:zoom<.3?1:zoom<.7?2:zoom<2?3:4;
      const bounds=query.bounds || {x0:-1e30,y0:-1e30,x1:1e30,y1:1e30};
      assert(['x0','y0','x1','y1'].every(k=>Number.isFinite(bounds[k]))&&bounds.x0<=bounds.x1&&bounds.y0<=bounds.y1,'VIEWPORT','Invalid viewport bounds');
      const filter=String(query.filter || '').slice(0,200).toLowerCase();
      const params=[bounds.x0,bounds.x1,bounds.y0,bounds.y1,filter,filter,filter];
      const from=" FROM spatial s JOIN nodes n ON n.row_id=s.row_id WHERE s.x1>=? AND s.x0<=? AND s.y1>=? AND s.y0<=? AND (?='' OR instr(lower(n.label),?)>0 OR instr(lower(n.id),?)>0)";
      const summary=db.prepare('SELECT COUNT(*) AS count,MIN(n.x) AS xmin,MAX(n.x) AS xmax,MIN(n.y) AS ymin,MAX(n.y) AS ymax'+from).get(...params);
      const total=db.prepare('SELECT COUNT(*) AS count FROM nodes').get().count;
      const aggregated=lod<3||summary.count>this.nodeBudget;
      let nodes=[],edges=[];
      if(aggregated&&summary.count){
        const cells=lod===0?4:lod===1?8:16;
        const size=Math.max(1,(summary.xmax-summary.xmin+1)/cells,(summary.ymax-summary.ymin+1)/cells);
        nodes=db.prepare('SELECT CAST((n.x-?)/? AS INTEGER) AS gx,CAST((n.y-?)/? AS INTEGER) AS gy,AVG(n.x) AS x,AVG(n.y) AS y,COUNT(*) AS count,MIN(n.x) AS x0,MAX(n.x) AS x1,MIN(n.y) AS y0,MAX(n.y) AS y1'+from+' GROUP BY gx,gy LIMIT ?').all(summary.xmin,size,summary.ymin,size,...params,this.nodeBudget).map(g=>({id:'cluster:'+g.gx+':'+g.gy,kind:'cluster',x:g.x,y:g.y,count:g.count,label:g.count+' records',bounds:{x0:g.x0,x1:g.x1,y0:g.y0,y1:g.y1}}));
      }else{
        nodes=db.prepare('SELECT n.id,n.x,n.y,n.label,n.kind,n.source_line'+from+' ORDER BY n.id LIMIT ?').all(...params,this.nodeBudget).map(n=>({...n,source:data.source}));
        if(nodes.length){
          const ids=nodes.map(n=>n.id),marks=ids.map(()=>'?').join(',');
          edges=db.prepare('SELECT source,target FROM edges WHERE source IN ('+marks+') AND target IN ('+marks+')'+(query.selection?' AND (source=? OR target=?)':'')+' LIMIT ?').all(...ids,...ids,...(query.selection?[query.selection,query.selection]:[]),this.edgeBudget);
        }
      }
      const allBounds=db.prepare('SELECT MIN(x) AS x0,MAX(x) AS x1,MIN(y) AS y0,MAX(y) AS y1 FROM nodes').get();
      return {schema_version:'workbench.projection.v1',resource_id:id,revision:data.revision,lod,aggregated,source_count:total,visible_count:summary.count,nodes,edges,extent:allBounds,layout:this.registry.get(id).manifest.adapter==='database'?'ordered-record-grid':'source-coordinates',budgets:{nodes:this.nodeBudget,edges:this.edgeBudget},storage:'sqlite-rtree',truncated:!aggregated&&summary.count>nodes.length,elapsed_ms:performance.now()-start};
    }finally{if(data.transient)db.close();}
  }
  async detail(id,nodeId,signal){
    const data=await this.source(id,signal);
    try{const node=data.db.prepare('SELECT id,x,y,label,kind,source_line FROM nodes WHERE id=?').get(nodeId);if(!node)throw new WorkbenchError('NODE_NOT_FOUND','Object not found',404);const raw=data.db.prepare('SELECT value FROM provenance WHERE id=?').get(nodeId);if(raw){const provenance=JSON.parse(raw.value);const result=await readDatabase(this.root,this.registry.get(id).manifest,{operation:'record',table:provenance.table,schema:provenance.schema,keys:provenance.keys},signal);return {...node,provenance,record:result.record,source:provenance.source};}return {...node,source:data.source};}
    finally{if(data.transient)data.db.close();}
  }
  async close(){
    this.closed=true;await Promise.allSettled([...this.builds.values()]);
    for(const value of this.cache.values())value.db.close();this.cache.clear();
  }
}
module.exports={ProjectionService};
