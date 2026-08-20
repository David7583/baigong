<p align="center">
  <img src="assets/baigong-logo.png" alt="百工 Logo" width="240">
</p>

<h1 align="center">百工</h1>

<p align="center"><strong>面向 AI Agent、AI Governance 与 Agent Harness 的可溯源、可审计、可回溯本地底座 Demo</strong></p>

<p align="center">
  <code>AI Agent</code> · <code>AI Governance</code> · <code>Agent Harness</code> ·
  <code>Traceable</code> · <code>Auditable</code> · <code>Local-first</code> ·
  <code>Python 3.12</code> · <code>Windows</code> · <code>Apache-2.0</code>
</p>

**百工**是一套面向本地 AI 数据与执行治理的 Data–Action–Data 演示系统。它从人工构造的
JSON 样例出发，依次完成数据发现、接纳与来源登记、结构治理、SQLite 身份锚定、DuckDB
分析物化、BGE-M3 向量生成、Chroma 写入和结果回流准备。

本目录是可独立安装和测试的演示发布包。它用于验证最小闭环和模块接口，不包含真实用户数据、
正式数据库、API 密钥或模型权重。百工不是一个已经封装完成的通用 Agent 产品；它展示的是
Agent 在读取记忆、调用工具或执行动作时，如何获得可验证的数据来源、受约束的执行入口和可回看的运行证据。

English documentation: [README_EN.md](README_EN.md)

## 面向 AI Agent、Governance 与 Harness 的定位

AI Agent 不应只“得到一个答案”，还应能够说明输入来自哪里、经过了哪些处理、由哪个版本执行，
以及失败后如何定位和回到安全状态。百工把这些要求放进同一条可运行链路：

| 方向 | 百工提供的基础能力 |
| --- | --- |
| AI Agent | 为记忆、检索和工具执行提供稳定对象身份、结构化上下文和明确的输入输出契约 |
| AI Governance | 分离原始事实与派生结果，保留 lineage、运行清单、显式授权门和结构化错误 |
| Agent Harness | 用固定版本编排串联 Data、Action、向量化与回流节点，并约束路径、配置、模型和数据库写入 |

因此，百工更接近一个**可治理的 Agent 执行与数据 Harness 样板**：模型和上层 Agent 可以替换，
但来源、身份、边界、运行证据和回退入口不能随意丢失。

## 可溯源、可回溯与可回退

百工的核心价值不是“把数据写进多个库”，而是让每一步都能回到来源并解释其形成过程：

```text
原始样本
  ↓ 来源路径、内容哈希、稳定 ID
Data 发现与接纳
  ↓ handoff / lineage / inventory
Action 结构治理与身份锚定
  ↓ concept、instance、字符位置与运行 ID
DuckDB / BGE-M3 / Chroma 派生
  ↓ 子流程 completion manifest 与写入状态
Action Return
  ↓ cycle_id、总 completion manifest、可检查结果
下一轮 Data 或上层 Agent
```

- **来源可溯源**：原始输入与派生结果分离；`asset_id`、内容哈希、来源路径和字符位置用于返回证据。
- **运行可回溯**：`run_id`、`cycle_id`、Action lineage、子流程完成清单和回流清单共同记录一次执行经过。
- **版本可核验**：总入口固定子脚本版本，发布清单为脚本、配置模板和样例记录 SHA-256。
- **动作受治理**：数据库写入、真实模型调用和可选 Neo4j 写入需要显式确认；dry-run 可以提前暴露配置与 schema 问题。
- **失败可诊断**：错误以结构化 JSON 返回，不把部分失败伪装成完整成功，也不静默切换模型或数据库。
- **结果可回退**：测试写入被限制在 `temp/`；`v0005` 保留为代码回退入口，`v0006` 作为当前受检入口。

这里的“回退”不是承诺自动撤销所有外部数据库事务，而是确保代码版本、测试数据、执行证据和
写入边界足够清楚，使失败能够被定位、隔离、重放或由操作者安全撤回。

## 当前状态

- 总入口：`scripts/orchestration/action/data_action_chain_pipeline_v0006.py`
- 回退入口：`scripts/orchestration/action/data_action_chain_pipeline_v0005.py`
- 已验证平台：Windows、Python 3.12.7
- 已验证 GPU：NVIDIA GeForce RTX 4060 Laptop GPU
- 已验证 PyTorch：`2.9.0+cu126`
- 已验证主链：SQLite、DuckDB、BGE-M3、Chroma
- Neo4j：代码和配置模板保留，但属于尚未验收的可选分支

最近一次隔离验收包括 dry-run、mock 全链路和真实 BGE-M3 小批量测试。真实模型测试生成了
6 个 1024 维向量，最终状态为 `ready_for_data_discovery`。去敏后的验收摘要见
[`docs/acceptance/acceptance_report_v0001.json`](docs/acceptance/acceptance_report_v0001.json)。

## 快速安装

### 图形安装（推荐）

在 Windows 中双击：

```text
launch_installer.cmd
```

安装器只有开始按钮、当前阶段、活动进度条和日志。它会串行完成：

1. 创建或复用项目本地 `.venv`；
2. 检测 NVIDIA GPU，选择 CUDA 或 CPU 路线；
3. 安装 Python 依赖；
4. 从 `*.example.yml` 复制缺失的活动配置；
5. 验证 PyTorch、CUDA 和主要运行库。

安装 CUDA 版 PyTorch 需要下载约 2.6 GB。安装期间不要同时启动另一个 pip 进程写入同一
虚拟环境。完整日志保存在 `logs/`。

### 命令行安装

```powershell
Set-Location '<百工解压目录>'
python -m venv .venv
& '.\.venv\Scripts\Activate.ps1'
```

NVIDIA GPU 路线：

```powershell
python -m pip install --no-deps --timeout 600 --retries 10 `
  torch==2.9.0 --index-url https://download.pytorch.org/whl/cu126
python -m pip install --timeout 300 --retries 5 `
  -r .\requirements\requirements-data-action-demo.txt
```

CPU 路线：

```powershell
python -m pip install --no-deps --timeout 600 --retries 10 `
  torch==2.9.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install --timeout 300 --retries 5 `
  -r .\requirements\requirements-data-action-demo.txt
```

验证环境：

```powershell
python -c "import torch, yaml, duckdb, chromadb, sentence_transformers; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())"
```

依赖文件固定本次验收使用的直接依赖版本，但不包含全部传递依赖 wheel 的哈希，因此不声明
跨软件源的逐字节可复现安装；边界详见 `requirements/README.md`。

## 配置

发布包以 `*.example.yml` 保存配置模板。图形安装器会复制缺失的活动配置但不会覆盖已有文件。
命令行安装时可执行：

```powershell
Get-ChildItem .\config -Recurse -Filter *.example.yml | ForEach-Object {
    $destination = $_.FullName -replace '\.example\.yml$', '.yml'
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
}
```

默认向量配置使用 `BAAI/bge-m3` 和 CUDA。CPU 用户应在本地活动配置
`config/embedding_generator_config_v0002.yml` 中将 `backend.device` 改为 `cpu`。
模型权重不随发布包提供；本地运行需要已有 Hugging Face 缓存或在首次使用时允许下载。

## 运行前预检

推荐使用根目录的隔离测试入口。它会复制人工样例并把数据库、向量、日志和运行证据全部放入
新的 `temp/<批次>/`，拒绝覆盖已有批次：

```powershell
.\run_demo_test.ps1 -Mode DryRun
.\run_demo_test.ps1 -Mode Mock
```

只查看完整命令而不创建文件：

```powershell
.\run_demo_test.ps1 -Mode Mock -PlanOnly
```

查看总入口的全部参数：

```powershell
python .\scripts\orchestration\action\data_action_chain_pipeline_v0006.py --help
```

`--dry-run` 会解析活动配置、检查路径和 Action 业务库 schema，但不会执行流水线：

- 数据库存在且四表正确：`preflight.ready=true`
- 数据库尚未创建：`action_data_db.status=missing`
- 普通文件或错误数据库：返回结构化 JSON 错误

首次隔离 mock 测试应把所有可写路径放在 `temp/<批次名>/`，并显式使用：

```text
--test-mode --mock-api --init-action-db
--confirm-database-write --confirm-execution
```

真实 BGE-M3 测试还需使用 `--confirm-model-api`。不要在真实数据或正式数据库上进行 Demo 测试。

## 数据库说明

发布包的 `sql/` 目录不含数据库，只包含用途说明：

- `action_data.db` 是 Demo 运行时的 4 表业务库；测试时只能在 `temp/` 下创建。
- `action.db` 是 13 表开发登记库，不属于 Demo 运行数据，也不随本发布包提供。
- 测试不得把 `AGENTS.md`、空文件或 13 表 `action.db` 当成 `--action-db`。

详见 `sql/README.md`。

## 目录结构

```text
百工/
├─ launch_installer.cmd        图形安装入口
├─ run_demo_test.ps1           隔离 dry-run / mock 测试入口
├─ assets/                     Logo 等发布视觉资产
├─ installer/                  安装器代码
├─ scripts/                    Data 与 Action 脚本及编排入口
├─ config/                     配置模板和本地活动配置
├─ data/data_raw/test/         人工构造的唯一示例数据
├─ requirements/               主依赖与 Neo4j 可选依赖
├─ docs/acceptance/             去敏验收摘要
├─ sql/                        空数据库目录及说明
├─ temp/                       隔离测试产物（不发布）
├─ DEPENDENCY_MANIFEST.json    机器可读依赖与 SHA-256 清单
└─ FILE_LIST.md                人工审核文件清单
```

## 数据与安全边界

- 原始示例数据只读；派生结果不得覆盖它。
- 向量命中和分析结果属于派生线索，不能冒充原始事实；应通过稳定 ID 回到 SQLite 来源记录。
- 同一对象跨 SQLite、DuckDB 和 Chroma 时应保持可检查的身份映射。
- 测试数据库、向量、日志和运行产物必须位于 `temp/` 或 `logs/`。
- 不提交真实数据、数据库、模型权重、Token、密码或本机私有配置。
- 不复制其他项目的 `.venv` 作为部署方式。
- 不安装或调用 Neo4j，除非明确选择并单独验收该可选分支。

## 发布完整性

- `DEPENDENCY_MANIFEST.json` 记录脚本、配置模板、示例数据和发布辅助文件的 SHA-256。
- `FILE_LIST.md` 提供人工可读的发布文件清单。
- Logo 保留原始 C2PA 内容凭证，其中包含生成工具的来源声明与签名元数据；该凭证不作为代码执行，也不包含项目所有者身份。
- 去敏验收摘要不包含带本机绝对路径的原始运行清单；这一限制已在摘要中明确记录。
- `v0006` 是当前入口；`v0005` 保留为无需删除文件的回退方式。
- 每次真实运行还应保留总 completion manifest、子流程清单、Action lineage 和 Action Return 清单；发布文件完整性与运行证据完整性分别检查。
- 运行产物、活动配置和虚拟环境由 `.gitignore` 排除。

如果安装失败，请保留安装器日志或 PowerShell 的完整错误输出。仅有“卡住”不足以判断问题；
应同时检查进度行、网络吞吐、Python 进程和最终退出码。

本项目采用 **Apache License 2.0**；完整条款以 GitHub 仓库根目录的 `LICENSE` 文件为准。
第三方依赖、模型和服务仍分别受其自身许可证与条款约束。项目可在以后由明确的新需求、真实
缺陷或测试证据驱动继续更新。本轮风险收口后不再追加探索性优化。
