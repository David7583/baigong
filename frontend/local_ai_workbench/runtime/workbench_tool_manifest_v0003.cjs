// ============================================================
// 文件名: workbench_tool_manifest_v0003.cjs
// 中文名: 独立工具声明扫描与嵌入启动
// 版本号: v0003
//
// 主层级: action
// 层级: workbench / runtime / tools
// 脚本定位: tool.manifest.json 工具接入契约的执行适配器
//
// 职责说明:
// - 统一发现独立工具，按显式打开操作启动并嵌入其本地界面
//
// 本脚本做什么:
// - 验证声明、运行入口、身份与就绪信号，复用独立后台
//
// 本脚本不做什么:
// - 不安装依赖，不执行远程地址，不在扫描时启动进程
//
// 制度边界声明:
// - 限项目内脚本与无窗口启动，拒绝链接逃逸、错误身份与非回环地址
// - 关闭工作台不停止独立工具；停止需对应工具实例认证
//
// 可更新: True
// ============================================================

// ============================================================
// ALIAS_META
// ============================================================
// alias: workbench_tool_manifest_v0003
// family: workbench_tool_manifest
// role: independent_tool_manifest_adapter
// version: v0003
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_tool_manifest_v0003.cjs
// input:
//   - tool.v1 local manifest and explicit open or stop
// output:
//   - catalog and verified embedded loopback endpoint
// depends_on:
//   - workbench_contracts_v0001
// used_by:
//   - workbench_tools_v0002
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {assert,localUrl}=require('./workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING='utf8',SCRIPT_FAMILY='workbench_tool_manifest',SCRIPT_NAME='workbench_tool_manifest_v0003',SCRIPT_VERSION='v0003';
class ManifestTools {
 constructor(root,store,directory){this.root=path.resolve(root);this.store=store;this.directory=directory;this.node=path.join(this.root,".tools/node/node.exe");this.python=require('./release_paths_v0001.cjs').python(this.root);}
 inside(file){const value=fs.realpathSync(file);assert(value.startsWith(this.root+path.sep),'TOOL_PATH','工具入口必须在当前项目内');return value;}
 read(file){file=this.inside(file);assert(path.basename(file)==='tool.manifest.json'&&fs.statSync(file).size<=16384,'TOOL_MANIFEST','工具声明文件无效');const value=JSON.parse(fs.readFileSync(file,'utf8'));assert(value.schema_version==='tool.v1'&&typeof value.id==='string'&&/^tool\.[\w.-]{1,70}$/.test(value.id)&&typeof value.name==='string'&&value.name.length<=100,'TOOL_MANIFEST','工具声明格式错误');assert(['python','node'].includes(value.runtime)&&typeof value.entry==='string'&&!path.isAbsolute(value.entry),'TOOL_RUNTIME','请选择项目内 Python 或 Node 入口');const entry=this.inside(path.resolve(path.dirname(file),value.entry));assert(['.py','.js','.cjs'].includes(path.extname(entry)),'TOOL_ENTRY','工具入口类型无效');assert(value.args===undefined||(Array.isArray(value.args)&&value.args.length<=20&&value.args.every(x=>typeof x==='string'&&x.length<=1000)),'TOOL_ARGS','工具参数无效');return {...value,entry,manifest:file};}
 scan(directory){const bases=directory?[this.inside(directory)]:['scripts/action/development/scripts','frontend/local_ai_workbench/tools'].map(x=>path.join(this.root,x));const found=[];let count=0;const walk=(folder,depth)=>{if(!fs.existsSync(folder)||depth>8)return;assert(++count<=4000,'TOOL_SCAN_LIMIT','请缩小工具扫描范围');for(const entry of fs.readdirSync(folder,{withFileTypes:true})){if(entry.isSymbolicLink())continue;if(entry.isDirectory()&&!['node_modules','.venv','.git','__pycache__','outputs','runtime','data'].includes(entry.name))walk(path.join(folder,entry.name),depth+1);else if(entry.isFile()&&entry.name==='tool.manifest.json'){try{const value=this.read(path.join(folder,entry.name));assert(!['tool.action-chat','tool.text-inspector'].includes(value.id)&&!found.some(x=>x.id===value.id),'TOOL_DUPLICATE','工具标识已占用');found.push({id:value.id,name:value.name,description:value.description||'独立声明工具',version:value.version||'unversioned',category:'tools',source:'local-scan',integration:'manifest',manifest:value.manifest,available:true,native:false});}catch(error){found.push({id:'tool.invalid-'+crypto.createHash('sha256').update(path.join(folder,entry.name)).digest('hex').slice(0,16),name:'工具声明待修复',category:'tools',source:'local-scan',available:false,description:error.message});}}}};for(const base of bases)walk(base,0);return found;}
 async healthy(state,item){try{localUrl(state.url);const health=await fetch(state.url+'health',{signal:AbortSignal.timeout(1000)}).then(r=>r.json());return health.instance===state.instance&&health.tool===item.id;}catch{return false;}}
 async open(item){assert(item.available!==false,'TOOL_UNAVAILABLE','工具声明需要修复');const manifest=this.read(item.manifest),key='studio_manifest_tool:'+item.id,prior=this.store.get(key);if(prior&&await this.healthy(prior,item))return {...prior,id:item.id,name:item.name,independent:true};if(prior){let alive=true;try{process.kill(prior.pid,0);}catch(error){if(error.code==="ESRCH")alive=false;}assert(!alive,"TOOL_UNAVAILABLE","已有工具进程的健康状态无法确认，保留实例并拒绝重复启动");}const instance=crypto.randomUUID(),folder=path.join(this.directory,'tool-launches',instance),ready=path.join(folder,'ready.json');const executable=manifest.runtime==='python'?this.python:this.node;assert(fs.existsSync(executable),'TOOL_DEPENDENCY','项目内运行依赖不可用');if(manifest.runtime==='python')assert(fs.existsSync(path.join(this.root,'.venv/pyvenv.cfg')),'TOOL_DEPENDENCY','项目虚拟环境缺少 pyvenv.cfg，请修复环境后重试');fs.mkdirSync(folder,{recursive:true});const log=fs.openSync(path.join(folder,'process.log'),'a'),env={...process.env};delete env.ELECTRON_RUN_AS_NODE;const args=[manifest.entry,...(manifest.args||[]),'--ready-file',ready,'--instance',instance];if(manifest.accept_root===true)args.push('--root',this.root);const child=spawn(executable,args,{cwd:path.dirname(manifest.entry),env,detached:true,windowsHide:true,stdio:['ignore',log,log]});fs.closeSync(log);let failed;child.once('error',error=>failed=error);child.unref();for(let n=0;n<150;n++){if(failed||child.exitCode!==null)throw new Error('工具启动失败，请检查 process.log');if(fs.existsSync(ready)){const state=JSON.parse(fs.readFileSync(ready,'utf8'));assert(state.instance===instance&&Number.isInteger(state.pid)&&state.pid>0&&(state.pid===child.pid||(manifest.runtime==='python'&&state.launcher_pid===child.pid)),'TOOL_IDENTITY','工具就绪身份错误');localUrl(state.url);if(await this.healthy(state,item)){this.store.put(key,state);return {...state,id:item.id,name:item.name,independent:true};}}await new Promise(resolve=>setTimeout(resolve,100));}throw new Error('工具未返回有效就绪信号');}
 async stop(item){const key='studio_manifest_tool:'+item.id,state=this.store.get(key);assert(state&&await this.healthy(state,item),'TOOL_OWNER','无法确认此工具实例');const response=await fetch(state.url+'shutdown',{method:'POST',headers:{'X-Tool-Instance':state.instance},signal:AbortSignal.timeout(3000)});assert(response.ok,'TOOL_STOP','工具拒绝停止');
  let exited=false;const deadline=Date.now()+10000;while(Date.now()<deadline){try{process.kill(state.pid,0);}catch(error){if(error.code==='ESRCH'){exited=true;break;}throw error;}await new Promise(resolve=>setTimeout(resolve,100));}
  assert(exited,'TOOL_STOP_PENDING','工具已接收停止请求，进程尚未确认退出；没有强制终止');this.store.put(key,null);return {stopped:true,process_exited:true};}
}
module.exports={ManifestTools};
