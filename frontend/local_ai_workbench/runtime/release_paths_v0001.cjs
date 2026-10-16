// ============================================================
// 文件名: release_paths_v0001.cjs
// 中文名: 发布版 Python 解释器定位
// 版本号: v0001
// 主层级: action
// 层级: workbench / runtime
// 脚本定位: 运行时路径解析
// 职责说明: 从显式环境变量或后端虚拟环境定位解释器。
// 本脚本做什么: 解析与校验文件路径。
// 本脚本不做什么: 不安装依赖、不启动程序。
// 制度边界声明: 不回退到开发机的环境。
// 可更新: True
// ALIAS_META
// alias: release_paths_v0001
// family: release_paths
// role: runtime_location
// version: v0001
// status: staging
// entry_point: runtime/release_paths_v0001.cjs
// input: backend root, WORKBENCH_PYTHON
// output: interpreter path
// depends_on: Node stdlib
// used_by: workbench adapters
// ============================================================
'use strict';
const fs=require('node:fs'),path=require('node:path');
const SCRIPT_NAME='release_paths_v0001',SCRIPT_FAMILY='release_paths',SCRIPT_VERSION='v0001',DEFAULT_ENCODING='utf8';
function python(root,windowless=false){
 const selected=process.env.WORKBENCH_PYTHON || path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const result=path.resolve(root,selected);
 if(!fs.existsSync(result))throw Error('Python environment missing. Create backend .venv or set WORKBENCH_PYTHON.');
 if(windowless&&process.platform==='win32'){
  const hidden=path.join(path.dirname(result),'pythonw.exe');
  if(fs.existsSync(hidden))return hidden;
 }
 return result;
}
module.exports={python};
