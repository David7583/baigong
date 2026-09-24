# ============================================================
# 文件名: sitecustomize.py
# 中文名: 发布测试进程网络与写入边界守卫
# 版本号: v0001
#
# 主层级: action
# 层级: tests / runtime_guard
# 脚本定位: Python 启动自动载入的测试限定守卫，保留 Python 要求的特殊文件名
#
# 职责说明:
# - 拒绝网络连接及任务目录外可审计的 Python 写入
#
# 本脚本做什么:
# - 在明确测试环境变量存在时安装审计钩子
#
# 本脚本不做什么:
# - 不替代操作系统沙箱，不声称拦截原生库全部系统调用
#
# 制度边界声明:
# - 只作用于测试子进程；未设置测试根时不安装钩子
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: release_runtime_guard_v0001
# family: release_runtime_guard
# role: test_runtime_guard
# version: v0001
# status: experimental
# entry_point: tests/runtime_guard/sitecustomize.py
# input:
#   - RELEASE_TEST_ROOT environment variable
# output:
#   - audit denials
# depends_on:
#   - Python stdlib
# used_by:
#   - release_preflight_v0001
# ============================================================

from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import unquote


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "release_runtime_guard"
SCRIPT_NAME = "sitecustomize.py"
SCRIPT_VERSION = "v0001"


# ============================================================
# 工具函数区
# ============================================================

def install(root: Path) -> None:
    normal_root = Path(os.path.normpath(str(root).removeprefix("\\\\?\\")))
    def check(value):
        if value is None or isinstance(value, int):
            return
        raw = os.fsdecode(value)
        if raw.startswith("file:"):
            raw = unquote(raw[5:].rsplit("?", 1)[0]).removeprefix("//?/")
            local = raw.lstrip("/")
            if len(local) > 2 and local[1] == ":":
                raw = local
        path = Path(raw).resolve()
        if path == Path(os.devnull).resolve():
            return
        if not Path(os.path.normpath(str(path).removeprefix("\\\\?\\"))).is_relative_to(normal_root):
            raise PermissionError("release_test_write_outside_task:" + str(path))

    def audit(event, args):
        if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo"}:
            raise PermissionError("release_test_network_disabled")
        if event == "open":
            path, mode, flags = args
            if (mode and any(x in mode for x in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                check(path)
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "sqlite3.connect"}:
            if event != "sqlite3.connect" or str(args[0]) != ":memory:":
                check(args[0])
        elif event in {"os.rename", "os.link", "os.symlink"}:
            check(args[0])
            check(args[1])

    sys.addaudithook(audit)


# ============================================================
# Python 启动钩子入口
# ============================================================

if os.environ.get("RELEASE_TEST_ROOT"):
    install(Path(os.environ["RELEASE_TEST_ROOT"]).resolve())
