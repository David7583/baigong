# 百工 SQL 目录说明 / Baigong SQL Directory

本发布包不附带任何 SQLite 数据库。本目录仅作为正式数据库位置的结构占位和安全说明。

This release does not bundle any SQLite database. This directory is only a documented structural
placeholder for formal database locations.

## 数据库身份 / Database identities

- `action_data.db`：4 表 Action 业务库，表为 `concept_units`、`instance_units`、
  `unit_attributes`、`ingestion_run_log`。Demo 测试只能在 `temp/<批次>/` 下初始化它。
- `action.db`：13 表开发登记库，用于脚本、功能、任务和功能块等开发登记。它不属于 Demo
  运行数据库，也不随本发布包提供。

- `action_data.db`: the four-table Action business database. Demo tests may initialize it only below
  `temp/<batch>/`.
- `action.db`: the thirteen-table development registry for scripts, functions, tasks, and function
  blocks. It is not a Demo runtime database and is not included in this release.

不得通过创建空数据库文件来伪装初始化成功；必须由已固定的 schema 初始化脚本显式建库并验证。
Do not create an empty file as a fake initialized database. Use the pinned schema initializer and
validate the resulting schema explicitly.

测试结束后，测试数据库仍应留在 `temp/` 作为证据或由用户明确决定清理，不得自动移动到本目录。
After a test, keep its database under `temp/` as evidence unless the user explicitly decides to
remove it. Never move a test database into this directory automatically.
