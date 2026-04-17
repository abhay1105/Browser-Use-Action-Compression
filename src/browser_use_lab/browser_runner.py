from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .trace_format import TraceContext, extract_first_url


@dataclass
class RunOutput:
    status: str
    result: dict[str, Any] | None
    error: str | None
    messages: list[dict[str, Any]]
    events: list[dict[str, Any]]


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ts_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _event(events: list[dict[str, Any]], event_type: str, payload: dict[str, Any] | None = None) -> None:
    events.append(
        {
            "timestamp": ts_ms(),
            "type": event_type,
            "payload": payload or {},
        }
    )


def _safe_call(value: Any, method_name: str) -> Any:
    method = getattr(value, method_name, None)
    if callable(method):
        try:
            return method()
        except Exception:
            return None
    return None


def _to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v, depth + 1) for v in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _to_jsonable(value.model_dump(), depth + 1)
        except Exception:
            return str(value)
    if hasattr(value, "dict") and callable(value.dict):
        try:
            return _to_jsonable(value.dict(), depth + 1)
        except Exception:
            return str(value)
    if hasattr(value, "__dict__"):
        try:
            return _to_jsonable(vars(value), depth + 1)
        except Exception:
            return str(value)
    return str(value)


def _parse_actions(raw_actions: Any) -> list[dict[str, Any]]:
    actions = _to_jsonable(raw_actions)
    if not isinstance(actions, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in actions:
        if isinstance(item, dict) and len(item) == 1:
            name = next(iter(item.keys()))
            inp = item[name]
            parsed.append(
                {
                    "name": str(name),
                    "input": inp if isinstance(inp, dict) else {"value": inp},
                }
            )
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("action") or "browser_action")
            parsed.append({"name": name, "input": item})
        else:
            parsed.append({"name": "browser_action", "input": {"value": item}})
    return parsed


def _append_tool_events(events: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    for index, action in enumerate(actions, start=1):
        tool_use_id = f"tool_{index:03d}"
        _event(
            events,
            "tool_use",
            {
                "toolUseId": tool_use_id,
                "name": action["name"],
                "input": action["input"],
            },
        )
        _event(
            events,
            "tool_result",
            {
                "toolUseId": tool_use_id,
                "name": action["name"],
                "durationMs": None,
                "text": "",
                "imageCount": 0,
            },
        )


def _build_messages(prompt_text: str, history_obj: Any, started_ms: int) -> list[dict[str, Any]]:
    user_message = {
        "id": f"user_{started_ms}",
        "role": "user",
        "timestamp": started_ms,
        "blocks": [{"type": "text", "text": prompt_text}],
    }

    thoughts = _safe_call(history_obj, "model_thoughts")
    actions = _safe_call(history_obj, "model_actions")
    final_result = _safe_call(history_obj, "final_result")
    errors = _safe_call(history_obj, "errors")

    assistant_blocks: list[dict[str, Any]] = []

    if isinstance(thoughts, list):
        for thought in thoughts:
            text = str(thought).strip()
            if text:
                assistant_blocks.append({"type": "text", "text": text})

    for index, action in enumerate(_parse_actions(actions), start=1):
        assistant_blocks.append(
            {
                "type": "tool_use",
                "id": f"tool_{index:03d}",
                "name": action["name"],
                "input": action["input"],
            }
        )
        assistant_blocks.append(
            {
                "type": "tool_result",
                "toolUseId": f"tool_{index:03d}",
                "text": "browser-use action executed",
                "imageData": None,
            }
        )

    if final_result is not None and str(final_result).strip():
        assistant_blocks.append({"type": "text", "text": str(final_result)})

    if isinstance(errors, list) and errors:
        assistant_blocks.append({"type": "text", "text": "Errors: " + "; ".join(str(e) for e in errors)})

    if not assistant_blocks:
        assistant_blocks.append({"type": "text", "text": "No assistant output captured."})

    assistant_message = {
        "id": f"asst_{started_ms}",
        "role": "assistant",
        "timestamp": ts_ms(),
        "blocks": assistant_blocks,
    }

    return [user_message, assistant_message]


def build_task_record(*, task_id: str, prompt_text: str, device_id: str) -> dict[str, Any]:
    url = extract_first_url(prompt_text)
    if not url:
        lowered = prompt_text.lower()
        for keyword, candidate in (
            ("amazon", "https://www.amazon.com/"),
            ("newegg", "https://www.newegg.com/"),
            ("wikipedia", "https://www.wikipedia.org/"),
        ):
            if keyword in lowered:
                url = candidate
                break
    if not url:
        host_match = re.search(r"\b([a-z0-9-]+\.[a-z]{2,})(/[^\s)\"']*)?", prompt_text.lower())
        if host_match:
            host = host_match.group(1)
            path = host_match.group(2) or "/"
            url = f"https://{host}{path}"

    return {
        "id": task_id,
        "type": "start_local_agent_goal",
        "url": url,
        "goal": prompt_text,
        "taskPrompt": prompt_text,
        "deviceId": device_id,
        "createdAt": utc_iso(),
    }


def _make_llm(model: str, provider: str) -> Any:
    provider_slug = provider.strip().lower()

    if provider_slug == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when --provider=openai")

        try:
            # browser-use>=0.12 expects browser_use.llm BaseChatModel implementations.
            from browser_use import ChatOpenAI as BrowserUseChatOpenAI

            return BrowserUseChatOpenAI(model=model, api_key=api_key)
        except Exception:
            # Backward compatibility for older browser-use releases that accepted LangChain models.
            try:
                from langchain_openai import ChatOpenAI
            except Exception as exc:  # pragma: no cover
                raise RuntimeError("Unable to create an OpenAI chat model for browser-use") from exc
            return ChatOpenAI(model=model, temperature=0, api_key=api_key)

    if provider_slug == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required when --provider=anthropic")
        try:
            from browser_use import ChatAnthropic
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("browser-use ChatAnthropic class is unavailable") from exc
        return ChatAnthropic(model=model, api_key=api_key)

    raise RuntimeError(f"Unsupported provider: {provider}")


async def _run_browser_use_task(prompt_text: str, model: str, provider: str, headless: bool) -> Any:
    try:
        from browser_use import Agent
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "browser-use is not installed. Install dependencies first (pip install -e .)."
        ) from exc

    llm = _make_llm(model, provider)

    kwargs: dict[str, Any] = {"task": prompt_text, "llm": llm}
    signature = inspect.signature(Agent.__init__)
    if "use_vision" in signature.parameters:
        kwargs["use_vision"] = True
    if "headless" in signature.parameters:
        kwargs["headless"] = headless
    elif "browser_profile" in signature.parameters:
        try:
            from browser_use import BrowserProfile

            kwargs["browser_profile"] = BrowserProfile(headless=headless)
        except Exception:
            # If BrowserProfile cannot be imported/initialized, rely on browser-use defaults.
            pass

    agent = Agent(**kwargs)
    run_result = agent.run()
    if inspect.isawaitable(run_result):
        return await run_result
    return run_result


async def run_single_prompt(
    *,
    prompt_text: str,
    context: TraceContext,
    model: str,
    provider: str,
    headless: bool,
) -> RunOutput:
    events: list[dict[str, Any]] = []
    started_ms = ts_ms()

    _event(
        events,
        "task_received",
        {
            "taskId": context.task.get("id"),
            "taskType": context.task.get("type"),
            "goal": context.goal,
            "url": context.task.get("url"),
            "sessionId": context.session_id,
        },
    )
    _event(events, "session_initialized", {"sessionId": context.session_id})
    _event(events, "agent_loop_started", {"sessionId": context.session_id, "userPrompt": prompt_text})
    _event(events, "user_prompt", {"text": prompt_text})

    try:
        _event(events, "browser_use_run_started", {"model": model, "provider": provider, "headless": headless})
        history = await _run_browser_use_task(prompt_text, model, provider, headless)
        _event(events, "browser_use_run_finished", {})

        final_result = _safe_call(history, "final_result")
        model_actions = _safe_call(history, "model_actions")
        model_thoughts = _safe_call(history, "model_thoughts")
        errors = _safe_call(history, "errors")
        parsed_actions = _parse_actions(model_actions)
        _append_tool_events(events, parsed_actions)

        result_payload = {
            "status": "completed",
            "final_result": final_result,
            "browser_use": {
                "model_actions": _to_jsonable(model_actions),
                "model_thoughts": _to_jsonable(model_thoughts),
                "errors": _to_jsonable(errors),
                "history": _to_jsonable(history),
            },
        }

        messages = _build_messages(prompt_text, history, started_ms)
        _event(
            events,
            "task_completed",
            {"taskId": context.task.get("id"), "hasResult": bool(final_result)},
        )
        _event(events, "agent_loop_finished", {"sessionId": context.session_id, "error": None})
        return RunOutput(
            status="completed",
            result=result_payload,
            error=None,
            messages=messages,
            events=events,
        )
    except Exception as exc:
        err_msg = str(exc)
        _event(events, "task_failed", {"taskId": context.task.get("id"), "error": err_msg})
        _event(events, "agent_loop_error", {"error": err_msg})
        _event(events, "agent_loop_finished", {"sessionId": context.session_id, "error": err_msg})

        messages = [
            {
                "id": f"user_{started_ms}",
                "role": "user",
                "timestamp": started_ms,
                "blocks": [{"type": "text", "text": prompt_text}],
            },
            {
                "id": f"asst_{started_ms}",
                "role": "assistant",
                "timestamp": ts_ms(),
                "blocks": [
                    {
                        "type": "text",
                        "text": f"Task failed: {err_msg}",
                    }
                ],
            },
        ]

        return RunOutput(
            status="failed",
            result=None,
            error=f"{err_msg}\n{traceback.format_exc()}".strip(),
            messages=messages,
            events=events,
        )


def run_single_prompt_sync(
    *,
    prompt_text: str,
    context: TraceContext,
    model: str,
    provider: str,
    headless: bool,
) -> RunOutput:
    return asyncio.run(
        run_single_prompt(
            prompt_text=prompt_text,
            context=context,
            model=model,
            provider=provider,
            headless=headless,
        )
    )
