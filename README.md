# 百工 · Local AI Workbench 后端

此分支提供 Local AI Workbench 的 Node 本机 API、Python 管理能力、AI 总控与聊天服务。默认仅加载聊天，保留资源管理、加载/卸载、规范审计等基础框架。AI 获客经营中心已从此发布移除。

这是 `workbench-backend` 分支；[main 分支](https://github.com/David7583/baigong/tree/main)继续保留既有 v0.3.0 数据处理流水线，本分支不替换它的发布记录。

## 配套前端

[baigong-workbench-frontend](https://github.com/David7583/baigong-workbench-frontend) 是独立公开源码仓库。两者应使用同一发布版本 `0.7.0-chat.1`。下载源码 ZIP 后也能按下列方式初始化；初始化会为 ZIP 副本建立规范审计所需的本地 Git 元数据。

## 安装

Windows x64，Python 3.12、Node.js 24、Git。安装依赖需要联网。不要复制原工作台的虚拟环境、数据库或密钥。

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
& .\.venv\Scripts\python.exe setup_workbench_v0001.py
```

初始化只创建空配置和此电脑专属聊天身份，不开启 AI 权限，不发送模型请求，重复执行保留已有配置。之后按照前端 README 启动桌面；桌面负责启动配套后端，关闭桌面不会删除会话。

单独运行 API（没有前端目录时，API 仍可运行，页面资源不会自动下载）：

```powershell
$env:WORKBENCH_FRONTEND_ROOT = (Resolve-Path '..\baigong-workbench-frontend').Path
npm start
```

服务仅绑定 `127.0.0.1`；端口由系统分配，启动时输出地址。`GET /api/session` 获取本机会话令牌；API 使用 `X-Workbench-Token`。高风险操作继续要求受限桌面通道与规范审批，不能将其直接部署为公网服务。

## 模型配置

- `config/action/config/independent_action_chat_release.yml`：聊天模型、路由及上下文设置。
- `config/action/ai/ai_api_catalog_v0001.yml`：Provider、协议、模型目录。
- `config/action/ai/api_keys_v0002.example.yml`：无密钥示例；初始化生成被 Git 忽略的本机文件。
- `config/action/ai/action_ai_controller_config_v0003.yml`：统一许可、审计和请求边界。

默认通过 `OPENAI_API_KEY` 环境变量读取用户自己的密钥。设置环境变量后重新启动工作台，在聊天页面显式开启总许可和应用许可。所有实际模型请求仍经过原有 AI 总控。不会自动切换 Provider、下载模型或绕过权限。

会话在 `runtime/workbench_action_chat/`，工作台状态在 `frontend/local_ai_workbench/.runtime/`，总控状态在 `data_runtime/`，规范审计在 `governance/`。聊天启动日志位于工作台数据目录下的 `chat-launches/<实例>/process.log`。这些都不随发布包分发。

## 范围与测试

源码复用现有工作台，未另建模型服务。Python 仅锁定基础所需依赖；向量库、CAD、外部 Agent 等专业能力及其环境由用户另行接入。跨平台路径已有适配，但未完成 macOS/Linux 桌面验收。

请在专用副本中初始化，再运行：

```powershell
$env:RELEASE_TEST_ROOT = (Resolve-Path '.').Path
$env:WORKBENCH_FRONTEND_ROOT = (Resolve-Path '..\baigong-workbench-frontend').Path
npm test
```

测试会创建本地人工运行状态与审计，不应在真实业务目录执行。详见 [验收说明](docs/RELEASE_VALIDATION.md)。沿用百工 Apache-2.0 许可及 NOTICE。没有附带模型权重、真实业务数据、凭证或已安装依赖。

## 功能成熟度与开源协作

当前 Windows 空工作台基线已验收启动、聊天界面、许可关闭、保存/重启持久化和加载/卸载。编排及专业资源接口存在，但在线模型、第三方工具、通用 AI 编排分析、向量模型和跨平台运行不等于已全面验收。未来独立安装包和自动发布不是当前交付功能。配置远程模型/工具可能发送数据；不承诺所有数据绝不离开本机。

开发、测试和 PR 流程见 [CONTRIBUTING.md](CONTRIBUTING.md)；另见[安全报告](SECURITY.md)、[第三方清单](THIRD_PARTY.md)、[仓库审计](docs/REPOSITORY_AUDIT.md)及 [Zenodo 准备](docs/ZENODO.md)。CITATION.cff 仅描述工作台后端，旧数据流水线 DOI 不应作为它的版本 DOI。
