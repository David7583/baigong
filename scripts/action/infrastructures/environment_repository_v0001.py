# File: environment_repository_v0001.py
# 中文名: 工作台加载依赖与当前环境双清单
# Version: v0001
# Main Layer: action
# Layer: infrastructures / environment
# Responsibilities: 读取真实安装信息和工具程序声明，生成两个可追溯清单。
# Does not: 打包、安装、执行资源、读取业务数据或导出凭证。
# Boundary: 仅观察；JSON交接；未知资源依赖如实标记，不猜测版本。
# ALIAS_META
# alias: environment_repository_v0001
# family: environment_repository
# role: dependency_inventory_worker
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/environment_repository_v0001.py
# input: environment.repository.v1 inspect request
# output: loaded workbench requirements and installed environment inventory
# depends_on: Python standard library
# used_by: workbench_environment_v0001
import argparse
import ast
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_NAME='environment_repository_v0001'
SCRIPT_VERSION='v0001'
SCHEMA='environment.repository.v1'
def require(ok,message):
    if not ok:raise ValueError(message)
def inspect(root,context):
    require(set(context)<= {'tools','capabilities'},'清单上下文字段无效')
    site=root/'.venv/Lib/site-packages';python=[];modules={}
    for distribution in importlib.metadata.distributions(path=[str(site)]) if site.is_dir() else []:
        name=distribution.metadata.get('Name','');version=distribution.version
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}',name):continue
        top=(distribution.read_text('top_level.txt') or '').splitlines()
        if not top:top=list({str(file).split('/')[0].split('.')[0] for file in distribution.files or [] if str(file).split('/')[0].isidentifier()})
        for module in top:modules.setdefault(module,set()).add(name)
        python.append({'name':name,'version':version,'path':'.venv/Lib/site-packages/'+distribution._path.name})
    python.sort(key=lambda x:x['name'].lower());python_by_name={x['name']:x for x in python}
    frontend=root/'frontend/local_ai_workbench';package_file=frontend/'package.json'
    package=json.loads(package_file.read_text('utf-8-sig')) if package_file.is_file() else {}
    declared={**package.get('dependencies',{}),**package.get('devDependencies',{})};node=[];seen=set()
    def scan_node(folder,depth=0):
        if not folder.is_dir() or depth>6:return
        candidates=[]
        for child in sorted(folder.iterdir()):
            if child.is_symlink() or child.name.startswith('.'):continue
            if child.name.startswith('@') and child.is_dir():candidates.extend(x for x in child.iterdir() if x.is_dir() and not x.is_symlink())
            elif child.is_dir():candidates.append(child)
        for child in candidates:
            manifest=child/'package.json'
            if manifest.is_file() and manifest.stat().st_size<1024*1024:
                try:
                    value=json.loads(manifest.read_text('utf-8'));name=value.get('name');version=value.get('version')
                    if isinstance(name,str) and isinstance(version,str):
                        identity=manifest.relative_to(root).as_posix()
                        if identity not in seen:
                            require(len(seen)<10000,'npm清单超过10000包预算');seen.add(identity)
                            node.append({'name':name,'version':version,'path':str(child.relative_to(root)).replace('\\','/'),'direct':name in declared,'requested':declared.get(name)})
                except (ValueError,UnicodeError):pass
            scan_node(child/'node_modules',depth+1)
    scan_node(frontend/'node_modules');node.sort(key=lambda x:(x['name'].lower(),x['path']))
    node_by_name={}
    for item in node:
        if item['path']=='frontend/local_ai_workbench/node_modules/'+item['name']:node_by_name[item['name']]=item
    tools=[];required=set();require(isinstance(context.get('tools',[]),list) and len(context.get('tools',[]))<=300,'加载工具超出预算')
    for item in context.get('tools',[]):
        require(isinstance(item,dict) and isinstance(item.get('id'),str),'工具身份无效')
        dependency_names=set();unknown=set()
        folders=[]
        if item['id'] in ('tool.scene-notes','tool.data-sandbox'):folders=[frontend/'tools/landscape/shared']
        elif item.get('program_directory'):
            candidate=(root/item['program_directory']).resolve()
            if candidate.is_relative_to(root/'frontend/local_ai_workbench/tools') or candidate.is_relative_to(root/'scripts/action/development/scripts'):folders=[candidate]
        for folder in folders:
            for file in list(folder.glob('*.py'))[:200]:
                if not file.resolve().is_relative_to(root) or file.stat().st_size>2*1024*1024:continue
                try:
                    tree=ast.parse(file.read_text('utf-8-sig'))
                    imports=[x.name.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.Import) for x in n.names]+[n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module and n.level==0]
                    for module in imports:
                        if module in modules:dependency_names.update(modules[module])
                        elif module not in sys.stdlib_module_names and not (folder/(module+'.py')).is_file():unknown.add(module)
                except (SyntaxError,UnicodeError):unknown.add('source declaration unreadable')
        required.update(dependency_names)
        tools.append({'id':item['id'],'name':str(item.get('name',''))[:120],'runtime':str(item.get('runtime','external'))[:30],'version':str(item.get('version','unversioned'))[:100],'loaded':True,'status':str(item.get('status','available'))[:30],'python_dependencies':sorted(dependency_names),'unresolved_imports':sorted(unknown),'dependency_basis':'static imports matched to actual installed distribution metadata' if folders else 'no dependency declaration available'})
    caps=context.get('capabilities',[]);require(isinstance(caps,list) and len(caps)<=300,'加载资源超出预算')
    cleaned=[]
    for item in caps:
        require(isinstance(item,dict) and item.get('kind') in ('skill','github','library','mcp','json_cli'),'资源类型无效')
        cleaned.append({key:item[key] for key in ('id','kind','name','version','source','license','status','load_basis') if key in item})
    runtime_node=None;node_exe=root/'.tools/node/node.exe'
    if node_exe.is_file():
        import subprocess
        result=subprocess.run([str(node_exe),'--version'],capture_output=True,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
        if result.returncode==0:runtime_node=result.stdout.strip()
    vendors=[];core=frontend/'workspace/vendor/three/three.core.mjs'
    if core.is_file():
        match=re.search(r"REVISION\s*=\s*['\"]([^'\"]+)['\"]",core.read_text('utf-8'))
        if match:vendors.append({'name':'Three.js','version':'r'+match[1],'path':'frontend/local_ai_workbench/workspace/vendor/three','basis':'actual vendored REVISION'})
    live={'tools':sorted(tools,key=lambda x:x['id']),'resources':sorted(cleaned,key=lambda x:x['id']),'python_requirements':[python_by_name[name] for name in sorted(required) if name in python_by_name],'node_requirements':[{'name':name,'requested':version,'version':node_by_name.get(name,{}).get('version'),'status':'installed' if name in node_by_name else 'missing'} for name,version in sorted(declared.items())],'vendored':vendors,'scope':'Workbench runtime, loaded tools, loaded capability IDs and enabled Skills. Static declarations do not prove every optional branch is currently executing.'}
    environment={'python':{'version':sys.version.split()[0],'packages':python},'node':{'version':runtime_node,'packages':node},'vendored':vendors,'scope':'This workspace .venv, frontend node_modules and vendored runtime; not all software installed in Windows.'}
    facts={'schema_version':'workbench.requirements.v1','workbench':{'name':package.get('name'),'version':package.get('version'),'main':package.get('main')},'loaded_requirements':live,'current_environment':environment,'observation_only':True}
    facts['fingerprint']=hashlib.sha256(json.dumps(facts,sort_keys=True,ensure_ascii=False).encode()).hexdigest();facts['observed_at']=time.time()
    return facts
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--request',required=True);parser.add_argument('--response',required=True);args=parser.parse_args()
    root=Path(args.root).resolve();source=Path(args.request).resolve();response=Path(args.response).resolve()
    require(source.is_relative_to(root) and response.is_relative_to(root),'交接路径超出工作区');raw=source.read_bytes();rid=None
    try:
        require(len(raw)<=1024*1024,'请求超出预算');request=json.loads(raw);rid=request.get('run_id')
        require(request.get('schema_version')==SCHEMA and request.get('root')==str(root) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',rid or '') and request.get('producer',{}).get('script') in ('workbench_environment_v0001','environment_acceptance_v0001') and request.get('producer',{}).get('version')=='v0001','清单交接身份无效')
        require(request['operation']=='inspect','本模块只提供双清单观察')
        result=inspect(root,request.get('input',{}).get('context',{}))
        output={'schema_version':SCHEMA,'run_id':rid,'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'source_sha256':hashlib.sha256(raw).hexdigest(),'status':'completed','result':result};code=0
    except Exception as error:
        output={'schema_version':SCHEMA,'run_id':rid,'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'source_sha256':hashlib.sha256(raw).hexdigest(),'status':'failed','error':str(error)};code=2
    response.parent.mkdir(parents=True,exist_ok=True);temporary=response.with_suffix('.tmp');temporary.write_text(json.dumps(output,ensure_ascii=False,allow_nan=False),'utf-8');temporary.replace(response);return code
if __name__=='__main__':raise SystemExit(main())
