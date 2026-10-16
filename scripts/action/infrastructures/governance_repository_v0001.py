# ============================================================
# 文件名: governance_repository_v0001.py
# 中文名: 规范权威文件、版本索引与执行审计
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / governance
# 脚本定位: Git 规范文件与 SQLite 查询审计的独立 JSON 入口
# 职责说明:
# - 保存可读取的规范、提示词、历史、校验与限定例外
# 本脚本做什么:
# - 使用现有项目 Git 和 SQLite，不执行规范中的任意代码
# 本脚本不做什么:
# - 不授予 AI 权限，不控制工作台管理边界外的程序
# 制度边界声明:
# - 原规范只读引用；修改使用版本前置条件和限定 Git 路径
# 可更新: True
# ============================================================
# ALIAS_META
# alias: governance_repository_v0001
# family: governance_repository
# role: standards_metadata_and_audit_worker
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/governance_repository_v0001.py
# input:
#   - governance.repository.v1 JSON request
# output:
#   - structured standards, versions and factual validation states
# depends_on:
#   - Python standard library and existing Git
# used_by:
#   - workbench_governance_v0001
# ============================================================
import argparse
import difflib
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

SCRIPT_NAME='governance_repository_v0001'
SCRIPT_FAMILY='governance_repository'
SCRIPT_VERSION='v0001'
DEFAULT_ENCODING='utf-8'
SCHEMA='governance.repository.v1'
KINDS={'principle','engineering','workflow','prompt'}
CHECK_STATES={'passed','failed','needs_confirmation','approved_exception','unavailable'}
def require(ok,message):
    if not ok:raise ValueError(message)
def stable(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
def digest(value):return hashlib.sha256(stable(value).encode()).hexdigest()
def now():return time.time()
def identity(value):
    require(isinstance(value,str) and re.fullmatch(r'[a-z][a-z0-9_.-]{2,100}',value),'RULE_ID_INVALID');return value
def clean_record(value):
    require(isinstance(value,dict),'RULE_OBJECT_REQUIRED')
    allowed={'schema_version','id','kind','name','description','content','scopes','enforcement','state','references','validators','steps','prerequisites','failure_handling','source_refs','version','updated_at','change_registration'}
    require(set(value)<=allowed,'RULE_FIELDS_INVALID');identity(value.get('id'))
    require(value.get('schema_version')=='workbench.governance.rule.v1' and value.get('kind') in KINDS,'RULE_SCHEMA_INVALID')
    require(isinstance(value.get('enforcement'),str) and re.fullmatch(r'[a-z][a-z0-9_.-]{2,60}',value['enforcement']) and value.get('state') in {'draft','active','retired'},'RULE_STATUS_INVALID')
    for key,limit in [('name',150),('description',2000),('content',60000)]:require(isinstance(value.get(key),str) and len(value[key])<=limit and (key!='name' or value[key].strip()),'RULE_TEXT_INVALID:'+key)
    for key in ['scopes','references','validators','steps','prerequisites','failure_handling','source_refs']:
        value.setdefault(key,[]);require(isinstance(value[key],list) and len(value[key])<=100 and all(isinstance(x,str) and len(x)<=2000 for x in value[key]),'RULE_LIST_INVALID:'+key)
    for id in value['references']:identity(id)
    require(all(x in {'github.intake.v1','tool.independence.v1'} for x in value['validators']),'VALIDATOR_NOT_REGISTERED')
    require(type(value.get('version')) is int and 1<=value['version']<=100000,'RULE_VERSION_INVALID')
    if 'change_registration' in value:
        registration=value['change_registration'];require(isinstance(registration,dict) and registration.get('rule_id')==value['id'] and registration.get('to_version')==value['version'] and isinstance(registration.get('reason'),str) and len(registration['reason'])>=5 and isinstance(registration.get('id'),str),'CHANGE_REGISTRATION_INVALID')
        require(registration.get('approval',{}).get('reviewer')=='local-user' and registration['approval'].get('channel')=='electron-native-dialog','CHANGE_APPROVAL_INVALID')
    return value
def rule(id,kind,name,content,**extra):
    return clean_record({'schema_version':'workbench.governance.rule.v1','id':id,'kind':kind,'name':name,'description':name,'content':content,'scopes':['global'],'enforcement':'ordinary','state':'active','references':[],'validators':[],'steps':[],'prerequisites':[],'failure_handling':[],'source_refs':[],'version':1,'updated_at':now(),**extra})
def seeds():
    principles=[('local-data','本地数据优先','业务数据保留在本地事实源；展示索引不替代原数据。'),('independent-tools','独立工具原则','工具应可以独立启动并执行核心功能；工作台负责集成和观察。'),('reuse','复用与低耦合','优先复用已有数据库、资源身份、总控和渲染能力。'),('traceability','数据完整性与追溯','事实与派生判断分离，跨库身份、来源和版本保持可追溯。'),('rollback','可回退','晋升前保留可运行基线、测试证据与回退路径。'),('lifecycle','工作台与工具生命周期分离','切换或关闭观察标签不得无故终止独立工具任务。')]
    result=[rule('principle.'+id,'principle',name,text,source_refs=['AGENTS.md','application_agent.md']) for id,name,text in principles]
    result += [rule('engineering.github-intake','engineering','GitHub 受管理接入规范','正式启用前核验来源、固定版本与内容哈希、许可证明和依赖声明；代码风险与使用许可需要人工确认。缺失检查不能当作通过。',enforcement='mandatory',scopes=['github'],validators=['github.intake.v1'],source_refs=['docs/development/action_db_feature_development_and_registration_workflow.md']),rule('engineering.independent-tools','engineering','独立应用检查标准','检查启动入口、依赖、环境、健康检查、数据存储、适配接口、独立运行、退出与异常处理。旧工具先审计差距，不批量停用。新接入的工具必须检查或得到有范围的人工例外。',enforcement='mandatory',scopes=['tools'],validators=['tool.independence.v1'],references=['principle.independent-tools','principle.lifecycle'])]
    result += [rule('workflow.github-intake','workflow','第三方 GitHub 接入流程','复用现有能力收藏与 GitHub 监管机制。',scopes=['github'],references=['engineering.github-intake'],prerequisites=['明确公开来源、版本和使用目的'],steps=['登记来源，不自动启用','通过已有收藏器下载固定版本并保存许可','核验实际文件与内容哈希','在规范中心检查并完成人工风险确认','通过受管理启用入口启用；运行授权仍单独管理'],failure_handling=['下载失败保留来源登记','检查失败保持禁用','未知许可或缺少检查能力显示待确认，不伪装通过']),rule('workflow.tool-intake','workflow','独立工具接入流程','复用 tool.v1 发现、注册、健康和停止机制。',scopes=['tools'],references=['engineering.independent-tools'],prerequisites=['独立入口与依赖声明'],steps=['建立工具声明与独立启动说明','静态审计，记录未证实项','显式运行独立验证并确认退出','接入工作台；标签与进程生命周期分离'],failure_handling=['缺失声明形成差距报告','未知工具不自动试运行','旧工具缺口保留功能并显示审计状态'])]
    result += [rule('prompt.development','prompt','Codex 开发规范提示词','执行前读取适用规范：\n{{rule:principle.traceability}}\n{{rule:principle.rollback}}\n沿用已有架构和接口，先查真实调用链，再隔离开发、实际验收、晋升和记录回退。',references=['principle.traceability','principle.rollback']),rule('prompt.github-intake','prompt','GitHub 接入提示词','按此规范接入资源：\n{{rule:engineering.github-intake}}\n记录真实来源、版本、许可与文件核验。人工检查未完成时不得自行宣称通过或启用。',scopes=['github'],references=['engineering.github-intake']),rule('prompt.tool-intake','prompt','独立工具开发与接入提示词','遵守：\n{{rule:engineering.independent-tools}}\n报告已证明的独立能力、缺失声明和实际验收结果；提示词不能替代执行校验。',scopes=['tools'],references=['engineering.independent-tools'])]
    result.append(rule('gov.001','engineering','GOV-001：规范变更与等级管理规则','规范内容分类和约束等级分开；等级定义与强制规范变更必须提出申请，由用户通过受限桌面确认审批并经校验后生效。降级、缩小范围、停用及修改校验器均属于受控变更。AI 可以提出建议，不能自行批准或发布。审批、版本、差异与回滚记录永久保留；与其他规则冲突时本变更控制优先，执行失败保持原有效版本。此元规范的保护由独立后端边界执行，不能通过修改正文解除。',enforcement='mandatory',scopes=['global']))
    return result
class Repository:
    def __init__(self,root):
        self._levels_cache=None;self.root=root;self.folder=root/'governance';self.files=self.folder/'rules';self.files.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.folder/'catalog.sqlite3',timeout=30);self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA foreign_keys=ON')
        with self.db:self.db.executescript('''CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,enforcement TEXT NOT NULL,hash TEXT NOT NULL,body TEXT NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions(rule_id TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,git_commit TEXT,reason TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(rule_id,version));
CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,rule_version INTEGER NOT NULL,target_id TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,body TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,audit_id TEXT NOT NULL REFERENCES audits(id),check_id TEXT NOT NULL,reason TEXT NOT NULL,reviewer TEXT NOT NULL,expires_at REAL NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,operation TEXT NOT NULL,target TEXT NOT NULL,body TEXT NOT NULL,at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS rules_search ON rules(kind,state,name);
CREATE INDEX IF NOT EXISTS audits_target ON audits(target_id,rule_id,created_at DESC);
CREATE INDEX IF NOT EXISTS approvals_audit ON approvals(audit_id,check_id);
CREATE TABLE IF NOT EXISTS rule_levels(id TEXT PRIMARY KEY,name TEXT NOT NULL,base TEXT NOT NULL CHECK(base IN ('mandatory','ordinary')),description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,kind TEXT NOT NULL,target TEXT NOT NULL,body TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,created_at REAL NOT NULL,result TEXT);
CREATE TABLE IF NOT EXISTS change_registrations(id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,from_version INTEGER NOT NULL,to_version INTEGER NOT NULL,body TEXT NOT NULL,created_at REAL NOT NULL);
INSERT OR IGNORE INTO migrations VALUES(1,strftime('%s','now'));
INSERT OR IGNORE INTO migrations VALUES(2,strftime('%s','now'));
INSERT OR IGNORE INTO migrations VALUES(3,strftime('%s','now'));''')
        if 'behavior' not in {row[1] for row in self.db.execute('PRAGMA table_info(rule_levels)')}:
            with self.db:self.db.execute("ALTER TABLE rule_levels ADD COLUMN behavior TEXT NOT NULL DEFAULT 'warn'");self.db.execute("UPDATE rule_levels SET behavior='block' WHERE base='mandatory'");self.db.execute("INSERT OR IGNORE INTO migrations VALUES(4,strftime('%s','now'))")
    def index_level(self,item):
        self.db.execute('INSERT OR REPLACE INTO rule_levels(id,name,base,description,behavior) VALUES(?,?,?,?,?)',(item['id'],item['name'],item['base'],item.get('description',''),item['behavior']))
    def levels(self):
        if self._levels_cache is not None:return self._levels_cache
        file=self.folder/'levels.json'
        if file.exists():
            require(not file.is_symlink() and file.stat().st_size<=200000,'LEVEL_FILE_INVALID')
            value=json.loads(file.read_text(DEFAULT_ENCODING))
            require(value.get('schema_version')=='workbench.governance.levels.v1','LEVEL_SCHEMA_INVALID')
            if self.db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():require(stable(json.loads(self.git(['show','HEAD:governance/levels.json']).stdout))==stable(value),'LEVEL_AUTHORITY_CONFLICT')
            items=value['items'];require(isinstance(items,list) and 2<=len(items)<=100,'LEVEL_COUNT_INVALID')
            require(all(x.get('behavior') in {'block','warn','record'} and (x['base']!='mandatory' or x['behavior']=='block') for x in items),'LEVEL_BEHAVIOR_INVALID')
            require(len({x['id'] for x in items})==len(items),'LEVEL_ID_DUPLICATE')
            for item in items:
                identity(item['id']);require(item['base'] in {'mandatory','ordinary'} and isinstance(item['name'],str) and 1<=len(item['name'])<=100,'LEVEL_INVALID')
            require(any(x['id']=='mandatory' and x['base']=='mandatory' for x in items) and any(x['id']=='ordinary' and x['base']=='ordinary' for x in items),'BUILTIN_LEVEL_MISSING')
            self._levels_cache=items
            return items
        return [{'id':'mandatory','name':'强制规范','base':'mandatory','description':'受管理入口实施约束，变更必须显式登记。','behavior':'block'},{'id':'ordinary','name':'普通规范','base':'ordinary','description':'指导性规范，修改保留版本与原因。','behavior':'warn'}]
    def mandatory(self,code):
        levels={x['id']:x for x in self.levels()};require(code in levels,'RULE_LEVEL_NOT_REGISTERED')
        return levels[code]['base']=='mandatory' or levels[code]['behavior']=='block'
    def propose(self,kind,data):
        require(kind in {'rule','level','exception'},'CHANGE_KIND_INVALID')
        reason=str(data.get('reason','')).strip();require(5<=len(reason)<=1000,'CHANGE_REASON_REQUIRED')
        actor=str(data.get('actor','workbench-client')).strip();require(1<=len(actor)<=100,'CHANGE_ACTOR_REQUIRED')
        if kind=='rule':
            value=dict(data['record']);value.pop('change_registration',None);old=self.checked(value['id']) if self.db.execute('SELECT 1 FROM rules WHERE id=?',(value['id'],)).fetchone() else None
            require(data.get('expected_version',0)==(old['version'] if old else 0),'VERSION_CONFLICT')
            value['version']=(old['version']+1) if old else 1;value['updated_at']=now();clean_record(value);self.mandatory(value['enforcement'])
            if value['id']=='gov.001':require(value['enforcement']=='mandatory' and value['state']=='active' and value['scopes']==['global'],'GOV_001_PROTECTION_CANNOT_BE_DISABLED')
            payload={**data,'record':value,'before':old};target=value['id']
        elif kind=='exception':
            row=self.db.execute('SELECT body FROM audits WHERE id=?',(data.get('audit_id'),)).fetchone();require(row,'AUDIT_MISSING');audit=json.loads(row[0])
            allowed={x['id'] for x in audit['checks'] if x['waivable'] and x['status']!='passed'}
            require(isinstance(data.get('check_ids'),list) and data['check_ids'] and set(data['check_ids'])<=allowed,'CHECK_NOT_WAIVABLE')
            payload={**data,'before':audit};target=audit['target_id']
        else:
            identity(data['id']);levels=self.levels();old=next((x for x in levels if x['id']==data['id']),None);behavior=data.get('behavior','block' if data['base']=='mandatory' else 'warn')
            require(data['base'] in {'mandatory','ordinary'} and behavior in {'block','warn','record'} and (data['base']!='mandatory' or behavior=='block'),'LEVEL_BEHAVIOR_INVALID')
            if data['id'] in {'mandatory','ordinary'}:require(old and data['base']==old['base'] and (data['id']!='mandatory' or behavior=='block'),'BUILTIN_LEVEL_BASE_PROTECTED')
            payload={**data,'behavior':behavior,'expected_levels_hash':digest(levels),'before':old};target=data['id']
        payload.pop('_trusted_confirmation',None)
        fingerprint=digest(payload);id=uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?)',(id,kind,target,stable(payload),fingerprint,'pending_user_confirmation',actor,reason,now(),None))
            self.event('change_requested',target,{'proposal_id':id,'kind':kind,'actor':actor,'reason':reason,'fingerprint':fingerprint})
        return {'proposal':{'id':id,'kind':kind,'target':target,'status':'pending_user_confirmation','actor':actor,'reason':reason,'payload':payload,'fingerprint':fingerprint},'pending':True}
    def proposal(self,id):
        row=self.db.execute('SELECT * FROM proposals WHERE id=?',(id,)).fetchone();require(row,'PROPOSAL_MISSING');item=dict(row);item['payload']=json.loads(item.pop('body'));item['diff']=''.join(difflib.unified_diff(json.dumps(item['payload'].get('before'),ensure_ascii=False,indent=2).splitlines(True),json.dumps(item['payload'].get('record', {k:v for k,v in item['payload'].items() if k not in {'before','reason','actor','expected_levels_hash'}}),ensure_ascii=False,indent=2).splitlines(True),fromfile='生效前',tofile='拟发布'))
        return {'proposal':item}
    def decide(self,data):
        require(data.get('_trusted_confirmation') is True,'HUMAN_CONFIRMATION_CHANNEL_REQUIRED')
        item=self.proposal(data['id'])['proposal'];require(item['status']=='pending_user_confirmation','PROPOSAL_ALREADY_DECIDED');require(data['fingerprint']==item['fingerprint'],'PROPOSAL_FINGERPRINT_MISMATCH')
        if data.get('approved') is not True:
            with self.db:self.db.execute("UPDATE proposals SET status='rejected',result=? WHERE id=?",(stable({'reviewer':'local-user','at':now()}),item['id']));self.event('change_rejected',item['target'],{'proposal_id':item['id'],'reviewer':'local-user'})
            return {'rejected':True}
        with self.db:
            claimed=self.db.execute("UPDATE proposals SET status='user_confirmed_validating' WHERE id=? AND status='pending_user_confirmation'",(item['id'],));require(claimed.rowcount==1,'PROPOSAL_ALREADY_DECIDED')
            self.event('change_user_confirmed',item['target'],{'proposal_id':item['id'],'reviewer':'local-user','at':now()})
        payload=item['payload'];payload['_trusted_confirmation']=True;payload['approval']={'proposal_id':item['id'],'fingerprint':item['fingerprint'],'reviewer':'local-user','approved_at':now(),'channel':'electron-native-dialog'}
        try:
            result=self.register_level(payload) if item['kind']=='level' else self.approve(payload) if item['kind']=='exception' else self.save(payload)
        except Exception as error:
            with self.db:self.db.execute("UPDATE proposals SET status='validation_failed',result=? WHERE id=?",(stable({'error':str(error),'reviewer':'local-user','at':now()}),item['id']));self.event('change_validation_failed',item['target'],{'proposal_id':item['id'],'error':str(error)})
            raise
        with self.db:self.db.execute("UPDATE proposals SET status='published',result=? WHERE id=?",(stable(result),item['id']));self.event('change_published',item['target'],payload['approval'])
        return result
    def register_level(self,data):
        if data.get('_trusted_confirmation') is not True:return self.propose('level',data)
        require(data.get('approval',{}).get('reviewer')=='local-user','HUMAN_APPROVAL_REQUIRED')
        item={'id':identity(data['id']),'name':str(data['name']).strip(),'base':data['base'],'description':str(data.get('description','')).strip(),'behavior':data['behavior']}
        require(1<=len(item['name'])<=100 and item['base'] in {'mandatory','ordinary'} and len(item['description'])<=2000,'LEVEL_INVALID')
        reason=str(data.get('reason','')).strip();require(3<=len(reason)<=1000,'CHANGE_REASON_REQUIRED')
        self.db.execute('BEGIN IMMEDIATE');self._levels_cache=None;items=self.levels();require(digest(items)==data['expected_levels_hash'],'LEVEL_VERSION_CONFLICT');require(len(items)<100 or item['id'] in {x['id'] for x in items},'LEVEL_LIMIT')
        file=self.folder/'levels.json';relative=file.relative_to(self.root).as_posix()
        previous=file.read_bytes();require(stable(json.loads(self.git(['show','HEAD:'+relative]).stdout))==stable(json.loads(previous)),'LEVEL_AUTHORITY_CONFLICT')
        require(self.git(['diff','--cached','--quiet','--',relative],False).returncode==0,'LEVEL_HAS_STAGED_CHANGES')
        items=[x for x in items if x['id']!=item['id']]+[item]
        try:
            history=json.loads(previous).get('changes',[]);history.append({'id':uuid.uuid4().hex,'level_id':item['id'],'before':data.get('before'),'after':item,'reason':reason,'requester':data.get('actor','workbench-client'),'approval':data['approval'],'at':now()});file.write_text(stable({'schema_version':'workbench.governance.levels.v1','items':items,'changes':history})+'\n',DEFAULT_ENCODING);commit=self.commit([file],reason)
        except Exception:
            file.write_bytes(previous);self.db.rollback();raise
        with self.db:
            self.index_level(item)
            self.event('register_level',item['id'],{'reason':reason,'git_commit':commit,'base':item['base'],'behavior':item['behavior'],'approval':data['approval']})
        return {'item':item,'git_commit':commit}
    def git(self,args,check=True):
        result=subprocess.run(['git','-c','safe.directory='+str(self.root),'-c','commit.gpgsign=false','-c','core.hooksPath='+str(self.folder/'metadata_hooks_disabled'),'-c','user.name=Local AI Workbench','-c','user.email=workbench@localhost','-C',str(self.root),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30)
        if check:require(result.returncode==0,'GIT_UNAVAILABLE_OR_CONFLICT: '+result.stderr[:400])
        return result
    def event(self,operation,target,body):self.db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(uuid.uuid4().hex,operation,target,stable(body),now()))
    def file(self,id):return self.files/(identity(id)+'.json')
    def source(self,id):
        file=self.file(id);require(file.is_file() and not file.is_symlink() and file.stat().st_size<=200000,'RULE_SOURCE_MISSING')
        value=clean_record(json.loads(file.read_text(DEFAULT_ENCODING)))
        if id=='gov.001':require(value['state']=='active' and value['enforcement']=='mandatory' and value['scopes']==['global'],'GOV_001_PROTECTION_CANNOT_BE_DISABLED')
        return value
    def commit(self,paths,reason):
        relative=[p.relative_to(self.root).as_posix() for p in paths];self.git(['add','-f','--',*relative]);self.git(['commit','--only','-m','Governance: '+reason[:120],'--',*relative]);return self.git(['rev-parse','HEAD']).stdout.strip()
    def index(self,value,commit,reason):
        body=stable(value);hash=digest(value)
        self.db.execute('INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?,?,?,?,?)',(value['id'],value['kind'],value['name'],value['version'],value['state'],value['enforcement'],hash,body,value['updated_at']))
        self.db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?,?)',(value['id'],value['version'],body,hash,commit,reason,now()))
    def recover_history(self,id):
        relative=self.file(id).relative_to(self.root).as_posix()
        commits=self.git(['log','--format=%H','--reverse','--',relative]).stdout.splitlines()
        require(len(commits)<=1000,'HISTORY_LIMIT_REQUIRES_MANUAL_RECOVERY')
        for commit in commits:
            result=self.git(['show',commit+':'+relative],False)
            if result.returncode:continue
            value=clean_record(json.loads(result.stdout))
            reason=self.git(['log','-1','--format=%s',commit]).stdout.strip()
            self.db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?,?)',(id,value['version'],stable(value),digest(value),commit,reason,value['updated_at']))
            registration=value.get('change_registration')
            if registration:self.db.execute('INSERT OR IGNORE INTO change_registrations VALUES(?,?,?,?,?,?)',(registration['id'],id,registration['from_version'],registration['to_version'],stable(registration),registration['registered_at']))
    def initialize(self,tools):
        if self.db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():return
        self.git(['rev-parse','--show-toplevel'])
        existing=list(self.files.glob('*.json'))
        level_file=self.folder/'levels.json'
        marker=self.folder/'policy_bootstrap_v0001.json'
        if existing:
            require(marker.is_file() and level_file.is_file(),'EXISTING_RULES_NEED_POLICY_BOOTSTRAP')
            require(stable(json.loads(self.git(['show','HEAD:governance/levels.json']).stdout))==stable(json.loads(level_file.read_text(DEFAULT_ENCODING))),'LEVEL_AUTHORITY_CONFLICT')
            with self.db:
                for item in self.levels():self.index_level(item)
            bootstrap=json.loads(marker.read_text(DEFAULT_ENCODING));relative=marker.relative_to(self.root).as_posix()
            require(stable(json.loads(self.git(['show','HEAD:'+relative]).stdout))==stable(bootstrap),'POLICY_BOOTSTRAP_NOT_COMMITTED')
            for file in existing:
                value=self.source(file.stem);relative=file.relative_to(self.root).as_posix()
                require(digest(json.loads(self.git(['show','HEAD:'+relative]).stdout))==digest(value),'SOURCE_NOT_COMMITTED')
                commit=self.git(['log','-1','--format=%H','--',relative]).stdout.strip()
                with self.db:self.index(value,commit,'recovered index from Git authority');self.recover_history(value['id'])
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('legacy_tools',stable(bootstrap['legacy_tools'])))
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('initialized','true'))
                self.event('recover_index','governance',{'rules':len(existing)})
            return
        values=seeds();created=[]
        levels=self.levels();level_file.write_text(stable({'schema_version':'workbench.governance.levels.v1','items':levels})+'\n',DEFAULT_ENCODING);created.append(level_file)
        source_docs=[('engineering.root-agent','系统工程总规则','AGENTS.md'),('engineering.application-agent','应用接入与 AI 总控规范','application_agent.md'),('engineering.development-agent','开发与晋升纪律','development_agent.md'),('engineering.script-format','编程语言脚本格式规范','PROGRAMMING_LANGUAGE_SCRIPT_FORMAT.md'),('engineering.format-template','脚本格式母版','script_format_agent.md'),('workflow.action-registration','Action 开发登记流程','docs/development/action_db_feature_development_and_registration_workflow.md')]
        for id,name,relative in source_docs:
            file=(self.root/relative).resolve()
            if file.is_file() and file.is_relative_to(self.root) and file.stat().st_size<=200000:
                content=file.read_text(DEFAULT_ENCODING)
                if len(content)<=60000:values.append(rule(id,'workflow' if id.startswith('workflow.') else 'engineering',name,content,enforcement='ordinary',source_refs=[relative,'sha256:'+hashlib.sha256(file.read_bytes()).hexdigest()]))
        for value in values:
            file=self.file(value['id']);require(not file.exists(),'SEED_SOURCE_ALREADY_EXISTS_USE_REINDEX');file.write_text(stable(value)+'\n',DEFAULT_ENCODING);created.append(file)
        bootstrap={'schema_version':'workbench.governance.bootstrap.v1','legacy_tools':sorted({x['id'] for x in tools}),'created_at':now()}
        marker.write_text(stable(bootstrap)+'\n',DEFAULT_ENCODING);created.append(marker)
        try:commit=self.commit(created,'initial standards and prompt templates')
        except Exception:
            # Files remain as recovery evidence, never falsely report successful initialization.
            raise
        with self.db:
            for value in values:self.index(value,commit,'initial seed; existing Markdown remains unchanged')
            for item in levels:self.index_level(item)
            self.db.execute('INSERT INTO settings VALUES(?,?)',('legacy_tools',stable(bootstrap['legacy_tools'])))
            self.db.execute('INSERT INTO settings VALUES(?,?)',('initialized','true'));self.event('initialize','governance',{'git_commit':commit,'rules':len(values)})
    def checked(self,id):
        value=self.source(id);row=self.db.execute('SELECT * FROM rules WHERE id=?',(id,)).fetchone();require(row and row['hash']==digest(value),'AUTHORITY_INDEX_CONFLICT_REINDEX_REQUIRED');return value
    def listing(self,data):
        query=str(data.get('query','')).lower();scope=data.get('scope');items=[]
        for row in self.db.execute('SELECT * FROM rules ORDER BY kind,name'):
            value=json.loads(row['body']);value['requires_gate']=self.mandatory(value['enforcement']);value['execution_behavior']=next(x['behavior'] for x in self.levels() if x['id']==value['enforcement']);value['source_path']=self.file(value['id']).relative_to(self.root).as_posix();value['source_health']='indexed'
            try:value['source_health']='verified' if digest(self.source(value['id']))==row['hash'] else 'conflict'
            except Exception:value['source_health']='missing_or_invalid'
            if query and query not in stable(value).lower():continue
            if data.get('kind') and value['kind']!=data['kind']:continue
            if scope and 'global' not in value['scopes'] and scope not in value['scopes']:continue
            items.append(value)
        return {'items':items,'levels':self.levels(),'validators':[{'id':'github.intake.v1','name':'GitHub 来源、版本、许可与风险确认','automatic':'文件身份与声明检查','manual':'代码风险及使用范围审核'},{'id':'tool.independence.v1','name':'独立工具声明与运行验证','automatic':'声明检查；文本检查器独立运行','manual':'未知工具运行与专业功能验收'}],'legacy_tools':json.loads(self.db.execute("SELECT value FROM settings WHERE key='legacy_tools'").fetchone()[0]),'git':{'available':self.git(['rev-parse','HEAD'],False).returncode==0,'scope':'existing project repository; only governance/rules are committed'},'storage':'governance/catalog.sqlite3','authority':'governance/rules/*.json'}
    def save(self,data):
        value=dict(data['record']);value.pop('change_registration',None);old=None;id=identity(value.get('id'));row=self.db.execute('SELECT * FROM rules WHERE id=?',(id,)).fetchone()
        if row:
            old=self.checked(id);require(data.get('expected_version')==old['version'],'VERSION_CONFLICT');value['version']=old['version']+1
        else:require(data.get('expected_version') in (None,0),'VERSION_CONFLICT');value['version']=1
        value['updated_at']=now();value=clean_record(value);reason=str(data.get('reason','')).strip();require(3<=len(reason)<=1000,'CHANGE_REASON_REQUIRED')
        mandatory_change=self.mandatory(value['enforcement']) or (old is not None and self.mandatory(old['enforcement']))
        registration=None
        if mandatory_change:
            if data.get('_trusted_confirmation') is not True:return self.propose('rule',data)
            require(data.get('approval',{}).get('reviewer')=='local-user','HUMAN_APPROVAL_REQUIRED')
            require(len(reason)>=5,'MANDATORY_CHANGE_REASON_REQUIRED')
            registration={'id':uuid.uuid4().hex,'rule_id':id,'from_version':old['version'] if old else 0,'to_version':value['version'],'from_level':old['enforcement'] if old else None,'to_level':value['enforcement'],'reason':reason,'actor':data.get('actor','workbench-client'),'approval':data['approval'],'registered_at':now(),'changed_fields':sorted(k for k in value if not old or old.get(k)!=value[k])}
            value['change_registration']=registration
        if id=='gov.001':require(value['enforcement']=='mandatory' and value['state']=='active' and value['scopes']==['global'],'GOV_001_PROTECTION_CANNOT_BE_DISABLED')
        require(all(self.db.execute('SELECT 1 FROM rules WHERE id=?',(ref,)).fetchone() for ref in value['references']),'REFERENCE_MISSING')
        file=self.file(id);relative=file.relative_to(self.root).as_posix();require(self.git(['diff','--cached','--quiet','--',relative],False).returncode==0,'RULE_HAS_UNCOMMITTED_STAGED_CHANGES')
        previous=file.read_bytes() if file.exists() else None;self.db.execute('BEGIN IMMEDIATE')
        try:
            # Recheck the indexed version after acquiring the write lock.
            current=self.db.execute('SELECT version FROM rules WHERE id=?',(id,)).fetchone();require((current[0] if current else 0)==(row['version'] if row else 0),'VERSION_CONFLICT')
            temporary=file.with_suffix('.tmp');temporary.write_text(stable(value)+'\n',DEFAULT_ENCODING);temporary.replace(file)
            try:commit=self.commit([file],reason)
            except Exception:
                if previous is not None:file.write_bytes(previous)
                else:file.unlink(missing_ok=True)
                raise
            self.index(value,commit,reason)
            if registration:
                self.db.execute('INSERT INTO change_registrations VALUES(?,?,?,?,?,?)',(registration['id'],id,registration['from_version'],registration['to_version'],stable(registration),registration['registered_at']))
                self.event('registered_mandatory_change',id,{**registration,'git_commit':commit})
            self.event('save',id,{'version':value['version'],'git_commit':commit,'reason':reason,'actor':data.get('actor','workbench-client')});self.db.commit()
            return {'item':value,'git_commit':commit}
        except Exception:self.db.rollback();raise
    def history(self,data):
        id=identity(data['id']);rows=[dict(x) for x in self.db.execute('SELECT * FROM versions WHERE rule_id=? ORDER BY version DESC',(id,))]
        for row in rows:row['body']=json.loads(row['body'])
        diff=''
        if len(rows)>=2:diff=''.join(difflib.unified_diff(json.dumps(rows[1]['body'],ensure_ascii=False,indent=2).splitlines(True),json.dumps(rows[0]['body'],ensure_ascii=False,indent=2).splitlines(True),fromfile='v'+str(rows[1]['version']),tofile='v'+str(rows[0]['version'])))
        return {'items':rows,'diff':diff,'registrations':[json.loads(x[0]) for x in self.db.execute('SELECT body FROM change_registrations WHERE rule_id=? ORDER BY to_version DESC',(id,))]}
    def reindex(self):
        self.git(['rev-parse','HEAD']);indexed=[];conflicts=[]
        for file in sorted(self.files.glob('*.json'))[:1000]:
            try:
                value=self.source(file.stem);require(value['id']==file.stem,'RULE_PATH_ID_MISMATCH');relative=file.relative_to(self.root).as_posix()
                committed=json.loads(self.git(['show','HEAD:'+relative]).stdout);require(digest(committed)==digest(value),'SOURCE_NOT_COMMITTED')
                row=self.db.execute('SELECT version,hash FROM rules WHERE id=?',(value['id'],)).fetchone();require(not row or row['hash']==digest(value) or value['version']>row['version'],'EXTERNAL_EDIT_MUST_INCREMENT_VERSION')
                if row and value['version']>row['version'] and (self.mandatory(value['enforcement']) or self.mandatory(json.loads(self.db.execute('SELECT body FROM rules WHERE id=?',(value['id'],)).fetchone()[0])['enforcement'])):
                    require(value.get('change_registration',{}).get('to_version')==value['version'],'MANDATORY_CHANGE_REGISTRATION_REQUIRED')
                commit=self.git(['log','-1','--format=%H','--',relative]).stdout.strip()
                with self.db:self.index(value,commit,'explicit reindex from Git authority');self.recover_history(value['id'])
                indexed.append(value['id'])
            except Exception as error:conflicts.append({'file':file.name,'error':str(error)})
        with self.db:self.event('reindex','governance',{'indexed':indexed,'conflicts':conflicts})
        return {'indexed':indexed,'conflicts':conflicts}
    def restore(self,data):
        row=self.db.execute('SELECT body FROM versions WHERE rule_id=? AND version=?',(identity(data['id']),data['version'])).fetchone();require(row,'VERSION_MISSING');value=json.loads(row[0]);return self.save({'record':value,'expected_version':data['expected_version'],'reason':data.get('reason','恢复历史版本为新版本'),'register_mandatory_change':data.get('register_mandatory_change')})
    def compose(self,data):
        ids=data.get('ids',[]);require(isinstance(ids,list) and 1<=len(ids)<=20,'PROMPT_SELECTION_INVALID')
        def expand(id,seen):
            require(id not in seen and len(seen)<8,'REFERENCE_CYCLE_OR_DEPTH');value=self.checked(id);body=re.sub(r'\{\{rule:([a-z0-9_.-]+)\}\}',lambda match:expand(match[1],seen+[id]),value['content']);require(len(body)<=200000,'COMPOSITION_TOO_LARGE');return '# '+value['name']+' [v'+str(value['version'])+']\n'+body
        return {'text':'\n\n'.join(expand(identity(id),[]) for id in ids),'ids':ids}
    def validate(self,data):
        rule_id=identity(data['rule_id']);record=self.checked(rule_id);require(record['state']=='active','RULE_NOT_ACTIVE');target=data['target'];require(isinstance(target,dict) and isinstance(target.get('id'),str) and len(target['id'])<180,'VALIDATION_TARGET_INVALID')
        checks=[]
        def check(id,status,message,waivable=False):checks.append({'id':id,'status':status,'message':message,'waivable':waivable})
        if 'github.intake.v1' in record['validators']:
            check('source','passed' if re.fullmatch(r'https://github\.com/[\w.-]+/[\w.-]+/?',str(target.get('source',''))) else 'failed','公开 GitHub 仓库来源',True)
            version=str(target.get('version',''));check('version','passed' if version and version.lower() not in {'unknown','unversioned','main','master','head','latest'} else 'failed','固定版本声明，不接受浮动分支',True)
            check('files','passed' if target.get('verification_status')=='verified' and re.fullmatch(r'[a-f0-9]{64}',str(target.get('bundle_hash',''))) else 'failed','已有资源核验器证明当前文件与登记哈希一致')
            license=str(target.get('license','')).strip();known=license.lower() in {'mit','apache-2.0','bsd-2-clause','bsd-3-clause','isc','cc0','cc0-1.0','mpl-2.0','gpl-3.0-only','gpl-3.0-or-later','lgpl-3.0-only','lgpl-3.0-or-later','unlicense'}
            check('license','passed' if known and target.get('license_file_present') else 'needs_confirmation','可识别许可声明与本地证明文件；内容和使用条件仍需人工审核',True)
            check('dependencies','passed' if target.get('dependency_declaration_present') else 'needs_confirmation','依赖/构建声明可定位，不等于所有依赖已安装',True)
            check('risk_review','needs_confirmation','人工确认代码风险、使用目的与许可适用范围；未实现漏洞扫描，不冒充通过',True)
        elif 'tool.independence.v1' in record['validators']:
            facts=target.get('facts',{})
            for id,label in [('entry','独立启动入口'),('dependencies','依赖声明'),('environment','环境配置'),('health','健康检查'),('storage','数据存储位置'),('adapter','工作台适配接口'),('shutdown','退出与异常处理')]:
                check(id,'passed' if facts.get(id) is True else 'needs_confirmation',label,True)
            evidence=target.get('standalone_evidence');check('standalone','passed' if isinstance(evidence,dict) and evidence.get('health_verified') and evidence.get('exit_verified') and evidence.get('no_workbench_environment') else 'failed' if isinstance(evidence,dict) and evidence.get('status')=='failed' else 'unavailable','显式、隔离的独立启动与退出验证；未知工具不自动执行',True)
        else:check('validator','unavailable','此规范尚未关联可执行校验器；需要用户确认适用规则和限定例外',True)
        level=next(x for x in self.levels() if x['id']==record['enforcement']);fingerprint=digest({'target':target,'level':level});approved={}
        for row in self.db.execute('SELECT p.*,a.fingerprint,a.rule_version FROM approvals p JOIN audits a ON a.id=p.audit_id WHERE a.target_id=? AND a.rule_id=? AND p.expires_at>?',(target['id'],rule_id,now())):
            if row['fingerprint']==fingerprint and row['rule_version']==record['version']:approved[row['check_id']]=row['id']
        for item in checks:
            if item['status']!='passed' and item['waivable'] and item['id'] in approved:item['status']='approved_exception';item['approval_id']=approved[item['id']]
        status='passed' if all(x['status']=='passed' for x in checks) else 'approved_exception' if all(x['status'] in {'passed','approved_exception'} for x in checks) else 'failed' if any(x['status']=='failed' for x in checks) else 'unavailable' if any(x['status']=='unavailable' for x in checks) else 'needs_confirmation'
        audit={'id':uuid.uuid4().hex,'rule_id':rule_id,'rule_version':record['version'],'target_id':target['id'],'fingerprint':fingerprint,'level':level,'status':status,'checks':checks,'target':target,'created_at':now(),'legacy_audit_only':bool(data.get('legacy_audit_only'))}
        with self.db:self.db.execute('INSERT INTO audits VALUES(?,?,?,?,?,?,?,?)',(audit['id'],rule_id,record['version'],target['id'],fingerprint,status,stable(audit),audit['created_at']));self.event('validate',target['id'],{'audit_id':audit['id'],'status':status})
        return audit
    def approve(self,data):
        if data.get('_trusted_confirmation') is not True:return self.propose('exception',data)
        require(data.get('approval',{}).get('reviewer')=='local-user','HUMAN_APPROVAL_REQUIRED')
        row=self.db.execute('SELECT body FROM audits WHERE id=?',(data.get('audit_id'),)).fetchone();require(row,'AUDIT_MISSING');audit=json.loads(row[0]);reason=str(data.get('reason','')).strip();require(5<=len(reason)<=2000,'EXCEPTION_REASON_REQUIRED');ids=data.get('check_ids');require(isinstance(ids,list) and ids and len(ids)<=20,'EXCEPTION_CHECK_SELECTION_REQUIRED')
        allowed={x['id'] for x in audit['checks'] if x['waivable'] and x['status']!='passed'};require(set(ids)<=allowed,'CHECK_NOT_WAIVABLE')
        expiry=now()+min(max(int(data.get('duration_hours',24)),1),168)*3600
        with self.db:
            for id in set(ids):self.db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,audit['id'],id,reason,'local-user',expiry,now()))
            self.event('approve',audit['target_id'],{'audit_id':audit['id'],'checks':ids,'reason':reason,'expires_at':expiry,'approval':data['approval']})
        return {'approved':ids,'expires_at':expiry,'scope':'exact target fingerprint and rule version; does not grant execution permission'}
    def dispatch(self,operation,data):
        if operation=='initialize':self.initialize(data.get('tools',[]));return self.listing({})
        require(self.db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone(),'GOVERNANCE_NOT_INITIALIZED')
        if operation=='list':return self.listing(data)
        if operation=='registerLevel':return self.register_level(data)
        if operation=='proposal':return self.proposal(data['id'])
        if operation=='decide':return self.decide(data)
        if operation=='changes':return {'items':[self.proposal(row[0])['proposal'] for row in self.db.execute('SELECT id FROM proposals ORDER BY created_at DESC LIMIT 200')]}
        if operation=='get':return {'item':self.checked(identity(data['id']))}
        if operation=='reindex':return self.reindex()
        if operation=='save':return self.save(data)
        if operation=='history':return self.history(data)
        if operation=='restore':return self.restore(data)
        if operation=='compose':return self.compose(data)
        if operation=='validate':return self.validate(data)
        if operation=='approve':return self.approve(data)
        if operation=='audits':return {'items':[json.loads(x[0]) for x in self.db.execute('SELECT body FROM audits ORDER BY created_at DESC LIMIT 200')],'approvals':[dict(x) for x in self.db.execute('SELECT * FROM approvals ORDER BY created_at DESC LIMIT 200')],'events':[dict(x) for x in self.db.execute('SELECT * FROM events ORDER BY at DESC LIMIT 200')]}
        if operation=='record_operation':
            with self.db:self.event(str(data['operation'])[:100],str(data['target'])[:180],{'status':data['status'],'audit_id':data.get('audit_id')})
            return {'recorded':True}
        raise ValueError('OPERATION_UNKNOWN')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--request',required=True);parser.add_argument('--response',required=True);args=parser.parse_args();root=Path(args.root).resolve();request=Path(args.request).resolve();response=Path(args.response).resolve()
    require(request.is_relative_to(root) and response.is_relative_to(root),'HANDOFF_PATH_ESCAPE');raw=request.read_bytes();run_id=None;repo=None
    try:
        require(len(raw)<=1000000,'REQUEST_TOO_LARGE');value=json.loads(raw);run_id=value.get('run_id');require(value.get('schema_version')==SCHEMA and value.get('root')==str(root) and re.fullmatch(r'[\w-]{1,100}',run_id or '') and value.get('producer',{}).get('script') in {'workbench_governance_v0001','governance_acceptance_v0001'} and value.get('producer',{}).get('version')=='v0001','HANDOFF_IDENTITY_INVALID')
        repo=Repository(root);result=repo.dispatch(value['operation'],value.get('input',{}));output={'schema_version':SCHEMA,'run_id':run_id,'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'source_sha256':hashlib.sha256(raw).hexdigest(),'status':'completed','result':result};code=0
    except Exception as error:output={'schema_version':SCHEMA,'run_id':run_id,'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'source_sha256':hashlib.sha256(raw).hexdigest(),'status':'failed','error':str(error)};code=2
    finally:
        if repo:repo.db.close()
    response.parent.mkdir(parents=True,exist_ok=True);temporary=response.with_suffix('.tmp');temporary.write_text(stable(output),DEFAULT_ENCODING);temporary.replace(response);return code
if __name__=='__main__':raise SystemExit(main())
