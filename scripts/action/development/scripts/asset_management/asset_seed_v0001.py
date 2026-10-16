# ============================================================
# File: asset_seed_v0001.py
# 中文名: 已确认本地内容资产的显式原地登记
# Version: v0001
# Main Layer: action
# Layer: development / scripts / asset_management
# Responsibilities: 登记真实模型及可独立复用纹理；保留既有物理路径。
# Does not: 下载资产、移动资源、导入软件依赖或扫描整个磁盘。
# Boundary: 仅显式调用；JSON 交接；来源取自已有 PROVENANCE；失败留证。
# Updatable: True
# ALIAS_META
# alias: asset_seed_v0001
# family: asset_seed
# role: maintenance
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/asset_management/asset_seed_v0001.py
# input: --root
# output: stable asset IDs and adoption audit
# depends_on: content_asset_repository_v0001
# used_by: []
# ============================================================
import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

SCRIPT_NAME='asset_seed_v0001'
SCRIPT_FAMILY='asset_seed'
SCRIPT_VERSION='v0001'
DEFAULT_ENCODING='utf-8'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args();root=Path(args.root).resolve()
    config=root/'config/action/content_assets_v0001.json';location=json.loads(config.read_text('utf-8-sig')) if config.exists() else {'root':'assets/threejs_content'}
    base=(root/location['root']).resolve();require=base.is_relative_to((root/'assets').resolve());assert require,'正式资产目录必须位于本工作区 assets 内'
    jobs=[]
    names={'fern_02':('Fern 02 · 蕨类植物','Rico Cilliers / Rob Tuytel',['植物','蕨类']),'potted_plant_04':('Potted Plant 04 · 盆栽','James Ray Cock',['植物','盆栽'])}
    for id,(name,authors,categories) in names.items():
        folder=base/'models/plants/polyhaven'/id;provenance=folder/'PROVENANCE.json'
        if not provenance.is_file():continue
        proof=json.loads(provenance.read_text('utf-8'));entry=folder/(id+'_1k.gltf');assert entry.is_file()
        jobs.append({'path':str(entry),'name':name,'categories':categories,'tags':['Poly Haven','1K'],'alias':'polyhaven.'+id,'source':'Poly Haven / '+authors,'url':proof['source'],'license':proof['license'],'license_file':str(provenance.relative_to(root)).replace('\\','/')})
        for texture in sorted((folder/'textures').glob('*.jpg')):
            label='基础色' if '_diff_' in texture.name else '法线（OpenGL）' if '_nor_gl_' in texture.name else 'AO / 粗糙度 / 金属度合并通道'
            original=next((f.get('url','') for f in proof['files'] if f['path'].split('/')[-1]==texture.name),'')
            jobs.append({'path':str(texture),'name':id+' · '+label,'kind':'材质与纹理','categories':['纹理',label],'tags':['Poly Haven','PBR','1K'],'alias':'polyhaven.'+texture.stem,'source':'Poly Haven / '+authors,'url':original,'license':proof['license'],'license_file':str(provenance.relative_to(root)).replace('\\','/')})
    adopted=[]
    for job in jobs:
        identity='seed-'+uuid.uuid4().hex;folder=root/'runtime/intermediate_tasks'/identity;folder.mkdir(parents=True);source=folder/'request.json';target=folder/'response.json';raw=json.dumps({'schema_version':'asset.repository.v1','run_id':identity,'root':str(root),'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'operation':'adopt','input':job},ensure_ascii=False).encode();source.write_bytes(raw)
        child=subprocess.run([sys.executable,'-B',str(root/'scripts/action/infrastructures/content_asset_repository_v0001.py'),'--root',str(root),'--request',str(source),'--response',str(target)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=60)
        result=json.loads(target.read_text('utf-8')) if target.exists() else {};assert child.returncode==0 and result.get('status')=='completed',result;assert result['source_sha256']==hashlib.sha256(raw).hexdigest()
        adopted.append({'id':result['result']['item']['id'],'name':job['name'],'path':result['result']['item']['path'],'duplicate':result['result']['duplicate']})
        cleaner=root/'scripts/action/infrastructures/intermediate_cleanup_v0001.py'
        if cleaner.exists():
            manifest=folder/'task.json';manifest.write_text(json.dumps({'schema_version':'intermediate-task.v1','run_id':identity,'status':'completed','active':False,'result_verified':True,'recovery_required':False,'consumers':[{'id':SCRIPT_NAME,'acknowledged':True}],'intermediates':[{'path':str(p.relative_to(root)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'regenerable':True,'protected':False} for p in (source,target)]}),encoding='utf-8')
            subprocess.run([sys.executable,'-B',str(cleaner),'--root',str(root),'--manifest',str(manifest.relative_to(root))],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30,check=True)
    print(json.dumps({'status':'completed','registered':len(adopted),'physical_files_moved':0,'items':adopted},ensure_ascii=False))

if __name__=='__main__':main()
