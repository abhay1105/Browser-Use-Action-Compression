#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


IGNORED_ACTION_KEYS = {"interacted_element", "_meta"}


@dataclass
class TraceRecord:
    task_id: str
    url: str | None
    goal: str
    model_actions: list[dict[str, Any]]
    action_names: list[str]
    task_path: Path
    trace_path: Path


@dataclass
class Interval:
    start: int
    end: int
    weight: int
    macro_name: str
    trace_id: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use an LLM to mine macro API docs from browser trace folders and analyze compression impact.",
    )
    parser.add_argument(
        "--trace-folder",
        required=True,
        help="Path to folder containing trace examples (for example browser_traces/.../run_001)",
    )
    parser.add_argument(
        "--output-root",
        default="macros/llm_generated",
        help="Output root for generated docs and analysis",
    )
    parser.add_argument(
        "--replace-output",
        action="store_true",
        help="Delete existing output folder before writing new artifacts",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic"],
        default="anthropic",
        help="LLM provider",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="LLM model for macro mining",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="LLM temperature",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4000,
        help="Max tokens for LLM output",
    )
    parser.add_argument(
        "--max-traces",
        type=int,
        default=None,
        help="Optional cap on traces sent to LLM",
    )
    parser.add_argument(
        "--max-actions-per-trace",
        type=int,
        default=80,
        help="Optional cap on actions sent to LLM per trace",
    )
    parser.add_argument(
        "--max-string-len",
        type=int,
        default=180,
        help="Truncate long string fields to this length in LLM payload",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip LLM call and derive draft macros heuristically (useful for local debugging).",
    )
    return parser.parse_args()


def sanitize_scalar(value: Any, max_string_len: int) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if len(text) > max_string_len:
            return text[: max_string_len - 3] + "..."
        return text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:max_string_len]


def sanitize_value(value: Any, max_string_len: int, depth: int = 0) -> Any:
    if depth > 4:
        return sanitize_scalar(value, max_string_len)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= 30:
                out["__truncated__"] = True
                break
            out[str(k)] = sanitize_value(v, max_string_len, depth + 1)
        return out
    if isinstance(value, list):
        out_list = [sanitize_value(v, max_string_len, depth + 1) for v in value[:20]]
        if len(value) > 20:
            out_list.append({"__truncated__": True})
        return out_list
    return sanitize_scalar(value, max_string_len)


def extract_action_name_and_input(action_item: Any, max_string_len: int) -> tuple[str, dict[str, Any]]:
    if not isinstance(action_item, dict):
        return "unknown_action", {"value": sanitize_scalar(action_item, max_string_len)}

    if isinstance(action_item.get("name"), str):
        raw_input = action_item.get("input")
        return action_item["name"].strip().lower(), (
            sanitize_value(raw_input, max_string_len) if isinstance(raw_input, dict) else {}
        )

    candidate_keys = [k for k in action_item.keys() if k not in IGNORED_ACTION_KEYS]
    if not candidate_keys:
        return "unknown_action", {}

    if len(candidate_keys) == 1:
        key = candidate_keys[0]
        value = action_item.get(key)
        if isinstance(value, dict):
            return key.strip().lower(), sanitize_value(value, max_string_len)
        return key.strip().lower(), {"value": sanitize_scalar(value, max_string_len)}

    for preferred in (
        "navigate",
        "find",
        "input",
        "click",
        "wait",
        "scroll",
        "extract_content",
        "replace_file",
        "write_file",
        "done",
    ):
        if preferred in action_item:
            value = action_item.get(preferred)
            if isinstance(value, dict):
                return preferred, sanitize_value(value, max_string_len)
            return preferred, {"value": sanitize_scalar(value, max_string_len)}

    key = sorted(candidate_keys)[0]
    value = action_item.get(key)
    if isinstance(value, dict):
        return key.strip().lower(), sanitize_value(value, max_string_len)
    return key.strip().lower(), {"value": sanitize_scalar(value, max_string_len)}


def find_trace_pairs(trace_folder: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for trace_path in sorted(trace_folder.rglob("trace.json")):
        task_path = trace_path.parent / "task.json"
        if task_path.exists():
            pairs.append((task_path, trace_path))
    return pairs


def load_trace_records(
    *,
    trace_folder: Path,
    max_traces: int | None,
    max_actions_per_trace: int,
    max_string_len: int,
) -> list[TraceRecord]:
    records: list[TraceRecord] = []
    for task_path, trace_path in find_trace_pairs(trace_folder):
        task_payload = json.loads(task_path.read_text(encoding="utf-8"))
        trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
        task_obj = task_payload.get("task") if isinstance(task_payload.get("task"), dict) else {}
        result = trace_payload.get("result") if isinstance(trace_payload.get("result"), dict) else {}
        browser_use = result.get("browser_use") if isinstance(result.get("browser_use"), dict) else {}
        raw_actions = browser_use.get("model_actions")
        model_actions = raw_actions if isinstance(raw_actions, list) else []

        compact_actions: list[dict[str, Any]] = []
        action_names: list[str] = []
        for action_item in model_actions[:max_actions_per_trace]:
            action_name, action_input = extract_action_name_and_input(action_item, max_string_len)
            compact_actions.append({"action": action_name, "input": action_input})
            action_names.append(action_name)

        task_id = str(trace_payload.get("taskId") or task_obj.get("id") or trace_path.parent.name)
        url = task_obj.get("url") or trace_payload.get("url")
        goal = str(task_obj.get("goal") or task_obj.get("taskPrompt") or task_payload.get("goal") or "").strip()

        records.append(
            TraceRecord(
                task_id=task_id,
                url=str(url) if isinstance(url, str) and url.strip() else None,
                goal=goal,
                model_actions=compact_actions,
                action_names=action_names,
                task_path=task_path,
                trace_path=trace_path,
            )
        )

        if max_traces is not None and len(records) >= max_traces:
            break

    return records


def traces_for_prompt(records: list[TraceRecord]) -> list[dict[str, Any]]:
    return [
        {
            "task_id": record.task_id,
            "url": record.url,
            "goal": record.goal,
            "model_actions": record.model_actions,
        }
        for record in records
    ]


def build_prompt(dataset_rows: list[dict[str, Any]]) -> str:
    return (
        "You are an expert in browser-agent action compression.\n"
        "Given multiple task traces, mine reusable macros that reduce repeated action calls.\n\n"
        "Return STRICT JSON (no markdown) with this exact top-level shape:\n"
        "{\n"
        "  \"macros\": [\n"
        "    {\n"
        "      \"name\": \"string\",\n"
        "      \"description\": \"string\",\n"
        "      \"one_shot_example\": \"string\",\n"
        "      \"when_to_use\": \"string\",\n"
        "      \"parameters\": [\n"
        "        {\n"
        "          \"name\": \"string\",\n"
        "          \"type\": \"string\",\n"
        "          \"meaning\": \"string\",\n"
        "          \"required\": true,\n"
        "          \"example\": \"string\"\n"
        "        }\n"
        "      ],\n"
        "      \"action_sequence_pattern\": [\"action_name_1\", \"action_name_2\"],\n"
        "      \"confidence\": 0.0\n"
        "    }\n"
        "  ],\n"
        "  \"notes\": \"string\"\n"
        "}\n\n"
        "Requirements:\n"
        "- Macros must be semantically meaningful and task-context aware (not just naive action-name chains).\n"
        "- Prefer reusable workflows for this dataset's goals and websites.\n"
        "- Parameter meanings should be practical and human-usable.\n"
        "- `action_sequence_pattern` should be concrete action names from traces for compression estimation.\n"
        "- Macros should be simple and consist of 2-4 actions at most for best reusability and compression.\n"
        "- Return between 3-5 of the most important macros.\n\n"
        "Trace data:\n"
        f"{json.dumps(dataset_rows, indent=2)}"
    )


def extract_json_blob(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def call_claude(prompt: str, model: str, temperature: float, max_tokens: int) -> tuple[str, dict[str, Any]]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for LLM macro mining.")
    try:
        from anthropic import Anthropic
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("anthropic package is required. Install it in your environment.") from exc

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(
        block.text
        for block in getattr(response, "content", []) or []
        if getattr(block, "type", "") == "text"
    ).strip()
    parsed = extract_json_blob(text)
    if not isinstance(parsed, dict):
        raise RuntimeError("Failed to parse Claude response as JSON.")
    return text, parsed


def heuristic_macros(records: list[TraceRecord]) -> dict[str, Any]:
    sequences = [record.action_names for record in records if len(record.action_names) >= 3]
    counter: Counter[tuple[str, str, str]] = Counter()
    for seq in sequences:
        for idx in range(len(seq) - 2):
            counter[(seq[idx], seq[idx + 1], seq[idx + 2])] += 1

    macros = []
    for i, (pattern, count) in enumerate(counter.most_common(8), start=1):
        macros.append(
            {
                "name": f"macro_{i:03d}_{'_'.join(pattern)}",
                "description": (
                    "Heuristic macro generated without LLM. "
                    f"Represents a frequent 3-step action flow: {' -> '.join(pattern)}."
                ),
                "one_shot_example": "Use when the task needs this frequent action flow in sequence.",
                "when_to_use": "When the same sequence appears repeatedly in similar browsing contexts.",
                "parameters": [],
                "action_sequence_pattern": list(pattern),
                "confidence": min(0.95, 0.4 + (count / 20.0)),
            }
        )

    return {"macros": macros, "notes": "Generated via heuristic fallback (--skip-llm)."}


def sanitize_macro_docs(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    raw_macros = parsed.get("macros")
    if not isinstance(raw_macros, list):
        return []

    clean: list[dict[str, Any]] = []
    for idx, macro in enumerate(raw_macros, start=1):
        if not isinstance(macro, dict):
            continue
        name = str(macro.get("name") or f"macro_{idx:03d}").strip()
        description = str(macro.get("description") or "").strip()
        one_shot_example = str(macro.get("one_shot_example") or "").strip()
        when_to_use = str(macro.get("when_to_use") or "").strip()

        params = macro.get("parameters")
        clean_params: list[dict[str, Any]] = []
        if isinstance(params, list):
            for param in params:
                if not isinstance(param, dict):
                    continue
                clean_params.append(
                    {
                        "name": str(param.get("name") or "").strip(),
                        "type": str(param.get("type") or "string").strip(),
                        "meaning": str(param.get("meaning") or "").strip(),
                        "required": bool(param.get("required", True)),
                        "example": param.get("example"),
                    }
                )

        pattern = macro.get("action_sequence_pattern")
        clean_pattern = []
        if isinstance(pattern, list):
            clean_pattern = [str(item).strip().lower() for item in pattern if str(item).strip()]

        confidence_raw = macro.get("confidence", 0.5)
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = 0.5

        clean.append(
            {
                "name": re.sub(r"\s+", "_", name).strip("_"),
                "description": description,
                "one_shot_example": one_shot_example,
                "when_to_use": when_to_use,
                "parameters": clean_params,
                "action_sequence_pattern": clean_pattern,
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
    return clean


def find_pattern_intervals(action_names: list[str], pattern: list[str], macro_name: str, trace_id: str) -> list[Interval]:
    intervals: list[Interval] = []
    if not pattern or len(pattern) <= 1:
        return intervals
    m = len(pattern)
    for start in range(len(action_names) - m + 1):
        if action_names[start : start + m] == pattern:
            intervals.append(
                Interval(
                    start=start,
                    end=start + m,
                    weight=m - 1,
                    macro_name=macro_name,
                    trace_id=trace_id,
                )
            )
    return intervals


def select_intervals_max_weight(intervals: list[Interval]) -> list[Interval]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: (x.end, x.start))
    n = len(intervals)
    prev = [-1] * n
    ends = [item.end for item in intervals]
    for i in range(n):
        lo, hi = 0, i - 1
        best = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if ends[mid] <= intervals[i].start:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        prev[i] = best

    dp = [0] * n
    choose = [False] * n

    for i in range(n):
        include = intervals[i].weight + (dp[prev[i]] if prev[i] >= 0 else 0)
        exclude = dp[i - 1] if i > 0 else 0
        if include > exclude:
            dp[i] = include
            choose[i] = True
        else:
            dp[i] = exclude

    selected: list[Interval] = []
    i = n - 1
    while i >= 0:
        if choose[i]:
            selected.append(intervals[i])
            i = prev[i]
        else:
            i -= 1
    selected.reverse()
    return selected


def compression_analysis(records: list[TraceRecord], macros: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_calls = sum(len(record.action_names) for record in records)
    selected_by_trace: dict[str, list[Interval]] = {}
    macro_usage_counter: Counter[str] = Counter()
    macro_saved_counter: Counter[str] = Counter()

    for record in records:
        intervals: list[Interval] = []
        for macro in macros:
            pattern = macro.get("action_sequence_pattern")
            if not isinstance(pattern, list):
                continue
            clean_pattern = [str(name).strip().lower() for name in pattern if str(name).strip()]
            intervals.extend(
                find_pattern_intervals(record.action_names, clean_pattern, macro.get("name", "unnamed_macro"), record.task_id)
            )
        chosen = select_intervals_max_weight(intervals)
        selected_by_trace[record.task_id] = chosen
        for interval in chosen:
            macro_usage_counter[interval.macro_name] += 1
            macro_saved_counter[interval.macro_name] += interval.weight

    total_saved_calls = sum(interval.weight for intervals in selected_by_trace.values() for interval in intervals)
    projected_calls = max(0, baseline_calls - total_saved_calls)
    reduction_pct = (100.0 * total_saved_calls / baseline_calls) if baseline_calls else 0.0
    compression_ratio = (baseline_calls / projected_calls) if projected_calls else float("inf")

    macro_rows = []
    for macro in macros:
        macro_name = macro.get("name", "unnamed_macro")
        macro_rows.append(
            {
                "name": macro_name,
                "estimated_uses": int(macro_usage_counter.get(macro_name, 0)),
                "estimated_saved_calls": int(macro_saved_counter.get(macro_name, 0)),
                "action_sequence_pattern": macro.get("action_sequence_pattern", []),
            }
        )
    macro_rows.sort(key=lambda row: row["estimated_saved_calls"], reverse=True)

    trace_rows = []
    for record in records:
        saved = sum(item.weight for item in selected_by_trace.get(record.task_id, []))
        trace_rows.append(
            {
                "task_id": record.task_id,
                "baseline_calls": len(record.action_names),
                "projected_calls": max(0, len(record.action_names) - saved),
                "saved_calls": saved,
                "selected_macros": [
                    {
                        "name": item.macro_name,
                        "start": item.start,
                        "end": item.end,
                        "saved_calls": item.weight,
                    }
                    for item in selected_by_trace.get(record.task_id, [])
                ],
            }
        )

    return {
        "baseline_calls": baseline_calls,
        "projected_calls": projected_calls,
        "estimated_saved_calls": total_saved_calls,
        "estimated_reduction_pct": round(reduction_pct, 3),
        "estimated_compression_ratio": round(compression_ratio, 6) if math_is_finite(compression_ratio) else None,
        "macro_estimates": macro_rows,
        "per_trace_estimates": trace_rows,
    }


def math_is_finite(value: float) -> bool:
    try:
        return value == value and value not in (float("inf"), float("-inf"))
    except Exception:
        return False


def write_svg_bar_chart(
    output_path: Path,
    title: str,
    labels: list[str],
    values: list[float],
    *,
    color: str = "#2f6fed",
) -> None:
    width = 1280
    height = 720
    margin_left = 90
    margin_right = 40
    margin_top = 70
    margin_bottom = 240

    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_value = max(values) if values else 1.0
    if max_value <= 0:
        max_value = 1.0

    n = max(1, len(values))
    bar_w = plot_w / n
    bars = []
    label_nodes = []
    for i, value in enumerate(values):
        x = margin_left + i * bar_w
        h = (value / max_value) * plot_h
        y = margin_top + plot_h - h
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(bar_w - 8, 2):.2f}" height="{h:.2f}" fill="{color}" />'
        )
        label = labels[i] if i < len(labels) else str(i)
        if len(label) > 32:
            label = label[:29] + "..."
        safe_label = (
            label.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        label_x = x + max(bar_w - 8, 2) / 2
        label_nodes.append(
            f'<text x="{label_x:.2f}" y="{height - margin_bottom + 20}" font-size="11" '
            f'text-anchor="end" transform="rotate(-45 {label_x:.2f},{height - margin_bottom + 20})">{safe_label}</text>'
        )

    y_ticks = []
    for i in range(6):
        frac = i / 5
        y = margin_top + plot_h - frac * plot_h
        v = max_value * frac
        y_ticks.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" stroke="#d0d7de"/>')
        y_ticks.append(
            f'<text x="{margin_left - 12}" y="{y + 4:.2f}" font-size="11" text-anchor="end">{v:.1f}</text>'
        )

    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2:.1f}" y="34" text-anchor="middle" font-size="20" font-family="Arial">{safe_title}</text>',
        *y_ticks,
        f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{width - margin_right}" y2="{margin_top + plot_h}" stroke="#333"/>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#333"/>',
        *bars,
        *label_nodes,
        "</svg>",
    ]
    output_path.write_text("\n".join(svg), encoding="utf-8")


def build_trace_length_histogram(records: list[TraceRecord], bins: int = 8) -> tuple[list[str], list[float]]:
    lengths = [len(record.action_names) for record in records]
    if not lengths:
        return ["0"], [0.0]
    min_len = min(lengths)
    max_len = max(lengths)
    if min_len == max_len:
        return [f"{min_len}"], [float(len(lengths))]
    width = max(1, (max_len - min_len + 1) // bins)
    buckets: Counter[str] = Counter()
    for length in lengths:
        start = min_len + ((length - min_len) // width) * width
        end = min(start + width - 1, max_len)
        label = f"{start}-{end}"
        buckets[label] += 1
    labels = sorted(buckets.keys(), key=lambda x: int(x.split("-")[0]))
    values = [float(buckets[label]) for label in labels]
    return labels, values


def derive_output_dir(trace_folder: Path, output_root: Path) -> Path:
    parts = list(trace_folder.parts)
    if "browser_traces" in parts:
        idx = parts.index("browser_traces")
        tail = parts[idx + 1 :]
        if tail:
            return output_root.joinpath(*tail)
    fallback = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(trace_folder).strip()).strip("_") or "trace_folder"
    return output_root / fallback


def write_markdown_report(
    output_path: Path,
    records: list[TraceRecord],
    macros: list[dict[str, Any]],
    compression: dict[str, Any],
) -> None:
    lines = [
        "# LLM Macro Mining Report",
        "",
        f"- Generated at: `{now_iso()}`",
        f"- Traces analyzed: `{len(records)}`",
        f"- Macros generated: `{len(macros)}`",
        f"- Baseline calls: `{compression.get('baseline_calls', 0)}`",
        f"- Projected calls: `{compression.get('projected_calls', 0)}`",
        f"- Estimated saved calls: `{compression.get('estimated_saved_calls', 0)}`",
        f"- Estimated reduction: `{compression.get('estimated_reduction_pct', 0)}%`",
        "",
        "## Macro docs",
        "",
    ]
    for macro in macros:
        lines.extend(
            [
                f"### {macro.get('name', 'unnamed_macro')}",
                f"- Description: {macro.get('description', '')}",
                f"- When to use: {macro.get('when_to_use', '')}",
                f"- One-shot example: {macro.get('one_shot_example', '')}",
                f"- Action pattern: `{' -> '.join(macro.get('action_sequence_pattern', []))}`",
                "",
            ]
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    # Ensure keys in project .env are available even when script is run from another cwd.
    project_env = Path(__file__).resolve().parent / ".env"
    if project_env.exists():
        load_dotenv(dotenv_path=project_env, override=False)
    else:
        load_dotenv(override=False)

    args = parse_args()
    trace_folder = Path(args.trace_folder).resolve()
    if not trace_folder.exists():
        raise SystemExit(f"Trace folder not found: {trace_folder}")

    records = load_trace_records(
        trace_folder=trace_folder,
        max_traces=args.max_traces,
        max_actions_per_trace=args.max_actions_per_trace,
        max_string_len=args.max_string_len,
    )
    if not records:
        raise SystemExit("No usable trace pairs with model_actions found in the provided folder.")

    output_root = Path(args.output_root).resolve()
    output_dir = derive_output_dir(trace_folder, output_root)
    if args.replace_output and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    llm_rows = traces_for_prompt(records)
    prompt = build_prompt(llm_rows)

    # Save assembled dataset and prompt for reproducibility.
    (output_dir / "trace_dataset.json").write_text(json.dumps(llm_rows, indent=2), encoding="utf-8")
    (output_dir / "macro_prompt.txt").write_text(prompt, encoding="utf-8")

    print(f"Loaded traces: {len(records)}", flush=True)
    print(f"Output dir: {output_dir}", flush=True)

    if args.skip_llm:
        parsed = heuristic_macros(records)
        raw_response_text = json.dumps(parsed, indent=2)
        print("Using heuristic fallback (--skip-llm).", flush=True)
    else:
        print(f"Calling {args.provider}:{args.model} for macro mining...", flush=True)
        raw_response_text, parsed = call_claude(
            prompt=prompt,
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        print("LLM response received.", flush=True)

    (output_dir / "llm_raw_response.txt").write_text(raw_response_text, encoding="utf-8")

    macros = sanitize_macro_docs(parsed)
    compression = compression_analysis(records, macros)

    notes = parsed.get("notes")
    macros_payload = {
        "generated_at": now_iso(),
        "source_trace_folder": str(trace_folder),
        "output_folder": str(output_dir),
        "model": {
            "provider": args.provider,
            "name": args.model,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "skip_llm": bool(args.skip_llm),
        },
        "trace_summary": {
            "num_traces": len(records),
            "task_ids": [record.task_id for record in records],
            "urls": sorted({record.url for record in records if record.url}),
            "total_model_actions": sum(len(record.action_names) for record in records),
        },
        "notes": str(notes) if notes is not None else "",
        "macros": macros,
        "compression_analysis": compression,
    }

    macros_json_path = output_dir / "macros.json"
    macros_json_path.write_text(json.dumps(macros_payload, indent=2), encoding="utf-8")

    # Figures
    action_counter = Counter(name for record in records for name in record.action_names)
    top_actions = action_counter.most_common(18)
    write_svg_bar_chart(
        output_dir / "action_frequency_top.svg",
        "Top Action Frequencies",
        [name for name, _ in top_actions] if top_actions else ["none"],
        [float(count) for _, count in top_actions] if top_actions else [0.0],
        color="#1f7a8c",
    )

    hist_labels, hist_values = build_trace_length_histogram(records)
    write_svg_bar_chart(
        output_dir / "trace_action_length_histogram.svg",
        "Trace Action Length Histogram",
        hist_labels,
        hist_values,
        color="#f39c12",
    )

    macro_estimates = compression.get("macro_estimates", [])
    macro_labels = [row.get("name", "macro") for row in macro_estimates[:15]]
    macro_values = [float(row.get("estimated_saved_calls", 0)) for row in macro_estimates[:15]]
    write_svg_bar_chart(
        output_dir / "macro_estimated_saved_calls.svg",
        "Estimated Saved Calls by Macro",
        macro_labels if macro_labels else ["none"],
        macro_values if macro_values else [0.0],
        color="#8e44ad",
    )

    baseline = float(compression.get("baseline_calls", 0))
    projected = float(compression.get("projected_calls", 0))
    write_svg_bar_chart(
        output_dir / "compression_overview.svg",
        "Compression Overview (Calls)",
        ["baseline_calls", "projected_calls"],
        [baseline, projected],
        color="#27ae60",
    )

    # Additional machine-readable analysis.
    (output_dir / "compression_analysis.json").write_text(json.dumps(compression, indent=2), encoding="utf-8")
    write_markdown_report(output_dir / "analysis.md", records, macros, compression)

    print(f"Wrote macro API docs: {macros_json_path}", flush=True)
    print(f"Wrote figures + analysis in: {output_dir}", flush=True)


if __name__ == "__main__":
    main()
