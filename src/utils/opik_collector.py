"""
Opik Collector — 从 Opik 平台收集 Agent 运行追踪数据。

提供以下指标：
- Token 消耗（input/output/cache）
- 行为链长度（tool call spans 数量）
- 思维链长度（LLM spans 内的 reasoning tokens）
- 中间结果（每个 tool call 的 input/output）
- 最终结果（trace finalize 数据）
- 执行时间（每个 span 的 duration）
- 多 Agent 追踪（sub-agent spans）
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)


class OpikCollector:
    """从 Opik API 收集可观测性数据。"""

    def __init__(
        self,
        api_url: str = "http://localhost:5173/api",
        api_key: str = "",
        project_name: str = "wildclaw-bench",
        workspace_name: str = "default",
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.project_name = project_name
        self.workspace_name = workspace_name

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def collect_task_traces(
        self,
        task_id: str,
        lookback_minutes: int = 30,
    ) -> dict[str, Any]:
        """
        收集指定 task 的 Opik 追踪数据，返回结构化指标。

        Returns:
            {
                "traces": [...],
                "summary": {
                    "total_tokens": int,
                    "input_tokens": int,
                    "output_tokens": int,
                    "cost_usd": float,
                    "total_duration_ms": int,
                    "llm_call_count": int,
                    "tool_call_count": int,
                    "subagent_count": int,
                    "action_chain_length": int,
                    "thinking_chain_length": int,
                },
                "tool_calls": [...],
                "subagents": [...],
                "llm_spans": [...],
            }
        """
        try:
            traces = self._fetch_recent_traces(lookback_minutes)
        except Exception as exc:
            logger.warning("Failed to fetch Opik traces: %s", exc)
            return self._empty_result()

        if not traces:
            logger.info("No Opik traces found for project=%s", self.project_name)
            return self._empty_result()

        # 筛选与 task_id 相关的 traces（通过 tags 或 metadata）
        relevant_traces = self._filter_traces_by_task(traces, task_id)
        if not relevant_traces:
            # 如果没有精确匹配，取最近的 traces
            relevant_traces = traces[:5]

        # 获取每个 trace 的 spans
        all_spans: list[dict] = []
        for trace in relevant_traces:
            trace_id = trace.get("id", "")
            if trace_id:
                spans = self._fetch_spans_for_trace(trace_id)
                all_spans.extend(spans)

        # 分类 spans
        llm_spans = [s for s in all_spans if s.get("type") == "llm"]
        tool_spans = [s for s in all_spans if s.get("type") == "tool"]
        subagent_spans = [s for s in all_spans if s.get("type") == "general" and "subagent" in s.get("name", "").lower()]

        # 计算汇总指标
        summary = self._compute_summary(relevant_traces, llm_spans, tool_spans, subagent_spans)

        # 提取 tool calls 的中间结果
        tool_calls = self._extract_tool_calls(tool_spans)

        # 提取 sub-agent 信息
        subagents = self._extract_subagent_info(subagent_spans)

        return {
            "traces": [self._sanitize_trace(t) for t in relevant_traces],
            "summary": summary,
            "tool_calls": tool_calls,
            "subagents": subagents,
            "llm_spans": [self._sanitize_span(s) for s in llm_spans[:50]],
        }

    def _fetch_recent_traces(self, lookback_minutes: int) -> list[dict]:
        """获取最近的 traces。"""
        since = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
        url = f"{self.api_url}/v1/private/traces"
        params = {
            "project_name": self.project_name,
            "limit": 50,
        }
        resp = requests.get(url, headers=self._headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("content", data.get("traces", []))
        logger.warning("Opik traces API returned %s: %s", resp.status_code, resp.text[:200])
        return []

    def _fetch_spans_for_trace(self, trace_id: str) -> list[dict]:
        """获取特定 trace 的所有 spans。"""
        url = f"{self.api_url}/v1/private/spans"
        params = {
            "trace_id": trace_id,
            "limit": 200,
        }
        try:
            resp = requests.get(url, headers=self._headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("content", data.get("spans", []))
        except Exception as exc:
            logger.warning("Failed to fetch spans for trace %s: %s", trace_id, exc)
        return []

    def _filter_traces_by_task(self, traces: list[dict], task_id: str) -> list[dict]:
        """根据 task_id 过滤 traces。"""
        result = []
        for t in traces:
            tags = t.get("tags", [])
            metadata = t.get("metadata", {})
            name = t.get("name", "")
            if (
                task_id in tags
                or metadata.get("task_id") == task_id
                or task_id in name
            ):
                result.append(t)
        return result

    def _compute_summary(
        self,
        traces: list[dict],
        llm_spans: list[dict],
        tool_spans: list[dict],
        subagent_spans: list[dict],
    ) -> dict[str, Any]:
        """计算汇总指标。"""
        total_input = 0
        total_output = 0
        total_cost = 0.0
        total_duration_ms = 0

        for span in llm_spans:
            usage = span.get("usage", {}) or span.get("metadata", {}).get("usage", {})
            total_input += usage.get("prompt_tokens", 0) + usage.get("input", 0)
            total_output += usage.get("completion_tokens", 0) + usage.get("output", 0)
            cost = span.get("metadata", {}).get("cost", {})
            if isinstance(cost, dict):
                total_cost += cost.get("total", 0.0)
            elif isinstance(cost, (int, float)):
                total_cost += cost

        for trace in traces:
            start = trace.get("start_time")
            end = trace.get("end_time")
            if start and end:
                try:
                    s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    total_duration_ms += int((e - s).total_seconds() * 1000)
                except (ValueError, TypeError):
                    pass

        # 思维链长度：统计 LLM spans 中包含 reasoning/thinking 的数量
        thinking_count = 0
        for span in llm_spans:
            output = span.get("output", {})
            if isinstance(output, dict):
                choices = output.get("choices", [])
                for choice in choices:
                    msg = choice.get("message", {})
                    if msg.get("reasoning_content") or msg.get("thinking"):
                        thinking_count += 1
                        break

        return {
            "total_tokens": total_input + total_output,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost_usd": round(total_cost, 6),
            "total_duration_ms": total_duration_ms,
            "llm_call_count": len(llm_spans),
            "tool_call_count": len(tool_spans),
            "subagent_count": len(subagent_spans),
            "action_chain_length": len(tool_spans) + len(subagent_spans),
            "thinking_chain_length": thinking_count,
        }

    def _extract_tool_calls(self, tool_spans: list[dict]) -> list[dict]:
        """提取 tool call 的中间结果。"""
        results = []
        for span in tool_spans:
            results.append({
                "name": span.get("name", "unknown"),
                "input": self._truncate(span.get("input", {}), 2000),
                "output": self._truncate(span.get("output", {}), 2000),
                "error": span.get("metadata", {}).get("error"),
                "duration_ms": self._span_duration_ms(span),
                "start_time": span.get("start_time"),
                "end_time": span.get("end_time"),
            })
        return results

    def _extract_subagent_info(self, subagent_spans: list[dict]) -> list[dict]:
        """提取 sub-agent 信息。"""
        results = []
        for span in subagent_spans:
            results.append({
                "name": span.get("name", "unknown"),
                "metadata": span.get("metadata", {}),
                "duration_ms": self._span_duration_ms(span),
                "start_time": span.get("start_time"),
                "end_time": span.get("end_time"),
                "output": self._truncate(span.get("output", {}), 1000),
            })
        return results

    def _span_duration_ms(self, span: dict) -> int:
        """计算 span 持续时间（毫秒）。"""
        start = span.get("start_time")
        end = span.get("end_time")
        if not start or not end:
            return 0
        try:
            s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            return int((e - s).total_seconds() * 1000)
        except (ValueError, TypeError):
            return 0

    def _sanitize_trace(self, trace: dict) -> dict:
        """清理 trace 数据以适合序列化输出。"""
        return {
            "id": trace.get("id"),
            "name": trace.get("name"),
            "start_time": trace.get("start_time"),
            "end_time": trace.get("end_time"),
            "tags": trace.get("tags", []),
            "metadata": trace.get("metadata", {}),
            "token_usage": trace.get("usage", {}),
        }

    def _sanitize_span(self, span: dict) -> dict:
        """清理 span 数据。"""
        return {
            "id": span.get("id"),
            "name": span.get("name"),
            "type": span.get("type"),
            "start_time": span.get("start_time"),
            "end_time": span.get("end_time"),
            "duration_ms": self._span_duration_ms(span),
            "input_preview": self._truncate(span.get("input", {}), 500),
            "output_preview": self._truncate(span.get("output", {}), 500),
        }

    @staticmethod
    def _truncate(data: Any, max_len: int) -> Any:
        """截断过长的数据。"""
        if isinstance(data, str) and len(data) > max_len:
            return data[:max_len] + "...[truncated]"
        if isinstance(data, dict):
            text = json.dumps(data, ensure_ascii=False, default=str)
            if len(text) > max_len:
                return text[:max_len] + "...[truncated]"
            return data
        return data

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        """返回空结果。"""
        return {
            "traces": [],
            "summary": {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "total_duration_ms": 0,
                "llm_call_count": 0,
                "tool_call_count": 0,
                "subagent_count": 0,
                "action_chain_length": 0,
                "thinking_chain_length": 0,
            },
            "tool_calls": [],
            "subagents": [],
            "llm_spans": [],
        }
