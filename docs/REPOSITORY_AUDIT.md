# GitHub 仓库审计与 Zenodo 准备报告

日期：2026-10-16。范围：Local AI Workbench 前端与 workbench-backend 后端源码、可达 Git 历史、旧流水线正式源码包，以及此前视频草稿交付。

## 结论

Apache-2.0 文本完整、保持不变；基础开源文档、引用元数据、依赖清单和只读 CI 已补齐。改动均位于独立审计分支，供 Draft PR 审查。本次没有合并稳定分支、创建/发布 Release、上传 Zenodo、授权外部账号或重写历史。仓库具备引用元数据和后续连接准备条件，**不等于正式发布已获准**。

仍需人工决定：后端工作台与旧流水线的 Zenodo 存档系列关系、Logo 广泛再分发范围、视频配音服务的发布许可，以及仓库分支保护策略。未知权利不认定为自由商用。

## 固定基线与证据范围

| 对象 | 本次检查基线 |
| --- | --- |
| 前端 main | `00baf4b4358aac5f3027fba21083be1af31d3249` |
| 后端 workbench-backend | `6bb19fb4a85df6c77eec1469e655b313df7d0298` |
| 旧流水线 main | `6c49f5ca0a6c6a57559b007533b29a679bb97f2d` |
| 前端视频 Draft PR #1 | `a74eae38d4626e163583d96fc4230c4e21a7bf96` |
| 工作台源码版本 | `0.7.0-chat.1`，不是本次新发版本 |

本次从 GitHub 完整克隆分支/标签，未使用浅克隆。公开证据见 [历史扫描摘要](audit/history-scan-summary.json)、[第三方机器清单](audit/dependency-inventory.json)和 [GitHub 设置快照](audit/github-settings.json)。具体许可证与分发边界见 [THIRD_PARTY.md](../THIRD_PARTY.md)。

## 逐项审计

| 项目 | 状态 | 证据 | 风险与处理 |
| --- | --- | --- | --- |
| Apache LICENSE | 通过，未改许可 | 两仓库正文与 Apache 官方文本仅空白归一后完全一致，含 1–9 节和附录 | 官方原文 SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`；不擅自变更 |
| 项目版权 | 保留，部分待权利人确认 | 原 NOTICE 为 Copyright 2026 Ruixin Yang；用户明确确认作者姓名 | 无 ORCID/机构推测；作者确认不能证明所有历史贡献的全部权利，保留第三方声明 |
| NOTICE | 前端已补充 | 随源码分发 Three.js，已有 MIT LICENSE；前端 NOTICE 增加真实来源归属 | 后端未发现需凭空新增的第三方著作权主体；不把依赖全部改标 Apache |
| Three.js | 通过，说明已修正 | 固定 npm 0.186.1 上游与 19 个 .mjs 逐文件比较 | 原 NOTICE 只说三个文件，已覆盖 addons；差异是导入路径、后缀、空白及一处注释导入后缀，保留原实现署名 |
| Node/Python 依赖 | 已清点，有条件通过 | npm 锁文件 15 项、基础 Python 锁文件 13 项；安装元数据及许可证文件哈希 | extract-zip 安装包缺独立 LICENSE，原生子依赖需补证；certifi MPL、Electron/Chromium 原生组件等须保留适用条款；不作统一无限制商用声明 |
| 可选模型/插件 | 已识别，按实际分发待复核 | AST 中的 duckdb/neo4j/torch/chromadb/bpy 等；旧 main 101 项元数据 | 空版未附带权重和专业环境；未固定版本的外部服务、插件许可不能由本次源码审计兜底 |
| 视频、音频、字体、Logo | 部分通过，配音权利待确认 | 既有视频 ASSETS.md、文件哈希及制作环境元数据 | 微软字体仅渲染，字体文件未附带；edge-tts 客户端许可不是配音服务授权，正式发布前确认或替换；Logo 当前用途已获用户授权，广泛再许可范围另确认 |
| CONTRIBUTING | 已补齐 | 环境、隔离测试、分支/PR、清单更新、许可证流程 | 不要求贡献者转让版权；不得使用真实个人数据测试 |
| SECURITY | 已补齐并启用入口 | 两仓库 private-vulnerability-reporting API 复核 enabled=true | 未虚构联系邮箱、响应 SLA；公开 Issue 不披露敏感利用细节 |
| README | 已修正 | 前端中英功能边界；后端公开前端链接；保留安装/启动/测试路径 | 不将框架接口、在线模型配置或概念视频当成全功能验收；不宣称所有数据绝不外发 |
| .gitignore | 已加强 | 增加私钥、证书、本机审计状态、视频/音频、打包文件、字体、二进制及构建缓存忽略 | .gitignore 不影响已跟踪内容，所以另检 Git 清单与历史；新增原生依赖必须单独评审 |
| SOURCE_MANIFEST | 已修复后端陈旧哈希 | 基线前端 203 项全部匹配；后端 365 项中 182 项不匹配：149 项可由 CRLF/LF 差异解释，另 33 项仅换行不能解释 | 不修改这些业务文件，按实际 Git 暂存区重新生成；旧清单不能作为内容真实性证明。新增校验和明确维护命令防止漂移 |
| 当前及 Git 历史凭证 | 未发现所检模式命中 | 前端 4 commits / 232 blobs，后端 15 commits / 560 blobs；检查所有可达旧内容 | 0 命中不等于无任何未知秘密。backend 69 个路径告警均为 runtime 源码，未见实际私钥、数据库或运行资料；无历史重写 |
| 历史图片/二进制 | 已清点 | 仅 Logo、README 截图、视频海报与接触表；与既有隔离采集记录一致 | 没有可达历史中的 exe/模型/字体/视频/数据库。图像不是仅靠字符串扫描判定隐私，沿用已验收隔离采集证据 |
| 已有正式分发包 | 通过源码范围检查 | v0.3.0 ZIP SHA-256 `f23b56e4e6e99bed0406c500b5bc368460e737fc07ee90a16144f58f90c6de9e`；148 项 | 带 LICENSE、NOTICE、THIRD_PARTY_NOTICES.json，无模型及已安装二进制；这是旧流水线源码包，不能代替未来 Workbench 安装器审计 |
| Workbench 二进制安装包 | 不适用/尚未交付 | 当前只有源码；本机 Electron 有 LICENSE 和 LICENSES.chromium.html | 将来打包需收集实际 Electron/Python/Node/原生库完整声明及对应源码义务 |
| CITATION.cff | 已创建 | CFF 1.2.0，Ruixin Yang，Apache-2.0，与 package.json 版本一致 | 不填写 DOI、ORCID、机构或未确认发布日期。旧 main 已有 CFF，本次不混淆两个产品线 |
| .zenodo.json | 不需要 | 单一 CFF 来源 | 防止 Zenodo 优先读取 JSON 后忽略 CFF；未来例外需单独评审 |
| Zenodo 连接 | 现状核实，未改变 | 后端已有 zenodo.org 活动 hook；前端无连接 | 后端已有正式存档，Release 是仓库级，不按分支隔离；作者须先决定工作台的引用系列 |
| Issues / Discussions | Issues 已启用；Discussions 未启用 | GitHub API 设置快照 | 已加问题模板；Discussions 建议待社区交流需求出现再启用，不强行增加维护负担 |
| Branch Protection | 尚未配置，需维护者决定 | 两仓库 main 及后端 workbench-backend 均未 protected，无 rulesets | 建议禁止 force push/删除，要求 PR 和状态检查；独立维护者如何满足审阅人数需先决定，避免锁死唯一维护者 |
| GitHub Actions | 已添加可审查配置 | 固定 SHA 的官方 checkout/setup-node/setup-python，Windows 校验与集成测试 | contents:read，无发布/自动合并步骤，无 pull_request_target；对应仓库的配套源码按已验收 SHA 固定 |

## 本次验证与验收记录

本地 9 项检查全部通过（含 3 个预期失败的防回归检查）；API 与真实 Electron 聊天回归各 1 项通过。GitHub Actions 的全新 Windows 环境依赖安装和集成测试均已通过：[前端 CI](https://github.com/David7583/baigong-workbench-frontend/actions/runs/38921533744)、[后端 CI](https://github.com/David7583/baigong/actions/runs/38921539383)。本地和远程结果记录在 [validation.json](audit/validation.json)，测试命令如下：

```powershell
python tests/repository_check_v0001.py --schema <官方CFF-1.2.0-schema.json>
# 在隔离运行副本，先按 README 初始化
node --test backend/tests/release_contract_test_v0001.cjs
node --test frontend/tests/release_contract_test_v0001.cjs
```

校验涵盖完整 CFF JSON Schema、版本一致性、Apache 正文、清单成员及哈希、Python AST/JS 语法、禁止打包文件与高置信凭证模式；另对非法 CFF、源码篡改和遗漏清单文件验证失败路径。集成验收不调用真实模型、不开收费权限、不使用真实数据库。固定候选、运行副本和日志相互分离。

之前视频技术验收：1920×1080、30 fps、1800 帧、60.000 秒、H.264/AAC、音画时长差 0、完整解码 0 错误；视频 SHA-256 `e89aa69a46be8800290be6cdd7c53850631f30b30d672bcbc4874b3ec87f8a97`。视频及工程放在前端 [Draft PR #1](https://github.com/David7583/baigong-workbench-frontend/pull/1)所关联的既有**未发布**草稿附件中，普通源码不含 MP4/音频/大 ZIP。本次审计没有新建或发布该草稿。技术验收与素材发布权利是两个独立条件。

## 仍需所有者决策

1. **存档边界**：旧流水线 v0.3.0 的真实 DOI 为 [10.5281/zenodo.22929364](https://doi.org/10.5281/zenodo.22929364)。决定工作台后端是否加入旧系列或先拆仓库；不能把旧 DOI 贴给工作台。前端 Zenodo 连接须由本人授权启用。
2. **素材与商业分发**：确认旁白服务授权，必要时替换声音；确认 Logo 再分发/商标边界；将来安装器必须复核实际完整依赖。未确认项不标记为可自由使用。
3. **分支保护**：前端 main、后端 workbench-backend 建议要求 `source-and-chat-windows` 检查、PR、禁止强推和删除；旧 main 只适用旧流水线的检查，不能直接套用只在工作台分支触发的 CI。若要求独立审阅者，需先有另一位有权限的维护者。单人仓库可先要求 PR/检查并保留明确人工批准记录。
4. **GitHub 安全功能**：后端已有 secret scanning / push protection，前端尚未启用；建议在前端启用。两仓库 Dependabot security updates 当前关闭，可先开漏洞告警/更新并评估噪声。没有擅自改写这些配置。
5. **正式发布**：审查并批准 Draft PR 与目标版本；随后按 [ZENODO.md](ZENODO.md)操作。没有 DOI 的工作台不填虚构徽章。

## 回退与限制

源码改动可按提交 revert；它们没有迁移业务数据库或改变 AI 权限。私密漏洞报告为唯一实际调整的仓库开关，可在 Security 设置中关闭；更改前均为 false，之后复核 true。任何已暴露秘密若后续发现，应先轮换，历史处理由所有者决定。

此审计不覆盖远程服务内部、不可达/已删除服务器历史、未提供的 LFS 数据、全部将来插件或未来安装包，也不是全面漏洞/渗透测试。依赖许可证以记录的版本和文件为依据，元数据缺失和商业服务条款未知已单列；没有用“未检出”替代完整安全保证。
