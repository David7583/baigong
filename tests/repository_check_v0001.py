# ============================================================
# 文件名: repository_check_v0001.py
# 中文名: 开源仓库元数据与源码清单校验
# 版本号: v0001
# 主层级: action
# 层级: repository / tests
# 脚本定位: 只读仓库验收入口
# 职责说明: 校验引用元数据、许可证、源码哈希和禁止提交的产物。
# 本脚本做什么: 读取当前源码与固定 CFF Schema，失败时返回非零状态。
# 本脚本不做什么: 不修改仓库，不调用模型，不发布版本。
# 制度边界声明: 仅访问公开 CFF Schema；不读取用户运行数据。
# 可更新: True
# ALIAS_META
# alias: repository_check_v0001
# family: repository_check
# role: repository_validation
# version: v0001
# status: staging
# entry_point: tests/repository_check_v0001.py
# input: repository files, optional --schema path
# output: validation summary
# depends_on: PyYAML, jsonschema
# used_by: contributors, GitHub Actions
# ============================================================
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re
import subprocess
import urllib.request
import yaml
from jsonschema import Draft7Validator

SCRIPT_NAME = 'repository_check_v0001'
SCRIPT_FAMILY = 'repository_check'
SCRIPT_VERSION = 'v0001'
DEFAULT_ENCODING = 'utf-8'
ROOT = Path(__file__).resolve().parents[1]
SCHEMA_URL = 'https://raw.githubusercontent.com/citation-file-format/citation-file-format/1.2.0/schema.json'
SCHEMA_SHA256 = '0b8d22140da702d766df318dcff3a91af2f39521298dcf36d76315fd99cc169b'
APACHE_NORMALIZED_SHA256 = '0ffddef9e48f8a09aed5caf2d44f7ba1c1be2d9b8e0a6f693b1635b2d5566645'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', type=Path, help='Offline copy of the exact official CFF 1.2.0 schema')
    args = parser.parse_args()
    if args.schema:
        schema_bytes = args.schema.read_bytes()
    else:
        with urllib.request.urlopen(SCHEMA_URL, timeout=30) as response:
            schema_bytes = response.read()
    assert hashlib.sha256(schema_bytes).hexdigest() == SCHEMA_SHA256, 'Unexpected CFF schema digest'
    schema = json.loads(schema_bytes)
    Draft7Validator.check_schema(schema)
    citation = yaml.safe_load((ROOT / 'CITATION.cff').read_text(encoding=DEFAULT_ENCODING))
    Draft7Validator(schema, format_checker=Draft7Validator.FORMAT_CHECKER).validate(citation)
    package = json.loads((ROOT / 'package.json').read_text(encoding=DEFAULT_ENCODING))
    assert citation['license'] == package['license'] == 'Apache-2.0'
    assert citation['version'] == package['version']
    assert not (ROOT / '.zenodo.json').exists(), 'Keep a single citation metadata source unless explicitly reviewed'
    normalized = re.sub(rb'\s+', b' ', (ROOT / 'LICENSE').read_bytes()).strip()
    assert hashlib.sha256(normalized).hexdigest() == APACHE_NORMALIZED_SHA256, 'Apache license text changed'
    for name in ('NOTICE', 'CONTRIBUTING.md', 'SECURITY.md', 'THIRD_PARTY.md', 'docs/REPOSITORY_AUDIT.md', 'docs/ZENODO.md'):
        assert (ROOT / name).is_file(), 'Missing ' + name
    manifest = json.loads((ROOT / 'SOURCE_MANIFEST.json').read_text(encoding=DEFAULT_ENCODING))
    tracked = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0')
    tracked = set(filter(None, tracked)) - {'SOURCE_MANIFEST.json'}
    listed = {entry['path'] for entry in manifest['files']}
    assert len(listed) == len(manifest['files']), 'Duplicate manifest paths'
    assert tracked == listed, 'Manifest membership differs from Git index: ' + repr(sorted(tracked ^ listed))
    blocked = re.compile(r'(?i)(?:^|/)(?:node_modules|\.venv|data_runtime|\.runtime|governance|product_videos)/|\.(?:sqlite[0-9]*|db|pem|pfx|p12|mp4|mp3|wav|exe|msi|zip|ttf|ttc|woff2?)$|(?:^|/)\.env(?:\.|$)')
    secrets = [rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', rb'(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{70,}', rb'(?<![A-Za-z0-9])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}']
    for entry in manifest['files']:
        name = entry['path']
        assert not blocked.search(name), 'Unexpected distributable file: ' + name
        assert name not in ('config/action/ai/api_keys_v0002.yml', 'config/action/ai/ollama_config_v0001.yml')
        target = (ROOT / name).resolve()
        assert target.is_relative_to(ROOT.resolve()), 'Manifest path escapes repository'
        data = target.read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], 'Source hash mismatch: ' + name
        assert not any(re.search(pattern, data) for pattern in secrets), 'Potential credential in ' + name
        if name.endswith('.py'):
            ast.parse(data.decode('utf-8-sig'), filename=name)
        elif name.endswith(('.cjs', '.mjs', '.js')):
            subprocess.run(['node', '--check', str(target)], check=True, capture_output=True)
    print(json.dumps({'status':'passed', 'cff':'1.2.0', 'license':'Apache-2.0', 'tracked_files_checked':len(listed), 'model_requests':0}))

if __name__ == '__main__':
    main()
