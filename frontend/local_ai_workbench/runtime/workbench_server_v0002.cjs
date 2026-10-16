// ============================================================
// 文件名: workbench_server_v0002.cjs
// 中文名: 本机查询、命令、事件服务与渐进启动
// 版本号: v0002
//
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: 本机查询、命令、事件服务与渐进启动
//
// 职责说明:
// - 本机查询、命令、事件服务与渐进启动
//
// 本脚本做什么:
// - 提供回环 HTTP 查询、命令、SSE 事件和静态视图，协调挂载与工作区状态
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
// alias: workbench_server_v0002
// family: workbench_server
// role: workbench_component
// version: v0002
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_server_v0002.cjs
// input:
//   - project root, private data directory and service config
// output:
//   - authenticated loopback API, event stream and desktop UI
// depends_on:
//   - workbench_store_v0001
//   - workbench_registry_v0001
//   - workbench_projects_v0001
//   - workbench_actions_v0001
//   - workbench_adapters_v0001
//   - workbench_projection_v0001
// used_by:
//   - workbench_desktop_v0001
// ============================================================

'use strict';
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const crypto=require('node:crypto');
const {Store}=require('./workbench_store_v0001.cjs');
const {Registry}=require('./workbench_registry_v0001.cjs');
const {Actions}=require('./workbench_actions_v0001.cjs');
const {Adapters}=require('../adapters/workbench_adapters_v0001.cjs');
const {ProjectionProxy}=require('../visualization/workbench_projection_proxy_v0001.cjs');
const {WorkbenchError,assert,workspace}=require('./workbench_contracts_v0001.cjs');
const {readDatabase}=require('../adapters/workbench_database_adapter_v0001.cjs');
const {discover,registryFacts}=require('../adapters/workbench_discovery_v0001.cjs');
const {Projects}=require('./workbench_projects_v0001.cjs');
const {Studio}=require('./workbench_studio_v0001.cjs');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_server';
const SCRIPT_NAME = 'workbench_server_v0002';
const SCRIPT_VERSION = 'v0002';

// ============================================================
// 配置、输入与响应工具
// ============================================================
function findRoot(start=__dirname) {
  let at=path.resolve(start);
  while(!fs.existsSync(path.join(at,'AGENTS.md'))||!fs.existsSync(path.join(at,'scripts/action'))) {
    const next=path.dirname(at);if(next===at)throw new WorkbenchError('PROJECT_ROOT','Project root not found');at=next;
  }return at;
}
function readConfig(file) {
  const config=JSON.parse(fs.readFileSync(file,'utf8'));
  assert(config.schema_version==='workbench.config.v1','CONFIG_SCHEMA','Invalid configuration version');
  return config;
}
async function body(request) {
  let value='',size=0;
  for await(const chunk of request){size+=chunk.length;if(size>262144)throw new WorkbenchError('BODY_SIZE','Request exceeds size budget',413);value+=chunk;}
  try{return JSON.parse(value || '{}');}catch{throw new WorkbenchError('JSON','Invalid JSON');}
}
function json(response,status,data){
  response.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
  response.end(JSON.stringify(data));
}
// ============================================================
// 服务编排：事实由 Adapter 提供，Core 只管理观察与索引
// ============================================================
async function createServer(options={}) {
  const appRoot=path.resolve(__dirname,'..'),root=options.root || findRoot();
  const config=options.config || readConfig(path.join(appRoot,'config/workbench_config_v0001.json'));
  const directory=options.dataDir || path.join(appRoot,'.runtime');
  const store=new Store(directory),adapters=new Adapters(root,config);
  const registry=new Registry(store,adapters,options.manifestDir || path.join(directory,'manifests'),config);
  if(registry.enabled && config.initial_resources && !store.get('initial_resources_loaded')){
    for(const item of config.initial_resources){
      const existing=path.join(registry.directory,item.resource.id.replace(/:/g,'_')+'.json');
      if(!fs.existsSync(existing))registry.import(item);
    }
    store.put('initial_resources_loaded',true);
  }
  const actions=new Actions(registry,store,adapters,root,{...config,data_directory:directory});
  const projection=new ProjectionProxy(registry,root,store,{...config,data_directory:directory});
  const projects=new Projects(root,registry,store,actions);
  const studio=new Studio(root,store,projects,directory);
  const token=crypto.randomBytes(32).toString('hex'),clients=new Set();
  let origin,refreshTimer,closing=false,databaseSelection=0;
  if(!registry.enabled){const previous=store.get('workspace');if(previous){store.put('previous_business_workspace',previous);store.put('workspace',workspace({...previous,tabs:['home'],active:'home'}));}}
  const snapshots=()=>({schema_version:'workbench.snapshot.v1',cursor:store.cursor(),resources:registry.list(),runs:registry.enabled?store.runs():[],problems:registry.problems,business:{enabled:registry.enabled},workspace:store.get('workspace',workspace({tabs:['home'],active:'home'})),bootstrap:{phase:registry.busy?'discovery':'ready',ui_ready:true,zero_resource:registry.resources.size===1}});
  const server=http.createServer(async(request,response)=>{
    try {
      if(request.headers.host!==new URL(origin).host)throw new WorkbenchError('HOST','Unexpected host',403);
      if(request.headers['sec-fetch-site']==='cross-site')throw new WorkbenchError('ORIGIN','Cross-site access refused',403);
      const url=new URL(request.url,origin),route=url.pathname;
      const agentFeed=route.match(/^\/agent\/([a-zA-Z0-9_.-]+)\/skills$/);
      if(agentFeed&&request.method==='GET')return json(response,200,studio.feed(agentFeed[1],request.headers.authorization));
      if(route==='/api/session'&&request.method==='GET') {
        response.setHeader('Set-Cookie','workbench_session='+token+'; HttpOnly; SameSite=Strict; Path=/');
        return json(response,200,{token});
      }
      if(route.startsWith('/api/')||route==='/events/stream') {
        const cookie=request.headers.cookie?.split(';').some(x=>x.trim()==='workbench_session='+token);
        if(request.headers['x-workbench-token']!==token&&!cookie)throw new WorkbenchError('AUTH','Session required',401);
        if(request.method!=='GET'){
          if(request.headers['x-workbench-token']!==token)throw new WorkbenchError('CSRF','Explicit session header required',403);
          if(request.headers.origin && request.headers.origin!==origin)throw new WorkbenchError('ORIGIN','Unexpected origin',403);
        }
        if(route==='/api/workbench/health')return json(response,200,{service:'local_ai_workbench',version:'0.3.0-alpha',status:'ok'});
        if(route==='/events/stream'){
          assert(request.method==='GET','METHOD','GET required');
          response.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
          response.flushHeaders();
          clients.add(response);
          const send=event=>{if(!response.destroyed&&!response.write('id: '+event.seq+'\nevent: change\ndata: '+JSON.stringify(event)+'\n\n'))response.destroy();};
          let after=Number(request.headers['last-event-id'] || url.searchParams.get('after') || 0);
          if(!Number.isSafeInteger(after)||after<0)after=0;
          const current=store.cursor();
          if(after>current||current-after>500)response.write('event: reset\ndata: {}\n\n');
          else for(const event of store.events(after,500))send(event);
          store.on('event',send);
          const heartbeat=setInterval(()=>response.write(': heartbeat\n\n'),15000);
          request.on('close',()=>{clearInterval(heartbeat);store.off('event',send);clients.delete(response);});
          return;
        }
        if(request.method==='GET') {
          if(route==='/api/workbench/studio')return json(response,200,studio.snapshot());
          if(route==='/api/workbench/studio/tools')return json(response,200,{items:studio.toolCatalog()});
          const skillDetail=route.match(/^\/api\/workbench\/studio\/skills\/([a-zA-Z0-9-]+)$/);
          if(skillDetail)return json(response,200,studio.readSkill(skillDetail[1]));
          if(route==='/api/workbench/user/home')return json(response,200,projects.home());
          if(route==='/api/workbench/user/catalog')return json(response,200,{items:projects.catalog()});
          if(route==='/api/workbench/business/dashboard'){
            if(!registry.enabled)throw new WorkbenchError('BUSINESS_UNLOADED','Load a business context first',409);
            const candidateSource=store.get('discovery_catalog')?.registry?.source;
            const source=registry.list().some(x=>x.entry.path===candidateSource)?candidateSource:null;
            let facts=null,problem=null;try{facts=source?registryFacts(source):null;}catch{problem='Registry unavailable';}
            return json(response,200,{loaded:true,resources:registry.list().filter(x=>x.resource_id!=='workbench.system'),registry:facts,problem,runs:store.runs(),semantics:'Locations and registration are facts; runtime requires independent observation'});
          }
          if(route==='/api/workbench/bootstrap'||route==='/api/workbench/snapshot')return json(response,200,snapshots());
          if(route==='/api/workbench/resources')return json(response,200,{items:registry.list()});
          if(route==='/api/workbench/runs')return json(response,200,{items:store.runs()});
          if(route==='/api/workbench/actions')return json(response,200,{items:store.runs().map(({run_id,resource_id,action,state,result,error})=>({run_id,resource_id,action,state,result,error}))});
          if(route==='/api/workbench/events')return json(response,200,{items:store.events(Math.max(0,store.cursor()-100))});
          if(route==='/api/workbench/system')return json(response,200,registry.public(registry.get('workbench.system')));
          if(route==='/api/workbench/views')return json(response,200,{items:registry.list().flatMap(x=>x.views.map(v=>({...v,resource_id:x.resource_id})))});
          const queryProjection=route.match(/^\/api\/workbench\/views\/([^/]+)\/projection$/);
          if(queryProjection){
            const viewId=decodeURIComponent(queryProjection[1]);
            const owner=registry.list().find(x=>x.views.some(v=>v.view_id===viewId));
            if(!owner)throw new WorkbenchError('VIEW_NOT_FOUND','View not found',404);
            const query=JSON.parse(url.searchParams.get('query') || '{}');
            const controller=new AbortController();response.on('close',()=>{if(!response.writableEnded)controller.abort();});
            return json(response,200,await projection.query(owner.resource_id,query,controller.signal));
          }
          const singleView=route.match(/^\/api\/workbench\/views\/([^/]+)$/);
          if(singleView){
            const id=decodeURIComponent(singleView[1]);
            const owner=registry.list().find(x=>x.views.some(v=>v.view_id===id));
            if(!owner)throw new WorkbenchError('VIEW_NOT_FOUND','View not found',404);
            return json(response,200,{...owner.views.find(v=>v.view_id===id),resource_id:owner.resource_id});
          }
          const detail=route.match(/^\/api\/workbench\/resources\/([^/]+)$/);
          if(detail)return json(response,200,registry.public(registry.get(decodeURIComponent(detail[1]))));
          const logs=route.match(/^\/api\/workbench\/resources\/([^/]+)\/logs$/);
          if(logs)return json(response,200,actions.processLogs(decodeURIComponent(logs[1])));
          const node=route.match(/^\/api\/workbench\/resources\/([^/]+)\/objects\/([^/]+)$/);
          if(node)return json(response,200,await projection.detail(decodeURIComponent(node[1]),decodeURIComponent(node[2])));
          const legacy=route.match(/^\/api\/workbench\/legacy\/(functions|tasks|usage|events|system)$/);
          if(legacy){
            if(!registry.enabled)throw new WorkbenchError('BUSINESS_UNLOADED','Load a business context first',409);
            try{return json(response,200,{items:adapters.legacyData(legacy[1]),source:'legacy',observed_at:new Date().toISOString()});}
            catch{return json(response,503,{error:{code:'LEGACY_UNAVAILABLE',message:'Legacy source unavailable'}});}
          }
        }
        if(request.method==='POST'){
          const input=await body(request);
          const studioRoute=route.match(/^\/api\/workbench\/studio\/(layout|category|add|remove|reset|scan|importSkills|download|toggle|connect|bind|open|stop)$/);
          if(studioRoute)return json(response,200,await studio[studioRoute[1]](input));
          const userRoute=route.match(/^\/api\/workbench\/user\/(mode|select|favorite|remove|open|consume|opened|attach|bind|read|empty)$/);
          if(userRoute)return json(response,200,await projects[userRoute[1]](input));
          if(route==='/api/workbench/business/discover'){const result=await discover(root,input);store.put('discovery_catalog',result);return json(response,200,result);}
          if(route==='/api/workbench/business/load'){
            const catalog=store.get('discovery_catalog');assert(catalog&&Array.isArray(input.candidate_ids)&&input.candidate_ids.length>0&&input.candidate_ids.length<=300,'BUSINESS_SELECTION','Select identified candidates');
            const selected=[...new Set(input.candidate_ids)].map(id=>{const value=catalog.candidates.find(x=>x.id===id);assert(value,'CANDIDATE_MISSING','Rescan missing candidate');return value;});
            await registry.unload();registry.selectedIds=selected.map(x=>x.id);store.put('business_resource_ids',registry.selectedIds);
            for(const item of selected)registry.import(item.manifest);
            await registry.refresh();store.event('business.loaded',{count:selected.length});return json(response,200,{loaded:true,count:selected.length});
          }
          if(route==='/api/workbench/business/unload'){
            await projects.empty();return json(response,200,{loaded:false});
          }
          if(route==='/api/workbench/diagnostics'){
            assert(['RENDERER_ERROR','PROJECTION_ERROR'].includes(input.code)&&typeof input.message==='string','DIAGNOSTIC','Invalid view diagnostic');
            registry.problem(input.code,input.message.slice(0,500),'frontend');return json(response,202,{accepted:true});
          }
          if(route==='/api/workbench/resources'){const result=registry.import(input);void registry.refresh();return json(response,201,result);}
          if(route==='/api/workbench/databases'){
            assert(['sqlite','duckdb','chroma'].includes(input.driver),'DATABASE_DRIVER','Unsupported database driver');
            const mounted=registry.import({schema_version:'workbench.resource.v1',resource:{id:input.resource_id || 'database:'+crypto.randomUUID(),type:'datasource',name:input.name || input.driver},
              adapter:'database',driver:input.driver,path:input.path,capabilities:['inspect','health','attach','detach'],views:[{view_id:(input.resource_id || crypto.randomUUID())+'.table',view_type:'table',renderer:'DOM',data_mode:'page'}]});
            void registry.refresh();return json(response,201,mounted);
          }
          if(route==='/api/workbench/databases/select'){
            const selection=++databaseSelection;
            const item=registry.get(input.resource_id);
            assert(item.manifest.adapter==='database','DATABASE_TYPE','Not a mounted database');
            assert(!registry.detached.has(item.resource_id),'DATABASE_DETACHED','Reconnect this database before selecting it');
            const checked=await readDatabase(root,item.manifest,{},undefined,10000);
            assert(selection===databaseSelection,'DATABASE_SELECTION_SUPERSEDED','A newer database selection superseded this request');
            const context=workspace(store.get('workspace',{}));context.active_database=item.resource_id;store.put('workspace',context);
            store.event('workspace.database_changed',{resource_id:item.resource_id});
            return json(response,200,{active_database:item.resource_id,read_only:checked.read_only});
          }
          const databaseRead=route.match(/^\/api\/workbench\/databases\/([^/]+)\/read$/);
          const databaseView=route.match(/^\/api\/workbench\/databases\/([^/]+)\/view$/);
          if(databaseView){
            const item=registry.get(decodeURIComponent(databaseView[1]));assert(item.manifest.adapter==='database','DATABASE_TYPE','Not a database');
            assert(typeof input.table==='string'&&input.table.length<200,'DATABASE_TABLE','Select a table');
            assert(input.keys===undefined||(Array.isArray(input.keys)&&input.keys.length<=12&&input.keys.every(x=>typeof x==='string'&&x.length<200)),'DATABASE_KEYS','Invalid key columns');
            const catalog=await readDatabase(root,item.manifest,{});assert(catalog.tables.some(x=>x.name===input.table&&x.schema===(input.schema || 'main')),'DATABASE_TABLE','Table not found');
            const result=registry.import({...item.manifest,projection:{table:input.table,schema:input.schema || 'main',keys:input.keys},views:[...item.views.filter(x=>x.view_type!=='graph'),{view_id:item.resource_id+'.records',view_type:'graph',renderer:'Canvas',data_mode:'viewport'}]});
            return json(response,200,result);
          }
          if(databaseRead){
            const item=registry.get(decodeURIComponent(databaseRead[1]));
            assert(item.manifest.adapter==='database','DATABASE_TYPE','Not a mounted database');
            assert(!registry.detached.has(item.resource_id),'DATABASE_DETACHED','Database observation is disconnected');
            const controller=new AbortController();response.on('close',()=>{if(!response.writableEnded)controller.abort();});
            return json(response,200,await readDatabase(root,item.manifest,input,controller.signal,10000));
          }
          if(route==='/api/workbench/discover'){void registry.refresh().catch(()=>{});return json(response,202,{accepted:true});}
          if(route==='/api/workbench/workspace'){const result=workspace(input);store.put('workspace',result);store.event('workspace.changed',{});return json(response,200,result);}
          if(route==='/api/workbench/actions')return json(response,202,actions.submit(input));
          const cancel=route.match(/^\/api\/workbench\/runs\/([^/]+)\/cancel$/);
          if(cancel)return json(response,200,actions.cancel(cancel[1]));
          const project=route.match(/^\/api\/workbench\/views\/([^/]+)\/projection$/);
          if(project){
            const viewId=decodeURIComponent(project[1]);
            const resource=[...registry.resources.values()].find(x=>x.views.some(v=>v.view_id===viewId));
            if(!resource)throw new WorkbenchError('VIEW_NOT_FOUND','View not found',404);
            const controller=new AbortController();response.on('close',()=>{if(!response.writableEnded)controller.abort();});
            return json(response,200,await projection.query(resource.resource_id,input,controller.signal));
          }
          if(route==='/api/workbench/shutdown'){json(response,200,{stopping:true});setImmediate(()=>close());return;}
        }
        throw new WorkbenchError('ROUTE','Unknown route or method',404);
      }
      if(request.method!=='GET'&&request.method!=='HEAD')throw new WorkbenchError('METHOD','Method not allowed',405);
      // Only presentation files are exposed; runtime, config and dependencies stay private.
      const relative=route==='/'?'workspace/studio.html':route==='/legacy'?'workspace/legacy.html':decodeURIComponent(route.slice(1));
      assert(/^(workspace|visualization)\//.test(relative)&&!relative.endsWith('.cjs'),'STATIC_PATH','Private asset');
      const target=path.resolve(appRoot,relative);
      assert(target.startsWith(appRoot+path.sep),'STATIC_PATH','Invalid asset path');
      const type={'.html':'text/html','.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml'}[path.extname(target)];
      assert(type,'STATIC_TYPE','Unsupported asset type');
      const bytes=await fs.promises.readFile(target);
      response.writeHead(200,{'Content-Type':type+'; charset=utf-8','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",'X-Content-Type-Options':'nosniff','X-Frame-Options':'SAMEORIGIN'});
      response.end(request.method==='HEAD'?undefined:bytes);
    }catch(error){
      if(response.headersSent){response.end();return;}
      json(response,error.http || (error.code==='ENOENT'?404:400),{error:{code:error.code || 'INTERNAL',message:error instanceof WorkbenchError?error.message:'Request failed'}});
    }
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(options.port ?? config.port ?? 0,'127.0.0.1',resolve);});
  origin='http://127.0.0.1:'+server.address().port;
  // UI and snapshot are available before discovery and probes.
  const ready=new Promise(resolve=>setImmediate(()=>registry.refresh().catch(error=>registry.problem('BOOTSTRAP_FAILED','Discovery failed')).finally(resolve)));
  refreshTimer=setInterval(()=>registry.refresh().catch(()=>{}),Math.max(1000,config.refresh_ms || 30000));
  async function close(){
    if(closing)return;closing=true;clearInterval(refreshTimer);
    for(const client of clients)client.end();
    await actions.close();await projection.close();await registry.close();
    await new Promise(resolve=>server.close(resolve));store.close();
    options.onClose?.();
  }
  return {server,origin,token,store,registry,actions,projection,studio,ready,close};
}
// ============================================================
// CLI / main
// ============================================================
async function main(){
  const args=process.argv.slice(2),value=name=>args.includes(name)?args[args.indexOf(name)+1]:undefined;
  const config=readConfig(value('--config') || path.join(__dirname,'../config/workbench_config_v0001.json'));
  if(args.includes('--dry-run')){findRoot();process.stdout.write(JSON.stringify({status:'dry-run',config_valid:true})+'\n');return;}
  const app=await createServer({config,root:value('--root'),dataDir:value('--data-dir'),port:value('--port')===undefined?undefined:Number(value('--port')),onClose:()=>process.exit(0)});
  process.stdout.write(JSON.stringify({status:'started',url:app.origin,pid:process.pid})+'\n');
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>app.close());
}
if(require.main===module)main().catch(error=>{process.stderr.write(JSON.stringify({status:'error',error_type:error.code || 'ServerError',detail:error.message})+'\n');process.exitCode=2;});
module.exports={createServer,findRoot,readConfig};
