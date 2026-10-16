# 第三方依赖与素材审计

审计日期：2026-10-16。机器清单见 [dependency-inventory.json](docs/audit/dependency-inventory.json)。清单区分随源码分发、安装时下载、可选能力、视频制作工具和外部服务；许可证元数据不是对全部商业用途的保证。

## 当前源码及基础安装

- 项目自身源码沿用 Apache-2.0；保留现有 Ruixin Yang 版权声明。作者确认不等于独立完成全部代码权属调查。
- 前端随源码附带 Three.js 0.186.1，MIT；保留 `workspace/vendor/three/LICENSE`、上游声明与修改说明。19 个 .mjs 文件只做导入路径/扩展名及空白适配，具体清单见前端 `docs/audit/three-files.json`。上游源码注释中的来源不应删除。
- npm lockfile 记录 15 个安装依赖（本地安装版本逐项一致；14 项包含顶层许可证/NOTICE 文件）。`@electron-internal/extract-zip` 1.0.5 在 package.json 及[上游 v1.0.5 README](https://github.com/electron/extract-zip/tree/v1.0.5)声明 BSD-2-Clause，安装包未附独立 LICENSE，并含原生 .node 文件；正式捆绑前须补齐上游许可与原生子依赖证据。Python 基础锁文件记录 13 个依赖；源码不打包 node_modules、虚拟环境或 Python/Node/Electron 二进制。
- MIT、BSD、ISC、Apache、PSF 等声明应随实际分发的组件保留。`certifi` 为 MPL-2.0；若分发其副本或修改，须按文件级要求提供许可证和相应源码，不把该文件改标为 Apache-2.0。依据：[Mozilla MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)。
- Electron 的 npm 元数据为 MIT，但实际发行还包含 Chromium/Node 及其子组件。已检查本地 Electron 44.4.5 的 `dist/LICENSE` 与约 20 MB 的 `dist/LICENSES.chromium.html`；**将来打安装包必须一并保留这些实际版本的声明**。不能仅以 npm 的 MIT 标签覆盖所有二进制子组件。Python/Node 运行时也需保留各自 LICENSE 与 bundled-library notices。

## 可选能力及旧数据流水线

后端 AST 导入扫描发现 duckdb、neo4j、sentence_transformers、torch、chromadb、numpy、tokenizers、bpy/mathutils 等可选接口，未列入空工作台基础安装。版本未固定、未实际打包的插件、MCP 服务、Agent、CAD 与模型工具须按安装的具体版本另行核实，不能宣称任意插件都能商业使用。尤其 Blender/bpy 的 GPL 以及 Neo4j 不同产品/版本条款，需要结合实际集成和分发方式评审。

旧 main 的 101 项依赖元数据完整保留于机器清单，来源为 main 的 THIRD_PARTY_NOTICES.json，不冒充本次重新安装逐项验证。部分仅有分类器或较长许可证文本，标为待逐项复核。正式 v0.3.0 ZIP 已检查：148 项，带 LICENSE、NOTICE、THIRD_PARTY_NOTICES.json，不包含已安装的二进制依赖或模型权重。

BGE-M3 固定修订 `5617a9f61b028005a4858fdac845db406aefb181` 的[模型卡](https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md)声明 MIT；该修订单独 LICENSE URL 返回 404。权重没有随源码或已检查 ZIP 分发；将来打包权重时须保存可核验许可、版权和来源，不能把模型卡标签当作完整再分发材料。远程模型服务另受账号和服务条款约束。

## 宣传视频、Logo、字体与音频

视频在前端 Draft PR #1 及既有未发布草稿附件中；本次审计不发布或重传。素材清单及哈希在视频工程 ASSETS.md、assets_manifest.json。

| 素材/工具 | 证据与范围 | 结论/发布条件 |
| --- | --- | --- |
| 百工 Logo | 用户提供并授权用于 README/本次视频；文件哈希见源码清单 | 已授权当前用途，但没有据此虚构原作者、商标许可或无限制再许可；公开商业素材包用途由权利人确认 |
| UI 截图/实录 | 隔离环境、人工家庭样例、真实运行；概念动画明确标注 | 没有真实家庭资料；截图里的第三方 UI/标识不改变其权利 |
| 原创背景音乐/图形 | render.py 程序合成，无外部采样 | 工程代码和原创生成素材按项目声明使用 |
| 中文旁白 | edge-tts 7.3.1 调用 Microsoft zh-CN-XiaoxiaoNeural | **待确认**：开源客户端 LGPL-3.0 分类器不授予微软服务或声音输出的再分发权。尚未确认适用于此调用方式的商业宣传许可；正式发布前核实或替换为授权明确的声音 |
| 微软雅黑 | 仅把字体渲染进图像和视频，不分发字体文件 | [微软字体 FAQ](https://learn.microsoft.com/en-us/typography/fonts/font-faq)区分渲染输出和字体再分发；构建者须有合法字体来源。不附带 .ttf/.ttc |
| Pillow / NumPy / Playwright / Electron | 本地制作和采集工具；版本及许可证列入清单 | 不打包其程序；实际 wheel/二进制有额外子组件义务，不能只看顶层包标签 |
| FFmpeg | 本机 2025-03-10 build，启用 GPL、version3 和 libx264；程序未打包 | 使用工具生成视频不等于分发工具本身。若捆绑该构建须另满足 GPL 及对应源码义务；专利/编解码器问题不由 Apache 许可消除。[FFmpeg 许可说明](https://ffmpeg.org/legal.html) |

完整产品并无“所有数据绝不离开本机”的承诺；配置在线模型或在线配音会发送请求。本次仓库校验与验收不调用收费模型。

## 再分发检查

源代码 ZIP 保留 LICENSE、NOTICE、此清单及 vendored 许可证；安装包还必须从实际构建环境收集各运行时/轮子/原生库的完整许可证和版权文本，核对对应源码提供义务。当前没有新增 Workbench 安装包，不能用源码审计替代未来安装包审计。Apache 官方依据：[许可证全文](https://www.apache.org/licenses/LICENSE-2.0.txt)、[FAQ](https://www.apache.org/foundation/license-faq.html)。
