# ============================================================
# 文件名: workflow_transform_v0002.py
# 中文名: 工作流纯数据变换执行器
# 版本号: v0002
# 主层级: action
# 层级: infrastructure / workflow
# 脚本定位: 显式 JSON 文件契约执行器
# 职责说明: 执行注册的纯数据操作并返回具名输出端口。
# 本脚本做什么: 校验来源、运行标识和预算；原子发布输出。
# 本脚本不做什么: 不运行任意代码、不访问业务库、不调用模型。
# 制度边界声明: 输入只读；失败保留交接；不依赖同名旧版本。
# 可更新: True
# ============================================================
# ALIAS_META
# alias: workflow_transform_v0002
# family: workflow_transform
# role: workflow_transform
# version: v0002
# status: active
# entry_point: scripts/action/infrastructures/workflow_transform_v0002.py
# input:
#   - workbench.workflow.transform.v2 JSON file
# output:
#   - identity checked ports or explicit failure JSON
# depends_on:
#   - Python standard library
# used_by:
#   - workbench_workflows_v0002
# ============================================================
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DEFAULT_ENCODING = 'utf8'
SCRIPT_FAMILY = 'workflow_transform'
SCRIPT_NAME = 'workflow_transform_v0002'
SCRIPT_VERSION = 'v0002'
SCHEMA = 'workbench.workflow.transform.v2'


class TransformError(ValueError):
    pass


def transform(kind, values, params):
    value = values.get('value')
    if kind == 'text':
        result = params['text']
    elif kind == 'json':
        result = params['data']
    elif kind == 'filter':
        items = value.get('items', []) if isinstance(value, dict) else value
        if not isinstance(items, list):
            raise TransformError('FILTER_LIST_REQUIRED')
        field, term = params['field'], params['contains'].casefold()
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,100}', field):
            raise TransformError('FILTER_FIELD_INVALID')
        result = {'items': [x for x in items if term in str(x.get(field, '') if isinstance(x, dict) else x).casefold()]}
    elif kind == 'template':
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        result = params['template'].replace('{{value}}', text)
    elif kind == 'output':
        result = value
    elif kind == 'merge':
        left, right = values['left'], values['right']
        if isinstance(left, list) and isinstance(right, list):
            result = left + right
        elif isinstance(left, dict) and isinstance(right, dict):
            result = {**left, **right}
        else:
            raise TransformError('MERGE_REQUIRES_TWO_ARRAYS_OR_OBJECTS')
    elif kind == 'select':
        result = value
        for part in params['path'].split('.'):
            if part in {'__proto__', 'constructor', 'prototype'}:
                raise TransformError('RESERVED_FIELD')
            try:
                result = result[int(part)] if isinstance(result, list) else result[part]
            except (ValueError, TypeError, KeyError, IndexError):
                raise TransformError('FIELD_NOT_FOUND: ' + part) from None
    elif kind == 'branch':
        equal = type(value) is type(params['equals']) and value == params['equals']
        return {'yes' if equal else 'no': value}
    else:
        raise TransformError('TRANSFORM_NOT_REGISTERED')
    return {'value': result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--response', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    source, destination = Path(args.request).resolve(), Path(args.response).resolve()
    if not source.is_relative_to(root) or not destination.is_relative_to(root) or source == destination:
        raise TransformError('HANDOFF_ESCAPE')
    raw = source.read_bytes()
    request = {}
    try:
        request = json.loads(raw)
        if (len(raw) > 4000000 or request.get('schema_version') != SCHEMA
                or request.get('producer') != {'script': 'workbench_workflows_v0002', 'version': 'v0002'}
                or Path(request.get('root', '')).resolve() != root
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request.get('run_id', ''))):
            raise TransformError('TRANSFORM_CONTRACT')
        ports = transform(request['operation'], request.get('inputs', {}), request.get('params', {}))
        if len(json.dumps(ports, ensure_ascii=False).encode(DEFAULT_ENCODING)) > 2000000:
            raise TransformError('TRANSFORM_OUTPUT_BUDGET')
        result = {'status': 'completed', 'result': {'ports': ports}}
    except Exception as error:
        result = {'status': 'failed', 'error': str(error), 'error_type': type(error).__name__}
    result.update(schema_version=SCHEMA, run_id=request.get('run_id'),
                  producer={'script': SCRIPT_NAME, 'version': SCRIPT_VERSION},
                  source_sha256=hashlib.sha256(raw).hexdigest())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding=DEFAULT_ENCODING)
    temp.replace(destination)
    return 0 if result['status'] == 'completed' else 2


if __name__ == '__main__':
    sys.exit(main())
