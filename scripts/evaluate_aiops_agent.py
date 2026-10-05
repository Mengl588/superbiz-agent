"""Evaluate the AIOps Agent with normal, timeout, and empty-result cases.

The evaluator uses the real Planner/Executor/Replanner and configured LLM. RAG
retrieval is replaced with a deterministic no-op because it already has a
separate retrieval benchmark and should not make Agent metrics depend on Milvus.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


# The repository's current .env contains a legacy CLS transport value. These
# local evaluation defaults match the bundled FastMCP servers and can still be
# overridden explicitly by shell environment variables.
os.environ.setdefault("MCP_CLS_TRANSPORT", "streamable-http")
os.environ.setdefault("MCP_CLS_URL", "http://127.0.0.1:8003/mcp")
os.environ.setdefault("MCP_MONITOR_TRANSPORT", "streamable-http")
os.environ.setdefault("MCP_MONITOR_URL", "http://127.0.0.1:8004/mcp")
os.environ.setdefault("AIOPS_AGENT_EVAL_DISABLE_RAG", "1")

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_qwq import ChatQwen
from loguru import logger

from app.agent.evaluation import (
    EvaluationContext,
    start_evaluation,
    stop_evaluation,
)
from app.config import config


@dataclass
class EvalCase:
    case_id: str
    scenario: str
    question: str
    expected_tool_groups: list[list[str]]
    expected_keywords: list[str]
    fault_tool: str = ""
    fault_attempts: int = 3


@dataclass
class EvalResult:
    case_id: str
    run: int
    scenario: str
    task_success: bool
    tool_groups_passed: bool
    keywords_passed: bool
    tool_calls: int
    successful_tool_calls: int
    tool_call_success_rate: float
    fault_injected: bool
    replan_count: int
    replan_recovered: bool
    latency_seconds: float
    called_tools: list[str]
    missing_tool_groups: list[list[str]]
    missing_keywords: list[str]
    final_response: str
    error: str = ""


@tool
async def evaluation_knowledge_stub(query: str) -> str:
    """Return no RAG context during the isolated Agent workflow benchmark."""

    return "Agent 评测模式：RAG 检索由独立基准测试覆盖，本用例不提供额外知识库上下文。"


def patch_rag_for_isolated_agent_eval() -> None:
    """Avoid coupling Agent metrics to a locally running Milvus instance."""

    import importlib

    planner_module = importlib.import_module("app.agent.aiops.planner")
    executor_module = importlib.import_module("app.agent.aiops.executor")
    replanner_module = importlib.import_module("app.agent.aiops.replanner")
    planner_module.retrieve_knowledge = evaluation_knowledge_stub
    executor_module.retrieve_knowledge = evaluation_knowledge_stub
    replanner_module.retrieve_knowledge = evaluation_knowledge_stub


def load_cases(path: Path) -> list[EvalCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for item in raw_cases:
        cases.append(
            EvalCase(
                case_id=str(item["id"]),
                scenario=str(item.get("scenario", "normal")),
                question=str(item["question"]),
                expected_tool_groups=[list(group) for group in item["expected_tool_groups"]],
                expected_keywords=list(item["expected_keywords"]),
                fault_tool=str(item.get("fault_tool", "")),
                fault_attempts=int(item.get("fault_attempts", 3)),
            )
        )
    return cases


def logical_tool_calls(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse physical retry attempts into logical calls by request object."""

    grouped: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for attempt in attempts:
        request_id = int(attempt["request_id"])
        if request_id not in grouped:
            grouped[request_id] = {
                "tool_name": attempt["tool_name"],
                "transport_success": False,
                "usable_result": False,
                "injected_fault": "",
                "first_timestamp": attempt["timestamp"],
                "last_timestamp": attempt["timestamp"],
                "attempts": 0,
            }
            order.append(request_id)
        call = grouped[request_id]
        call["attempts"] += 1
        call["last_timestamp"] = attempt["timestamp"]
        call["transport_success"] = call["transport_success"] or bool(
            attempt["transport_success"]
        )
        call["usable_result"] = call["usable_result"] or bool(attempt["usable_result"])
        if attempt.get("injected_fault"):
            call["injected_fault"] = attempt["injected_fault"]
    return [grouped[request_id] for request_id in order]


def evaluate_output(
    case: EvalCase,
    context: EvaluationContext,
    final_response: str,
    run_error: str,
) -> tuple[EvalResult, list[dict[str, Any]]]:
    calls = logical_tool_calls(context.tool_attempts)
    usable_tools = {
        call["tool_name"] for call in calls if call["usable_result"]
    }
    called_tools = [call["tool_name"] for call in calls]

    missing_tool_groups = [
        group for group in case.expected_tool_groups if not usable_tools.intersection(group)
    ]
    missing_keywords = [
        keyword for keyword in case.expected_keywords if keyword.lower() not in final_response.lower()
    ]
    tool_groups_passed = not missing_tool_groups
    keywords_passed = bool(final_response.strip()) and not missing_keywords
    task_success = tool_groups_passed and keywords_passed and not run_error

    successful_calls = sum(1 for call in calls if call["transport_success"])
    success_rate = successful_calls / len(calls) if calls else 0.0
    injected_timestamps = [
        call["first_timestamp"] for call in calls if call["injected_fault"]
    ]
    first_fault_time = min(injected_timestamps) if injected_timestamps else float("inf")
    replans_after_fault = [
        action
        for action in context.replan_actions
        if action["action"] == "replan" and action["timestamp"] >= first_fault_time
    ]
    successful_calls_after_fault = [
        call
        for call in calls
        if call["usable_result"] and call["last_timestamp"] >= first_fault_time
    ]
    replan_recovered = bool(
        context.fault_injected
        and replans_after_fault
        and successful_calls_after_fault
        and task_success
    )

    result = EvalResult(
        case_id=case.case_id,
        run=0,
        scenario=case.scenario,
        task_success=task_success,
        tool_groups_passed=tool_groups_passed,
        keywords_passed=keywords_passed,
        tool_calls=len(calls),
        successful_tool_calls=successful_calls,
        tool_call_success_rate=success_rate,
        fault_injected=context.fault_injected,
        replan_count=sum(1 for item in context.replan_actions if item["action"] == "replan"),
        replan_recovered=replan_recovered,
        latency_seconds=0.0,
        called_tools=called_tools,
        missing_tool_groups=missing_tool_groups,
        missing_keywords=missing_keywords,
        final_response=final_response,
        error=run_error,
    )
    return result, calls


async def run_case(case: EvalCase, run_number: int) -> tuple[EvalResult, dict[str, Any]]:
    from app.services.aiops_service import AIOpsService

    context = EvaluationContext(
        case_id=case.case_id,
        scenario=case.scenario,
        fault_tool=case.fault_tool,
        fault_attempts=case.fault_attempts,
    )
    token = start_evaluation(context)
    started = time.perf_counter()
    final_response = ""
    run_error = ""
    events: list[dict[str, Any]] = []

    try:
        service = AIOpsService()
        session_id = f"eval-{case.case_id}-{run_number}-{uuid.uuid4().hex[:8]}"
        async for event in service.execute(case.question, session_id=session_id):
            events.append(event)
            if event.get("type") == "complete":
                final_response = str(event.get("response", ""))
            elif event.get("type") == "error":
                run_error = str(event.get("message", "Agent execution error"))
    except Exception as exc:
        run_error = f"{type(exc).__name__}: {exc}"
    finally:
        stop_evaluation(token)

    result, calls = evaluate_output(case, context, final_response, run_error)
    result.run = run_number
    result.latency_seconds = round(time.perf_counter() - started, 3)
    detail = {
        "case": asdict(case),
        "result": asdict(result),
        "logical_tool_calls": calls,
        "physical_tool_attempts": context.tool_attempts,
        "replan_actions": context.replan_actions,
        "events": events,
    }
    return result, detail


async def verify_model_access() -> None:
    llm = ChatQwen(
        model=config.rag_model,
        api_key=config.dashscope_api_key,
        temperature=0,
    )
    response = await llm.ainvoke([HumanMessage(content="仅回复 OK")])
    if not getattr(response, "content", ""):
        raise RuntimeError("LLM preflight returned an empty response")


def write_csv(path: Path, results: list[EvalResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "case_id",
        "run",
        "scenario",
        "task_success",
        "tool_groups_passed",
        "keywords_passed",
        "tool_calls",
        "successful_tool_calls",
        "tool_call_success_rate",
        "fault_injected",
        "replan_count",
        "replan_recovered",
        "latency_seconds",
        "called_tools",
        "missing_tool_groups",
        "missing_keywords",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row = asdict(result)
            row.pop("final_response")
            row["called_tools"] = " | ".join(result.called_tools)
            row["missing_tool_groups"] = json.dumps(
                result.missing_tool_groups, ensure_ascii=False
            )
            row["missing_keywords"] = " | ".join(result.missing_keywords)
            writer.writerow(row)


def build_summary(results: list[EvalResult]) -> dict[str, Any]:
    total = len(results)
    completed = sum(item.task_success for item in results)
    normal = [item for item in results if item.scenario == "normal"]
    fault = [item for item in results if item.scenario != "normal"]

    normal_calls = sum(item.tool_calls for item in normal)
    normal_successful_calls = sum(item.successful_tool_calls for item in normal)
    all_calls = sum(item.tool_calls for item in results)
    all_successful_calls = sum(item.successful_tool_calls for item in results)
    recovered = sum(item.replan_recovered for item in fault)

    return {
        "total_runs": total,
        "task_success_count": completed,
        "end_to_end_task_completion_rate": completed / total if total else 0.0,
        "normal_tool_calls": normal_calls,
        "normal_successful_tool_calls": normal_successful_calls,
        "normal_tool_call_success_rate": (
            normal_successful_calls / normal_calls if normal_calls else 0.0
        ),
        "overall_tool_call_success_rate": (
            all_successful_calls / all_calls if all_calls else 0.0
        ),
        "fault_case_count": len(fault),
        "fault_injected_count": sum(item.fault_injected for item in fault),
        "replan_recovery_count": recovered,
        "replan_recovery_rate": recovered / len(fault) if fault else 0.0,
        "average_latency_seconds": (
            sum(item.latency_seconds for item in results) / total if total else 0.0
        ),
        "metric_definitions": {
            "end_to_end_task_completion_rate": (
                "required tool groups passed AND final response contains all expected keywords"
            ),
            "normal_tool_call_success_rate": (
                "successful logical MCP calls / logical MCP calls in normal cases; retries deduplicated"
            ),
            "replan_recovery_rate": (
                "fault cases with injected fault, explicit replan after fault, a later usable tool result, "
                "and successful final task / all fault cases"
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the AIOps Agent workflow.")
    parser.add_argument("--dataset", default="eval/aiops_agent_test_set.json")
    parser.add_argument("--output", default="eval/aiops_agent_eval_results.csv")
    parser.add_argument("--details", default="eval/aiops_agent_eval_details.json")
    parser.add_argument("--summary", default="eval/aiops_agent_summary.json")
    parser.add_argument(
        "--scenario",
        choices=["all", "normal", "timeout", "empty"],
        default="all",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the dataset and exit without calling MCP servers or the LLM.",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    cases = load_cases(Path(args.dataset))
    duplicate_ids = sorted(
        {case.case_id for case in cases if sum(item.case_id == case.case_id for item in cases) > 1}
    )
    invalid_fault_cases = [
        case.case_id
        for case in cases
        if case.scenario != "normal" and not case.fault_tool
    ]
    if duplicate_ids or invalid_fault_cases:
        raise ValueError(
            f"Invalid dataset: duplicate_ids={duplicate_ids}, "
            f"fault_cases_without_tool={invalid_fault_cases}"
        )
    if args.validate_only:
        scenario_counts = {
            scenario: sum(case.scenario == scenario for case in cases)
            for scenario in ("normal", "timeout", "empty")
        }
        print(
            json.dumps(
                {"dataset_valid": True, "total_cases": len(cases), **scenario_counts},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    patch_rag_for_isolated_agent_eval()

    if not args.skip_preflight:
        try:
            await verify_model_access()
        except Exception as exc:
            print(f"LLM preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            print(
                "Update DASHSCOPE_API_KEY (and API base if needed) before running the real benchmark.",
                file=sys.stderr,
            )
            return 2

    if args.scenario != "all":
        cases = [case for case in cases if case.scenario == args.scenario]
    if args.limit > 0:
        cases = cases[: args.limit]

    results: list[EvalResult] = []
    details: list[dict[str, Any]] = []
    for run_number in range(1, args.repeats + 1):
        for index, case in enumerate(cases, start=1):
            print(
                f"[{run_number}/{args.repeats}] [{index}/{len(cases)}] "
                f"{case.case_id} ({case.scenario})"
            )
            result, detail = await run_case(case, run_number)
            results.append(result)
            details.append(detail)
            print(
                f"  task_success={result.task_success} "
                f"tool_calls={result.successful_tool_calls}/{result.tool_calls} "
                f"replan_recovered={result.replan_recovered}"
            )

    output_path = Path(args.output)
    details_path = Path(args.details)
    summary_path = Path(args.summary)
    write_csv(output_path, results)
    details_path.write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = build_summary(results)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Results: {output_path}")
    print(f"Details: {details_path}")
    print(f"Summary: {summary_path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args())))


if __name__ == "__main__":
    main()
