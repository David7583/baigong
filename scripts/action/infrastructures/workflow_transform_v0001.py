# ============================================================
# 文件名: workflow_transform_v0001.py
# 中文名: 工作流标准数据变换
# 版本号: v0001
# 主层级: action
# 层级: workbench / platform
# 脚本定位: typed_json_transform
# 职责说明:
# - 工作流标准数据变换
# 本脚本做什么:
# - 复用现有契约与受管理接口，记录真实状态
# 本脚本不做什么:
# - 不改写业务事实，不自动授权、批准或调用模型
# 制度边界声明:
# - 仅管理工作台配置与派生展示，可回退
# 可更新: True
# ============================================================
# ALIAS_META
# alias: workflow_transform_v0001
# family: workflow_transform
# role: typed_json_transform
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/workflow_transform_v0001.py
# input:
#   - explicit versioned requests and existing metadata
# output:
#   - factual results and structured errors
# depends_on:
#   - existing workbench APIs and standard library
# used_by:
#   - workbench_platform
# ============================================================
import argparse, hashlib, json, re, sys
from pathlib import Path
SCRIPT_NAME='workflow_transform_v0001'
SCRIPT_FAMILY='workflow_transform'
SCRIPT_VERSION='v0001'
DEFAULT_ENCODING='utf-8'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--request',required=True);parser.add_argument('--response',required=True);a=parser.parse_args()
    root=Path(a.root).resolve();source=Path(a.request).resolve();dest=Path(a.response).resolve()
    if not source.is_relative_to(root) or not dest.is_relative_to(root):raise ValueError('HANDOFF_ESCAPE')
    raw=source.read_bytes();v=json.loads(raw);result=None
    try:
        if len(raw)>2000000 or v.get('schema_version')!='workbench.workflow.transform.v1' or v.get('producer')!={'script':'workbench_workflows_v0001','version':'v0001'} or v.get('root')!=str(root):raise ValueError('TRANSFORM_CONTRACT')
        kind=v['operation'];params=v.get('params',{});value=v.get('value')
        if kind=='text':result=str(params.get('text',''))
        elif kind=='filter':
            items=value.get('items',[]) if isinstance(value,dict) else value
            if not isinstance(items,list):raise ValueError('FILTER_LIST_REQUIRED')
            field=params.get('field','name');term=str(params.get('contains','')).casefold()
            if not isinstance(field,str) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,100}',field):raise ValueError('FILTER_FIELD_INVALID')
            result={'items':[x for x in items if term in str(x.get(field,'') if isinstance(x,dict) else x).casefold()]}
        elif kind=='template':
            text=str(value) if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
            result=str(params.get('template','{{value}}')).replace('{{value}}',text)
        elif kind=='output':result=value
        else:raise ValueError('TRANSFORM_NOT_REGISTERED')
        body={'status':'completed','result':result}
    except Exception as e:body={'status':'failed','error':str(e)}
    body.update(schema_version='workbench.workflow.transform.v1',run_id=v.get('run_id'),producer={'script':SCRIPT_NAME,'version':SCRIPT_VERSION},source_sha256=hashlib.sha256(raw).hexdigest())
    dest.parent.mkdir(parents=True,exist_ok=True);temp=dest.with_suffix('.tmp');temp.write_text(json.dumps(body,ensure_ascii=False,allow_nan=False),encoding='utf-8');temp.replace(dest)
    return 0 if body['status']=='completed' else 2
if __name__=='__main__':sys.exit(main())
