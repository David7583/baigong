# ============================================================
# 文件名: action_chat_providers_v0001.py
# 中文名: 新版独立 Action Chat Provider 适配脚本
# 版本号: v0001
#
# 主层级: action
# 层级: development / independent_action_chat / providers
# 脚本定位: Chat Orchestrator 与模型厂商协议之间的唯一调用边界
#
# 职责说明:
# - 提供统一 Provider 接口、注册表和模型目录
# - 实现 OpenAI-compatible、Anthropic 与离线测试 Provider
#
# 本脚本做什么:
# - 校验 Provider/Model/密钥可用性并执行单次聊天请求
# - 统一返回文本、usage、耗时、重试和错误结构
#
# 本脚本不做什么:
# - 不决定是否路由或检索，不自动切换 Provider，不保存会话
#
# 制度边界声明:
# - 密钥只从配置声明的环境变量读取且不进入返回、日志或异常
# - 失败保持所选 Provider，不进行未经授权的跨 Provider 降级
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_providers_v0001
# family: action_chat_providers
# role: model_provider_gateway
# version: v0001
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_providers_v0001.py
# input:
#   - validated provider catalog
#   - normalized chat messages
# output:
#   - ChatResult
# depends_on:
#   - Python stdlib: abc, json, os, time, urllib
#   - action_chat_contracts_v0001.py
# used_by:
#   - action_chat_router_v0001.py
#   - action_chat_service_v0001.py
# ============================================================

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Type

from .action_chat_contracts_v0001 import (
    ChatResult,
    DEFAULT_USAGE,
    ProviderError,
    ProviderStatus,
    classified_error,
    validate_chat_result,
)


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_providers"
SCRIPT_NAME = "action_chat_providers_v0001"
SCRIPT_VERSION = "v0001"


# ============================================================
# 异常类型
# ============================================================

class ProviderUnavailableError(ProviderError):
    """Raised when a selected provider lacks config, model, dependency, or key."""


class ProviderResponseError(ProviderError):
    """Raised when a provider returns an invalid response."""


# ============================================================
# 数据结构
# ============================================================

# Public data structures are defined in action_chat_contracts_v0001.py.


# ============================================================
# 工具函数区
# ============================================================

def _enabled_models(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [dict(item) for item in config.get("models", []) if item.get("enabled", True)]


def _api_key(config: Dict[str, Any]) -> str:
    env_name = str(config.get("api_key_env", "")).strip()
    if not env_name:
        return ""
    return os.environ.get(env_name, "").strip()


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout_seconds: float) -> Dict[str, Any]:
    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode(DEFAULT_ENCODING),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode(DEFAULT_ENCODING)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode(DEFAULT_ENCODING, errors="replace")
        raise ProviderResponseError(f"provider HTTP {exc.code}: {detail[:500]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderUnavailableError(f"provider connection failed: {type(exc).__name__}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError("provider returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProviderResponseError("provider response must be a JSON object")
    return value


def _usage(raw: Dict[str, Any]) -> Dict[str, int]:
    return {
        "input_tokens": int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0),
        "output_tokens": int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0),
    }


# ============================================================
# 默认映射
# ============================================================

DEFAULT_TIMEOUT_MS = 60000
DEFAULT_RETRY_COUNT = 0


# ============================================================
# 核心类
# ============================================================

class ProviderAdapter(ABC):
    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config)
        self.provider_id = str(config.get("provider_id", ""))

    def list_models(self) -> List[Dict[str, Any]]:
        return _enabled_models(self.config)

    def validate(self) -> ProviderStatus:
        configured = bool(self.provider_id and self.list_models())
        key_required = bool(str(self.config.get("api_key_env", "")).strip())
        available = bool(configured and self.config.get("enabled", False) and (not key_required or _api_key(self.config)))
        if not self.config.get("enabled", False):
            message = "disabled"
        elif key_required and not _api_key(self.config):
            message = f"missing environment variable: {self.config.get('api_key_env')}"
        else:
            message = "available"
        return ProviderStatus(self.provider_id, configured, available, message, self.list_models())

    def ensure_model(self, model: str) -> None:
        if model not in {item["id"] for item in self.list_models()}:
            raise ProviderUnavailableError(f"model is not enabled: {self.provider_id}/{model}")
        status = self.validate()
        if not status.available:
            raise ProviderUnavailableError(status.message)

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        timeout_ms: int,
        retry_count: int,
        temperature: float,
    ) -> ChatResult:
        raise NotImplementedError


class MockProviderAdapter(ProviderAdapter):
    def validate(self) -> ProviderStatus:
        configured = bool(self.provider_id and self.list_models())
        available = bool(configured and self.config.get("enabled", False))
        return ProviderStatus(self.provider_id, configured, available, "available" if available else "disabled", self.list_models())

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        timeout_ms: int,
        retry_count: int,
        temperature: float,
    ) -> ChatResult:
        del timeout_ms, retry_count, temperature
        self.ensure_model(model)
        started = time.perf_counter()
        last = next((item.get("content", "") for item in reversed(messages) if item.get("role") == "user"), "")
        if "[[MOCK_ERROR]]" in last:
            raise ProviderResponseError("mock provider failure requested")
        is_router_request = any(
            item.get("role") == "system" and "Router" in item.get("content", "")
            for item in messages
        )
        if is_router_request:
            try:
                router_input = json.loads(last)
                router_message = str(router_input.get("message", "")) if isinstance(router_input, dict) else ""
            except json.JSONDecodeError:
                router_message = last
            need_retrieval = any(token in router_message for token in ("以前", "原文", "数据库", "检索", "统计", "关系"))
            response_text = json.dumps({
                "matched": True,
                "confidence": 0.88,
                "ambiguity": False,
                "escalate_to_ai": False,
                "intent": "retrieval_request" if need_retrieval else "normal_chat",
                "reason": "离线 Mock AI Router 决策",
                "need_retrieval": need_retrieval,
                "optional_backends": [],
            }, ensure_ascii=False)
        else:
            response_text = str(self.config.get("mock_prefix", "离线测试回答：")) + last
        result = ChatResult(
            status="ok",
            provider=self.provider_id,
            model=model,
            response_text=response_text,
            usage={"input_tokens": max(1, len(last) // 4), "output_tokens": max(1, len(response_text) // 4)},
            duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )
        return validate_chat_result(result)


class OpenAICompatibleProviderAdapter(ProviderAdapter):
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        timeout_ms: int,
        retry_count: int,
        temperature: float,
    ) -> ChatResult:
        self.ensure_model(model)
        base = str(self.config.get("api_base", "")).rstrip("/")
        if not base:
            raise ProviderUnavailableError("api_base is missing")
        endpoint = str(self.config.get("chat_path", "/chat/completions"))
        url = base + (endpoint if endpoint.startswith("/") else "/" + endpoint)
        headers = {}
        key = _api_key(self.config)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {"model": model, "messages": messages, "temperature": temperature, "stream": False}
        started = time.perf_counter()
        last_error: BaseException | None = None
        attempts = max(0, int(retry_count)) + 1
        for attempt in range(attempts):
            try:
                raw = _post_json(url, payload, headers, max(0.1, timeout_ms / 1000.0))
                choices = raw.get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    raise ProviderResponseError("provider response has no choices")
                message = choices[0].get("message", {})
                text = message.get("content") if isinstance(message, dict) else None
                if not isinstance(text, str):
                    raise ProviderResponseError("provider response has no message content")
                return validate_chat_result(ChatResult(
                    status="ok",
                    provider=self.provider_id,
                    model=model,
                    response_text=text,
                    usage=_usage(raw.get("usage", {}) if isinstance(raw.get("usage"), dict) else {}),
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    retry_count=attempt,
                    raw_finish_reason=choices[0].get("finish_reason"),
                ))
            except ProviderError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


class AnthropicProviderAdapter(ProviderAdapter):
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        timeout_ms: int,
        retry_count: int,
        temperature: float,
    ) -> ChatResult:
        self.ensure_model(model)
        base = str(self.config.get("api_base", "")).rstrip("/")
        if not base:
            raise ProviderUnavailableError("api_base is missing")
        system_parts = [item["content"] for item in messages if item.get("role") == "system"]
        conversation = [item for item in messages if item.get("role") in {"user", "assistant"}]
        payload: Dict[str, Any] = {
            "model": model,
            "messages": conversation,
            "temperature": temperature,
            "max_tokens": int(self.config.get("max_output_tokens", 2048)),
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        headers = {
            "x-api-key": _api_key(self.config),
            "anthropic-version": str(self.config.get("api_version", "2023-06-01")),
        }
        started = time.perf_counter()
        last_error: BaseException | None = None
        attempts = max(0, int(retry_count)) + 1
        for attempt in range(attempts):
            try:
                raw = _post_json(base + "/messages", payload, headers, max(0.1, timeout_ms / 1000.0))
                content = raw.get("content")
                if not isinstance(content, list):
                    raise ProviderResponseError("Anthropic response has no content list")
                text = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") == "text")
                if not text:
                    raise ProviderResponseError("Anthropic response has no text content")
                return validate_chat_result(ChatResult(
                    status="ok",
                    provider=self.provider_id,
                    model=model,
                    response_text=text,
                    usage=_usage(raw.get("usage", {}) if isinstance(raw.get("usage"), dict) else {}),
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    retry_count=attempt,
                    raw_finish_reason=raw.get("stop_reason"),
                ))
            except ProviderError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


class ProviderRegistry:
    def __init__(self, provider_configs: Dict[str, Dict[str, Any]]):
        self._configs = {key: dict(value) for key, value in provider_configs.items()}
        self._adapter_types: Dict[str, Type[ProviderAdapter]] = dict(DEFAULT_ADAPTER_TYPES)
        self._instances: Dict[str, ProviderAdapter] = {}

    def register_adapter_type(self, adapter_name: str, adapter_type: Type[ProviderAdapter]) -> None:
        if not adapter_name or not issubclass(adapter_type, ProviderAdapter):
            raise ProviderError("invalid provider adapter registration")
        self._adapter_types[adapter_name] = adapter_type

    def get(self, provider_id: str) -> ProviderAdapter:
        if provider_id not in self._configs:
            raise ProviderUnavailableError(f"provider is not registered: {provider_id}")
        if provider_id not in self._instances:
            config = self._configs[provider_id]
            adapter_name = str(config.get("adapter", ""))
            adapter_type = self._adapter_types.get(adapter_name)
            if adapter_type is None:
                raise ProviderUnavailableError(f"provider adapter is not registered: {adapter_name}")
            self._instances[provider_id] = adapter_type(config)
        return self._instances[provider_id]

    def catalog(self) -> List[Dict[str, Any]]:
        catalog: List[Dict[str, Any]] = []
        for provider_id in self._configs:
            status = self.get(provider_id).validate()
            catalog.append({
                "provider_id": provider_id,
                "display_name": self._configs[provider_id].get("display_name", provider_id),
                "enabled": bool(self._configs[provider_id].get("enabled", False)),
                "available": status.available,
                "message": status.message,
                "models": status.models,
            })
        return catalog

    def chat(
        self,
        provider_id: str,
        model: str,
        messages: List[Dict[str, str]],
        *,
        timeout_ms: int,
        retry_count: int,
        temperature: float,
    ) -> ChatResult:
        return self.get(provider_id).chat(
            messages,
            model,
            timeout_ms=timeout_ms,
            retry_count=retry_count,
            temperature=temperature,
        )


# ============================================================
# Schema / 契约辅助函数
# ============================================================

DEFAULT_ADAPTER_TYPES: Dict[str, Type[ProviderAdapter]] = {
    "mock": MockProviderAdapter,
    "openai_compatible": OpenAICompatibleProviderAdapter,
    "anthropic": AnthropicProviderAdapter,
}
