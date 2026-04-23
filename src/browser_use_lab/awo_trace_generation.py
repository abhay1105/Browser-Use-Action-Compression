from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .trace_format import extract_first_url

EXTENSION_TAB_ID = 1
EXTENSION_SUPPORTED_TOOLS = {
    "computer",
    "navigate",
    "read_page",
    "form_input",
    "find",
    "get_page_text",
    "javascript_tool",
}


def _safe_call(value: Any, method_name: str) -> Any:
    method = getattr(value, method_name, None)
    if callable(method):
        try:
            return method()
        except Exception:
            return None
    return None


def _to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v, depth + 1) for v in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _to_jsonable(value.model_dump(mode="json", exclude_none=True), depth + 1)
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


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        return None
    return host or None


def _infer_domain_from_goal(goal: str) -> str | None:
    url = extract_first_url(goal)
    if url:
        return _domain_from_url(url)
    host_match = re.search(r"\b([a-z0-9-]+\.[a-z]{2,})(/[^\s)\"']*)?", goal.lower())
    if host_match:
        return host_match.group(1)
    return None


def _action_name_and_input(action_dump: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    ignored = {"interacted_element", "result"}
    action_keys = [k for k in action_dump.keys() if k not in ignored]
    if not action_keys:
        return "unknown_action", {}
    action_name = str(action_keys[0])
    raw_input = action_dump.get(action_name)
    if isinstance(raw_input, dict):
        return action_name, _to_jsonable(raw_input)
    if raw_input is None:
        return action_name, {}
    return action_name, {"value": _to_jsonable(raw_input)}


def _normalized_action_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower())


def _derive_tool_action(action_name: str, action_input: dict[str, Any]) -> tuple[str | None, str | None]:
    name = _normalized_action_name(action_name)
    if name in {"done", "finish", "complete", "task_done", "stop"}:
        return None, None

    if name in {"navigate", "go_to_url", "open", "open_url", "visit"}:
        return "navigate", None
    if name in {
        "read_page",
        "extract_page_content",
        "get_page_content",
        "extract_content",
        "extract",
        "get_page_text",
        "scrape",
    }:
        return "read_page", None
    if name in {"find", "search_page", "find_on_page"}:
        return "find", None
    if name in {"form_input", "fill_form"}:
        return "form_input", None
    if name in {"javascript_tool", "javascript", "execute_javascript", "run_js", "eval_js"}:
        return "javascript_tool", None

    computer_actions = {
        "click",
        "left_click",
        "right_click",
        "double_click",
        "input_text",
        "type",
        "key",
        "press_key",
        "scroll",
        "drag",
        "hover",
        "wait",
        "screenshot",
    }
    if name in computer_actions:
        action = name
        if action == "input_text":
            action = "type"
        if action == "press_key":
            action = "key"
        if "action" in action_input and action_input.get("action"):
            action = str(action_input["action"])
        return "computer", action

    if name.startswith("computer_"):
        action = name[len("computer_") :] or None
        return "computer", action

    # Last-resort compatibility mapping: keep unknowns inside supported tool namespace.
    return "javascript_tool", None


def _semantic_target_from_raw(raw: Any) -> dict[str, str] | None:
    payload = _to_jsonable(raw)
    if not isinstance(payload, dict):
        return None

    attributes = payload.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}

    role = (
        attributes.get("role")
        or payload.get("role")
        or attributes.get("type")
        or payload.get("node_name")
    )
    name = (
        payload.get("ax_name")
        or attributes.get("aria-label")
        or attributes.get("name")
        or attributes.get("placeholder")
    )
    if not role and not name:
        return None
    return {"role": str(role or "element"), "name": str(name or "unknown")}


def _is_yelp_search_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if "yelp.com" not in (parsed.netloc or "").lower():
        return False
    return parsed.path.startswith("/search")


def _yelp_query_text(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    desc = (query.get("find_desc") or [""])[0].strip()
    loc = (query.get("find_loc") or [""])[0].strip()
    if desc and loc:
        return f"{desc} in {loc}"
    return desc or loc


def _event(
    *,
    tool: str,
    action: str | None,
    input_payload: dict[str, Any],
    url: str | None,
    success: bool,
    semantic_target: dict[str, str] | None,
    timestamp: int,
    fallback_domain: str,
) -> dict[str, Any]:
    normalized_url = str(url) if url else None
    return {
        "tool": tool,
        "action": action,
        "input": _to_jsonable(input_payload),
        "url": normalized_url,
        "success": success,
        "domain": _domain_from_url(normalized_url) or fallback_domain,
        "timestamp": timestamp,
        "semanticTarget": semantic_target,
    }


def _computer_input(action: str, base_input: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(base_input or {})
    payload["action"] = action
    payload.setdefault("tabId", EXTENSION_TAB_ID)
    return payload


def _read_page_input(action_input: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"filter": "interactive", "tabId": EXTENSION_TAB_ID}
    query = action_input.get("query")
    if isinstance(query, str) and query.strip():
        payload["query"] = query.strip()
    return payload


def _navigate_input(action_input: dict[str, Any], target_url: str | None) -> dict[str, Any]:
    payload = dict(action_input)
    payload.setdefault("tabId", EXTENSION_TAB_ID)
    if target_url:
        payload["url"] = target_url
    return payload


def _append(events: list[dict[str, Any]], entry: dict[str, Any]) -> int:
    events.append(entry)
    return int(entry["timestamp"]) + 1


def _extract_action_name(action_record: dict[str, Any]) -> str:
    for key in action_record.keys():
        if key not in {"interacted_element", "result"}:
            return _normalized_action_name(str(key))
    return "unknown_action"


def _step_signature(step_actions: list[dict[str, Any]]) -> tuple[str, ...]:
    names: list[str] = []
    for action in step_actions:
        if isinstance(action, dict):
            name = _extract_action_name(action)
            if name:
                names.append(name)
    return tuple(names)


class StallMonitor:
    def __init__(self, *, stall_window: int, logger: logging.Logger):
        self.stall_window = max(4, stall_window)
        self.logger = logger
        self.loop_abort_reason: str | None = None
        self._last_step_logged = -1

    async def on_step_end(self, agent: Any) -> None:
        history = getattr(agent, "history", None)
        if history is None:
            return

        step_count = _safe_call(history, "number_of_steps") or 0
        if step_count <= 0:
            return

        action_steps = _safe_call(history, "action_history") or []
        urls = _safe_call(history, "urls") or []
        latest_actions = action_steps[-1] if action_steps else []
        latest_sig = _step_signature(latest_actions)
        latest_url = urls[-1] if urls else None

        if step_count != self._last_step_logged:
            self._last_step_logged = step_count
            self.logger.info(
                "step=%s url=%s actions=%s",
                step_count,
                latest_url or "-",
                ",".join(latest_sig) if latest_sig else "none",
            )

        if self.loop_abort_reason is not None or len(action_steps) < self.stall_window:
            return

        recent_steps = action_steps[-self.stall_window :]
        recent_urls = [str(u or "").split("#", 1)[0] for u in urls[-self.stall_window :]]
        recent_sigs = [_step_signature(step) for step in recent_steps]
        non_empty_sigs = [sig for sig in recent_sigs if sig]
        if len(non_empty_sigs) < self.stall_window - 1:
            return

        if any("done" in sig for sig in non_empty_sigs):
            return

        unique_sigs = len(set(non_empty_sigs))
        unique_urls = len(set(recent_urls))
        mostly_waiting = sum(
            1
            for sig in non_empty_sigs
            if all(name in {"wait", "scroll", "screenshot", "read_page"} for name in sig)
        )

        should_abort = (unique_sigs <= 2 and unique_urls <= 2) or (mostly_waiting >= self.stall_window - 1)
        if should_abort:
            self.loop_abort_reason = (
                f"Detected non-progressing loop over the last {self.stall_window} steps "
                f"(unique_signatures={unique_sigs}, unique_urls={unique_urls})."
            )
            self.logger.warning(self.loop_abort_reason)
            try:
                agent.stop()
            except Exception:
                self.logger.exception("Failed to stop agent after loop detection")


def _build_sample_events(
    *,
    history_obj: Any,
    started_at_ms: int,
    fallback_domain: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    history_steps = getattr(history_obj, "history", None) or []
    timestamp = started_at_ms
    current_url: str | None = None

    for step in history_steps:
        state = getattr(step, "state", None)
        url = getattr(state, "url", None) if state is not None else None
        interacted = getattr(state, "interacted_element", None) if state is not None else None
        model_output = getattr(step, "model_output", None)
        actions = getattr(model_output, "action", None) if model_output is not None else None
        results = getattr(step, "result", None) or []

        if not actions:
            continue

        for index, action_obj in enumerate(actions):
            try:
                action_dump = action_obj.model_dump(exclude_none=True, mode="json")
            except Exception:
                converted = _to_jsonable(action_obj)
                action_dump = converted if isinstance(converted, dict) else {"unknown_action": converted}

            action_name, action_input = _action_name_and_input(action_dump)
            tool, action = _derive_tool_action(action_name, action_input)
            if not tool:
                continue

            result_obj = results[index] if index < len(results) else None
            result_success = getattr(result_obj, "success", None) if result_obj is not None else None
            result_error = getattr(result_obj, "error", None) if result_obj is not None else None
            success = bool(result_success) if result_success is not None else not bool(result_error)

            interacted_raw = None
            if isinstance(interacted, list) and index < len(interacted):
                interacted_raw = interacted[index]
            semantic_target = _semantic_target_from_raw(interacted_raw)
            state_url = str(url) if url else current_url

            if tool == "navigate":
                target_url = str(action_input.get("url") or state_url or "").strip() or None
                if target_url and _is_yelp_search_url(target_url):
                    query_text = _yelp_query_text(target_url)
                    home_url = "https://www.yelp.com/"
                    timestamp = _append(
                        events,
                        _event(
                            tool="navigate",
                            action=None,
                            input_payload=_navigate_input({"new_tab": False}, home_url),
                            url=home_url,
                            success=success,
                            semantic_target=None,
                            timestamp=timestamp,
                            fallback_domain=fallback_domain,
                        ),
                    )
                    timestamp = _append(
                        events,
                        _event(
                            tool="computer",
                            action="screenshot",
                            input_payload=_computer_input("screenshot"),
                            url=home_url,
                            success=success,
                            semantic_target=None,
                            timestamp=timestamp,
                            fallback_domain=fallback_domain,
                        ),
                    )
                    timestamp = _append(
                        events,
                        _event(
                            tool="computer",
                            action="left_click",
                            input_payload=_computer_input("left_click", {"coordinate": [460, 90]}),
                            url=home_url,
                            success=success,
                            semantic_target={"role": "searchbox", "name": "Search Yelp"},
                            timestamp=timestamp,
                            fallback_domain=fallback_domain,
                        ),
                    )
                    if query_text:
                        timestamp = _append(
                            events,
                            _event(
                                tool="computer",
                                action="type",
                                input_payload=_computer_input("type", {"text": query_text}),
                                url=home_url,
                                success=success,
                                semantic_target={"role": "searchbox", "name": "Search Yelp"},
                                timestamp=timestamp,
                                fallback_domain=fallback_domain,
                            ),
                        )
                    timestamp = _append(
                        events,
                        _event(
                            tool="computer",
                            action="key",
                            input_payload=_computer_input("key", {"text": "Enter"}),
                            url=home_url,
                            success=success,
                            semantic_target={"role": "searchbox", "name": "Search Yelp"},
                            timestamp=timestamp,
                            fallback_domain=fallback_domain,
                        ),
                    )

                timestamp = _append(
                    events,
                    _event(
                        tool="navigate",
                        action=None,
                        input_payload=_navigate_input(action_input, target_url),
                        url=target_url or state_url,
                        success=success,
                        semantic_target=None,
                        timestamp=timestamp,
                        fallback_domain=fallback_domain,
                    ),
                )
                current_url = target_url or state_url
                timestamp = _append(
                    events,
                    _event(
                        tool="computer",
                        action="screenshot",
                        input_payload=_computer_input("screenshot"),
                        url=current_url,
                        success=success,
                        semantic_target=None,
                        timestamp=timestamp,
                        fallback_domain=fallback_domain,
                    ),
                )
                continue

            if tool == "read_page":
                timestamp = _append(
                    events,
                    _event(
                        tool="computer",
                        action="screenshot",
                        input_payload=_computer_input("screenshot"),
                        url=state_url,
                        success=success,
                        semantic_target=None,
                        timestamp=timestamp,
                        fallback_domain=fallback_domain,
                    ),
                )
                timestamp = _append(
                    events,
                    _event(
                        tool="read_page",
                        action=None,
                        input_payload=_read_page_input(action_input),
                        url=state_url,
                        success=success,
                        semantic_target=None,
                        timestamp=timestamp,
                        fallback_domain=fallback_domain,
                    ),
                )
                continue

            if tool == "computer":
                action_name = action or str(action_input.get("action") or "noop")
                normalized_action = _normalized_action_name(action_name)
                if normalized_action == "input_text":
                    normalized_action = "type"
                if normalized_action == "press_key":
                    normalized_action = "key"
                timestamp = _append(
                    events,
                    _event(
                        tool="computer",
                        action=normalized_action,
                        input_payload=_computer_input(normalized_action, action_input),
                        url=state_url,
                        success=success,
                        semantic_target=semantic_target,
                        timestamp=timestamp,
                        fallback_domain=fallback_domain,
                    ),
                )
                current_url = state_url
                continue

            if tool not in EXTENSION_SUPPORTED_TOOLS:
                continue

            timestamp = _append(
                events,
                _event(
                    tool=tool,
                    action=action,
                    input_payload=action_input,
                    url=state_url,
                    success=success,
                    semantic_target=semantic_target,
                    timestamp=timestamp,
                    fallback_domain=fallback_domain,
                ),
            )
            current_url = state_url

    return events


def _estimate_task_success(history_obj: Any, *, error: str | None, loop_abort_reason: str | None) -> bool:
    if error or loop_abort_reason:
        return False
    if history_obj is None:
        return False

    is_successful = _safe_call(history_obj, "is_successful")
    if is_successful is not None:
        return bool(is_successful)

    has_errors = _safe_call(history_obj, "has_errors")
    if has_errors:
        return False

    final_result = _safe_call(history_obj, "final_result")
    return bool(str(final_result or "").strip())


def _extract_usage_summary(history_obj: Any) -> dict[str, Any] | None:
    usage = getattr(history_obj, "usage", None) if history_obj is not None else None
    if usage is None:
        return None
    payload = _to_jsonable(usage)
    if not isinstance(payload, dict):
        return None

    return {
        "total_prompt_tokens": payload.get("total_prompt_tokens"),
        "total_completion_tokens": payload.get("total_completion_tokens"),
        "total_tokens": payload.get("total_tokens"),
        "total_prompt_cost": payload.get("total_prompt_cost"),
        "total_prompt_cached_tokens": payload.get("total_prompt_cached_tokens"),
        "total_prompt_cached_cost": payload.get("total_prompt_cached_cost"),
        "total_completion_cost": payload.get("total_completion_cost"),
        "total_cost": payload.get("total_cost"),
        "entry_count": payload.get("entry_count"),
        "by_model": payload.get("by_model"),
    }


def _make_llm(model: str, provider: str) -> Any:
    provider_slug = provider.strip().lower()

    if provider_slug == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when --provider=openai")

        try:
            from browser_use import ChatOpenAI as BrowserUseChatOpenAI

            return BrowserUseChatOpenAI(model=model, api_key=api_key)
        except Exception:
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


async def run_single_prompt_awo(
    *,
    prompt_text: str,
    trace_id: str,
    model: str,
    provider: str,
    max_steps: int,
    max_runtime_seconds: int,
    step_timeout: int,
    stall_window: int,
    headless: bool,
    user_data_dir: str | None,
    profile_directory: str,
    track_cost: bool,
    logger: logging.Logger,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    started_at_ms = int(time.time() * 1000)
    fallback_domain = _infer_domain_from_goal(prompt_text) or "unknown"
    completed_at_ms = started_at_ms
    history_obj: Any = None
    error: str | None = None

    monitor = StallMonitor(stall_window=stall_window, logger=logger)

    try:
        from browser_use import Agent
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "browser-use is not installed. Install dependencies first (pip install -e .)."
        ) from exc

    llm = _make_llm(model, provider)
    kwargs: dict[str, Any] = {
        "task": prompt_text,
        "llm": llm,
        "calculate_cost": track_cost,
        "step_timeout": step_timeout,
        "loop_detection_enabled": True,
        "loop_detection_window": max(20, stall_window * 2),
    }
    signature = inspect.signature(Agent.__init__)
    if "use_vision" in signature.parameters:
        kwargs["use_vision"] = True
    if "headless" in signature.parameters:
        kwargs["headless"] = headless
    elif "browser_profile" in signature.parameters:
        try:
            from browser_use import BrowserProfile

            profile_kwargs: dict[str, Any] = {
                "headless": headless,
                "profile_directory": profile_directory,
                # Ensure each prompt run tears down its browser session so we do not
                # accumulate dozens of open Chrome windows in visible mode.
                "keep_alive": False,
            }
            if user_data_dir:
                profile_kwargs["user_data_dir"] = user_data_dir
            kwargs["browser_profile"] = BrowserProfile(**profile_kwargs)
        except Exception:
            logger.exception(
                "Failed to configure BrowserProfile(headless=%s, user_data_dir=%s, profile_directory=%s)",
                headless,
                user_data_dir,
                profile_directory,
            )

    agent = Agent(**kwargs)

    try:
        run_result = agent.run(max_steps=max_steps, on_step_end=monitor.on_step_end)
        if inspect.isawaitable(run_result):
            history_obj = await asyncio.wait_for(run_result, timeout=max_runtime_seconds)
        else:
            history_obj = run_result
    except asyncio.TimeoutError:
        error = f"Trace timed out after {max_runtime_seconds} seconds"
        logger.warning(error)
        try:
            agent.stop()
        except Exception:
            logger.exception("Failed to stop agent after timeout")
        history_obj = getattr(agent, "history", None)
    except Exception as exc:
        error = str(exc)
        logger.exception("Prompt execution failed")
        history_obj = getattr(agent, "history", None)
    finally:
        completed_at_ms = int(time.time() * 1000)
        try:
            await agent.close()
        except Exception:
            logger.exception("Failed to close browser agent cleanly")

    events = _build_sample_events(
        history_obj=history_obj,
        started_at_ms=started_at_ms,
        fallback_domain=fallback_domain,
    )

    top_domain = fallback_domain
    for event in events:
        if event.get("domain"):
            top_domain = str(event["domain"])
            break

    trace_entry = {
        "id": trace_id,
        "domain": top_domain,
        "goal": prompt_text,
        "events": events,
        "startedAt": started_at_ms,
        "completedAt": completed_at_ms,
        "taskSuccess": _estimate_task_success(
            history_obj,
            error=error,
            loop_abort_reason=monitor.loop_abort_reason,
        )
        and bool(events),
    }

    usage_summary = _extract_usage_summary(history_obj) if track_cost else None
    return trace_entry, usage_summary


def run_single_prompt_awo_sync(
    *,
    prompt_text: str,
    trace_id: str,
    model: str,
    provider: str,
    max_steps: int,
    max_runtime_seconds: int,
    step_timeout: int,
    stall_window: int,
    headless: bool,
    user_data_dir: str | None,
    profile_directory: str,
    track_cost: bool,
    logger: logging.Logger,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    return asyncio.run(
        run_single_prompt_awo(
            prompt_text=prompt_text,
            trace_id=trace_id,
            model=model,
            provider=provider,
            max_steps=max_steps,
            max_runtime_seconds=max_runtime_seconds,
            step_timeout=step_timeout,
            stall_window=stall_window,
            headless=headless,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
            track_cost=track_cost,
            logger=logger,
        )
    )
