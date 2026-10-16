# ============================================================
# 文件名: engineering_contract_audit_v0001.py
# 中文名: 独立脚本文件契约静态审查器
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / engineering_audit
# 脚本定位: 输出同名旧版依赖与业务模块交接候选问题
# 职责说明:
# - 只读分析项目自有源码，区分直接依赖与名称残留
# 本脚本做什么:
# - 检查 AST 导入、动态加载和同名旧版子进程线索
# 本脚本不做什么:
# - 不执行源码，不读配置凭证，不以静态通过代替行为验收
# 制度边界声明:
# - 结果只含相对文件名、行号和依赖；未知问题不得伪报已解决
# 可更新: True
# ============================================================
# ============================================================
# ALIAS_META
# ============================================================
# alias: engineering_contract_audit_v0001
# family: engineering_contract_audit
# role: readonly_script_contract_architecture_audit
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/engineering_contract_audit_v0001.py
# input:
#   - project root and optional explicit source entry list
# output:
#   - architecture audit JSON
# depends_on:
#   - Python stdlib
# used_by:
#   - development and fixed candidate engineering gates
# ============================================================
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'engineering_contract_audit'
SCRIPT_NAME = 'engineering_contract_audit_v0001'
SCRIPT_VERSION = 'v0001'


def audit(root, entries=None):
    root = Path(root).resolve()
    sources = []
    for folder in ['scripts', 'frontend']:
        for file in (root / folder).rglob('*.py'):
            if not any(part in {'node_modules', '__pycache__', '.runtime', 'Workbench', 'outputs'} for part in file.relative_to(root).parts):
                sources.append(file)
    if entries is not None:
        requested = {str(Path(item).as_posix()) for item in entries}
        sources = [file for file in sources if file.relative_to(root).as_posix() in requested]
        if len(sources) != len(requested):
            raise ValueError('declared_entry_missing')
    families = {}
    for file in sources:
        match = re.fullmatch(r'(.+)_v(\d+)', file.stem)
        if match:
            families[file.stem] = (match[1], int(match[2]))
    same, imports, identities, dynamic, syntax = [], [], [], [], []
    for file in sources:
        relative = file.relative_to(root).as_posix()
        text = file.read_text(DEFAULT_ENCODING)
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            syntax.append({'file': relative, 'line': exc.lineno}); continue
        own = re.fullmatch(r'(.+)_v(\d+)', file.stem)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module or '']
                for module in modules:
                    match = re.fullmatch(r'(.+)_v(\d+)', module.split('.')[-1])
                    if match:
                        record = {'file': relative, 'line': node.lineno, 'dependency': module}
                        imports.append(record)
                        if own and match[1] == own[1] and int(match[2]) < int(own[2]):
                            same.append(record)
            if isinstance(node, ast.Call):
                called = ast.unparse(node.func)
                if called.endswith(('spec_from_file_location', 'import_module', 'Popen', 'run', 'run_owned', 'with_name')):
                    strings = [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                    for value in strings:
                        match = re.fullmatch(r'(.+)_v(\d+)(?:\.py)?', Path(value).name)
                        if match and own and match[1] == own[1] and int(match[2]) < int(own[2]):
                            same.append({'file': relative, 'line': node.lineno, 'dependency': value, 'kind': 'dynamic_or_process'})
                    if 'spec_from_file_location' in called or 'import_module' in called:
                        dynamic.append({'file': relative, 'line': node.lineno})
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                if own and any(isinstance(t, ast.Name) and t.id == 'SCRIPT_NAME' for t in node.targets):
                    declared = node.value.value.removesuffix('.py')
                    match = re.fullmatch(r'(.+)_v(\d+)', declared)
                    if match and declared != file.stem:
                        identities.append({'file': relative, 'line': node.lineno, 'declared': declared})
    return {'schema_version': 'engineering-audit.v1', 'status': 'passed' if not (same or syntax or identities or imports or dynamic) else 'requires_rectification',
            'checked_python_files': len(sources), 'same_family_older_dependencies': same, 'project_version_module_imports': imports,
            'dynamic_business_imports_to_review': dynamic, 'identity_mismatches': identities, 'syntax_errors': syntax,
            'single_responsibility': 'requires_declared_role_and_behavior_review_not_function_count',
            'limits': 'Python static evidence; JavaScript/process indirection and complete runtime closure also require independent acceptance'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--entries', type=Path)
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args()
    try:
        result = audit(args.root, json.loads(args.entries.read_text(DEFAULT_ENCODING)) if args.entries else None)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if args.strict and result['status'] != 'passed' else 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
