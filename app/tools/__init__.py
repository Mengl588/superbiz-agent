"""工具模块 - 供 Agent 调用的各种工具"""

import os

from langchain_core.tools import tool

from app.tools.time_tool import get_current_time


if os.getenv("AIOPS_AGENT_EVAL_DISABLE_RAG") == "1":
    @tool
    def retrieve_knowledge(query: str) -> str:
        """Return fixed context while the isolated Agent benchmark is running."""

        return "Agent 评测模式：RAG 检索由独立基准测试覆盖。"
else:
    from app.tools.knowledge_tool import retrieve_knowledge

__all__ = [
    "retrieve_knowledge",
    "get_current_time",
]
