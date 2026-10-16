# ============================================================
# File: workbench_skill_archive_v0001.py
# 中文名: Skill压缩包受限解包
# Version: v0001
# Main Layer: action
# Layer: workbench / adapters
# Updatable: True
# Purpose: 将下载的Zip包解压到全新隔离目录。
# Boundary: 不执行包内程序，拒绝路径逃逸、链接、超大文件和压缩炸弹。
# ============================================================
# ALIAS_META
# alias: workbench_skill_archive
# family: workbench_skill_archive
# role: bounded_zip_extractor
# version: v0001
# status: active
# entry_point: frontend/local_ai_workbench/adapters/workbench_skill_archive_v0001.py
# input:
#   - Zip file and new extraction directory
# output:
#   - inert extracted files
# depends_on:
#   - Python standard library
# used_by:
#   - workbench_studio_v0001
# ============================================================
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import zipfile

SCRIPT_NAME = 'workbench_skill_archive_v0001.py'
SCRIPT_FAMILY = 'workbench_skill_archive'
SCRIPT_VERSION = 'v0001'
DEFAULT_ENCODING = 'utf-8'
MAX_BYTES = 100 * 1024 * 1024
MAX_FILES = 4000

# ============================================================
# 全量校验后解包；任何失败清理本次新目录
# ============================================================
def extract(archive: Path, target: Path) -> dict:
    if target.exists():
        raise ValueError('Extraction target already exists')
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_FILES or sum(x.file_size for x in entries) > MAX_BYTES:
            raise ValueError('Archive exceeds limits')
        names = set()
        for entry in entries:
            relative = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename
                    or ':' in entry.filename or stat.S_ISLNK(mode)
                    or any(part.rstrip(' .') != part for part in relative.parts)
                    or any(part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *['COM'+str(i) for i in range(1,10)], *['LPT'+str(i) for i in range(1,10)]} for part in relative.parts)):
                raise ValueError('Unsafe archive entry')
            folded = str(relative).casefold()
            if folded in names:
                raise ValueError('Duplicate archive entry')
            names.add(folded)
            if entry.file_size > 20 * 1024 * 1024:
                raise ValueError('Archive member exceeds limit')
        target.mkdir(parents=True)
        try:
            for entry in entries:
                destination = target.joinpath(*PurePosixPath(entry.filename).parts)
                if not destination.resolve().is_relative_to(target.resolve()):
                    raise ValueError('Archive path escaped')
                if entry.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(entry) as input_file, destination.open('xb') as output_file:
                        shutil.copyfileobj(input_file, output_file)
        except Exception:
            shutil.rmtree(target)
            raise
    return {'status': 'ok', 'files': len(entries)}

# ============================================================
# 显式入口
# ============================================================
if __name__ == '__main__':
    try:
        print(json.dumps(extract(Path(sys.argv[1]), Path(sys.argv[2]))))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
