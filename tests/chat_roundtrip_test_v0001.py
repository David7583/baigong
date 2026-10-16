# ============================================================
# 文件名: chat_roundtrip_test_v0001.py
# 中文名: 聊天总控与持久化人工服务验收
# 版本号: v0001
# 主层级: action
# 层级: release / tests
# 脚本定位: 真实聊天链路的本机 HTTP 人工模型验证
# 职责说明: 验证总控许可拒绝、准入、响应和会话恢复。
# 本脚本做什么: 在独立测试安装中创建人工配置及本机服务。
# 本脚本不做什么: 不调用真实模型，不更改正式聊天配置与许可。
# 制度边界声明: 人工服务明确标记；测试产物只写 runtime/release-model-test。
# 可更新: True
# ALIAS_META
# alias: chat_roundtrip_test_v0001
# family: chat_roundtrip_test
# role: local_fixture_acceptance
# version: v0001
# status: staging
# entry_point: tests/chat_roundtrip_test_v0001.py
# input: RELEASE_TEST_ROOT
# output: safe validation JSON
# depends_on: existing chat service and AI controller
# used_by: release operator
# ============================================================
from pathlib import Path
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import yaml

DEFAULT_ENCODING='utf-8'
SCRIPT_NAME='chat_roundtrip_test_v0001'
SCRIPT_FAMILY='chat_roundtrip_test'
SCRIPT_VERSION='v0001'


def main():
    root=Path(os.environ['RELEASE_TEST_ROOT']).resolve()
    folder=root/'runtime/release-model-test'
    folder.mkdir(parents=True,exist_ok=True)
    calls=[]
    class Fixture(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path=='/api/show':result={'capabilities':['completion']}
            else:
                assert self.path=='/api/chat';calls.append(body)
                result={'model':'release-fixture','message':{'role':'assistant','content':'人工测试响应：聊天链路已通过。'},'done':True,'done_reason':'stop','prompt_eval_count':12,'eval_count':8}
            raw=json.dumps(result,ensure_ascii=False).encode('utf-8');self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def write(name,data):
        p=folder/name;p.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8');return p.relative_to(root).as_posix()
    source=yaml.safe_load((root/'config/action/ai/ollama_config_v0001.example.yml').read_text('utf-8'))
    source['connection']['base_url']=f'http://127.0.0.1:{server.server_port}'
    source['model_inventory']=[{'model_id':'release-fixture','capabilities':['completion']}]
    control_cfg=yaml.safe_load((root/'config/action/ai/action_ai_controller_config_v0003.yml').read_text('utf-8'))
    control_cfg['state_path']='runtime/release-model-test/controller.sqlite3'
    control_cfg['sources']['ollama']=write('fixture.yml',source)
    control_file=write('controller.yml',control_cfg)
    sys.path.insert(0,str(root/'scripts/action/development/scripts'))
    from independent_action_chat.action_chat_server_v0014 import build_service
    from independent_action_chat.action_chat_config_v0002 import open_controller
    config=yaml.safe_load((root/'config/action/config/independent_action_chat_release.yml').read_text('utf-8'))
    config['ai_controller']['config']=control_file
    config['ai_controller']['token_file']='runtime/release-model-test/identity.json'
    config['provider_profiles']={'ollama':{'controller_provider':'ollama','profile':'default','operation':'chat','display_name':'人工测试服务','models':[{'id':'release-fixture','enabled':True}]}}
    config['runtime'].update(default_answer_provider='ollama',default_answer_model='release-fixture')
    config['router'].update(default_provider='ollama',default_model='release-fixture',default_mode='machine_only')
    config['storage']={k:'runtime/release-model-test/'+k for k in ('runtime_dir','log_dir','action_output_dir')}
    config_file=root/write('chat.yml',config)
    service=None
    try:
        with open_controller(root,config['ai_controller']) as controller:
            controller.initialize();identity=controller.register('independent_action_chat')
            (folder/'identity.json').write_text(json.dumps(identity),encoding='utf-8')
            service=build_service(config_file,None)
            denied=service.send('请回答你好')
            assert not calls and denied.get('error'),str(denied)
            controller.set_global(True);controller.set_permission('independent_action_chat',True)
            result=service.send('请回答你好')
            assert len(calls)==1,json.dumps(result,ensure_ascii=False)
            assert '人工测试响应' in json.dumps(result,ensure_ascii=False),str(result)
            session=service.state['session_id'];service.close();service=None
            service=build_service(config_file,None);history=service.session_history(session)
            assert any('人工测试响应' in m['content'] for m in history['messages'])
            controller.set_permission('independent_action_chat',False)
            service.send('请再次回答你好');assert len(calls)==1
        print(json.dumps({'status':'passed','fixture':True,'real_model_calls':0,'checks':['permission_denied','controller_http_roundtrip','history_restore','permission_revoke']}))
    finally:
        if service:service.close()
        server.shutdown();server.server_close()


if __name__=='__main__':main()
