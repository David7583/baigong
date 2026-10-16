# ============================================================
# 文件名: update_source_manifest_v0001.py
# 中文名: 更新暂存源码清单
# 版本号: v0001
# 主层级: action
# 层级: repository / tools
# 脚本定位: 显式维护源代码完整性清单
# 职责说明: 从 Git 暂存区生成源码 SHA-256 清单。
# 本脚本做什么: 只重写 SOURCE_MANIFEST.json。
# 本脚本不做什么: 不暂存、提交、推送或发布，不读取运行数据。
# 制度边界声明: 暂存区中的文件由贡献者先行选择。
# 可更新: True
# ALIAS_META
# alias: update_source_manifest_v0001
# family: update_source_manifest
# role: manifest_maintenance
# version: v0001
# status: staging
# entry_point: tools/update_source_manifest_v0001.py
# input: Git index
# output: SOURCE_MANIFEST.json
# depends_on: Python stdlib, Git
# used_by: contributors
# ============================================================
from pathlib import Path
import hashlib
import json
import subprocess

SCRIPT_NAME = 'update_source_manifest_v0001'
SCRIPT_FAMILY = 'update_source_manifest'
SCRIPT_VERSION = 'v0001'
DEFAULT_ENCODING = 'utf-8'
ROOT = Path(__file__).resolve().parents[1]

def main():
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args])
    paths = sorted(filter(None, git('ls-files', '-z').decode().split('\0')))
    package = json.loads(git('show', ':package.json'))
    result = {'schema_version':'workbench.source.v1', 'version':package['version'], 'files':[]}
    for name in paths:
        if name == 'SOURCE_MANIFEST.json':
            continue
        data = git('show', ':' + name)
        result['files'].append({'path':name, 'sha256':hashlib.sha256(data).hexdigest()})
    (ROOT / 'SOURCE_MANIFEST.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding=DEFAULT_ENCODING, newline='\n')
    print('Updated manifest for', len(result['files']), 'staged files; stage the manifest before committing.')

if __name__ == '__main__':
    main()
