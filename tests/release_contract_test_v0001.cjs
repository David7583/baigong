// ============================================================
// 文件名: release_contract_test_v0001.cjs
// 中文名: 空工作台发布链路验收
// 版本号: v0001
// 主层级: action
// 层级: release / tests
// 脚本定位: 隔离副本 API 集成验证
// 职责说明: 验证聊天、加载状态、身份与静态资源边界。
// 本脚本做什么: 操作测试安装中的空状态；保留失败证据。
// 本脚本不做什么: 不连接真实模型、不使用用户数据库。
// 制度边界声明: 必须在运行副本中显式设置 RELEASE_TEST_ROOT。
// 可更新: True
// ALIAS_META
// alias: release_contract_test_v0001
// family: release_contract_test
// role: release_acceptance
// version: v0001
// status: staging
// entry_point: tests/release_contract_test_v0001.cjs
// input: RELEASE_TEST_ROOT, WORKBENCH_FRONTEND_ROOT
// output: test assertions
// depends_on: Node stdlib, prepared isolated backend
// used_by: release operator
// ============================================================
'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const SCRIPT_NAME='release_contract_test_v0001',SCRIPT_FAMILY='release_contract_test',SCRIPT_VERSION='v0001',DEFAULT_ENCODING='utf8';
test('empty installation, chat, persistence and unload/reload',async()=>{
 const root=path.resolve(process.env.RELEASE_TEST_ROOT||'');
 assert.ok(process.env.RELEASE_TEST_ROOT,'Use an isolated installed run copy');
 const cp=require('node:child_process'),originalSpawn=cp.spawn,launches=[];
 cp.spawn=function(executable,args,options){if(args?.some(x=>String(x).includes('workbench_chat_host_v0003.py')))launches.push({executable,options});return originalSpawn.apply(this,arguments);};
 const {createServer}=require(path.join(root,'frontend/local_ai_workbench/runtime/workbench_server_v0017.cjs'));
 let app=await createServer({root,dataDir:path.join(root,'runtime/release-test-ui')});
 let chat;
 const get=async(route)=>{const r=await fetch(app.origin+'/api/workbench/'+route,{headers:{'X-Workbench-Token':app.token}});const v=await r.json();assert.equal(r.status,200,route+': '+JSON.stringify(v));return v;};
 try{
  await app.ready;
  assert.deepEqual(app.studio.toolCatalog().map(x=>x.id),['tool.action-chat']);
  for(const route of ['health','studio','capabilities','user/home','features','adapters','workflows','environment','ai/inventory']){
   const value=await get(route);assert.ok(value);
  }
  const page=await fetch(app.origin+'/');assert.equal(page.status,200);assert.match(await page.text(),/Local AI Workbench/);
  const privateFile=await fetch(app.origin+'/runtime/workbench_server_v0017.cjs');assert.notEqual(privateFile.status,200);
  const opening=await fetch(app.origin+'/api/workbench/studio/open',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':app.token},body:JSON.stringify({id:'tool.action-chat'})});
  chat=await opening.json();assert.equal(opening.status,200,JSON.stringify(chat));
  if(process.platform==='win32'){assert.ok(launches.length);assert.match(launches[0].executable,/pythonw\.exe$/i);assert.equal(launches[0].options.windowsHide,true);assert.equal(launches[0].options.detached,false);}
  assert.ok(!app.studio.categories().some(x=>x.id==='growth'));
  assert.equal((await fetch(app.origin+'/api/workbench/growth',{headers:{'X-Workbench-Token':app.token}})).status,404);
  const status=await fetch(chat.url+'api/status').then(r=>r.json());assert.equal(status.server_version,'v0014');
  const permission=await fetch(chat.url+'api/admin/ai-permissions').then(r=>r.json());
  assert.equal(permission.global_enabled,false);assert.equal(permission.app_allowed,false);
  const chatPage=await fetch(chat.url);assert.equal(chatPage.status,200);assert.match(await chatPage.text(),/html/i);
  const save=await fetch(chat.url+'api/chat/save',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json());assert.equal(save.verified,true);
  app.studio.remove({id:'tool.action-chat'});assert.equal(app.studio.toolCatalog().length,0);
  const found=await app.studio.scan({category:'tools',discover_only:true});assert.ok(found.found.some(x=>x.id==='tool.action-chat'));
  // Loading only the selected chat must not load the text inspector or business apps.
  app.store.put('studio_items',found.found.filter(x=>x.id==='tool.action-chat'));
  const stopped=await app.studio.stop({id:'tool.action-chat'});assert.equal(stopped.stopped,true);chat=null;
  app.store.put('release_test_marker','persistent');await app.close();
  app=await createServer({root,dataDir:path.join(root,'runtime/release-test-ui')});
  assert.equal(app.store.get('release_test_marker'),'persistent');assert.deepEqual(app.studio.toolCatalog().map(x=>x.id),['tool.action-chat']);
 }finally{if(chat)await app.studio.stop({id:'tool.action-chat'}).catch(()=>{});await app.close();cp.spawn=originalSpawn;}
});
