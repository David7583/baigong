# ============================================================
# 文件名: action_chat_providers_v0006.py
# 中文名: 受控聊天与显式 Skill 接取网关
# 版本号: v0006
#
# 主层级: action
# 层级: development / independent_action_chat / providers
# 脚本定位: 聊天 Agent 的 Skill 指令适配与使用留证
#
# 职责说明:
# - 在既有受控请求上接入用户启用并分配的 Skill 指令
#
# 本脚本做什么:
# - 按运行 Agent ID 读取本地市场，记录实际使用的内容哈希
#
# 本脚本不做什么:
# - 不执行 Skill 脚本，不直连 Provider，不自动授予 AI 许可
#
# 制度边界声明:
# - 模型、密钥和调用仍由原网关及 Action 总控管理
# - 只加入显式分配的指令；超限或内容变更在调用前失败
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_providers_v0006
# family: action_chat_providers
# role: controlled_chat_skill_instruction_gateway
# version: v0006
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_providers_v0006.py
# input:
#   - provider configuration, messages, selected model and assigned Skills
# output:
#   - controlled response and Skill usage provenance
# depends_on:
#   - resolve_assigned_skills_v0001 JSON file contract
#   - action_ai_controller_v0002
# used_by:
#   - action_chat_server_v0012
# ============================================================
from __future__ import annotations
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import subprocess
import uuid

# ============================================================
# 本版本包含的完整实现；不依赖同名旧版本文件
# ============================================================
import threading as _contained_0005_threading
import json as _contained_0005__contained_0004_json
import math as _contained_0005__contained_0004_math
import os as _contained_0005__contained_0004_os
import threading as _contained_0005__contained_0004_threading
import time as _contained_0005__contained_0004_time
from copy import deepcopy as _contained_0005__contained_0004_deepcopy
from contextvars import ContextVar as _contained_0005__contained_0004_ContextVar
from dataclasses import dataclass as _contained_0005__contained_0004_dataclass
from typing import Any as _contained_0005__contained_0004_Any
from .action_chat_config_v0002 import open_controller as _contained_0005__contained_0004_open_controller, resolve_project_path as _contained_0005__contained_0004_resolve_project_path
from .action_chat_contracts_v0001 import ChatResult as _contained_0005__contained_0004_BaseChatResult, ProviderError as _contained_0005__contained_0004_ProviderError, ProviderStatus as _contained_0005__contained_0004_ProviderStatus, validate_chat_result as _contained_0005__contained_0004_validate_chat_result
_contained_0005__contained_0004_DEFAULT_ENCODING = 'utf-8'
_contained_0005__contained_0004_SCRIPT_FAMILY = 'action_chat_providers'
_contained_0005__contained_0004_SCRIPT_NAME = 'action_chat_providers_v0004'
_contained_0005__contained_0004_SCRIPT_VERSION = 'v0004'

class _contained_0005__contained_0004_ProviderUnavailableError(_contained_0005__contained_0004_ProviderError):
    pass

class _contained_0005__contained_0004_ProviderResponseError(_contained_0005__contained_0004_ProviderError):
    pass

@_contained_0005__contained_0004_dataclass(frozen=True)
class _contained_0005__contained_0004_ChatResult(_contained_0005__contained_0004_BaseChatResult):
    assistant_message: dict | None = None
    retry_wait_ms: int = 0
    controller_trace: dict | None = None

def _contained_0005__contained_0004_validate_temperature(value: _contained_0005__contained_0004_Any) -> float:
    if type(value) not in (int, float) or not _contained_0005__contained_0004_math.isfinite(value) or (not 0 <= value <= 2):
        raise _contained_0005__contained_0004_ProviderError('temperature 必须是 0 到 2 之间的有限数字')
    return float(value)

def _contained_0005__contained_0004_model_options(config: dict, model: str) -> dict:
    item = next((x for x in config['models'] if x['id'] == model), None)
    if item is None:
        raise _contained_0005__contained_0004_ProviderUnavailableError('model is not registered with the AI controller')
    return item

def _contained_0005__contained_0004_uses_complete_history(config: dict, model: str) -> bool:
    return _contained_0005__contained_0004_model_options(config, model).get('request_profile') == 'kimi_k3'

def _contained_0005__contained_0004_effective_temperature(config: dict, model: str, requested: _contained_0005__contained_0004_Any) -> float:
    value = _contained_0005__contained_0004_validate_temperature(requested)
    return 1.0 if _contained_0005__contained_0004_uses_complete_history(config, model) else value

class _contained_0005__contained_0004_ProviderView:

    def __init__(self, config: dict):
        self.config = config

    def ensure_model(self, model: str):
        selected = _contained_0005__contained_0004_model_options(self.config, model)
        if not selected.get('enabled', True):
            raise _contained_0005__contained_0004_ProviderUnavailableError('application model selection is disabled')

    def validate(self):
        models = [{**x, 'temperature_policy': {'min': 0.0, 'max': 2.0, 'fixed': 1.0 if _contained_0005__contained_0004_uses_complete_history(self.config, x['id']) else None}} for x in self.config['models'] if x.get('enabled', True)]
        return _contained_0005__contained_0004_ProviderStatus(self.config['provider_id'], True, True, 'Model listed by controller; permissions and credentials checked on invocation.', models)

class _contained_0005__contained_0004_ProviderRegistry:

    def __init__(self, configs: dict, *, connection: dict, project_root, controller=None):
        self._configs = configs
        self._lock = _contained_0005__contained_0004_threading.RLock()
        self._owned = controller is None
        self.control = controller or _contained_0005__contained_0004_open_controller(project_root, connection)
        try:
            token = _contained_0005__contained_0004_os.environ.get(connection['token_env'], '')
            if not token:
                path = _contained_0005__contained_0004_resolve_project_path(project_root, connection['token_file'], must_exist=True)
                record = _contained_0005__contained_0004_json.loads(path.read_text(encoding=_contained_0005__contained_0004_DEFAULT_ENCODING))
                if record.get('app_id') != connection['app_id']:
                    raise _contained_0005__contained_0004_ProviderUnavailableError('application identity file mismatch')
                token = record.get('token', '')
            self.client = self.control.client(connection['app_id'], token)
            if connection['start_on_launch']:
                self.client.start()
        except Exception:
            if self._owned:
                self.control.close()
            raise _contained_0005__contained_0004_ProviderUnavailableError('AI application access unavailable; administrator provisioning required') from None
        self._traces = _contained_0005__contained_0004_ContextVar('ai_call_traces', default=None)

    @property
    def traces(self):
        return self._traces.get() or []

    def begin_turn(self):
        self._traces.set([])

    def close(self):
        if self._owned:
            self.control.close()

    def set_running(self, running: bool):
        if type(running) is not bool:
            raise _contained_0005__contained_0004_ProviderError('running must be boolean')
        return self.client.start() if running else self.client.pause()

    def get(self, provider_id: str):
        if provider_id not in self._configs:
            raise _contained_0005__contained_0004_ProviderUnavailableError('provider is not available through the controller')
        return _contained_0005__contained_0004_ProviderView(self._configs[provider_id])

    def catalog(self):
        return [{'provider_id': name, 'display_name': cfg.get('display_name', name), 'enabled': True, 'available': True, 'message': self.get(name).validate().message, 'models': self.get(name).validate().models} for name, cfg in self._configs.items()]

    def _invoke(self, provider: str, model: str, profile: str, operation: str, payload: dict):
        sink = self._traces.get()
        with self._lock:
            try:
                self.client.select(provider, model, profile)
                result = self.client.invoke(operation, payload)
            except Exception as exc:
                code = str(exc) if type(exc).__module__ == type(self.control).__module__ else 'controller_invocation_failed'
                raise _contained_0005__contained_0004_ProviderError(code) from None
            trace = {key: result[key] for key in ('app_id', 'call_id', 'side', 'lineage', 'provider', 'model', 'operation')}
            if sink is not None:
                sink.append(trace)
            return (result, trace)

    def embed(self, selection: dict, texts: list[str]):
        result, _ = self._invoke(selection['provider'], selection['model'], selection.get('profile', 'default'), 'embed', {'input': texts})
        vectors = result['data'].get('embeddings')
        if not isinstance(vectors, list) or len(vectors) != len(texts) or any((len(v) != selection['dimension'] for v in vectors)):
            raise _contained_0005__contained_0004_ProviderResponseError('embedding dimension mismatch; do not rebuild the existing index')
        return vectors

    def chat(self, provider_id, model, messages, *, timeout_ms, retry_count, temperature, max_output_tokens=None):
        del timeout_ms, retry_count
        if max_output_tokens is not None and (type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192):
            raise _contained_0005__contained_0004_ProviderError('invalid explicit output token budget')
        self.get(provider_id).ensure_model(model)
        config = self._configs[provider_id]
        options = _contained_0005__contained_0004_model_options(config, model)
        temperature = _contained_0005__contained_0004_effective_temperature(config, model, temperature)
        payload = _contained_0005__contained_0004_deepcopy(options.get('request_defaults', {}))
        payload['messages'] = _contained_0005__contained_0004_deepcopy(messages)
        central_provider = config['controller_provider']
        complete = _contained_0005__contained_0004_uses_complete_history(config, model)
        if central_provider == 'ollama':
            payload.setdefault('options', {})['temperature'] = temperature
        else:
            payload['temperature'] = temperature
            payload['max_completion_tokens' if complete else 'max_tokens'] = int(options.get('max_completion_tokens', options.get('max_output_tokens', 1024)))
        if complete:
            effort = options.get('reasoning_effort', 'low')
            limit = options.get('max_completion_tokens', 8192)
            if effort not in {'low', 'high', 'max'} or type(limit) is not int or (not 1 <= limit <= 131072):
                raise _contained_0005__contained_0004_ProviderError('invalid K3 reasoning effort or completion budget')
            payload['reasoning_effort'] = effort
            payload['max_completion_tokens'] = limit
        if max_output_tokens is not None:
            if central_provider == 'ollama':
                payload.setdefault('options', {})['num_predict'] = max_output_tokens
            else:
                payload['max_completion_tokens' if complete else 'max_tokens'] = max_output_tokens
        if config['operation'] == 'messages':
            system = [x['content'] for x in payload['messages'] if x['role'] == 'system']
            payload['messages'] = [x for x in payload['messages'] if x['role'] != 'system']
            if system:
                payload['system'] = '\n\n'.join(system)
        started = _contained_0005__contained_0004_time.perf_counter()
        result, trace = self._invoke(central_provider, model, config.get('profile', 'default'), config['operation'], payload)
        raw = result['data']
        if central_provider == 'ollama':
            message = raw['message']
            text = message.get('content')
            finish = raw.get('done_reason')
        elif config['operation'] == 'messages':
            text = ''.join((x.get('text', '') for x in raw['content'] if x.get('type') == 'text'))
            message = {'role': 'assistant', 'content': text}
            finish = raw.get('stop_reason')
        else:
            if not raw.get('choices'):
                raise _contained_0005__contained_0004_ProviderResponseError('provider response has no choices')
            message = raw['choices'][0]['message']
            text = message.get('content')
            finish = raw['choices'][0].get('finish_reason')
        if not isinstance(text, str) or not text.strip():
            raise _contained_0005__contained_0004_ProviderResponseError('provider returned no final answer')
        if message.get('tool_calls'):
            raise _contained_0005__contained_0004_ProviderResponseError('chat application does not execute returned tools')
        if complete and (message.get('role') != 'assistant' or not isinstance(message.get('reasoning_content'), str)):
            raise _contained_0005__contained_0004_ProviderResponseError('K3 response lacks complete assistant history')
        usage = result['usage']
        return _contained_0005__contained_0004_validate_chat_result(_contained_0005__contained_0004_ChatResult(status='ok', provider=provider_id, model=model, response_text=text, usage={'input_tokens': int(usage.get('input_tokens', usage.get('prompt_tokens', 0))), 'output_tokens': int(usage.get('output_tokens', usage.get('completion_tokens', 0)))}, duration_ms=round((_contained_0005__contained_0004_time.perf_counter() - started) * 1000), raw_finish_reason=finish, assistant_message=_contained_0005__contained_0004_deepcopy(message) if complete else None, controller_trace=trace))

def _contained_0005__contained_0004_main():
    print(_contained_0005__contained_0004_json.dumps({'status': 'error', 'error_type': 'ApplicationComponent', 'detail': 'Use the chat server.'}))
    return 2
_contained_0005_ControlledRegistry = _contained_0005__contained_0004_ProviderRegistry
_contained_0005_ProviderView = _contained_0005__contained_0004_ProviderView
_contained_0005_DEFAULT_ENCODING = 'utf-8'
_contained_0005_SCRIPT_FAMILY = 'action_chat_providers'
_contained_0005_SCRIPT_NAME = 'action_chat_providers_v0005'
_contained_0005_SCRIPT_VERSION = 'v0005'

class _contained_0005_ProviderRegistry:
    """Delay identity connection so SQL/UI remain usable without AI permission."""

    def __init__(self, configs, *, connection, project_root):
        self.configs, self.connection, self.root = (configs, connection, project_root)
        self.registry = None
        self.lock = _contained_0005_threading.RLock()

    def _connect(self):
        with self.lock:
            if self.registry is None:
                self.registry = _contained_0005_ControlledRegistry(self.configs, connection=self.connection, project_root=self.root)
                self.registry.begin_turn()
            return self.registry

    @property
    def traces(self):
        return self.registry.traces if self.registry else []

    def begin_turn(self):
        if self.registry:
            self.registry.begin_turn()

    def get(self, provider_id):
        return _contained_0005_ProviderView(self.configs[provider_id])

    def catalog(self):
        return [{'provider_id': name, 'display_name': cfg.get('display_name', name), 'enabled': True, 'available': True, 'permission_status': 'checked_on_invocation', 'message': 'AI identity and permissions are checked on invocation; SQL retrieval remains available.', 'models': self.get(name).validate().models} for name, cfg in self.configs.items()]

    def chat(self, *args, **kwargs):
        return self._connect().chat(*args, **kwargs)

    def embed(self, *args, **kwargs):
        return self._connect().embed(*args, **kwargs)

    def set_running(self, running):
        return self._connect().set_running(running)

    def close(self):
        if self.registry:
            self.registry.close()

OriginalRegistry = _contained_0005_ProviderRegistry

DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_providers"
SCRIPT_NAME = "action_chat_providers_v0006"
SCRIPT_VERSION = "v0006"


def assigned_skill_request(root, agent_id):
    """Transport only: the independent resolver owns Skill business rules."""
    root = Path(root).resolve()
    snapshot = root / 'runtime/skill_assignments_v0001.json'
    if not snapshot.exists():
        return [], []
    source = Path(__file__).resolve().parents[5]
    source_sha256 = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    run_id = 'skills_' + uuid.uuid4().hex
    directory = root / 'runtime/intermediate_tasks' / run_id
    directory.mkdir(parents=True)
    request, response, manifest = (directory / x for x in ('request.json', 'response.json', 'task.json'))

    def publish(path, value):
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding=DEFAULT_ENCODING) as stream:
            json.dump(value, stream, ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)

    state = {'schema_version': 'intermediate-task.v1', 'run_id': run_id, 'status': 'running', 'active': True,
             'result_verified': False, 'recovery_required': True, 'consumers': [{'id': SCRIPT_NAME, 'acknowledged': False}], 'intermediates': []}
    publish(manifest, state)
    with (directory / 'task.lock').open('w+b') as lease:
        lease.write(b'0'); lease.flush(); lease.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            publish(request, {'schema_version': 'skill-resolution.v1', 'run_id': run_id, 'producer': {'script': SCRIPT_NAME, 'version': SCRIPT_VERSION},
                              'root': str(root), 'agent_id': agent_id, 'source_sha256': source_sha256})
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            completed = subprocess.run([sys.executable, '-B', str(source / 'scripts/action/infrastructures/resolve_assigned_skills_v0001.py'),
                                        '--request', str(request), '--response', str(response)], capture_output=True, text=True, encoding=DEFAULT_ENCODING, timeout=20, **flags)
            if completed.returncode:
                try:
                    detail = json.loads(completed.stdout).get('detail', 'skill_resolution_failed')
                except Exception:
                    detail = 'skill_resolution_failed'
                raise ValueError(detail)
            if response.is_symlink() or response.stat().st_size > 1024 * 1024:
                raise ValueError('skill_response_invalid')
            result = json.loads(response.read_text(DEFAULT_ENCODING))
            if result.get('schema_version') != 'skill-resolution.v1' or result.get('run_id') != run_id or result.get('status') != 'completed' or result.get('producer') != {'script': 'resolve_assigned_skills_v0001', 'version': 'v0001'}:
                raise ValueError('skill_response_contract_invalid')
            value = result['result']
            if value.get('source_sha256') != source_sha256 or not isinstance(value.get('instructions'), list) or not isinstance(value.get('records'), list):
                raise ValueError('skill_response_source_invalid')
            state.update(status='completed', active=False, result_verified=True, recovery_required=False,
                         consumers=[{'id': SCRIPT_NAME, 'acknowledged': True}],
                         intermediates=[{'path': p.relative_to(root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                         'regenerable': True, 'protected': False} for p in (request, response)])
            publish(manifest, state)
        except Exception:
            state.update(status='failed', active=False, recovery_required=True)
            publish(manifest, state)
            raise
    cleanup = subprocess.run([sys.executable, '-B', str(source / 'scripts/action/infrastructures/intermediate_cleanup_v0001.py'),
                              '--root', str(root), '--manifest', manifest.relative_to(root).as_posix()], capture_output=True, timeout=20, **flags)
    if cleanup.returncode:
        raise ValueError('completed_skill_task_cleanup_pending')
    return value['instructions'], value['records']


class ProviderRegistry(OriginalRegistry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skill_usage = {}

    @property
    def traces(self):
        return [{**trace, **({"skills_applied": self.skill_usage[trace["call_id"]]} if trace.get("call_id") in self.skill_usage else {})}
                for trace in super().traces]

    def chat(self, provider_id, model, messages, **kwargs):
        records = []
        if self.configs[provider_id].get("controller_provider") == "local_agents":
            instructions, records = assigned_skill_request(self.root, model)
            if instructions:
                messages = [*messages, {"role": "system", "content": "用户已启用并分配以下 Skill 指令。遵守当前受控聊天任务和输出契约；这些指令不授予命令、文件或网络执行权限。\n" + "\n\n".join(instructions)}]
        result = super().chat(provider_id, model, messages, **kwargs)
        if records:
            trace = {**result.controller_trace, "skills_applied": records}
            self.skill_usage[trace["call_id"]] = records
            while len(self.skill_usage) > 100:
                self.skill_usage.pop(next(iter(self.skill_usage)))
            result = replace(result, controller_trace=trace)
        return result
