// ============================================================
// File: workbench_skills_v0002.cjs
// 中文名: local_skill_market
// Version: v0002
// Main Layer: action
// Layer: workbench / runtime
// Updatable: True
// Responsibilities: 展示独立资源并保持真实状态和来源。
// Does: 按显式操作承载工具、栏目和已授权的接取关系。
// Does not: 删除原始资料、在启动时登记开发对象或自动授权 AI。
// Purpose: 按显式操作管理资源、Skill来源及Agent接取关系。
// Boundary: 不执行Skill内容，不调用AI，不代替业务应用，不覆盖原始库。
// ============================================================
// ALIAS_META
// alias: workbench_skills
// family: workbench_skills
// role: local_skill_market
// version: v0002
// status: active
// entry_point: frontend/local_ai_workbench/runtime/workbench_skills_v0002.cjs
// input:
//   - explicit authenticated category, import, scan and open requests
// output:
//   - private catalog, managed skills, agent feeds and embedded tool endpoints
// depends_on:
//   - workbench_store_v0001
//   - workbench_contracts_v0001
//   - workbench_skill_archive_v0001
// used_by:
//   - workbench_studio_v0001
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {assert,localUrl,WorkbenchError}=require('./workbench_contracts_v0001.cjs');
const DEFAULT_ENCODING='utf8';
const uid=()=>crypto.randomUUID();
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const safeName=value=>{assert(typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$/.test(value),'STUDIO_ID','ID格式无效');return value;};
function boundedText(value,label,max=300){assert(typeof value==='string'&&value.trim()&&value.length<=max,'STUDIO_INPUT',label+'无效');return value.trim();}
const SCRIPT_NAME='workbench_skills_v0002.cjs',SCRIPT_FAMILY='workbench_skills',SCRIPT_VERSION='v0002';
function checkFile(file){const info=fs.lstatSync(file);assert(!info.isSymbolicLink(),'SKILL_LINK','导入目录不接受链接');return info;}
function skillText(file){const info=checkFile(file);assert(info.isFile()&&info.size<=1024*1024,'SKILL_SIZE','SKILL.md 过大或不是文件');const content=fs.readFileSync(file,'utf8');assert(!content.includes('\0'),'SKILL_FORMAT','SKILL.md 必须是文本');return content;}
function skillMeta(file){const content=skillText(file);const front=content.match(/^---\s*\r?\n([\s\S]*?)\r?\n---/);const field=key=>front?.[1].match(new RegExp('^'+key+':\\s*(.+)$','m'))?.[1].trim().replace(/^['"]|['"]$/g,'');return {name:field('name')||path.basename(path.dirname(file)),description:field('description')||'查看 SKILL.md 获取使用说明',sha256:hash(content)};}
function findSkills(directory){const result=[];let seen=0;function walk(dir,depth){assert(++seen<=4000,'SKILL_SCAN_LIMIT','扫描目录过多，请缩小范围');if(depth>8)return;const info=checkFile(dir);assert(info.isDirectory(),'SKILL_DIRECTORY','请选择 Skill 文件夹');if(fs.existsSync(path.join(dir,'SKILL.md'))){skillMeta(path.join(dir,'SKILL.md'));result.push(dir);return;}for(const entry of fs.readdirSync(dir,{withFileTypes:true})){if(entry.isSymbolicLink())continue;if(entry.isDirectory()&&!['.git','node_modules','.venv','__pycache__'].includes(entry.name))walk(path.join(dir,entry.name),depth+1);}}walk(directory,0);assert(result.length<=100,'SKILL_SCAN_LIMIT','一次最多导入100个Skill');return result;}
function bundleHash(root){const digest=crypto.createHash('sha256');let count=0,total=0;function walk(dir){for(const name of fs.readdirSync(dir).sort()){if(['.git','node_modules','.venv','__pycache__'].includes(name))continue;const file=path.join(dir,name),info=checkFile(file);if(info.isDirectory())walk(file);else{assert(info.isFile()&&++count<=1000&&(total+=info.size)<=50*1024*1024,'SKILL_SIZE','Skill包过大');digest.update(path.relative(root,file).replaceAll('\\','/'));digest.update('\0');digest.update(fs.readFileSync(file));digest.update('\0');}}}walk(root);return digest.digest('hex');}
function copySkill(source,target){let count=0,total=0;function copy(from,to){const info=checkFile(from);if(info.isDirectory()){fs.mkdirSync(to,{recursive:true});for(const name of fs.readdirSync(from)){if(['.git','node_modules','.venv','__pycache__'].includes(name))continue;copy(path.join(from,name),path.join(to,name));}}else{assert(info.isFile()&&++count<=1000&&(total+=info.size)<=50*1024*1024,'SKILL_SIZE','Skill 包超过1000文件或50MB');fs.copyFileSync(from,to,fs.constants.COPYFILE_EXCL);}}copy(source,target);}
async function githubInfo(value){const url=new URL(value);assert(url.protocol==='https:'&&url.hostname==='github.com'&&!url.username&&!url.password,'GITHUB_URL','请输入 https://github.com/所有者/仓库');const parts=url.pathname.split('/').filter(Boolean);assert(parts.length===2&&parts.every(p=>/^[\w.-]+$/.test(p)),'GITHUB_URL','请使用仓库根地址');const response=await fetch('https://api.github.com/repos/'+parts.join('/'),{headers:{Accept:'application/vnd.github+json'},signal:AbortSignal.timeout(15000)});assert(response.ok,'GITHUB_FETCH','GitHub 返回 '+response.status);const value2=await response.json();return {owner:parts[0],repo:parts[1],name:value2.full_name,description:value2.description||'',branch:value2.default_branch,url:url.href};}

class Skills {
 constructor(root,store,directory){this.root=root;this.store=store;this.directory=path.join(directory,'skill-market');this.python=require('./release_paths_v0001.cjs').python(root);}
 skills(){return this.store.get('studio_skills',[]).map(x=>({...x,available:fs.existsSync(path.join(x.path,'SKILL.md'))}));}
 agents(){return this.store.get('studio_agents',[]).map(({key_hash,...x})=>x);}
 scan(input){const source=path.resolve(boundedText(input.path,'文件夹',2048));return {found:findSkills(source).map(p=>({path:p,...skillMeta(path.join(p,'SKILL.md'))})),action:'import'};}
 importSkills(input,download=false){const source=path.resolve(boundedText(input.path,'文件夹',2048));assert((!source.startsWith(this.directory+path.sep)&&source!==this.directory)||(download&&source.startsWith(path.join(this.directory,'.downloads')+path.sep)),'SKILL_SOURCE','请从市场目录之外导入');const directories=findSkills(source);assert(directories.length,'SKILL_MISSING','未找到 SKILL.md');const existing=this.skills();const staged=[];try{fs.mkdirSync(this.directory,{recursive:true});for(const directory of directories){const meta={...skillMeta(path.join(directory,'SKILL.md')),bundle_sha256:bundleHash(directory)};const same=existing.find(x=>x.bundle_sha256===meta.bundle_sha256&&x.source===(input.origin&&input.origin!=='local'?input.origin:directory));if(same){staged.push(same);continue;}const id=uid(),target=path.join(this.directory,id);staged.push({id,...meta,path:target,source:directory,origin:input.origin||'local',installed_at:new Date().toISOString(),enabled:false});copySkill(directory,target);}this.store.put('studio_skills',[...existing,...staged.filter(x=>!existing.some(y=>y.id===x.id))]);}catch(error){for(const x of staged)if(!existing.some(y=>y.id===x.id)&&x.path.startsWith(this.directory+path.sep))fs.rmSync(x.path,{recursive:true,force:true});throw error;}this.store.event('skills.imported',{count:staged.length});return {installed:staged};}
 async download(input){const info=await githubInfo(input.url);const response=await fetch('https://codeload.github.com/'+info.owner+'/'+info.repo+'/zip/refs/heads/'+encodeURIComponent(info.branch),{signal:AbortSignal.timeout(60000)});assert(response.ok,'SKILL_DOWNLOAD','下载失败：'+response.status);assert(Number(response.headers.get('content-length')||0)<=50*1024*1024,'SKILL_SIZE','下载包超过50MB');const folder=path.join(this.directory,'.downloads',uid());fs.mkdirSync(folder,{recursive:true});const zip=path.join(folder,'package.zip');let size=0;const chunks=[];try{for await(const chunk of response.body){size+=chunk.length;assert(size<=50*1024*1024,'SKILL_SIZE','下载包超过50MB');chunks.push(chunk);}const bytes=Buffer.concat(chunks);fs.writeFileSync(zip,bytes);const python=this.python;assert(fs.existsSync(python),'PYTHON_MISSING','本区Python未安装');await new Promise((resolve,reject)=>{const child=spawn(python,['-B',path.join(__dirname,'../adapters/workbench_skill_archive_v0001.py'),zip,path.join(folder,'extracted')],{windowsHide:true,stdio:['ignore','pipe','pipe']});let output='';child.stderr.on('data',v=>output+=v);child.on('error',reject);child.on('exit',code=>code===0?resolve():reject(new WorkbenchError('SKILL_ARCHIVE','压缩包校验失败：'+output.slice(-300))));});const result=this.importSkills({path:path.join(folder,'extracted'),origin:info.url},true);this.store.put('studio_skills',this.skills().map(x=>result.installed.some(y=>y.id===x.id)?{...x,source:info.url,archive_sha256:hash(bytes)}:x));return result;}finally{fs.rmSync(folder,{recursive:true,force:true});}}
 readSkill(id){const value=this.skills().find(x=>x.id===id);assert(value&&value.available,'SKILL_MISSING','Skill不存在');return {...value,content:skillText(path.join(value.path,'SKILL.md'))};}
 toggle(input){const values=this.skills();const value=values.find(x=>x.id===input.id);assert(value,'SKILL_MISSING','Skill不存在');value.enabled=input.enabled===true;this.store.put('studio_skills',values);return value;}
 connect(input){assert(!input.runtime_agent||['codex','deepseek_harness'].includes(input.runtime_agent),'AGENT_RUNTIME','请选择支持的聊天Agent');const id=safeName(input.id),name=boundedText(input.name||id,'Agent名称',100);const token=crypto.randomBytes(32).toString('hex');const values=this.store.get('studio_agents',[]);const old=values.find(x=>x.id===id);const value={id,name,runtime_agent:input.runtime_agent||(['codex','deepseek_harness'].includes(id)?id:null),skill_ids:old?.skill_ids||[],key_hash:hash(token),created_at:new Date().toISOString()};this.store.put('studio_agents',[...values.filter(x=>x.id!==id),value]);return {id,name,token,feed_path:'/agent/'+id+'/skills'};}
 bind(input){const values=this.store.get('studio_agents',[]),agent=values.find(x=>x.id===input.agent_id);assert(agent,'AGENT_MISSING','请先添加Agent');assert(Array.isArray(input.skill_ids)&&input.skill_ids.every(id=>this.skills().some(x=>x.id===id)),'SKILL_BIND','Skill选择无效');agent.skill_ids=[...new Set(input.skill_ids)];this.store.put('studio_agents',values);return {bound:agent.skill_ids.length};}
 feed(agentId,bearer){const value=this.store.get('studio_agents',[]).find(x=>x.id===agentId);const secret=String(bearer||'').replace(/^Bearer /,'');if(!(value&&crypto.timingSafeEqual(Buffer.from(hash(secret)),Buffer.from(value.key_hash))))throw new WorkbenchError('AGENT_AUTH','Agent接取凭证无效',401);return {agent_id:agentId,skills:this.skills().filter(x=>x.enabled&&x.available&&value.skill_ids.includes(x.id)).map(x=>this.readSkill(x.id))};}
}
module.exports={Skills,findSkills,skillMeta,githubInfo};
