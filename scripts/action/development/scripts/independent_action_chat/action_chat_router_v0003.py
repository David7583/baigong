# ============================================================
# 文件名: action_chat_router_v0003.py
# 中文名: 新版独立 Action Chat 路由脚本
# 版本号: v0003
#
# 主层级: action
# 层级: development / independent_action_chat / router
# 脚本定位: 消息进入正式回答和可选检索前的结构化决策组件
#
# 职责说明:
# - 提供确定性 Machine Router 和可替换 AI Router Adapter
# - 实现 off、machine_only、hybrid、ai_only 四种运行模式
#
# 本脚本做什么:
# - 根据当前消息和有限会话窗口生成结构化决策
# - 在机器不确定时升级 AI，并在 AI 失败时安全回退留痕
#
# 本脚本不做什么:
# - 不执行检索、工具、Action 或正式回答，不保存会话
#
# 制度边界声明:
# - hybrid 明确规则命中时不得调用 AI Router
# - AI 输出必须校验；失败不得阻断普通聊天
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: action_chat_router_v0003
# family: action_chat_router
# role: structured_router
# version: v0003
# status: active
# entry_point: scripts/action/development/scripts/independent_action_chat/action_chat_router_v0003.py
# input:
#   - current message and bounded session context
#   - runtime router mode/provider/model
# output:
#   - RouterDecision and trace
# depends_on:
#   - Python stdlib: json, re, dataclasses, typing
#   - action_chat_contracts_v0001.py
#   - action_chat_providers_v0004.py
# used_by:
#   - action_chat_service_v0005.py
# ============================================================

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .action_chat_contracts_v0001 import (
    ContractError,
    ROUTER_MODES,
    RouterDecision,
    RouterError,
    validate_router_decision,
)
from .action_chat_providers_v0004 import ProviderRegistry


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "action_chat_router"
SCRIPT_NAME = "action_chat_router_v0003"
SCRIPT_VERSION = "v0003"


# ============================================================
# 异常类型
# ============================================================

class AiRouterOutputError(RouterError):
    """Raised when AI Router output is not a valid decision object."""


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class RouterTrace:
    mode: str
    machine_decision: Dict[str, Any]
    ai_called: bool
    ai_decision: Optional[Dict[str, Any]]
    escalation_reason: Optional[str]
    fallback_reason: Optional[str]
    final_decision: Dict[str, Any]


# ============================================================
# 工具函数区
# ============================================================

def _extract_json_object(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        raw = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AiRouterOutputError("AI Router returned invalid JSON") from exc
    if not isinstance(raw, dict):
        raise AiRouterOutputError("AI Router output must be an object")
    return raw


def _safe_default(reason: str, machine: RouterDecision) -> RouterDecision:
    return RouterDecision(
        matched=machine.matched,
        confidence=machine.confidence,
        ambiguity=machine.ambiguity,
        escalate_to_ai=False,
        intent=machine.intent if machine.matched else "normal_chat",
        reason="AI Router 不可用，使用机器 Router 安全默认决策",
        need_retrieval=machine.need_retrieval if machine.matched else False,
        optional_backends=machine.optional_backends if machine.matched else [],
        source="machine_fallback",
        fallback_reason=reason,
    )


# ============================================================
# 默认映射
# ============================================================

RETRIEVAL_PATTERNS = (
    re.compile(r"(以前|历史|原文|出处|来源|数据库|检索|查找|回忆|曾经).{0,16}(说过|讨论|记录|内容|资料|论述|哪里|什么)?", re.I),
    re.compile(r"(统计|趋势|频率|关系|关联|路径|节点|聚合)", re.I),
)
DIRECT_CHAT_PATTERNS = (
    re.compile(r"^(你好|您好|谢谢|再见|hello|hi)[！!。.\s]*$", re.I),
    re.compile(r"^(解释|改写|翻译|总结)[:：]", re.I),
)
ACTION_PATTERNS = (
    re.compile(r"(执行|删除|运行命令|修改文件|调用工具|创建任务)", re.I),
)

AI_ROUTER_SYSTEM_PROMPT = """你是只做结构化分类的 Router。不得回答用户问题，不得执行工具。
只输出一个 JSON 对象，字段必须为 matched, confidence, ambiguity, escalate_to_ai, intent, reason,
need_retrieval, optional_backends。intent 可为 normal_chat, retrieval_request, action_request。
optional_backends 只能从 sqlite_supplement, duckdb, neo4j 中选择。"""


# ============================================================
# 核心类
# ============================================================

class MachineRouter:
    def __init__(self, confidence_threshold: float):
        self.confidence_threshold = float(confidence_threshold)

    def decide(self, message: str, context: List[Dict[str, str]]) -> RouterDecision:
        text = message.strip()
        if not text:
            return RouterDecision(False, 0.0, True, True, "normal_chat", "空消息无法路由")
        if any(pattern.search(text) for pattern in ACTION_PATTERNS):
            return RouterDecision(True, 0.96, False, False, "action_request", "机器规则命中执行意图")
        if any(pattern.search(text) for pattern in RETRIEVAL_PATTERNS):
            optional: List[str] = []
            if re.search(r"统计|趋势|频率|聚合", text):
                optional.append("duckdb")
            if re.search(r"关系|关联|路径|节点", text):
                optional.append("neo4j")
            if re.search(r"精确|字段|编号|ID|原文", text, re.I):
                optional.append("sqlite_supplement")
            return RouterDecision(
                True,
                0.94,
                False,
                False,
                "retrieval_request",
                "机器规则命中检索意图",
                need_retrieval=True,
                optional_backends=optional,
            )
        if any(pattern.search(text) for pattern in DIRECT_CHAT_PATTERNS):
            return RouterDecision(True, 0.98, False, False, "normal_chat", "机器规则命中普通聊天")
        ambiguity = bool(re.search(r"(这个|那个|它|继续|刚才)", text)) and not context
        confidence = 0.35 if ambiguity else 0.62
        return RouterDecision(
            matched=False,
            confidence=confidence,
            ambiguity=ambiguity,
            escalate_to_ai=confidence < self.confidence_threshold or ambiguity,
            intent="normal_chat",
            reason="没有高置信规则命中",
        )


class AiRouterAdapter:
    def __init__(self, providers: ProviderRegistry, *, timeout_ms: int, retry_count: int):
        self.providers = providers
        self.timeout_ms = timeout_ms
        self.retry_count = retry_count

    def decide(
        self,
        *,
        provider_id: str,
        model: str,
        message: str,
        context: List[Dict[str, str]],
        answer_provider: str,
        answer_model: str,
        router_mode: str,
        session_id: str,
        previous_decision: Optional[Dict[str, Any]],
        temperature: float = 0.0,
    ) -> RouterDecision:
        payload = {
            "session_id": session_id,
            "message": message,
            "recent_context": context,
            "answer_selection": {"provider": answer_provider, "model": answer_model},
            "router_mode": router_mode,
            "previous_decision": previous_decision,
        }
        result = self.providers.chat(
            provider_id,
            model,
            [
                {"role": "system", "content": AI_ROUTER_SYSTEM_PROMPT + "\n用户配置的路由偏好（保持 JSON 契约）：\n" + getattr(self,"custom_prompt","")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            timeout_ms=self.timeout_ms,
            retry_count=self.retry_count,
            temperature=temperature,
            max_output_tokens=getattr(self,"max_output_tokens",2048),
        )
        return validate_router_decision(_extract_json_object(result.response_text), source="ai")


class RouterEngine:
    def __init__(self, machine: MachineRouter, ai: AiRouterAdapter):
        self.machine = machine
        self.ai = ai

    def route(
        self,
        *,
        mode: str,
        provider_id: str,
        model: str,
        message: str,
        context: List[Dict[str, str]],
        answer_provider: str,
        answer_model: str,
        session_id: str,
        previous_decision: Optional[Dict[str, Any]],
        temperature: float = 0.0,
    ) -> RouterTrace:
        if mode not in ROUTER_MODES:
            raise RouterError(f"invalid router mode: {mode}")
        machine = self.machine.decide(message, context)
        machine_map = asdict(machine)
        if mode == "off":
            final = RouterDecision(True, 1.0, False, False, "normal_chat", "Router 已关闭", source="off")
            return RouterTrace(mode, machine_map, False, None, None, None, asdict(final))
        if mode == "machine_only":
            final = machine if machine.matched else _safe_default("machine_only 使用安全默认", machine)
            return RouterTrace(mode, machine_map, False, None, None, final.fallback_reason, asdict(final))

        should_call_ai = mode == "ai_only" or machine.escalate_to_ai or not machine.matched or machine.ambiguity or machine.confidence < self.machine.confidence_threshold
        if mode == "hybrid" and not should_call_ai:
            return RouterTrace(mode, machine_map, False, None, None, None, machine_map)

        escalation_reason = "ai_only" if mode == "ai_only" else machine.reason
        try:
            ai_decision = self.ai.decide(
                provider_id=provider_id,
                model=model,
                message=message,
                context=context,
                answer_provider=answer_provider,
                answer_model=answer_model,
                router_mode=mode,
                session_id=session_id,
                previous_decision=previous_decision,
                temperature=temperature,
            )
            return RouterTrace(mode, machine_map, True, asdict(ai_decision), escalation_reason, None, asdict(ai_decision))
        except (RouterError, ContractError, Exception) as exc:
            fallback = _safe_default(f"{type(exc).__name__}: {exc}", machine)
            return RouterTrace(mode, machine_map, True, None, escalation_reason, fallback.fallback_reason, asdict(fallback))
