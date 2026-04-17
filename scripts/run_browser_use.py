#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from browser_use_lab.browser_runner import build_task_record, run_single_prompt_sync
from browser_use_lab.ids import slugify
from browser_use_lab.io_utils import ensure_dir, read_json
from browser_use_lab.trace_format import TraceContext, write_trace_pair


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run browser-use prompts and write OttoAuth-style traces",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to dataset JSON file under prompt_datasets/",
    )
    parser.add_argument(
        "--traces-dir",
        default="browser_traces",
        help="Directory for trace folders",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model used by browser-use",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("BROWSER_USE_PROVIDER", "openai"),
        choices=["openai", "anthropic"],
        help="LLM provider used by browser-use",
    )
    parser.add_argument(
        "--device-id",
        default=os.getenv("BROWSER_USE_DEVICE_ID", "browser-use-local"),
        help="Device id used in trace task metadata",
    )
    parser.add_argument(
        "--max-prompts",
        type=int,
        default=None,
        help="Optional cap on number of prompts from dataset",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run with visible browser window (default is headless)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create trace folders without calling browser-use",
    )
    return parser.parse_args()


def _validate_dataset(dataset: dict[str, Any]) -> tuple[str, int, str, list[dict[str, Any]]]:
    task = str(dataset.get("task") or "").strip()
    num_examples = int(dataset.get("num_examples") or 0)
    prompt_id = str(dataset.get("prompt_id") or "").strip()
    prompts = dataset.get("prompts") or []

    if not task:
        raise ValueError("Dataset missing 'task'")
    if num_examples <= 0:
        raise ValueError("Dataset missing valid 'num_examples'")
    if not prompt_id:
        raise ValueError("Dataset missing 'prompt_id'")
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("Dataset missing non-empty 'prompts' list")

    return task, num_examples, prompt_id, prompts


def _dry_run_output(prompt_text: str, task_id: str, session_id: str, task_url: str | None) -> dict[str, Any]:
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    tool_use_id = "tool_001"
    tool_name = "navigate" if task_url else "dry_run_noop"
    tool_input: dict[str, Any] = {"url": task_url} if task_url else {"prompt": prompt_text}
    return {
        "status": "completed",
        "result": {
            "status": "completed",
            "final_result": "dry-run completed",
            "browser_use": {
                "dry_run": True,
                "model_actions": [{tool_name: tool_input}],
            },
        },
        "error": None,
        "events": [
            {
                "timestamp": timestamp_ms,
                "type": "task_received",
                "payload": {
                    "taskId": task_id,
                    "taskType": "start_local_agent_goal",
                    "goal": prompt_text,
                    "url": task_url,
                    "sessionId": session_id,
                },
            },
            {
                "timestamp": timestamp_ms + 1,
                "type": "tool_use",
                "payload": {
                    "toolUseId": tool_use_id,
                    "name": tool_name,
                    "input": tool_input,
                },
            },
            {
                "timestamp": timestamp_ms + 2,
                "type": "tool_result",
                "payload": {
                    "toolUseId": tool_use_id,
                    "name": tool_name,
                    "durationMs": 0,
                    "text": "dry-run simulated action",
                    "imageCount": 0,
                },
            },
            {
                "timestamp": timestamp_ms + 3,
                "type": "task_completed",
                "payload": {"taskId": task_id, "hasResult": True},
            },
        ],
        "messages": [
            {
                "id": f"user_{timestamp_ms}",
                "role": "user",
                "timestamp": timestamp_ms,
                "blocks": [{"type": "text", "text": prompt_text}],
            },
            {
                "id": f"asst_{timestamp_ms}",
                "role": "assistant",
                "timestamp": timestamp_ms + 3,
                "blocks": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": tool_input,
                    },
                    {
                        "type": "tool_result",
                        "toolUseId": tool_use_id,
                        "text": "dry-run simulated action",
                        "imageData": None,
                    },
                    {"type": "text", "text": "dry-run completed"},
                ],
            },
        ],
    }


def _example_number(prompt_row: dict[str, Any], default_number: int) -> int:
    raw_id = str(prompt_row.get("id") or "").strip()
    match = re.search(r"_(\d+)$", raw_id)
    if match:
        value = int(match.group(1))
        if value > 0:
            return value
    return default_number


def _next_run_folder(dataset_trace_dir: Path) -> str:
    max_seen = 0
    for path in dataset_trace_dir.iterdir() if dataset_trace_dir.exists() else []:
        if not path.is_dir():
            continue
        match = re.fullmatch(r"run_(\d+)", path.name)
        if not match:
            continue
        max_seen = max(max_seen, int(match.group(1)))
    return f"run_{max_seen + 1:03d}"


def main() -> None:
    load_dotenv()
    args = parse_args()
    provider = str(args.provider).strip().lower()
    model = args.model or os.getenv("BROWSER_USE_MODEL", "").strip()
    if not model:
        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4.1-mini"

    dataset_path = Path(args.dataset).resolve()
    if not dataset_path.exists():
        raise SystemExit(f"Dataset file not found: {dataset_path}")

    dataset = read_json(dataset_path)
    task, num_examples, prompt_id, prompts = _validate_dataset(dataset)
    task_slug = slugify(task)

    traces_dir = ensure_dir(Path(args.traces_dir).resolve())
    dataset_prefix = f"{task_slug}_{num_examples}_{prompt_id}"
    dataset_trace_dir = ensure_dir(traces_dir / dataset_prefix)
    run_folder = _next_run_folder(dataset_trace_dir)
    run_trace_dir = ensure_dir(dataset_trace_dir / run_folder)
    selected_prompts = prompts[: args.max_prompts] if args.max_prompts else prompts

    total = len(selected_prompts)
    completed = 0
    failed = 0
    started_wall = time.time()

    print(f"Provider: {provider}", flush=True)
    print(f"Model: {model}", flush=True)
    print(f"Headless: {not args.show_browser}", flush=True)
    print(f"Total prompts selected: {total}", flush=True)
    print(f"Dataset run output: {run_trace_dir}", flush=True)
    print("---", flush=True)

    for offset, prompt_row in enumerate(selected_prompts):
        prompt_text = str(prompt_row.get("text") or "").strip()
        if not prompt_text:
            continue

        example_number = _example_number(prompt_row, offset + 1)
        example_id = f"{example_number:03d}"
        trace_dir = ensure_dir(run_trace_dir / example_id)

        task_id = f"task_{example_id}"
        session_id = f"session_{example_id}"
        task_record = build_task_record(
            task_id=task_id,
            prompt_text=prompt_text,
            device_id=args.device_id,
        )
        context = TraceContext(
            task=task_record,
            goal=prompt_text,
            session_id=session_id,
            server_url=None,
            device_id=args.device_id,
        )

        started_at = iso_now()
        example_started_wall = time.time()
        print(f"[{offset + 1}/{total}] starting example {example_id}", flush=True)

        if args.dry_run:
            outcome = _dry_run_output(prompt_text, task_id, session_id, context.task.get("url"))
        else:
            run_output = run_single_prompt_sync(
                prompt_text=prompt_text,
                context=context,
                model=model,
                provider=provider,
                headless=not args.show_browser,
            )
            outcome = {
                "status": run_output.status,
                "result": run_output.result,
                "error": run_output.error,
                "events": run_output.events,
                "messages": run_output.messages,
            }

        completed_at = None if outcome["status"] == "running" else iso_now()

        write_trace_pair(
            trace_dir=trace_dir,
            context=context,
            started_at=started_at,
            completed_at=completed_at,
            status=outcome["status"],
            result=outcome["result"],
            error=outcome["error"],
            events=outcome["events"],
            messages=outcome["messages"],
        )

        if outcome["status"] == "completed":
            completed += 1
            print(f"[{example_id}] completed in {time.time() - example_started_wall:.1f}s -> {trace_dir}", flush=True)
        else:
            failed += 1
            print(f"[{example_id}] failed in {time.time() - example_started_wall:.1f}s -> {trace_dir}", flush=True)

    print("---", flush=True)
    print(f"Dataset: {dataset_prefix}", flush=True)
    print(f"Run folder: {run_folder}", flush=True)
    print(f"Traces written to: {traces_dir}", flush=True)
    print(f"Elapsed seconds: {time.time() - started_wall:.1f}", flush=True)
    print(f"Completed: {completed}", flush=True)
    print(f"Failed: {failed}", flush=True)


if __name__ == "__main__":
    main()
