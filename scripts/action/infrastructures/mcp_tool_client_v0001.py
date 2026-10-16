# ============================================================
# File: mcp_tool_client_v0001.py
# 中文名: MCP 单次按需工具接取
# Version: v0001
# Main Layer: action
# Layer: infrastructures
# Responsibilities: 执行已解析的 MCP 初始化、发现与单次工具请求。
# Does: 支持 stdio 和 Streamable HTTP 的 JSON/SSE 响应。
# Does not: 自动安装、绕过能力授权、读取 Provider Key 或调用 AI 总控旁路。
# Boundary: 只接受核验后的 JSON 任务；不重放超时调用；只退出自身子进程。
# Updatable: True
# ============================================================
# ALIAS_META
# alias: mcp_tool_client_v0001
# family: mcp_tool_client
# role: mcp_transport
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/mcp_tool_client_v0001.py
# input:
#   - capability.mcp.v1 resolved JSON request
# output:
#   - capability.mcp.v1 JSON result
# depends_on:
#   - Python standard library
# used_by:
#   - capability_invoker_v0001
# ============================================================
import argparse
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

SCRIPT_NAME = 'mcp_tool_client_v0001'
SCRIPT_FAMILY = 'mcp_tool_client'
SCRIPT_VERSION = 'v0001'
DEFAULT_ENCODING = 'utf-8'
PROTOCOLS = {'2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'}
MAX_RESPONSE = 4 * 1024 * 1024


class MCPError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise MCPError('MCP_REDIRECT_REFUSED')


class Client:
    def __init__(self, descriptor, timeout=30):
        self.descriptor = descriptor
        self.timeout = timeout
        self.process = None
        self.session = None
        self.protocol = '2025-11-25'
        self.sequence = 0
        self.deadline = time.monotonic() + 60
        self.messages = queue.Queue(maxsize=200)
        self.opener = urllib.request.build_opener(NoRedirect())
        if descriptor['transport'] == 'stdio':
            inherited = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'PYTHONIOENCODING'}}
            for name in descriptor.get('env_names', []):
                if name in os.environ:
                    inherited[name] = os.environ[name]
            self.process = subprocess.Popen([descriptor['command'], *descriptor.get('args', [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=inherited, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=self.read_lines, daemon=True).start()

    def read_lines(self):
        try:
            while True:
                raw = self.process.stdout.readline(MAX_RESPONSE + 1)
                if not raw:
                    self.messages.put(MCPError('MCP_PROCESS_EXITED'))
                    return
                if len(raw) > MAX_RESPONSE:
                    raise MCPError('MCP_RESPONSE_BUDGET')
                self.messages.put(json.loads(raw))
        except Exception:
            self.messages.put(MCPError('MCP_INVALID_STDOUT'))

    def request(self, method, params=None, notification=False):
        remaining = min(self.timeout, self.deadline - time.monotonic())
        if remaining <= 0:
            raise MCPError('MCP_SESSION_TIMEOUT_OUTCOME_UNKNOWN')
        self.sequence += 1
        id = self.sequence
        message = {'jsonrpc': '2.0', 'method': method}
        if not notification:
            message['id'] = id
        if params is not None:
            message['params'] = params
        raw = json.dumps(message).encode()
        if self.process:
            self.process.stdin.write(raw + b'\n')
            self.process.stdin.flush()
            if notification:
                return None
            deadline = time.monotonic() + remaining
            while time.monotonic() < deadline:
                try:
                    result = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    raise MCPError('MCP_TIMEOUT_OUTCOME_UNKNOWN')
                if isinstance(result, Exception):
                    raise result
                if result.get('id') == id and 'method' not in result:
                    return self.validate(result, id)
                if 'method' in result and 'id' in result:
                    refused = {'jsonrpc': '2.0', 'id': result['id'], 'error': {'code': -32601, 'message': 'Client capability unavailable'}}
                    self.process.stdin.write(json.dumps(refused).encode() + b'\n')
                    self.process.stdin.flush()
            raise MCPError('MCP_TIMEOUT_OUTCOME_UNKNOWN')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': self.protocol}
        if self.session:
            headers['MCP-Session-Id'] = self.session
        if self.descriptor.get('token_env'):
            token = os.environ.get(self.descriptor['token_env'])
            if not token:
                raise MCPError('MCP_CREDENTIAL_UNAVAILABLE')
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(self.descriptor['url'], raw, headers, method='POST')
        try:
            with self.opener.open(request, timeout=remaining) as response:
                if response.headers.get('MCP-Session-Id'):
                    self.session = response.headers['MCP-Session-Id']
                if notification:
                    if response.status not in {200, 202, 204}:
                        raise MCPError('MCP_NOTIFICATION_REFUSED')
                    return None
                if response.headers.get_content_type() == 'text/event-stream':
                    total, data = 0, []
                    deadline = time.monotonic() + remaining
                    while time.monotonic() < deadline:
                        line = response.readline(MAX_RESPONSE + 1)
                        total += len(line)
                        if total > MAX_RESPONSE:
                            raise MCPError('MCP_RESPONSE_BUDGET')
                        if not line:
                            raise MCPError('MCP_STREAM_INCOMPLETE_OUTCOME_UNKNOWN')
                        if line.strip() == b'' and data:
                            result = json.loads(b'\n'.join(data))
                            data = []
                            if result.get('id') == id:
                                return self.validate(result, id)
                            if 'method' in result and 'id' in result:
                                raise MCPError('MCP_SERVER_REQUEST_UNSUPPORTED')
                        elif line.startswith(b'data:'):
                            data.append(line[5:].strip())
                    raise MCPError('MCP_TIMEOUT_OUTCOME_UNKNOWN')
                content = response.read(MAX_RESPONSE + 1)
                if len(content) > MAX_RESPONSE:
                    raise MCPError('MCP_RESPONSE_BUDGET')
                return self.validate(json.loads(content), id)
        except (TimeoutError, OSError) as error:
            raise MCPError('MCP_TRANSPORT_' + type(error).__name__ + '_OUTCOME_UNKNOWN') from None

    def validate(self, result, id):
        if result.get('jsonrpc') != '2.0' or type(result.get('id')) is not int or result.get('id') != id:
            raise MCPError('MCP_RESPONSE_IDENTITY')
        if 'error' in result:
            raise MCPError('MCP_REMOTE_ERROR_' + str(result['error'].get('code', 'UNKNOWN')))
        if 'result' not in result:
            raise MCPError('MCP_RESULT_MISSING')
        return result['result']

    def initialize(self):
        result = self.request('initialize', {'protocolVersion': self.protocol, 'capabilities': {}, 'clientInfo': {'name': 'LocalAIWorkbench', 'version': '0.7.0-alpha'}})
        if result.get('protocolVersion') not in PROTOCOLS or 'tools' not in result.get('capabilities', {}):
            raise MCPError('MCP_PROTOCOL_OR_TOOLS_UNSUPPORTED')
        self.protocol = result['protocolVersion']
        self.request('notifications/initialized', notification=True)
        return result

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except Exception:
                self.process.terminate()
                self.process.wait(timeout=3)
        elif self.session:
            try:
                headers = {'MCP-Session-Id': self.session, 'MCP-Protocol-Version': self.protocol}
                if self.descriptor.get('token_env'):
                    headers['Authorization'] = 'Bearer ' + os.environ[self.descriptor['token_env']]
                with self.opener.open(urllib.request.Request(self.descriptor['url'], headers=headers, method='DELETE'), timeout=3):
                    pass
            except Exception:
                pass


def execute(data):
    client = Client(data['descriptor'])
    try:
        info = client.initialize()
        tools, cursor = [], None
        for _ in range(20):
            page = client.request('tools/list', {'cursor': cursor} if cursor else {})
            tools.extend(page.get('tools', []))
            if len(tools) > 1000:
                raise MCPError('MCP_TOOLS_BUDGET')
            cursor = page.get('nextCursor')
            if not cursor:
                break
        if cursor:
            raise MCPError('MCP_PAGINATION_INCOMPLETE')
        if data['operation'] == 'discover':
            return {'server': info.get('serverInfo'), 'protocol_version': client.protocol, 'tools': tools}
        if data['operation'] != 'call' or data.get('confirm_execute') is not True:
            raise MCPError('MCP_EXECUTION_CONFIRMATION')
        if not any(x.get('name') == data.get('tool') for x in tools):
            raise MCPError('MCP_TOOL_MISSING')
        result = client.request('tools/call', {'name': data['tool'], 'arguments': data.get('arguments', {})})
        if result.get('isError'):
            raise MCPError('MCP_TOOL_EXECUTION_ERROR')
        return result
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--request', required=True)
    parser.add_argument('--response', required=True)
    args = parser.parse_args()
    raw = Path(args.request).read_bytes()
    request = json.loads(raw)
    try:
        if len(raw) > 262144 or request.get('schema_version') != 'capability.mcp.v1' or request.get('root') != str(Path(args.root).resolve()) or request.get('producer', {}).get('script') != 'capability_invoker_v0001':
            raise MCPError('CONTRACT_IDENTITY')
        output = {'status': 'completed', 'result': execute(request['input'])}
        code = 0
    except Exception as error:
        output = {'status': 'failed', 'error': str(error) if isinstance(error, MCPError) else type(error).__name__}
        code = 2
    output.update({'schema_version': 'capability.mcp.v1', 'run_id': request.get('run_id'), 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION}, 'source_sha256': hashlib.sha256(raw).hexdigest()})
    response = Path(args.response)
    temporary = response.with_suffix('.tmp')
    temporary.write_text(json.dumps(output, ensure_ascii=False), encoding=DEFAULT_ENCODING)
    temporary.replace(response)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
