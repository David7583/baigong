# ============================================================
# 文件名: setup_workbench_v0001.py
# 中文名: 干净工作台首次初始化
# 版本号: v0001
# 主层级: action
# 层级: release / setup
# 脚本定位: 显式创建空配置与聊天应用身份
# 职责说明: 为首次使用准备本机专属状态。
# 本脚本做什么: 复制空凭证示例，调用原 AI 总控初始化和注册接口。
# 本脚本不做什么: 不开启许可、不调用模型、不覆盖已有配置或身份。
# 制度边界声明: 凭证仅写入被 Git 忽略的 runtime；不输出凭证。
# 可更新: True
# ALIAS_META
# alias: setup_workbench_v0001
# family: setup_workbench
# role: explicit_local_provisioning
# version: v0001
# status: staging
# entry_point: setup_workbench_v0001.py
# input: explicit CLI invocation
# output: local empty state and safe status JSON
# depends_on: action_ai_controller_v0002, PyYAML, httpx
# used_by: user installation
# ============================================================
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import sys

DEFAULT_ENCODING='utf-8'
SCRIPT_NAME='setup_workbench_v0001'
SCRIPT_FAMILY='setup_workbench'
SCRIPT_VERSION='v0001'
ROOT=Path(__file__).resolve().parent


def main():
    if not shutil.which('git'):
        raise RuntimeError('Install Git first; the existing standards center uses Git history.')
    if not (ROOT/'.git').exists():
        subprocess.run(['git','init','--initial-branch=local-workbench',str(ROOT)],check=True,capture_output=True)
    for name in ('api_keys_v0002','ollama_config_v0001'):
        directory=ROOT/'config/action/ai'
        target=directory/(name+'.yml')
        if not target.exists():shutil.copyfile(directory/(name+'.example.yml'),target)
    source=ROOT/'scripts/action/infrastructures/action_ai_controller_v0002.py'
    spec=importlib.util.spec_from_file_location('_release_controller',source)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    identity=ROOT/'runtime/independent_action_chat/ai_application_access.json'
    with module.AIController(ROOT/'config/action/ai/action_ai_controller_config_v0003.yml',root=ROOT) as controller:
        if not controller.db_path.exists():controller.initialize()
        if not identity.exists():
            result=controller.register('independent_action_chat')
            identity.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(identity,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w',encoding=DEFAULT_ENCODING) as stream:
                json.dump({'app_id':result['app_id'],'token':result['token']},stream)
    print(json.dumps({'status':'ready','configuration_preserved':True,'permissions_changed':False,'model_requests':0}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
