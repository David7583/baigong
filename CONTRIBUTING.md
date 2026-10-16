# Contributing / 贡献指南

感谢提供可复现问题、文档修正和代码贡献。当前 Workbench 源码标识为 0.7.0-chat.1，Windows x64 是已验收平台；其他平台和专业插件需独立验证。

## 环境与测试

按 README 安装 Python 3.12、Node.js 24、Git 和配套仓库。前端基于 main，后端须使用 workbench-backend；不要把旧数据流水线 main 当作工作台后端。只在不含真实数据的专用副本初始化并执行 npm test，环境变量参见 README。

仓库规范校验：

```powershell
python -m pip install -r tools/repository-audit.requirements.txt
python tests/repository_check_v0001.py
```

校验器只下载固定哈希的官方 CFF 1.2.0 Schema；离线时加 `--schema <schema.json>`。它检查 Apache 文本、CFF/包版本、源码清单、Python/JS 语法、禁止打包的文件和高置信度凭证模式，不能替代安全评审。无单独 webpack/安装包构建命令：当前交付为源码，Electron 通过 npm start 运行。

## 分支与 Pull Request

1. 从 `workbench-backend` 创建 feat/、fix/ 或 chore/ 独立分支。后端 PR 的 base 必须为 workbench-backend；旧流水线的 main 在本次工作台任务范围外。
2. 提交小而完整的修改，使用 `feat: ...`、`fix: ...`、`docs: ...` 等清楚标题，不要求特定签名或版权转让。
3. 保留既有脚本版本/职责头部和能力契约。运行状态、密钥、人工测试数据库与源代码分离；不把模型推测或演示数据描述为真实事实。
4. 添加/更新实际需要的测试，记录命令、结果、平台和未验证部分；文档修正无需机械添加业务测试。
5. 新文件先 `git add`，然后运行 `python tools/update_source_manifest_v0001.py` 更新除清单自身外的 Git 暂存区文件哈希，再暂存清单、运行校验并提交。
6. 推送独立分支并创建 Draft PR，填写功能、测试、验收与回退方式。视频放演示附件或获批后的 Release asset，不提交大型视频。
7. 由维护者审阅并获得用户明确批准后才能合并或发布。CI 不执行自动合并、打标签、Release 或 Zenodo 上传。

## 许可证、隐私与依赖

贡献者只提交自己有权贡献的内容，并同意按项目 Apache-2.0 条款提供贡献；保留自己的版权，不要求转让。第三方内容须注明来源、版本、许可证、修改及适用署名义务，不可把模型/字体/图片/音频的未知权利直接标成 Apache。

修改依赖时同步 lockfile、THIRD_PARTY.md 和机器清单；新增二进制或模型须单独评审。不要提交 API key、token、真实家庭信息、个人聊天、本地数据库或截图中的秘密。敏感漏洞按 SECURITY.md 私下报告，普通问题用 Issues 模板。
