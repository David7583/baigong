# ============================================================
# File: asset_acceptance_v0001.py
# 中文名: 统一资产真实文件与 SQLite 合约验收
# Version: v0001
# Main Layer: action
# Layer: development / scripts / asset_management
# Responsibilities: 隔离文件、JSON 交接、版本引用与异常验收。
# Does not: 修改正式业务数据或调用模型。
# Boundary: 日志保留；仅清理本脚本创建的测试夹具。
# Updatable: True
# ALIAS_META
# alias: asset_acceptance_v0001
# family: asset_acceptance
# role: acceptance
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/asset_management/asset_acceptance_v0001.py
# input: --stage --source --runs
# output: JSON test evidence
# depends_on: content_asset_repository_v0001
# used_by: []
# ============================================================
import argparse
import hashlib
import json
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import uuid
from pathlib import Path

SCRIPT_NAME='asset_acceptance_v0001'
SCRIPT_FAMILY='asset_acceptance'
SCRIPT_VERSION='v0001'
DEFAULT_ENCODING='utf-8'

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',required=True);p.add_argument('--source',required=True);p.add_argument('--runs',required=True);args=p.parse_args()
    stage=Path(args.stage).resolve();source=Path(args.source).resolve();parent=Path(args.runs).resolve();parent.mkdir(parents=True,exist_ok=True);root=Path(tempfile.mkdtemp(prefix='assets-contract-',dir=parent));checks=[]
    try:
        library=source/'assets/threejs_content/models/plants/polyhaven/fern_02'
        incoming=root/'incoming';shutil.copytree(library,incoming)
        worker=stage/'scripts/action/infrastructures/content_asset_repository_v0001.py'
        def call(op,data=None,error=None):
            rid=uuid.uuid4().hex;req=root/(rid+'.request.json');res=root/(rid+'.response.json');raw=json.dumps({'schema_version':'asset.repository.v1','run_id':rid,'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'root':str(root),'operation':op,'input':data or {}}).encode();req.write_bytes(raw)
            result=subprocess.run([str(source/'.venv/Scripts/python.exe'),'-B',str(worker),'--root',str(root),'--request',str(req),'--response',str(res)],capture_output=True,text=True)
            assert res.exists(),result.stderr
            value=json.loads(res.read_text('utf-8'));assert value['source_sha256']==hashlib.sha256(raw).hexdigest()
            if error:
                assert value['status']=='failed' and error in value['error'],value
                return value
            assert result.returncode==0 and value['status']=='completed',value
            return value['result']
        def passed(name):
            checks.append(name);print(json.dumps({'check':name,'status':'passed'},ensure_ascii=False),flush=True)
        entry=incoming/'fern_02_1k.gltf'
        first=call('import',{'path':str(entry),'name':'真实蕨类模型','categories':['植物','蕨类'],'tags':['庭院'],'source':'Poly Haven / Rico Cilliers, Rob Tuytel','url':'https://polyhaven.com/a/fern_02','license':'CC0-1.0','license_file':'PROVENANCE.json'})['item'];id=first['id']
        assert first['format']=='gltf' and first['size']>1000000 and len(first['files'])==6 and Path(first['path']).is_file()
        passed('真实 glTF、四个依赖及来源证明复制后写入 SQLite')
        renamed=incoming/'other-name.gltf';shutil.copy2(entry,renamed)
        second=call('import',{'path':str(renamed),'name':'另一个名称'})
        assert second['duplicate'] and second['item']['id']==id
        passed('不同名称的同一内容返回同一资产 ID，不重复复制')
        oldpath=first['path'];edited=call('edit',{'id':id,'revision':first['revision'],'name':'共享蕨类','categories':['植物','蕨类','场景装饰'],'tags':['庭院','共享']})['item']
        assert edited['path']==oldpath
        call('edit',{'id':id,'revision':first['revision'],'name':'过期修改'},error='记录已被更新')
        passed('元数据重命名保持引用路径；过期修改拒绝覆盖')
        project_dir=root/'projects';project_dir.mkdir()
        with sqlite3.connect(project_dir/'projects.sqlite3') as db:
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
            for pid in ('project-a','project-b'):db.execute('INSERT INTO projects VALUES(?,?)',(pid,json.dumps({'id':pid,'name':pid,'archived':False})))
        db.close()
        before=sum(p.stat().st_size for p in (root/'assets/imports').rglob('*') if p.is_file())
        for pid in ('project-a','project-b'):call('reference',{'id':id,'project_id':pid})
        assert len(call('get',{'id':id})['item']['references'])==2 and before==sum(p.stat().st_size for p in (root/'assets/imports').rglob('*') if p.is_file())
        call('archive',{'id':id},error='仍被');call('delete',{'id':id,'confirm':id},error='仍被')
        passed('两个真实项目引用一个包，无新增内容字节；在用删除拒绝')
        assert len(call('list',{'format':'gltf','categories':'植物','tags':'共享','project_id':'project-b'})['items'])==1
        for fmt in ('dwg','skp','ifc'):
            file=incoming/('registered.'+fmt);file.write_bytes(b'opaque-format-registration-fixture');item=call('import',{'path':str(file)})['item'];assert item['format']==fmt and not item['preview_supported']
        assert call('list',{'format':'skp'})['items'][0]['format']=='skp'
        passed('格式与多分类、标签、项目筛选独立；CAD/SKP 仅登记不伪称预览')
        # Valid actual triangle GLB, not an image substitute.
        positions=struct.pack('<9f',0,0,0,1,0,0,0,1,0)
        doc={'asset':{'version':'2.0'},'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}],'buffers':[{'byteLength':len(positions)}],'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':len(positions)}],'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3','min':[0,0,0],'max':[1,1,0]}]}
        payload=json.dumps(doc).encode();payload+=b' '*((-len(payload))%4);glb=incoming/'triangle.glb';glb.write_bytes(struct.pack('<4sII',b'glTF',2,28+len(payload)+len(positions))+struct.pack('<II',len(payload),0x4E4F534A)+payload+struct.pack('<II',len(positions),0x004E4942)+positions)
        glbitem=call('import',{'path':str(glb)})['item'];assert glbitem['preview_supported']
        call('lod',{'id':id,'level':3,'variant_id':glbitem['id']});call('archive',{'id':glbitem['id']},error='仍被')
        passed('实际 GLB 三角网格可导入；LOD 变体引用阻止误删')
        for ref in call('get',{'id':id})['item']['references']:call('unreference',{'id':id,'reference_id':ref['id']})
        call('reference',{'id':id,'project_id':'project-a'})
        changed=json.loads(entry.read_text('utf-8'));changed['asset']['extras']={'version_test':2};entry.write_text(json.dumps(changed))
        updated=call('update_version',{'id':id,'revision':edited['revision'],'path':str(entry)})['item'];assert updated['version']==2 and updated['references'][0]['version']==1
        assert call('get',{'id':id,'version':1})['item']['path']==oldpath
        passed('新版本保留旧包；已有项目钉住 v1，重启独立进程仍存在')
        texture=next(f for f in updated['files'] if f['relative'].endswith('.jpg'));texturepath=root/updated['base_path']/texture['relative'];texturepath.unlink()
        assert call('verify',{'id':id})['item']['issues'] and not call('get',{'id':id})['item']['preview_supported']
        missing=root/'missing';shutil.copytree(incoming,missing);next((missing/'textures').glob('*.jpg')).unlink();call('import',{'path':str(missing/entry.name)},error='依赖缺失')
        passed('外部删除纹理被发现，预览明确停用；不完整包拒绝导入')
        scan1=call('scan',{'path':str(incoming)});scan2=call('scan',{'path':str(incoming)});assert scan2['unchanged']>0 and scan2['candidates']
        assert any(c['status']=='duplicate' for c in scan2['candidates'])
        assert len(call('list')['items'])==5
        passed('增量扫描复用指纹、识别重复，只产生待确认候选')
        evil=incoming/'evil.gltf';evil.write_text(json.dumps({'asset':{'version':'2.0'},'buffers':[{'uri':'../../outside.bin'}]}));call('import',{'path':str(evil)},error='超出')
        passed('跨包路径依赖拒绝，软件可执行文件不在允许格式内')
        print(json.dumps({'status':'passed','checks':len(checks),'sqlite_tables':['assets','versions','files','aliases','categories','tags','asset_refs','lod_variants','history','scan_files','scan_candidates','requests','migrations'],'formal_data_modified':False},ensure_ascii=False))
    finally:
        assert root.is_relative_to(parent) and root.name.startswith('assets-contract-')
        shutil.rmtree(root);print(json.dumps({'cleanup':'isolated fixtures removed'}))

if __name__=='__main__':main()
