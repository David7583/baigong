// ============================================================
// 文件名: workbench_contracts_v0001.cjs
// 中文名: 资源、视图、操作与布局契约校验
// 版本号: v0001
//
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: 资源、视图、操作与布局契约校验
//
// 职责说明:
// - 资源、视图、操作与布局契约校验
//
// 本脚本做什么:
// - 校验 Manifest、标识、路径、回环地址与工作区配置，归一化视图描述
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
// alias: workbench_contracts_v0001
// family: workbench_contracts
// role: workbench_component
// version: v0001
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_contracts_v0001.cjs
// input:
//   - resource manifests and workspace preferences
// output:
//   - normalized contracts or WorkbenchError
// depends_on:
//   - Node.js standard library
// used_by:
//   - workbench_registry_v0001
//   - workbench_server_v0001
// ============================================================

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const DEFAULT_ENCODING = 'utf8';
const SCRIPT_FAMILY = 'workbench_contracts';
const SCRIPT_NAME = 'workbench_contracts_v0001';
const SCRIPT_VERSION = 'v0001';

// ============================================================
// 错误类型与版本化契约
// ============================================================
class WorkbenchError extends Error {
  constructor(code, message, http = 400) { super(message); this.code = code; this.http = http; }
}
const ID = /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/;
const STATES = Object.freeze({
  health: ['available', 'degraded', 'unavailable', 'unknown'],
  runtime: ['running', 'stopped', 'starting', 'stopping', 'unknown', 'not_applicable'],
  connection: ['connected', 'disconnected', 'connecting', 'error', 'not_applicable'],
  run: ['queued', 'running', 'completed', 'failed', 'cancelled', 'paused'],
});
function assert(ok, code, message) { if (!ok) throw new WorkbenchError(code, message); }
function identifier(value) { assert(typeof value === 'string' && ID.test(value), 'INVALID_ID', 'Invalid object identifier'); return value; }
function inside(root, relative, mustExist = true) {
  assert(typeof relative === 'string' && relative.length > 0 && !path.isAbsolute(relative), 'INVALID_PATH', 'A project-relative path is required');
  const target = path.resolve(root, relative), base = fs.realpathSync(root);
  assert(target.startsWith(path.resolve(root) + path.sep), 'PATH_ESCAPE', 'Path is outside the project');
  if (mustExist) assert(fs.existsSync(target), 'PATH_MISSING', 'Declared source is missing');
  if (fs.existsSync(target)) assert(fs.realpathSync(target).startsWith(base + path.sep), 'PATH_ESCAPE', 'Symbolic path escapes project');
  return target;
}
function localUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new WorkbenchError('INVALID_URL', 'Invalid endpoint'); }
  assert(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && !url.username && !url.password && !url.hash, 'UNSAFE_URL', 'Only credential-free loopback HTTP endpoints are supported');
  return url;
}
function manifest(input) {
  assert(input && input.schema_version === 'workbench.resource.v1', 'MANIFEST_SCHEMA', 'Expected workbench.resource.v1');
  assert(input.resource && typeof input.resource === 'object', 'MANIFEST_RESOURCE', 'Resource is required');
  const id = identifier(input.resource.id), type = identifier(input.resource.type);
  assert(typeof input.resource.name === 'string' && input.resource.name.trim().length > 0 && input.resource.name.length <= 200, 'MANIFEST_NAME', 'Resource name is required');
  assert(Array.isArray(input.capabilities) && input.capabilities.length <= 40 && input.capabilities.every(x => typeof x === 'string' && /^[a-z][a-z0-9_.]*$/.test(x)), 'MANIFEST_CAPABILITY', 'Invalid capabilities');
  const adapter = input.adapter || 'http';
  assert(['system','http','sqlite','database','process','dataset','legacy'].includes(adapter), 'MANIFEST_ADAPTER', 'Unknown adapter');
  if(adapter==='database')assert(['sqlite','duckdb','chroma'].includes(input.driver)&&typeof input.path==='string','MANIFEST_DATABASE','Database driver and path are required');
  if (input.endpoint) localUrl(input.endpoint);
  const rawViews = input.views || [{view_id: id + '.overview', view_type:'table', renderer:'DOM', data_mode:'snapshot'}];
  assert(Array.isArray(rawViews) && rawViews.length <= 20, 'MANIFEST_VIEW', 'Invalid views');
  const renderers={dom:'DOM',svg:'SVG',canvas:'Canvas',webgl:'WebGL','three.js':'Three.js',pixijs:'PixiJS',custom:'custom','custom renderer':'custom'};
  const views=rawViews.map(value=>{
    const view=typeof value==='string'?{view_id:value,view_type:'table',renderer:'DOM',data_mode:'snapshot'}:value;
    assert(view&&typeof view==='object','MANIFEST_VIEW','View descriptor required');
    const renderer=renderers[String(view.renderer || 'DOM').toLowerCase()];
    assert(renderer,'MANIFEST_VIEW','Invalid renderer');
    return {...view,renderer};
  });
  const seen = new Set();
  for (const view of views) {
    identifier(view.view_id);
    assert(!seen.has(view.view_id), 'MANIFEST_VIEW', 'Duplicate view');
    seen.add(view.view_id);
    assert(['table','list','timeline','canvas','graph','3d','dashboard','terminal','embedded','custom'].includes(view.view_type), 'MANIFEST_VIEW', 'Invalid view type');
    assert(['snapshot','viewport','stream','page'].includes(view.data_mode || 'snapshot'), 'MANIFEST_VIEW', 'Invalid data mode');
  }
  if (input.commands) {
    assert(typeof input.commands === 'object' && !Array.isArray(input.commands), 'MANIFEST_COMMAND', 'Invalid commands');
    for (const [name, cmd] of Object.entries(input.commands)) {
      assert(input.capabilities.includes(name), 'MANIFEST_COMMAND', 'Undeclared command');
      localUrl(cmd.url);
      assert(['GET','POST'].includes(cmd.method || 'POST'), 'MANIFEST_COMMAND', 'Invalid command method');
    }
  }
  return { ...input, adapter, views, resource: {id, type, name: input.resource.name} };
}
function layout(value = {}) {
  const number = (key, fallback, low, high) => Number.isFinite(value[key]) ? Math.min(high, Math.max(low, value[key])) : fallback;
  return {
    left_width:number('left_width',250,180,440), right_width:number('right_width',300,200,480),
    bottom_height:number('bottom_height',190,100,450),
    left_collapsed: value.left_collapsed === true, right_collapsed:value.right_collapsed === true,
    bottom_collapsed:value.bottom_collapsed === true, toolbar_collapsed:value.toolbar_collapsed === true,
    focus_mode:value.focus_mode === true, theme:['dark','light'].includes(value.theme)?value.theme:'dark',
    reduced_motion:value.reduced_motion === true,
  };
}
function workspace(value) {
  assert(value && typeof value === 'object', 'WORKSPACE_SCHEMA', 'Workspace required');
  const tabs = Array.isArray(value.tabs) ? value.tabs.slice(0,20).filter(x=>typeof x==='string' && ID.test(x)) : ['home'];
  return {schema_version:'workbench.workspace.v1', tabs:[...new Set(tabs)], active:tabs.includes(value.active)?value.active:(tabs[0] || 'home'),
    layout:layout(value.layout), cameras: value.cameras && typeof value.cameras==='object'?value.cameras:{},
    favorites:Array.isArray(value.favorites)?value.favorites.filter(x=>typeof x==='string' && ID.test(x)).slice(0,100):[],
    explorer_category:typeof value.explorer_category==='string'&&ID.test(value.explorer_category)?value.explorer_category:'',
    active_database:typeof value.active_database==='string'&&ID.test(value.active_database)?value.active_database:null};
}
module.exports = {WorkbenchError, assert, identifier, inside, localUrl, manifest, layout, workspace, STATES};
