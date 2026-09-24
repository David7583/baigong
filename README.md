# 百工 Baigong

[中文](README.md) | [English](README_EN.md)

<p align="center">
  <img src="assets/baigong-logo.png" alt="百工标志：融合电路纹理的黑色蚂蚁" width="260">
</p>

**让聊过的内容，成为可以再次利用的知识。**

百工将支持格式的聊天记录，整理为本地保存、来源可追溯、可供 AI 检索利用的数据，让过去的交流为下一次思考提供依据。

**当前版本：v0.3.0**，核心入口为 `data_action_chain_pipeline_v0018.py`。沿用 [David7583/baigong](https://github.com/David7583/baigong) 项目；`v0018` 是脚本版本。

建议下载 Release 附件 `baigong-v0.3.0.zip`，它是本次验收的明确脚本集合。GitHub 自动生成的 Source code 还包含为保留项目历史而留下的旧脚本与安装器；复现旧入口请使用 [v0.2.0 标签](https://github.com/David7583/baigong/tree/v0.2.0)，新版安装按下文命令进行。

## 这个版本能做什么

- 接纳支持结构的聊天 JSON，提取文本、时间及消息来源身份，保留来源映射。
- 完成结构治理、SQLite 身份锚定、DuckDB 分析物化，以及 BGE-M3 本地稠密向量生成和 Chroma 写入。
- 支持增量处理，检查重复内容；记录执行证据，并对已验收的失败场景回滚。
- 为上层 AI 的语义检索提供数据基础；本次交付形态为脚本流水线。

本版本不包含开箱即用的聊天问答界面、自动经验总结、数字分身或人生模拟。语义检索已在人工样本上验证，不代表所有个人数据场景的检索质量保证。

## 支持哪些聊天记录

入口依据 JSON 结构识别格式，并校验完整契约，不仅根据文件名或平台名称判断。

| 输入结构 | 主要识别字段 | 当前验证范围 |
|---|---|---|
| 节点图式聊天导出 JSON（ChatGPT 风格） | 顶层数组；`conversation_id`、`mapping`、`current_node`；消息包含 `author.role`、`content.content_type` 等 | 已纳入本次人工样本 mock 与真实 BGE-M3 全链验收 |
| UUID 线性消息导出 JSON | 顶层数组；`uuid`、`chat_messages`；消息包含 `sender`、`text` | 已包含识别器与适配器；本轮未完成该格式的独立端到端验收 |
| 百工标准对话入口 JSON | 顶层对象；`schema_version` 为 `canonical_conversation_ingress_v0001`，并含 `dataset_id`、`source_envelope_id`、`conversations` | 提供标准入口契约及直通路径；源格式适配后进入此契约 |

字段名只用于说明结构，并非完整最小样例。节点关系、ID、元数据及内容类型仍须通过契约校验；缺字段、未知结构和不同平台版本的导出不能一概保证兼容。图片、音频或附件信息的保留不等于已完成其内容识别。当前不宣称可直接导入任意 PDF、Word、图片、音频或微信记录。

**后续计划支持更多聊天导出格式与文件类型，欢迎提交脱敏的格式样例和需求。**

## 后续方向

**研究方向 · Personal RSI：探索个人 AI 如何在长期记忆的基础上，通过反馈与评估，逐步改进自身表现。**

## 项目与引用

本项目继续在原 GitHub 仓库迭代，不另建 Baigong 2.0 仓库。

- [项目全部版本 DOI：10.5281/zenodo.22093153](https://doi.org/10.5281/zenodo.22093153)
- [已归档 v0.2.0 DOI：10.5281/zenodo.22093154](https://doi.org/10.5281/zenodo.22093154)

v0.3.0 的版本 DOI 以 Zenodo 完成归档后的记录为准。引用特定发布版本时请使用其对应 DOI，不能把 v0.2.0 的 DOI 标成 v0.3.0 的 DOI。

本发布包含 v0018 及其依赖集合。默认验收使用人工样本与 mock；显式指定本地 BGE-M3 后才进行真实离线 dense embedding。包内没有模型权重、个人数据、数据库或密钥。

## 已验证平台与安装

Windows x64、Python 3.12；本地 CPU 推理。安装时联网，运行验收时禁网。新建虚拟环境，不复制他人的环境：

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements/windows-py312.lock.txt
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -B tests/release_preflight_v0001.py --run-id m01 --mode all
```

真实模型从 https://huggingface.co/BAAI/bge-m3 获取。固定 revision 为 `5617a9f61b028005a4858fdac845db406aefb181`。可共享使用已有下载目录；不要把权重放进 Git。此包只使用 dense 模式，不包含 sparse/ColBERT 测试。

```powershell
& .\.venv\Scripts\python.exe -B tests/release_preflight_v0001.py --run-id r01 --mode all --model-path '<本地BGE-M3目录>'
```

每次使用简短的新 run-id；任务目录保持不变。Windows 下应选择简短的解压路径：结构治理子链会拒绝计划输出路径超过 240 字符的运行；扩展路径前缀不能绕过这项显式保护。结果在 evidence，日志与人工数据库在 temp。测试入口自动处理本轮验证过的 Windows 长路径。不要将未经确认的真实数据替换进验收样本。内存需求受模型与文本长度影响；CPU 测试不等于 GPU 验证。

## 范围与入口

主链：`scripts/orchestration/action/data_action_chain_pipeline_v0018.py`。帮助使用 `--help`。Action 数据库须先用 `scripts/action/tools/init_action_data_sql_schema_v0001.py` 初始化。验收工具展示完整显式参数及独立目录配置，不会初始化真实业务数据库。

本包沿用既有本地 embedding generator，真实模型回归不表示已完成宿主系统 AI 总控接入；接入宿主应用时仍须满足宿主的权限与总控规范。Neo4j 可选分支、HTTP 模型服务及其他平台未纳入本次验收。不得将结果扩张为这些能力已验证。

## 完整性与许可

`PACKAGE_MANIFEST.json` 记录除自身之外的每个包文件 SHA-256；`provenance/source_manifest.json` 记录候选来源与哈希。顶层 LICENSE/NOTICE 为本项目 Apache-2.0；BGE-M3 为 MIT，单独下载；Python 依赖的许可证与版本见 `THIRD_PARTY_NOTICES.json`，依赖不捆绑于此源代码包。

本包包含准备阶段对六个脚本的七处 Windows SQLite URI 兼容修正，修改仅规范化扩展长度路径前缀，不改变业务字段与入口接口。未验证 UNC 网络共享和所有特殊路径。

版本变更见 [CHANGELOG.md](CHANGELOG.md)，验收摘要见 [RELEASE_ACCEPTANCE.json](RELEASE_ACCEPTANCE.json)。
