# Zenodo 与版本发布流程（准备阶段）

## 已核实的现状

2026-10-16 只读核验：`David7583/baigong` 已有活动的 zenodo.org webhook；前端仓库没有该连接。本次没有授权应用、修改 webhook、创建 Release、上传存档或申请 DOI。

旧数据流水线已有真实记录：

- 概念 DOI：[10.5281/zenodo.22093153](https://doi.org/10.5281/zenodo.22093153)。
- v0.2.0：[10.5281/zenodo.22093154](https://doi.org/10.5281/zenodo.22093154)。
- v0.3.0：[10.5281/zenodo.22929364](https://doi.org/10.5281/zenodo.22929364)，记录指向 `baigong/tree/v0.3.0`。旧 main README 中“v0.3.0 DOI 待确认”的文字已经过时，本 PR 不修改该产品线。

这些是数据流水线的 DOI，不能当作工作台前端或新后端的版本 DOI。后端工作台与旧流水线在同一仓库：GitHub Release 属于整个仓库，Zenodo 连接不按分支隔离。**先由作者决定两条产品线是否延续同一概念记录，或先拆分后端仓库；未决定前不发布 Workbench Release。**

## 元数据

本仓库 `CITATION.cff` 使用 CFF 1.2.0，作者为用户确认的 Ruixin Yang，无 ORCID、机构或臆造 DOI。`version: 0.7.0-chat.1` 与 package.json 对齐，仅表示源码标识；不代表已正式发布。没有添加未经确认的发布日期。

`CITATION.cff` 是唯一来源，不添加 `.zenodo.json`。两者并存时 Zenodo 优先采用后者，容易造成漂移。使用 `python tests/repository_check_v0001.py` 按官方完整 Schema 校验；离线可传 `--schema <已下载的官方 schema.json>`，工具核验固定 SHA-256。

## 需要仓库所有者完成的步骤

1. 决定工作台后端与旧流水线的存档边界、后端目标仓库，以及前后端分别引用还是统一产品引用。
2. 登录自己的 Zenodo 账号，核对已有 baigong 连接的所有者和存档集合。前端如要接入，由本人在 Zenodo 的 GitHub 页面授权、同步并启用选定仓库；本工具不代为授权。
3. 核实 Logo 的对外再分发范围、宣传配音服务条款，并完成二进制安装包（如有）的第三方许可证打包检查。
4. 审核 Draft PR、CI 和验收记录，明确批准目标提交与版本。未经批准不合并、不打正式标签、不发布。

## 获批后的维护顺序

1. 在功能分支更新 package.json、前端 lockfile 根版本、CITATION.cff、变更记录和源码清单；前后端配对版本应一致。涉及旧流水线的内容单独评审。
2. 检查所有源码和 Release 附件，重跑测试；视频用 Release 附件，不进入普通源码提交。打标签前确认 GitHub 自动源码归档也没有不应分发的文件。
3. 通过 PR 合并到已经批准的稳定分支。由维护者人工在批准的提交上创建版本标签和 GitHub Release；现有 CI 只有读权限，没有发布步骤。
4. Zenodo 收到正式 Release 后，核对导入状态、作者、版本、许可证、归档文件及真实 DOI。草稿不当作正式发布；不要用发布再删除的方式测试集成。需要试验时使用独立测试仓库和 Zenodo Sandbox，并由用户授权。
5. 成功后以文档 PR 添加真实概念 DOI 徽章和版本 DOI 引用；不预填占位 DOI。不要改写已发布标签来补 DOI，保存版本—提交—GitHub Release—Zenodo Record 映射。下一版本发布前更新 CFF，已有具体 DOI 不应误指上一版本。

## 官方依据

- [CFF 1.2.0](https://github.com/citation-file-format/citation-file-format/blob/1.2.0/schema.json)
- [Zenodo 的 CFF 支持及优先级](https://help.zenodo.org/docs/github/describe-software/citation-file/)
- [启用仓库](https://help.zenodo.org/docs/github/enable-repository/)
- [Release 归档与核验](https://help.zenodo.org/docs/github/archive-software/github-upload/)
