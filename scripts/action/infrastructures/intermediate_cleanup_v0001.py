# ============================================================
# 文件名: intermediate_cleanup_v0001.py
# 中文名: 已确认任务中间文件清理器
# 版本号: v0001
# 主层级: action
# 层级: infrastructures / intermediate_cleanup
# 脚本定位: 按完成任务的精确文件清单清理可再生成中间产物
# 职责说明:
# - 校验任务归属、消费者确认、结果核验与清理范围
# 本脚本做什么:
# - 预览或清理任务登记文件，将每项删除结果写入独立日志
# 本脚本不做什么:
# - 不清理原始资料、数据库、索引、用户数据或回滚备份
# 制度边界声明:
# - 仅允许 runtime/intermediate_tasks 内无链接的完成任务
# - 运行中、失败、暂停和未经确认的任务拒绝清理
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: intermediate_cleanup_v0001
# family: intermediate_cleanup
# role: completed_task_intermediate_cleanup
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/intermediate_cleanup_v0001.py
# input:
#   - verified completed task manifest JSON
# output:
#   - cleanup preview/result JSON and audit log
# depends_on:
#   - Python stdlib
# used_by:
#   - future JSON task coordinators
# ============================================================
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENCODING = 'utf-8'
SCRIPT_FAMILY = 'intermediate_cleanup'
SCRIPT_NAME = 'intermediate_cleanup_v0001'
SCRIPT_VERSION = 'v0001'


class CleanupError(ValueError):
    pass


def linked(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def enclosed(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise CleanupError('relative_path_required')
    raw = root / relative
    if '..' in Path(relative).parts:
        raise CleanupError('parent_path_forbidden')
    for part in [raw, *raw.parents]:
        if part == root:
            break
        if part.exists() and linked(part):
            raise CleanupError('linked_path_forbidden')
    result = raw.resolve()
    if not result.is_relative_to(root):
        raise CleanupError('path_outside_root')
    return result


def cleanup(root, manifest_path, dry_run=True):
    root = Path(root).resolve()
    manifest = enclosed(root, manifest_path)
    base = root / 'runtime/intermediate_tasks'
    if not manifest.is_relative_to(base) or manifest.name != 'task.json' or manifest.parent.parent != base:
        raise CleanupError('manifest_outside_task_directory')
    task = json.loads(manifest.read_text(DEFAULT_ENCODING))
    if task.get('schema_version') != 'intermediate-task.v1' or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', task.get('run_id', '')):
        raise CleanupError('task_identity_invalid')
    if manifest.parent.name != task['run_id']:
        raise CleanupError('task_directory_identity_mismatch')
    if task.get('status') != 'completed' or task.get('result_verified') is not True or task.get('recovery_required') is not False:
        raise CleanupError('task_not_confirmed_complete')
    consumers = task.get('consumers')
    if not isinstance(consumers, list) or not consumers or any(c.get('acknowledged') is not True for c in consumers):
        raise CleanupError('consumers_not_acknowledged')
    if task.get('active') is not False:
        raise CleanupError('task_still_active')
    if (manifest.parent/'task.active').exists():
        raise CleanupError('task_execution_marker_present')
    items = task.get('intermediates')
    if not isinstance(items, list) or len(items) > 10000:
        raise CleanupError('intermediate_manifest_invalid')
    approved = []
    for item in items:
        if not isinstance(item, dict) or item.get('regenerable') is not True or item.get('protected') is not False:
            raise CleanupError('protected_or_nonregenerable_file')
        file = enclosed(root, item.get('path'))
        if file.parent != manifest.parent or file.suffix != '.json' or file == manifest:
            raise CleanupError('file_outside_owned_task')
        if file.exists():
            if not file.is_file() or file.stat().st_nlink != 1:
                raise CleanupError('file_is_not_exclusive_regular_file')
            if hashlib.sha256(file.read_bytes()).hexdigest() != item.get('sha256'):
                raise CleanupError('intermediate_changed')
        approved.append(file)
    result = {'status': 'dry_run' if dry_run else 'completed', 'run_id': task['run_id'],
              'files': [p.relative_to(root).as_posix() for p in approved], 'deleted': []}
    if dry_run:
        return result
    log = root / 'logs/intermediate_cleanup/cleanup.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    # The caller and cleanup share an OS lock; do not trust a stored active flag alone.
    lock = manifest.parent / 'task.lock'
    with lock.open('a+b') as lease:
        try:
            lease.seek(0)
            if not lease.read(1):
                lease.write(b'0'); lease.flush()
            lease.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CleanupError('task_lease_busy') from exc
        current = json.loads(manifest.read_text(DEFAULT_ENCODING))
        if current != task:
            raise CleanupError('task_manifest_changed')
        for file in approved:
            if file.exists():
                if linked(file) or file.stat().st_nlink != 1 or hashlib.sha256(file.read_bytes()).hexdigest() != next(i['sha256'] for i in items if enclosed(root, i['path']) == file):
                    raise CleanupError('intermediate_changed_before_delete')
                file.unlink()
                result['deleted'].append(file.relative_to(root).as_posix())
                with log.open('a', encoding=DEFAULT_ENCODING) as output:
                    output.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'run_id': task['run_id'],
                                             'event': 'deleted', 'path': file.relative_to(root).as_posix()}, ensure_ascii=False) + '\n')
        with log.open('a', encoding=DEFAULT_ENCODING) as output:
            output.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'event': 'completed', **result}, ensure_ascii=False) + '\n')
        manifest.unlink()
    lock.unlink(missing_ok=True)
    try:
        manifest.parent.rmdir()
    except OSError:
        result['remaining_unlisted_files'] = True
    return result


def _build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path)
    parser.add_argument('--manifest')
    parser.add_argument('--request', type=Path)
    parser.add_argument('--response', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    return parser


def main():
    args = _build_parser().parse_args()
    try:
        if args.request:
            request=json.loads(args.request.read_text(DEFAULT_ENCODING))
            if request.get('schema_version')!='intermediate-cleanup.v1' or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',request.get('run_id','')) or type(request.get('dry_run')) is not bool:
                raise CleanupError('cleanup_request_invalid')
            root=Path(request['root']).resolve()
            owner=root/'runtime/intermediate_tasks'/request['run_id']
            incoming=enclosed(root,args.request.absolute().relative_to(root).as_posix());outgoing=enclosed(root,args.response.absolute().relative_to(root).as_posix())
            if incoming.parent!=owner or outgoing.parent!=owner or incoming==outgoing:
                raise CleanupError('cleanup_request_ownership_invalid')
            target=enclosed(root,request['manifest'])
            if hashlib.sha256(target.read_bytes()).hexdigest()!=request.get('manifest_sha256'):
                raise CleanupError('cleanup_plan_changed_preview_again')
            result=cleanup(root,request['manifest'],request['dry_run'])
            payload={'schema_version':'intermediate-cleanup.v1','run_id':request['run_id'],'producer':{'script':SCRIPT_NAME,'version':SCRIPT_VERSION},'status':'completed','result':result}
            temporary=outgoing.with_suffix('.tmp');temporary.write_text(json.dumps(payload,ensure_ascii=False),DEFAULT_ENCODING);temporary.replace(outgoing)
        else:
            if args.root is None or args.manifest is None:raise CleanupError('root_and_manifest_required')
            result=cleanup(args.root,args.manifest,args.dry_run)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        error = {'status': 'error', 'error_type': type(exc).__name__, 'detail': str(exc) if isinstance(exc, CleanupError) else 'cleanup_failed'}
        print(json.dumps(error, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
