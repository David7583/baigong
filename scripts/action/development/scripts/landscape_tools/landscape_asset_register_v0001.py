# ============================================================
# File: landscape_asset_register_v0001.py
# 中文名: 已确认本地内容资产的显式原地登记
# Version: v0001
# Main Layer: action
# Layer: development / scripts / asset_management
# Responsibilities: 登记真实模型及可独立复用纹理；保留既有物理路径。
# Does not: 下载资产、移动资源、导入软件依赖或扫描整个磁盘。
# Boundary: 仅显式调用；JSON 交接；来源取自已有 PROVENANCE；失败留证。
# Updatable: True
# ALIAS_META
# alias: landscape_asset_register_v0001
# family: asset_seed
# role: maintenance
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/landscape_tools/landscape_asset_register_v0001.py
# input: --root
# output: stable asset IDs and adoption audit
# depends_on: content_asset_repository_v0002
# used_by: []
# ============================================================
import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

SCRIPT_NAME='landscape_asset_register_v0001'
SCRIPT_FAMILY='asset_seed'
SCRIPT_VERSION='v0001'
DEFAULT_ENCODING='utf-8'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args();root=Path(args.root).resolve()
    config=root/'config/action/content_assets_v0001.json';location=json.loads(config.read_text('utf-8-sig')) if config.exists() else {'root':'assets/threejs_content'}
    base=(root/location['root']).resolve();require=base.is_relative_to((root/'assets').resolve());assert require,'正式资产目录必须位于本工作区 assets 内'
    folder=base/'models/plants/polyhaven/tree_small_02';proof=json.loads((folder/'PROVENANCE.json').read_text('utf-8'))
    common={'bundle_root':str(folder),'source':'Poly Haven / Rico Cilliers','url':proof['source'],'license':'CC0','categories':['植物','乔木'],'tags':['Poly Haven','通用形态参照'],'license_file':str((folder/'PROVENANCE.json').relative_to(root)).replace('\\','/')}
    jobs=[{**common,'path':str(folder/'tree_small_02_1k.gltf'),'name':'Tree Small 02 · 原始乔木','alias':'polyhaven.tree_small_02','description':'约206万三角形原件。不是桂花种属模型；形态参照不可替代植物事实。'}]
    for level in (1,2,3):
        jobs.append({**common,'path':str(folder/f'lod{level}/tree_small_02_lod{level}.gltf'),'name':f'Tree Small 02 · LOD{level}','alias':f'polyhaven.tree_small_02.lod{level}','description':'meshoptimizer v0.25 派生轻量变体；来源与误差见同目录 PROVENANCE.json','tags':['Poly Haven',f'LOD{level}','派生']})
    for texture in sorted((folder/'textures').glob('*.jpg')):
        jobs.append({**common,'path':str(texture),'name':texture.stem,'kind':'材质与纹理','categories':['纹理','PBR'],'alias':'polyhaven.'+texture.stem})
    adopted=[]
    for job in jobs:
        identity='seed-'+uuid.uuid4().hex;folder=root/'runtime/intermediate_tasks'/identity;folder.mkdir(parents=True);source=folder/'request.json';target=folder/'response.json';raw=json.dumps({'schema_version':'asset.repository.v1','run_id':identity,'root':str(root),'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'operation':'adopt','input':job},ensure_ascii=False).encode();source.write_bytes(raw)
        child=subprocess.run([sys.executable,'-B',str(root/'scripts/action/infrastructures/content_asset_repository_v0002.py'),'--root',str(root),'--request',str(source),'--response',str(target)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=60)
        result=json.loads(target.read_text('utf-8')) if target.exists() else {};assert child.returncode==0 and result.get('status')=='completed',result;assert result['source_sha256']==hashlib.sha256(raw).hexdigest()
        adopted.append({'id':result['result']['item']['id'],'name':job['name'],'path':result['result']['item']['path'],'duplicate':result['result']['duplicate']})
        cleaner=root/'scripts/action/infrastructures/intermediate_cleanup_v0001.py'
        if cleaner.exists():
            manifest=folder/'task.json';manifest.write_text(json.dumps({'schema_version':'intermediate-task.v1','run_id':identity,'status':'completed','active':False,'result_verified':True,'recovery_required':False,'consumers':[{'id':SCRIPT_NAME,'acknowledged':True}],'intermediates':[{'path':str(p.relative_to(root)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'regenerable':True,'protected':False} for p in (source,target)]}),encoding='utf-8')
            subprocess.run([sys.executable,'-B',str(cleaner),'--root',str(root),'--manifest',str(manifest.relative_to(root))],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30,check=True)
    # Reuse the same repository protocol for existing LOD relations.
    for level in (1,2,3):
        identity='lod-'+uuid.uuid4().hex;folder=root/'runtime/intermediate_tasks'/identity;folder.mkdir(parents=True);source=folder/'request.json';target=folder/'response.json'
        source.write_text(json.dumps({'schema_version':'asset.repository.v1','run_id':identity,'root':str(root),'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'operation':'lod','input':{'id':adopted[0]['id'],'level':level,'variant_id':adopted[level]['id']}}),encoding='utf-8')
        child=subprocess.run([sys.executable,'-B',str(root/'scripts/action/infrastructures/content_asset_repository_v0002.py'),'--root',str(root),'--request',str(source),'--response',str(target)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=60)
        result=json.loads(target.read_text('utf-8'));assert child.returncode==0 and result['status']=='completed',result
    print(json.dumps({'status':'completed','registered':len(adopted),'physical_files_moved':0,'items':adopted},ensure_ascii=False))

if __name__=='__main__':main()
