# Changelog

## v0.3.0 — 2026-09-24

### 中文

- 核心入口更新为 v0018，发布附件包含 96 个业务脚本及其配置、Schema 和验收工具。
- 支持结构化聊天入口、消息身份与时间标注、增量处理、重复内容检查及已测试失败路径的回滚。
- Windows / Python 3.12 / CPU：完成独立环境安装、离线 BGE-M3 和解压包验收。mock 16/16、真实模型 17/17 通过。
- 保留六个脚本中的七处 Windows SQLite URI 兼容修正；结构子链的 240 字符路径保护不变。
- 新增一致的中英文说明、品牌图片、格式支持范围及 Personal RSI 研究方向。
- v0.3.0 使用命令行安装和新的验收入口。旧 GUI 安装器、启动脚本及旧入口不作为本版验收路径；需要复现旧版时使用 v0.2.0 标签。
- 模型权重、个人数据、数据库、密钥与内部准备资料不包含在发布附件中。

### English

- Promotes the v0018 core entry point in a curated asset containing 96 runtime scripts, configuration, schemas and acceptance tools.
- Includes structured chat ingress, message identity/time annotations, incremental processing, duplicate-content checks and tested failure rollback paths.
- Verified on Windows / Python 3.12 / CPU in a fresh virtual environment: 16/16 mock checks and 17/17 real offline BGE-M3 checks passed.
- Retains seven Windows SQLite URI fixes across six scripts; the structural sub-pipeline's 240-character path guard remains.
- Adds aligned bilingual documentation, the supplied brand image, explicit format support boundaries and the Personal RSI research direction.
- Uses command-line installation and the new acceptance entry. Historical GUI/launcher paths are outside this release's acceptance scope; use the v0.2.0 tag to reproduce them.
- The curated asset excludes model weights, personal data, databases, secrets and internal preparation records.

## Previous releases

See the v0.2.0 and v0.1.0 tags and GitHub Releases for their original code, documentation and acceptance scope.
