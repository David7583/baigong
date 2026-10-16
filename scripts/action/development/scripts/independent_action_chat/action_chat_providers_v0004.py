# ============================================================
# 文件名: action_chat_providers_v0004.py
# 中文名: 独立 Action Chat 总控接入适配器
# 版本号: v0004
#
# 主层级: action
# 层级: development / independent_action_chat / providers
# 脚本定位: 将聊天与 Router 请求转换为总控应用接口调用
#
# 职责说明:
# - 保留旧 ProviderRegistry 和 ChatResult 的应用契约
# - 将所有真实模型请求交给 Action AI 总控
#
# 本脚本做什么:
# - 继承 v0003 的温度和 K3 完整历史规则，新增调用来源字段
# - 读取本应用身份凭证并复用总控句柄
#
# 本脚本不做什么:
# - 不读取 Provider Key 或连接配置，不直接使用 SDK/HTTP，不开启管理许可
#
# 制度边界声明:
# - Provider 配置由总控读取，模型和 Prompt 由应用明确选择
# - 仅持有应用 Token，不记录 Token；失败不重试、不自动换模型
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_providers_v0004
# family: action_chat_providers
# role: model_provider_gateway
# version: v0004
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_providers_v0004.py
# input:
#   - public controller-backed model catalog and application identity
#   - normalized chat messages
# output:
#   - ChatResult with controller call identity
# depends_on:
#   - action_ai_controller_v0002
#   - action_chat_config_v0002
#   - action_chat_contracts_v0001
# used_by:
#   - action_chat_service_v0005
#   - action_chat_router_v0003
#   - action_chat_retrieval_v0003
# ============================================================

from __future__ import annotations

import json
import math
import os
import threading
import time
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .action_chat_config_v0002 import open_controller, resolve_project_path
from .action_chat_contracts_v0001 import ChatResult as BaseChatResult, ProviderError, ProviderStatus, validate_chat_result


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_providers"
SCRIPT_NAME = "action_chat_providers_v0004"
SCRIPT_VERSION = "v0004"


# ============================================================
# 异常类型
# ============================================================

class ProviderUnavailableError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class ChatResult(BaseChatResult):
    assistant_message: dict | None = None
    retry_wait_ms: int = 0
    controller_trace: dict | None = None


# ============================================================
# 工具函数区
# ============================================================

def validate_temperature(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
        raise ProviderError("temperature 必须是 0 到 2 之间的有限数字")
    return float(value)


def model_options(config: dict, model: str) -> dict:
    item = next((x for x in config["models"] if x["id"] == model), None)
    if item is None:
        raise ProviderUnavailableError("model is not registered with the AI controller")
    return item


def uses_complete_history(config: dict, model: str) -> bool:
    return model_options(config, model).get("request_profile") == "kimi_k3"


def effective_temperature(config: dict, model: str, requested: Any) -> float:
    value = validate_temperature(requested)
    return 1.0 if uses_complete_history(config, model) else value


# ============================================================
# 核心类
# ============================================================

class ProviderView:
    def __init__(self, config: dict):
        self.config = config

    def ensure_model(self, model: str):
        selected = model_options(self.config, model)
        if not selected.get("enabled", True):
            raise ProviderUnavailableError("application model selection is disabled")

    def validate(self):
        models = [{**x, "temperature_policy":{"min":0.0,"max":2.0,"fixed":1.0 if uses_complete_history(self.config,x["id"]) else None}}
                  for x in self.config["models"] if x.get("enabled",True)]
        return ProviderStatus(self.config["provider_id"],True,True,
                              "Model listed by controller; permissions and credentials checked on invocation.",models)


class ProviderRegistry:
    def __init__(self, configs: dict, *, connection: dict, project_root, controller=None):
        self._configs = configs
        self._lock = threading.RLock()
        self._owned = controller is None
        self.control = controller or open_controller(project_root, connection)
        try:
            token = os.environ.get(connection["token_env"], "")
            if not token:
                path = resolve_project_path(project_root, connection["token_file"], must_exist=True)
                record = json.loads(path.read_text(encoding=DEFAULT_ENCODING))
                if record.get("app_id") != connection["app_id"]:
                    raise ProviderUnavailableError("application identity file mismatch")
                token = record.get("token", "")
            self.client = self.control.client(connection["app_id"], token)
            if connection["start_on_launch"]:
                self.client.start()
        except Exception:
            if self._owned:
                self.control.close()
            raise ProviderUnavailableError("AI application access unavailable; administrator provisioning required") from None
        self._traces = ContextVar("ai_call_traces", default=None)

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
            raise ProviderError("running must be boolean")
        return self.client.start() if running else self.client.pause()

    def get(self, provider_id: str):
        if provider_id not in self._configs:
            raise ProviderUnavailableError("provider is not available through the controller")
        return ProviderView(self._configs[provider_id])

    def catalog(self):
        return [{"provider_id":name,"display_name":cfg.get("display_name",name),"enabled":True,
                 "available":True,"message":self.get(name).validate().message,"models":self.get(name).validate().models}
                for name,cfg in self._configs.items()]

    def _invoke(self, provider: str, model: str, profile: str, operation: str, payload: dict):
        sink = self._traces.get()
        with self._lock:
            try:
                self.client.select(provider,model,profile)
                result = self.client.invoke(operation,payload)
            except Exception as exc:
                # Controller exceptions contain safe codes; unknown errors never expose payload.
                code = str(exc) if type(exc).__module__ == type(self.control).__module__ else "controller_invocation_failed"
                raise ProviderError(code) from None
            trace = {key:result[key] for key in ("app_id","call_id","side","lineage","provider","model","operation")}
            if sink is not None:
                sink.append(trace)
            return result, trace

    def embed(self, selection: dict, texts: list[str]):
        result, _ = self._invoke(selection["provider"],selection["model"],selection.get("profile","default"),"embed",{"input":texts})
        vectors = result["data"].get("embeddings")
        if not isinstance(vectors,list) or len(vectors)!=len(texts) or any(len(v)!=selection["dimension"] for v in vectors):
            raise ProviderResponseError("embedding dimension mismatch; do not rebuild the existing index")
        return vectors

    def chat(self, provider_id, model, messages, *, timeout_ms, retry_count, temperature, max_output_tokens=None):
        del timeout_ms, retry_count  # Actual timeouts and retry policy belong to the controller.
        if max_output_tokens is not None and (type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192):
            raise ProviderError("invalid explicit output token budget")
        self.get(provider_id).ensure_model(model)
        config=self._configs[provider_id]
        options=model_options(config,model)
        temperature=effective_temperature(config,model,temperature)
        payload=deepcopy(options.get("request_defaults",{}))
        payload["messages"]=deepcopy(messages)
        central_provider=config["controller_provider"]
        complete=uses_complete_history(config,model)
        if central_provider=="ollama":
            payload.setdefault("options",{})["temperature"]=temperature
        else:
            payload["temperature"]=temperature
            payload["max_completion_tokens" if complete else "max_tokens"]=int(options.get("max_completion_tokens",options.get("max_output_tokens",1024)))
        if complete:
            effort=options.get("reasoning_effort","low")
            limit=options.get("max_completion_tokens",8192)
            if effort not in {"low","high","max"} or type(limit) is not int or not 1 <= limit <= 131072:
                raise ProviderError("invalid K3 reasoning effort or completion budget")
            payload["reasoning_effort"]=effort
            payload["max_completion_tokens"]=limit
        if max_output_tokens is not None:
            if central_provider == "ollama":
                payload.setdefault("options", {})["num_predict"] = max_output_tokens
            else:
                payload["max_completion_tokens" if complete else "max_tokens"] = max_output_tokens
        if config["operation"]=="messages":
            system=[x["content"] for x in payload["messages"] if x["role"]=="system"]
            payload["messages"]=[x for x in payload["messages"] if x["role"]!="system"]
            if system:payload["system"]="\n\n".join(system)
        started=time.perf_counter()
        result,trace=self._invoke(central_provider,model,config.get("profile","default"),config["operation"],payload)
        raw=result["data"]
        if central_provider=="ollama":
            message=raw["message"];text=message.get("content");finish=raw.get("done_reason")
        elif config["operation"]=="messages":
            text="".join(x.get("text","") for x in raw["content"] if x.get("type")=="text")
            message={"role":"assistant","content":text};finish=raw.get("stop_reason")
        else:
            if not raw.get("choices"):raise ProviderResponseError("provider response has no choices")
            message=raw["choices"][0]["message"];text=message.get("content");finish=raw["choices"][0].get("finish_reason")
        if not isinstance(text,str) or not text.strip():raise ProviderResponseError("provider returned no final answer")
        if message.get("tool_calls"):raise ProviderResponseError("chat application does not execute returned tools")
        if complete and (message.get("role")!="assistant" or not isinstance(message.get("reasoning_content"),str)):
            raise ProviderResponseError("K3 response lacks complete assistant history")
        usage=result["usage"]
        return validate_chat_result(ChatResult(status="ok",provider=provider_id,model=model,response_text=text,
            usage={"input_tokens":int(usage.get("input_tokens",usage.get("prompt_tokens",0))),"output_tokens":int(usage.get("output_tokens",usage.get("completion_tokens",0)))},
            duration_ms=round((time.perf_counter()-started)*1000),raw_finish_reason=finish,
            assistant_message=deepcopy(message) if complete else None,controller_trace=trace))


# ============================================================
# CLI / main 接口区
# ============================================================

def main():
    print(json.dumps({"status":"error","error_type":"ApplicationComponent","detail":"Use the chat server."}))
    return 2


if __name__=="__main__":
    raise SystemExit(main())
