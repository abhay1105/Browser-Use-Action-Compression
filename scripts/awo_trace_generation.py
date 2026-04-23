#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from browser_use_lab.awo_trace_generation import run_single_prompt_awo_sync
from browser_use_lab.io_utils import ensure_dir, read_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate resumable AWO traces in sample_traces.json-compatible format",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to dataset JSON file under prompt_datasets/",
    )
    parser.add_argument(
        "--output-dir",
        default="awo_browser_traces",
        help="Directory for AWO trace outputs",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run non-headless to allow manual login/CAPTCHA handling",
    )
    parser.add_argument(
        "--user-data-dir",
        default=None,
        help="Persistent Chromium user data directory to reuse authenticated sessions",
    )
    parser.add_argument(
        "--profile-directory",
        default="Default",
        help="Chromium profile directory name inside user-data-dir (default: Default)",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("BROWSER_USE_PROVIDER", "openai"),
        choices=["openai", "anthropic"],
        help="LLM provider used by browser-use",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model used by browser-use",
    )
    parser.add_argument(
        "--max-prompts",
        type=int,
        default=None,
        help="Optional cap on number of prompts from dataset",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=120,
        help="Maximum steps per prompt before stopping",
    )
    parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=900,
        help="Wall-clock timeout per prompt in seconds",
    )
    parser.add_argument(
        "--step-timeout",
        type=int,
        default=180,
        help="Per-step timeout in seconds passed to browser-use Agent",
    )
    parser.add_argument(
        "--stall-window",
        type=int,
        default=8,
        help="Recent-step window used for loop/non-progress detection",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output file instead of resuming",
    )
    parser.add_argument(
        "--no-cost-tracking",
        action="store_true",
        help="Disable cost tracking sidecar output",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console/file log verbosity",
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


def _example_number(prompt_row: dict[str, Any], default_number: int) -> int:
    raw_id = str(prompt_row.get("id") or "").strip()
    match = re.search(r"_(\d+)$", raw_id)
    if match:
        value = int(match.group(1))
        if value > 0:
            return value
    return default_number


def _trace_sort_key(trace: dict[str, Any]) -> tuple[int, str]:
    trace_id = str(trace.get("id") or "")
    match = re.search(r"(\d+)$", trace_id)
    if match:
        return (int(match.group(1)), trace_id)
    return (10**9, trace_id)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    tmp_path.replace(path)


def _load_json_if_exists(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return read_json(path)


def _setup_logger(*, level: str, log_path: Path) -> logging.Logger:
    logger = logging.getLogger("awo_trace_generation")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def main() -> None:
    load_dotenv()
    args = parse_args()

    provider = str(args.provider).strip().lower()
    model = args.model or os.getenv("BROWSER_USE_MODEL", "").strip()
    if not model:
        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4.1-mini"

    if args.max_steps <= 0:
        raise SystemExit("--max-steps must be positive")
    if args.max_runtime_seconds <= 0:
        raise SystemExit("--max-runtime-seconds must be positive")
    if args.step_timeout <= 0:
        raise SystemExit("--step-timeout must be positive")
    if args.stall_window < 4:
        raise SystemExit("--stall-window must be at least 4")

    dataset_path = Path(args.dataset).resolve()
    if not dataset_path.exists():
        raise SystemExit(f"Dataset file not found: {dataset_path}")

    output_dir = ensure_dir(Path(args.output_dir).resolve())
    runtime_user_data_dir = args.user_data_dir
    if args.show_browser and runtime_user_data_dir:
        profile_path = Path(runtime_user_data_dir).expanduser().resolve()
        if profile_path.exists():
            shutil.rmtree(profile_path)
            print(f"Cleared existing browser profile dir for fresh visible run: {profile_path}")
        runtime_user_data_dir = str(profile_path)

    output_path = output_dir / f"{dataset_path.stem}.json"
    costs_path = output_dir / f"{dataset_path.stem}.costs.json"
    log_path = output_dir / f"{dataset_path.stem}.log"
    logger = _setup_logger(level=args.log_level, log_path=log_path)

    logger.info("Dataset: %s", dataset_path)
    logger.info("Output: %s", output_path)
    logger.info("Costs sidecar: %s", costs_path)
    logger.info("Provider=%s model=%s", provider, model)
    logger.info(
        "Headless=%s max_steps=%s max_runtime_seconds=%s step_timeout=%s stall_window=%s",
        (not args.show_browser),
        args.max_steps,
        args.max_runtime_seconds,
        args.step_timeout,
        args.stall_window,
    )
    logger.info("user_data_dir=%s profile_directory=%s", runtime_user_data_dir, args.profile_directory)

    dataset = read_json(dataset_path)
    _task, _num_examples, _prompt_id, prompts = _validate_dataset(dataset)
    selected_prompts = prompts[: args.max_prompts] if args.max_prompts else prompts

    if args.overwrite:
        traces: list[dict[str, Any]] = []
        costs_by_trace: dict[str, Any] = {}
    else:
        loaded = _load_json_if_exists(output_path, [])
        if not isinstance(loaded, list):
            raise SystemExit(f"Existing output must be a JSON list: {output_path}")
        traces = [item for item in loaded if isinstance(item, dict) and str(item.get('id') or '').strip()]
        loaded_costs = _load_json_if_exists(costs_path, {})
        costs_by_trace = loaded_costs if isinstance(loaded_costs, dict) else {}

    seen_ids = {str(item.get("id")) for item in traces}
    logger.info("Resume mode: %s existing traces: %s", (not args.overwrite), len(seen_ids))

    started_wall = time.time()
    generated = 0
    skipped = 0
    failed = 0
    track_cost = not bool(args.no_cost_tracking)

    try:
        for offset, prompt_row in enumerate(selected_prompts):
            prompt_text = str(prompt_row.get("text") or "").strip()
            if not prompt_text:
                logger.warning("Skipping empty prompt at index %s", offset)
                skipped += 1
                continue

            example_number = _example_number(prompt_row, offset + 1)
            trace_id = f"trace_{example_number:03d}"
            if trace_id in seen_ids:
                logger.info("Skipping %s (already present)", trace_id)
                skipped += 1
                continue

            logger.info("[%s/%s] starting %s", offset + 1, len(selected_prompts), trace_id)
            trace_entry, usage_summary = run_single_prompt_awo_sync(
                prompt_text=prompt_text,
                trace_id=trace_id,
                model=model,
                provider=provider,
                max_steps=args.max_steps,
                max_runtime_seconds=args.max_runtime_seconds,
                step_timeout=args.step_timeout,
                stall_window=args.stall_window,
                headless=not args.show_browser,
                user_data_dir=runtime_user_data_dir,
                profile_directory=args.profile_directory,
                track_cost=track_cost,
                logger=logger,
            )

            traces.append(trace_entry)
            traces.sort(key=_trace_sort_key)
            seen_ids.add(trace_id)
            _write_json_atomic(output_path, traces)

            if track_cost:
                costs_by_trace[trace_id] = {
                    "usage": usage_summary,
                    "taskSuccess": bool(trace_entry.get("taskSuccess")),
                    "eventCount": len(trace_entry.get("events") or []),
                }
                _write_json_atomic(costs_path, costs_by_trace)

            if trace_entry.get("taskSuccess"):
                generated += 1
            else:
                failed += 1

            cost_value = None
            if isinstance(usage_summary, dict):
                cost_value = usage_summary.get("total_cost")
            logger.info(
                "finished %s success=%s events=%s total_cost=%s",
                trace_id,
                bool(trace_entry.get("taskSuccess")),
                len(trace_entry.get("events") or []),
                cost_value,
            )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user; resume is supported from existing output.")

    elapsed = time.time() - started_wall
    logger.info("Done. elapsed=%.1fs generated=%s failed=%s skipped=%s", elapsed, generated, failed, skipped)
    logger.info("Trace file: %s", output_path)
    if track_cost:
        logger.info("Costs file: %s", costs_path)


if __name__ == "__main__":
    main()
