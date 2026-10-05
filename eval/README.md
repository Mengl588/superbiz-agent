# RAG 检索测试集

`retrieval_test_set.csv` 用来评估知识库检索准确率，不直接评估大模型回答质量。

每一行包含：

- `question`: 用户提问
- `expected_file`: 这条问题应该命中的标准知识库文档
- `expected_alert`: 对应告警类型
- `expected_keywords`: 方便人工复核的关键词

推荐评估方式：

1. 对每个 `question` 执行一次知识库检索。
2. 取返回的 TopK 文档，例如当前项目默认 `RAG_TOP_K=3`。
3. 如果 TopK 结果里包含 `expected_file`，这条算命中。
4. 准确率计算公式：

```text
Hit@K = 命中数量 / 测试集总数量
```

例如 50 条测试问题里有 43 条在 Top3 命中标准文档：

```text
Hit@3 = 43 / 50 = 0.86
```

面试里可以把它描述为：基于人工标注的知识库检索测试集，使用 Hit@3 评估 Top3 检索命中率。

## AIOps Agent 端到端评测

`aiops_agent_test_set.json` 包含 20 条 AIOps 任务：12 条正常用例、4 条工具超时用例和 4 条空结果用例。评测使用真实 Planner / Executor / Replanner 和配置的 LLM，本地 MCP 服务提供可复现的监控及日志数据。

指标口径：

- `end_to_end_task_completion_rate`：必要工具组调用成功，并且最终报告包含标注的关键事实。
- `normal_tool_call_success_rate`：正常用例中成功逻辑工具调用数除以逻辑工具调用总数；同一次调用的重试会去重。
- `replan_recovery_rate`：故障已实际注入、故障后明确发生 `replan`、后续取得可用工具结果且最终任务完成的故障用例数，除以全部故障用例数。

先启动两个本地 MCP Server：

```powershell
python mcp_servers/cls_server.py
python mcp_servers/monitor_server.py
```

运行一条冒烟用例：

```powershell
python scripts/evaluate_aiops_agent.py --limit 1
```

运行完整实验：

```powershell
python scripts/evaluate_aiops_agent.py
```

为降低随机性，可以将每条用例重复三次：

```powershell
python scripts/evaluate_aiops_agent.py --repeats 3
```

输出文件包括逐用例 CSV、完整执行事件 JSON，以及汇总指标 JSON。Agent 评测会将 RAG 工具替换为固定的空上下文；RAG 检索能力应继续使用现有 `evaluate_retrieval.py` 独立评价。
