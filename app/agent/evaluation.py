"""Runtime tracing and fault injection for AIOps Agent evaluation.

The context is disabled during normal application execution. Evaluation scripts
enable it per test case, so production requests do not collect extra data or
inject failures.
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent


@dataclass
class EvaluationContext:
    case_id: str
    scenario: str = "normal"
    fault_tool: str = ""
    fault_attempts: int = 3
    tool_attempts: list[dict[str, Any]] = field(default_factory=list)
    replan_actions: list[dict[str, Any]] = field(default_factory=list)
    fault_injected: bool = False
    _fault_count: int = 0


_evaluation_context: ContextVar[EvaluationContext | None] = ContextVar(
    "aiops_evaluation_context",
    default=None,
)


def evaluation_enabled() -> bool:
    return _evaluation_context.get() is not None


def start_evaluation(context: EvaluationContext) -> Token:
    return _evaluation_context.set(context)


def stop_evaluation(token: Token) -> None:
    _evaluation_context.reset(token)


def current_evaluation() -> EvaluationContext | None:
    return _evaluation_context.get()


def record_replan_action(action: str, new_steps: list[str] | None = None) -> None:
    context = current_evaluation()
    if context is None:
        return
    context.replan_actions.append(
        {
            "action": action,
            "new_steps": list(new_steps or []),
            "timestamp": time.time(),
        }
    )


def _empty_result(tool_name: str) -> CallToolResult:
    payload = {
        "tool": tool_name,
        "total": 0,
        "items": [],
        "message": "evaluation fault injection: empty result",
    }
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        isError=False,
    )


async def evaluation_interceptor(request: MCPToolCallRequest, handler):
    """Trace each physical MCP attempt and optionally inject one fault."""

    context = current_evaluation()
    if context is None:
        return await handler(request)

    started = time.perf_counter()
    request_id = id(request)
    should_inject = bool(context.fault_tool and request.name == context.fault_tool)

    if should_inject and context.scenario == "timeout":
        if context._fault_count < context.fault_attempts:
            context._fault_count += 1
            context.fault_injected = True
            context.tool_attempts.append(
                {
                    "request_id": request_id,
                    "tool_name": request.name,
                    "server_name": request.server_name,
                    "arguments": dict(request.args),
                    "transport_success": False,
                    "usable_result": False,
                    "injected_fault": "timeout",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "timestamp": time.time(),
                }
            )
            raise TimeoutError(f"evaluation injected timeout for {request.name}")

    if should_inject and context.scenario == "empty" and not context.fault_injected:
        context.fault_injected = True
        result = _empty_result(request.name)
        context.tool_attempts.append(
            {
                "request_id": request_id,
                "tool_name": request.name,
                "server_name": request.server_name,
                "arguments": dict(request.args),
                "transport_success": True,
                "usable_result": False,
                "injected_fault": "empty",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "timestamp": time.time(),
            }
        )
        return result

    try:
        result = await handler(request)
        is_error = bool(getattr(result, "isError", False))
        context.tool_attempts.append(
            {
                "request_id": request_id,
                "tool_name": request.name,
                "server_name": request.server_name,
                "arguments": dict(request.args),
                "transport_success": not is_error,
                "usable_result": not is_error,
                "injected_fault": "",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "timestamp": time.time(),
            }
        )
        return result
    except Exception as exc:
        context.tool_attempts.append(
            {
                "request_id": request_id,
                "tool_name": request.name,
                "server_name": request.server_name,
                "arguments": dict(request.args),
                "transport_success": False,
                "usable_result": False,
                "injected_fault": "",
                "error_type": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "timestamp": time.time(),
            }
        )
        raise
