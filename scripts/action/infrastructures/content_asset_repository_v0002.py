# ============================================================
# File: content_asset_repository_v0002.py
# 中文名: 工作台数字资产元数据与文件登记
# Version: v0002
# Main Layer: action
# Layer: infrastructures
# Responsibilities: 独立 SQLite 资产身份、分类、版本、引用与增量发现。
# Does: 消费 JSON 交接；复制完整内容包；原地登记既有资源；保留来源。
# Does not: 渲染、调度 LOD、调用 AI、修改项目库或软件依赖。
# Boundary: 引用阻止删除；名称修改不移动文件；仅托管包可物理删除。
# Updatable: True
# ALIAS_META
# alias: content_asset_repository_v0002
# family: content_asset_repository
# role: asset_repository
# version: v0002
# status: active
# entry_point: scripts/action/infrastructures/content_asset_repository_v0002.py
# input:
#   - asset.repository.v1 JSON request
# output:
#   - asset.repository.v1 JSON response and assets/catalog.sqlite3
# depends_on:
#   - Python standard library
# used_by:
#   - workbench_assets_v0001
# ============================================================
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCRIPT_NAME = 'content_asset_repository_v0002'
SCRIPT_FAMILY = 'content_asset_repository'
SCRIPT_VERSION = 'v0002'
DEFAULT_ENCODING = 'utf-8'
SCHEMA = 'asset.repository.v1'
FORMATS = {'gltf','glb','fbx','obj','mtl','stl','ply','usd','usdz','dwg','dxf','skp','ifc','step','stp','iges','igs','png','jpg','jpeg','webp','tif','tiff','bmp','svg','hdr','exr','ktx2','basis','blend','pdf','txt','json'}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(file):
    h = hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def bounded(path, parent):
    path, parent = Path(path).resolve(), Path(parent).resolve()
    require(path.is_relative_to(parent), '文件超出允许目录')
    return path

def kind(fmt):
    if fmt in {'gltf','glb','fbx','obj','stl','ply','usd','usdz','blend'}:
        return '三维模型'
    if fmt in {'dwg','dxf','skp','ifc','step','stp','iges','igs'}:
        return 'CAD / 工程模型'
    if fmt in {'hdr','exr'}:
        return 'HDRI / 环境资源'
    if fmt in {'ktx2','basis','mtl'}:
        return '材质与纹理'
    if fmt in {'png','jpg','jpeg','webp','tif','tiff','bmp','svg'}:
        return '图片与参考素材'
    return '其他可复用资源'

def inspect_bundle(path, base=None):
    path = Path(path).resolve()
    require(path.is_file() and not path.is_symlink(), '资产文件不存在或是链接')
    fmt = path.suffix.lower().lstrip('.')
    require(fmt in FORMATS, '不支持登记此格式（软件和可执行文件不属于内容资产）')
    base = Path(base or path.parent).resolve()
    bounded(path, base)
    files, missing = {path}, []
    if fmt in {'gltf','glb'}:
        require(path.stat().st_size <= 512 * 1024 * 1024, '单模型超过 512 MiB 导入预算')
        if fmt == 'gltf':
            doc = json.loads(path.read_text('utf-8-sig'))
        else:
            with path.open('rb') as stream:
                head = stream.read(20)
                require(len(head) == 20, 'GLB 文件头不完整')
                magic, version, size, length, chunk = struct.unpack('<4sIIII', head)
                require(magic == b'glTF' and version == 2 and size == path.stat().st_size and chunk == 0x4E4F534A and length <= 16 * 1024 * 1024 and length <= size - 20, 'GLB 2 文件头或 JSON 块无效')
                doc = json.loads(stream.read(length).decode('utf-8'))
        require(str(doc.get('asset', {}).get('version')) == '2.0', '只支持可靠预览 glTF 2.0')
        for item in doc.get('buffers', []) + doc.get('images', []):
            uri = item.get('uri', '')
            if not uri or uri.startswith('data:'):
                continue
            parsed = urlsplit(uri)
            require(not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment and '\\' not in uri and ':' not in uri, '模型依赖必须为包内相对文件')
            dep = bounded(path.parent / unquote(parsed.path), base)
            if dep.is_file() and not dep.is_symlink():
                files.add(dep)
            else:
                missing.append(str(dep.relative_to(base)))
    # Companion provenance is part of the package. Never scan unrelated siblings.
    for filename in ('PROVENANCE.json','LICENSE','LICENSE.txt','LICENSE.md','README.md','SOURCE.md'):
        file = path.parent / filename
        if file.is_file() and file.stat().st_size < 2 * 1024 * 1024:
            files.add(bounded(file, base))
    records = [{'relative': str(file.relative_to(base)).replace('\\','/'), 'hash': digest(file), 'size': file.stat().st_size, 'role': 'entry' if file == path else 'dependency'} for file in sorted(files)]
    require(sum(x['size'] for x in records) <= 1024 * 1024 * 1024 and len(records) <= 1000, '资产包超出导入预算')
    # Sources/licenses do not change model identity; every rendering dependency does.
    identity = [{**x,'relative':'$entry' if x['role']=='entry' else os.path.relpath(base / x['relative'],path.parent).replace('\\','/')} for x in records if Path(x['relative']).name not in {'PROVENANCE.json','LICENSE','LICENSE.txt','LICENSE.md','README.md','SOURCE.md'}]
    identity.sort(key=lambda x:x['relative'])
    entry = str(path.relative_to(base)).replace('\\','/')
    package_hash = hashlib.sha256(json.dumps({'format':fmt,'files':identity}, sort_keys=True).encode()).hexdigest()
    return {'format': fmt, 'entry': entry, 'files': records, 'missing': missing, 'hash': package_hash, 'base': base}

DDL = '''
CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',format TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL DEFAULT '',url TEXT NOT NULL DEFAULT '',license TEXT NOT NULL DEFAULT '未确认',license_file TEXT NOT NULL DEFAULT '',current_version INTEGER NOT NULL,revision INTEGER NOT NULL DEFAULT 1,archived INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,imported_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(asset_id TEXT NOT NULL REFERENCES assets(id),version INTEGER NOT NULL,bundle_hash TEXT NOT NULL UNIQUE,entry_path TEXT NOT NULL,base_path TEXT NOT NULL,size INTEGER NOT NULL,managed INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(asset_id,version));
CREATE TABLE IF NOT EXISTS files(asset_id TEXT NOT NULL,version INTEGER NOT NULL,relative TEXT NOT NULL,hash TEXT NOT NULL,size INTEGER NOT NULL,role TEXT NOT NULL,PRIMARY KEY(asset_id,version,relative),FOREIGN KEY(asset_id,version) REFERENCES versions(asset_id,version));
CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id));
CREATE TABLE IF NOT EXISTS categories(asset_id TEXT NOT NULL REFERENCES assets(id),value TEXT NOT NULL,PRIMARY KEY(asset_id,value));
CREATE TABLE IF NOT EXISTS tags(asset_id TEXT NOT NULL REFERENCES assets(id),value TEXT NOT NULL,PRIMARY KEY(asset_id,value));
CREATE TABLE IF NOT EXISTS asset_refs(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,version INTEGER NOT NULL,project_id TEXT NOT NULL DEFAULT '',owner_type TEXT NOT NULL,owner_id TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(asset_id,project_id,owner_type,owner_id),FOREIGN KEY(asset_id,version) REFERENCES versions(asset_id,version));
CREATE TABLE IF NOT EXISTS lod_variants(asset_id TEXT NOT NULL REFERENCES assets(id),level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 3),variant_id TEXT NOT NULL REFERENCES assets(id),PRIMARY KEY(asset_id,level));
CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY,asset_id TEXT,operation TEXT NOT NULL,at TEXT NOT NULL,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scan_files(path TEXT PRIMARY KEY,mtime_ns INTEGER NOT NULL,size INTEGER NOT NULL,hash TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scan_candidates(id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,bundle_root TEXT NOT NULL,status TEXT NOT NULL,info TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,hash TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_outbox(id TEXT PRIMARY KEY,value TEXT NOT NULL,published INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS asset_format ON assets(format,archived);
CREATE INDEX IF NOT EXISTS asset_kind ON assets(kind,archived);
CREATE INDEX IF NOT EXISTS category_value ON categories(value,asset_id);
CREATE INDEX IF NOT EXISTS tag_value ON tags(value,asset_id);
CREATE INDEX IF NOT EXISTS refs_project ON asset_refs(project_id,asset_id);
CREATE INDEX IF NOT EXISTS refs_owner ON asset_refs(owner_type,owner_id);
'''

class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.home = self.root / 'assets'
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.home / 'catalog.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=30000')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        require(version <= 1, '资产数据库版本比当前程序新，禁止降级')
        self.db.executescript(DDL)
        self.db.execute('INSERT OR IGNORE INTO migrations VALUES(1,?)', (now(),))
        self.db.execute('PRAGMA user_version=1')
        self.db.commit()

    def id(self, value):
        row = self.db.execute('SELECT id FROM assets WHERE id=? UNION SELECT asset_id FROM aliases WHERE alias=?', (value,value)).fetchone()
        require(row, '资产不存在')
        return row[0]

    def audit(self, id, op, data):
        timestamp=now()
        self.db.execute('INSERT INTO history(asset_id,operation,at,value) VALUES(?,?,?,?)', (id,op,timestamp,json.dumps(data,ensure_ascii=False)))
        event={'schema_version':'asset.event.v1','event_id':uuid.uuid4().hex,'namespace':'content_assets','asset_id':id,'operation':op,'at':timestamp,'producer':SCRIPT_NAME,'details':data}
        self.db.execute('INSERT INTO event_outbox(id,value) VALUES(?,?)',(event['event_id'],json.dumps(event,ensure_ascii=False,sort_keys=True)))

    def publish(self):
        rows=self.db.execute('SELECT id,value FROM event_outbox WHERE published=0 LIMIT 100').fetchall()
        if not rows: return {'pending':0}
        folder=self.root/'actioning/content_assets/events';folder.mkdir(parents=True,exist_ok=True)
        for row in rows:
            file=folder/(row['id']+'.json')
            if file.exists():
                require(file.read_text('utf-8')==row['value'],'事件 ID 内容冲突')
            else:
                temporary=file.with_suffix('.'+uuid.uuid4().hex+'.tmp');temporary.write_text(row['value'],'utf-8');temporary.replace(file)
            self.db.execute('UPDATE event_outbox SET published=1 WHERE id=?',(row['id'],))
        self.db.commit()
        return {'pending':self.db.execute('SELECT COUNT(*) FROM event_outbox WHERE published=0').fetchone()[0]}

    def detail(self, value, version=None, verify=False):
        id = self.id(value)
        item = dict(self.db.execute('SELECT * FROM assets WHERE id=?', (id,)).fetchone())
        item['categories'] = [r[0] for r in self.db.execute('SELECT value FROM categories WHERE asset_id=? ORDER BY value', (id,))]
        item['tags'] = [r[0] for r in self.db.execute('SELECT value FROM tags WHERE asset_id=? ORDER BY value', (id,))]
        item['aliases'] = [r[0] for r in self.db.execute('SELECT alias FROM aliases WHERE asset_id=?', (id,))]
        item['versions'] = [dict(r) for r in self.db.execute('SELECT * FROM versions WHERE asset_id=? ORDER BY version DESC', (id,))]
        v = next((v for v in item['versions'] if v['version'] == int(version or item['current_version'])), None)
        require(v, '资产版本不存在')
        item.update(v)
        item['path'] = str(bounded(self.root / v['entry_path'], self.home))
        item['files'] = [dict(r) for r in self.db.execute('SELECT * FROM files WHERE asset_id=? AND version=?', (id,v['version']))]
        item['references'] = [dict(r) for r in self.db.execute('SELECT * FROM asset_refs WHERE asset_id=?', (id,))]
        item['lod_variants'] = [dict(r) for r in self.db.execute('SELECT * FROM lod_variants WHERE asset_id=?', (id,))]
        item['issues'] = []
        for f in item['files']:
            p = bounded(self.root / v['base_path'] / f['relative'], self.home)
            if not p.is_file():
                item['issues'].append({'file':f['relative'],'status':'missing'})
            elif verify and (p.stat().st_size != f['size'] or digest(p) != f['hash']):
                item['issues'].append({'file':f['relative'],'status':'changed'})
        item['health'] = '完整' if not item['issues'] else '缺失或已改变'
        item['preview_supported'] = item['format'] in {'gltf','glb','png','jpg','jpeg','webp'} and not item['issues']
        return item

    def labels(self, id, table, values):
        require(isinstance(values,list) and len(values)<=100 and all(isinstance(x,str) and 0<len(x.strip())<=120 for x in values), '分类或标签无效')
        self.db.execute(f'DELETE FROM {table} WHERE asset_id=?', (id,))
        self.db.executemany(f'INSERT INTO {table} VALUES(?,?)', [(id,x.strip()) for x in set(values)])

    def ingest(self, data, adopt=False):
        path = Path(data['path']).resolve()
        if adopt:
            bounded(path, self.home)
        bundle = inspect_bundle(path, data.get('bundle_root'))
        require(not bundle['missing'], '模型依赖缺失：'+', '.join(bundle['missing']))
        duplicate = self.db.execute('SELECT asset_id FROM versions WHERE bundle_hash=?', (bundle['hash'],)).fetchone()
        if duplicate:
            id = duplicate[0]
            if data.get('alias'):
                prior = self.db.execute('SELECT asset_id FROM aliases WHERE alias=?', (data['alias'],)).fetchone()
                require(not prior or prior[0]==id, '别名已经属于另一个资产')
                self.db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)',(data['alias'],id))
            self.audit(id,'duplicate_import',{'name':str(data.get('name',''))[:120]})
            return {'item':self.detail(id),'duplicate':True}
        id = self.id(data['id']) if data.get('id') else 'asset-'+uuid.uuid4().hex
        old = self.db.execute('SELECT * FROM assets WHERE id=?',(id,)).fetchone()
        if old:
            require(data.get('revision')==old['revision'], '记录已被更新，请刷新后重试')
        ver = old['current_version']+1 if old else 1
        base = bundle['base']
        if not adopt:
            target = bounded(self.home / 'imports' / bundle['format'] / bundle['hash'], self.home)
            if target.exists():
                require(all((target / f['relative']).is_file() and digest(target / f['relative'])==f['hash'] for f in bundle['files']), '托管包存在但校验不一致')
            else:
                temporary = target.with_name(target.name+'.partial-'+uuid.uuid4().hex)
                temporary.mkdir(parents=True)
                try:
                    for f in bundle['files']:
                        dest = bounded(temporary / f['relative'], temporary)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(base / f['relative'], dest)
                        require(digest(dest)==f['hash'], '复制后哈希校验失败')
                    temporary.rename(target)
                finally:
                    if temporary.exists():
                        shutil.rmtree(bounded(temporary,self.home))
            base = target
        timestamp = now()
        name = str(data.get('name') or path.stem).strip()
        require(0<len(name)<=120, '资产名称必须为 1–120 字符')
        if not old:
            self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (id,name,str(data.get('description',''))[:4000],bundle['format'],data.get('kind') or kind(bundle['format']),str(data.get('source',''))[:1000],str(data.get('url',''))[:2048],str(data.get('license') or '未确认')[:500],str(data.get('license_file',''))[:2048],ver,1,0,timestamp,timestamp,timestamp))
        else:
            self.db.execute('UPDATE assets SET current_version=?,format=?,updated_at=?,revision=revision+1 WHERE id=?',(ver,bundle['format'],timestamp,id))
        relbase = str(base.relative_to(self.root)).replace('\\','/')
        self.db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)',(id,ver,bundle['hash'],relbase+'/'+bundle['entry'],relbase,sum(f['size'] for f in bundle['files']),int(not adopt),timestamp))
        self.db.executemany('INSERT INTO files VALUES(?,?,?,?,?,?)',[(id,ver,f['relative'],f['hash'],f['size'],f['role']) for f in bundle['files']])
        for field in ('categories','tags'):
            if field in data:
                self.labels(id,field,data[field])
        if data.get('alias'):
            require(not self.db.execute('SELECT 1 FROM aliases WHERE alias=?',(data['alias'],)).fetchone(), '别名已存在')
            self.db.execute('INSERT INTO aliases VALUES(?,?)',(data['alias'],id))
        self.audit(id,'new_version' if old else 'import', {'version':ver,'hash':bundle['hash'],'adopted':adopt})
        return {'item':self.detail(id),'duplicate':False}

    def dispatch(self, op, data):
        if op in {'import','adopt','update_version'}:
            return self.ingest(data,op=='adopt')
        if op in {'get','resolve','verify'}:
            return {'item':self.detail(data['id'],data.get('version'),op=='verify')}
        if op=='list':
            args=[]
            where=['a.archived=?']; args.append(int(data.get('archived') in (True,1,'1','true')))
            for field in ('format','kind','source'):
                if data.get(field):
                    where.append('a.'+field+'=?'); args.append(data[field])
            for field in ('categories','tags'):
                if data.get(field):
                    where.append(f'EXISTS(SELECT 1 FROM {field} t WHERE t.asset_id=a.id AND t.value=?)'); args.append(data[field])
            if data.get('project_id'):
                where.append('EXISTS(SELECT 1 FROM asset_refs r WHERE r.asset_id=a.id AND r.project_id=?)'); args.append(data['project_id'])
            if data.get('q'):
                where.append('(a.name LIKE ? OR a.description LIKE ? OR a.source LIKE ? OR EXISTS(SELECT 1 FROM tags t WHERE t.asset_id=a.id AND t.value LIKE ?))'); args.extend(['%'+str(data['q'])[:200]+'%']*4)
            sort={'name':'a.name ASC','size':'v.size DESC','imported':'a.imported_at DESC'}.get(data.get('sort'),'a.updated_at DESC')
            ids=self.db.execute('SELECT a.id FROM assets a JOIN versions v ON v.asset_id=a.id AND v.version=a.current_version WHERE '+' AND '.join(where)+' ORDER BY '+sort+' LIMIT 500',args)
            return {'items':[self.detail(r[0]) for r in ids], 'facets':{f:[r[0] for r in self.db.execute('SELECT DISTINCT '+f+' FROM assets WHERE archived=0 ORDER BY '+f)] for f in ('format','kind','source')},'categories':[r[0] for r in self.db.execute('SELECT DISTINCT value FROM categories ORDER BY value')],'tags':[r[0] for r in self.db.execute('SELECT DISTINCT value FROM tags ORDER BY value')], 'database':str(self.home/'catalog.sqlite3'),'storage':str(self.home),'limit':500}
        if op=='edit':
            id=self.id(data['id']); current=self.detail(id)
            require(data.get('revision')==current['revision'],'记录已被更新，请刷新后重试')
            for field in ('name','description','kind','source','url','license','license_file'):
                if field in data:
                    text=str(data[field]).strip(); require(len(text)<=4000 and (field!='name' or 0<len(text)<=120),'元数据长度无效')
                    self.db.execute(f'UPDATE assets SET {field}=? WHERE id=?',(text,id))
            for field in ('categories','tags'):
                if field in data: self.labels(id,field,data[field])
            self.db.execute('UPDATE assets SET revision=revision+1,updated_at=? WHERE id=?',(now(),id)); self.audit(id,op,data)
            return {'item':self.detail(id)}
        if op=='reference':
            item=self.detail(data['id'],data.get('version')); require(not item['archived'],'归档资产不能新增引用')
            project=str(data.get('project_id','')); owner_type=str(data.get('owner_type','project')); owner=str(data.get('owner_id') or project)
            require(owner_type in {'project','scene','tool','agent'} and 0<len(owner)<=200,'引用身份无效')
            if project:
                project_db=self.root/'projects/projects.sqlite3'
                require(project_db.is_file(),'项目库不存在')
                with sqlite3.connect(project_db.as_uri()+'?mode=ro',uri=True) as db:
                    row=db.execute('SELECT value FROM projects WHERE id=?',(project,)).fetchone()
                    require(row and not json.loads(row[0]).get('archived'),'关联项目不存在或已归档')
                db.close()
            self.db.execute('INSERT INTO asset_refs VALUES(?,?,?,?,?,?,?) ON CONFLICT(asset_id,project_id,owner_type,owner_id) DO NOTHING',('ref-'+uuid.uuid4().hex,item['id'],item['version'],project,owner_type,owner,now()))
            self.audit(item['id'],op,{'project':project,'owner':owner})
            return {'item':self.detail(item['id'])}
        if op=='unreference':
            id=self.id(data['id']); ref=self.db.execute('SELECT owner_type FROM asset_refs WHERE id=? AND asset_id=?',(data['reference_id'],id)).fetchone();require(not ref or ref[0]!='scene','场景引用由所属工具维护，请在原工具移除资产引用并保存'); self.db.execute('DELETE FROM asset_refs WHERE id=? AND asset_id=?',(data['reference_id'],id)); self.audit(id,op,data)
            return {'item':self.detail(id)}
        if op=='sync_scene':
            owner=str(data['owner_id']); require(0<len(owner)<=200,'场景引用身份无效')
            incoming=[]
            existing={r['asset_id']:dict(r) for r in self.db.execute('SELECT * FROM asset_refs WHERE owner_type=? AND owner_id=?',('scene',owner))}
            for obj in data.get('objects',[]):
                props=obj.get('properties',{})
                if props.get('asset_id'):
                    id=self.id(props['asset_id'])
                    item=self.detail(id,props.get('asset_version') or existing.get(id,{}).get('version'))
                    require(not item['archived'],'场景引用了归档资产')
                    incoming.append((id,item['version']))
            self.db.execute('DELETE FROM asset_refs WHERE owner_type=? AND owner_id=?',('scene',owner))
            for id,version in set(incoming):
                self.db.execute('INSERT INTO asset_refs VALUES(?,?,?,?,?,?,?)',('ref-'+uuid.uuid4().hex,id,version,'','scene',owner,now()))
            self.audit(None,op,{'owner':owner,'asset_count':len(set(incoming))})
            return {'references':len(set(incoming))}
        if op=='lod':
            id=self.id(data['id']); level=int(data['level']); require(0<=level<=3,'LOD 层级无效')
            variant=self.id(data['variant_id']) if data.get('variant_id') else None
            if variant:
                require(variant!=id and self.detail(variant)['format'] in {'gltf','glb'} and not self.detail(variant)['archived'],'LOD 变体无效')
                self.db.execute('INSERT INTO lod_variants VALUES(?,?,?) ON CONFLICT(asset_id,level) DO UPDATE SET variant_id=excluded.variant_id',(id,level,variant))
            else: self.db.execute('DELETE FROM lod_variants WHERE asset_id=? AND level=?',(id,level))
            self.audit(id,op,data); return {'item':self.detail(id)}
        if op in {'archive','delete','restore'}:
            id=self.id(data['id']); item=self.detail(id)
            if op!='restore':
                require(not item['references'] and not self.db.execute('SELECT 1 FROM lod_variants WHERE variant_id=?',(id,)).fetchone(),'资产仍被项目、场景或 LOD 引用，请先查看并解除引用')
            # Physical destruction is deliberately separated from catalog archiving.
            require(op!='delete' or data.get('confirm')==id,'删除需要确认完整资产 ID')
            self.db.execute('UPDATE assets SET archived=?,revision=revision+1,updated_at=? WHERE id=?',(0 if op=='restore' else 1,now(),id)); self.audit(id,op,{'files_preserved':True})
            return {'item':self.detail(id),'files_preserved':True}
        if op=='scan':
            folder=Path(data['path']).resolve(); require(folder.is_dir(),'扫描目录不存在')
            require(folder!=Path(folder.anchor),'请选择明确的资源目录，不能扫描整块磁盘')
            errors=[]; checked=unchanged=0; candidates=[]
            for base, dirs, names in os.walk(folder,followlinks=False):
                dirs[:]=[d for d in dirs if d not in {'node_modules','.venv','.git','__pycache__','.tools','.python'} and not Path(base,d).is_symlink()]
                for name in names:
                    path=Path(base,name)
                    if path.suffix.lower().lstrip('.') not in FORMATS or path.is_symlink(): continue
                    checked+=1; require(checked<=10000,'扫描超出 10000 文件预算，请分目录处理')
                    try:
                        stat=path.stat(); previous=self.db.execute('SELECT * FROM scan_files WHERE path=?',(str(path),)).fetchone()
                        if previous and previous['mtime_ns']==stat.st_mtime_ns and previous['size']==stat.st_size and all(Path(f['path']).is_file() and Path(f['path']).stat().st_mtime_ns==f['mtime_ns'] and Path(f['path']).stat().st_size==f['size'] for f in json.loads(previous['result']).get('dependencies',[])):
                            info=json.loads(previous['result']); unchanged+=1
                        else:
                            bundle=inspect_bundle(path,folder)
                            info={'format':bundle['format'],'hash':bundle['hash'],'missing':bundle['missing'],'size':sum(f['size'] for f in bundle['files']),'dependencies':[{'path':str(folder / f['relative']),'mtime_ns':(folder / f['relative']).stat().st_mtime_ns,'size':f['size']} for f in bundle['files'] if f['role']!='entry']}
                            self.db.execute('INSERT INTO scan_files VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns,size=excluded.size,hash=excluded.hash,result=excluded.result',(str(path),stat.st_mtime_ns,stat.st_size,bundle['hash'],json.dumps(info)))
                        duplicate=self.db.execute('SELECT asset_id FROM versions WHERE bundle_hash=?',(info['hash'],)).fetchone()
                        info['duplicate_id']=duplicate[0] if duplicate else None
                        cid=hashlib.sha256(str(path).encode()).hexdigest()[:32]
                        status='duplicate' if duplicate else ('incomplete' if info['missing'] else 'pending')
                        self.db.execute('INSERT INTO scan_candidates VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET status=excluded.status,info=excluded.info,updated_at=excluded.updated_at',(cid,str(path),str(folder),status,json.dumps(info),now()))
                        candidates.append({'id':cid,'path':str(path),'status':status,**info})
                    except Exception as exc: errors.append({'path':str(path),'error':str(exc)})
            self.audit(None,op,{'path':str(folder),'checked':checked,'unchanged':unchanged,'errors':errors})
            return {'candidates':candidates[:500],'checked':checked,'unchanged':unchanged,'errors':errors}
        if op=='confirm_scan':
            row=self.db.execute('SELECT * FROM scan_candidates WHERE id=?',(data['candidate_id'],)).fetchone(); require(row,'扫描候选不存在')
            result=self.ingest({**data,'path':row['path'],'bundle_root':row['bundle_root']})
            self.db.execute('UPDATE scan_candidates SET status=? WHERE id=?',('imported',row['id'])); return result
        if op=='history':
            id=self.id(data['id']); return {'items':[dict(r) for r in self.db.execute('SELECT * FROM history WHERE asset_id=? ORDER BY seq DESC LIMIT 100',(id,))]}
        if op=='retry_events':
            return {'pending':self.db.execute('SELECT COUNT(*) FROM event_outbox WHERE published=0').fetchone()[0]}
        raise ValueError('未知资产操作')

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',required=True); parser.add_argument('--request',required=True); parser.add_argument('--response',required=True); args=parser.parse_args()
    raw=Path(args.request).read_bytes(); request=json.loads(raw); response={'schema_version':SCHEMA,'run_id':request.get('run_id'),'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'source_sha256':hashlib.sha256(raw).hexdigest()}
    repo=None
    try:
        require(request.get('schema_version')==SCHEMA and Path(request.get('root','')).resolve()==Path(args.root).resolve(),'JSON 交接身份不匹配')
        require(request.get('producer',{}).get('script') in {'workbench_assets_v0001','workbench_assets_v0002','asset_acceptance_v0001','landscape_service_v0002','landscape_service_v0003','asset_seed_v0001','landscape_asset_register_v0001'},'调用方身份不允许')
        repo=Repository(args.root)
        with repo.db:
            old=repo.db.execute('SELECT * FROM requests WHERE id=?',(request['run_id'],)).fetchone()
            if old:
                require(old['hash']==response['source_sha256'],'重复请求内容发生改变'); result=json.loads(old['result'])
            else:
                result=repo.dispatch(request['operation'],request.get('input',{}))
                if request['operation'] not in {'list','get','resolve','verify','history'}:
                    repo.db.execute('INSERT INTO requests VALUES(?,?,?)',(request['run_id'],response['source_sha256'],json.dumps(result,ensure_ascii=False)))
        response.update(status='completed',result=result)
        try:
            response['events']=repo.publish()
        except Exception as exc:
            response['event_warning']='数据已提交；事件待重试：'+str(exc)
    except Exception as exc:
        response.update(status='failed',error=str(exc))
    finally:
        if repo: repo.db.close()
    target=Path(args.response); target.parent.mkdir(parents=True,exist_ok=True); temporary=target.with_suffix('.partial'); temporary.write_text(json.dumps(response,ensure_ascii=False),encoding='utf-8'); temporary.replace(target)
    return 0 if response['status']=='completed' else 1

if __name__=='__main__':
    raise SystemExit(main())
