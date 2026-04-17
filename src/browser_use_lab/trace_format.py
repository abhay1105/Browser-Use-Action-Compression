from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import write_json

TRACE_SCHEMA_VERSION = 1


@dataclass
class TraceContext:
    task: dict[str, Any]
    goal: str
    session_id: str
    server_url: str | None
    device_id: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_file_name(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9._-]+", "-", str(value).strip().lower())
    sanitized = re.sub(r"^-+|-+$", "", sanitized)
    return (sanitized or "item")[:80]


def extract_first_url(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"https?://[^\s)\"']+", text)
    return match.group(0) if match else None


def compact_block(block: dict[str, Any]) -> dict[str, Any]:
    block_type = block.get("type")
    if block_type == "text":
        return {"type": "text", "text": block.get("text", "")}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.get("id"),
            "name": block.get("name"),
            "input": block.get("input") or {},
        }
    if block_type == "tool_result":
        return {
            "type": "tool_result",
            "toolUseId": block.get("toolUseId"),
            "text": block.get("text", ""),
            "hasImage": bool(block.get("imageData")),
        }
    screenshot_data = str(block.get("data") or "")
    return {
        "type": "screenshot",
        "hasData": bool(screenshot_data),
        "bytes": len(screenshot_data),
    }


def compact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for message in messages:
        compacted.append(
            {
                "id": message.get("id"),
                "role": message.get("role"),
                "timestamp": message.get("timestamp"),
                "blocks": [compact_block(b) for b in message.get("blocks", [])],
            }
        )
    return compacted


def build_task_payload(context: TraceContext, recorded_at: str) -> dict[str, Any]:
    return {
        "schemaVersion": TRACE_SCHEMA_VERSION,
        "recordedAt": recorded_at,
        "task": context.task,
        "goal": context.goal,
        "sessionId": context.session_id,
        "serverUrl": context.server_url,
        "deviceId": context.device_id,
    }


def build_trace_payload(
    *,
    context: TraceContext,
    started_at: str,
    completed_at: str | None,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
    trace_folder: str,
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schemaVersion": TRACE_SCHEMA_VERSION,
        "startedAt": started_at,
        "completedAt": completed_at,
        "status": status,
        "result": result,
        "error": error,
        "taskId": context.task.get("id"),
        "taskType": context.task.get("type"),
        "goal": context.goal,
        "url": context.task.get("url"),
        "sessionId": context.session_id,
        "serverUrl": context.server_url,
        "deviceId": context.device_id,
        "traceFolder": trace_folder,
        "events": events,
        "messages": compact_messages(messages),
    }


def write_trace_pair(
    *,
    trace_dir: Path,
    context: TraceContext,
    started_at: str,
    completed_at: str | None,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> None:
    recorded_at = now_iso()
    task_payload = build_task_payload(context, recorded_at)
    trace_payload = build_trace_payload(
        context=context,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        result=result,
        error=error,
        trace_folder=trace_dir.name,
        events=events,
        messages=messages,
    )
    write_json(trace_dir / "task.json", task_payload)
    write_json(trace_dir / "trace.json", trace_payload)
