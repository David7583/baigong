# ============================================================
# 文件名: __init__.py
# 中文名: 新版独立 Action Chat 包入口
# 版本号: v0001
#
# 主层级: action
# 层级: development / independent_action_chat / package
# 脚本定位: 新版独立 Action Chat 的无副作用包标识
#
# 职责说明:
# - 声明项目版本和稳定包边界
#
# 本脚本做什么:
# - 暴露包版本常量
#
# 本脚本不做什么:
# - 不启动服务，不加载配置，不连接数据库或模型
#
# 制度边界声明:
# - 导入包不得产生运行数据或外部调用
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: independent_action_chat_package_v0001
# family: independent_action_chat_package
# role: package_boundary
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/__init__.py
# input: []
# output:
#   - package version
# depends_on:
#   - Python stdlib
# used_by:
#   - independent_action_chat modules
# ============================================================

from __future__ import annotations


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "independent_action_chat_package"
SCRIPT_NAME = "independent_action_chat_package_v0001"
SCRIPT_VERSION = "v0001"

__version__ = SCRIPT_VERSION

