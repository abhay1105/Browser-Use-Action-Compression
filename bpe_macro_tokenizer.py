#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IGNORED_ACTION_KEYS = {
    "interacted_element",
    "_meta",
}


@dataclass
class ActionStep:
    episode_id: str
    step_index: int
    action_name: str
    action_input: dict[str, Any]
    token: str


@dataclass
class MacroCandidate:
    macro_id: str
    symbol: str
    length: int
    support: int
    estimated_saved_calls: int
    sequence_tokens: list[str]
    occurrences: list[tuple[int, int]]
    name: str
    description: str
    parameters: list[dict[str, Any]]
    code_steps: list[dict[str, Any]]
    sample_episode_ids: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mine reusable browser-use macros from trace folders using a BPE-style merge process.",
    )
    parser.add_argument(
        "--trace-run-dir",
        required=True,
        help="Path like browser_traces/<dataset>/run_001",
    )
    parser.add_argument(
        "--output-root",
        default="macros",
        help="Root directory where macro outputs are written",
    )
    parser.add_argument(
        "--num-merges",
        type=int,
        default=80,
        help="Max BPE merge steps",
    )
    parser.add_argument(
        "--min-pair-support",
        type=int,
        default=3,
        help="Minimum pair frequency to continue merging",
    )
    parser.add_argument(
        "--min-macro-support",
        type=int,
        default=3,
        help="Minimum support for final macro candidates",
    )
    parser.add_argument(
        "--min-macro-length",
        type=int,
        default=3,
        help="Minimum primitive action length for a macro",
    )
    parser.add_argument(
        "--max-macros",
        type=int,
        default=20,
        help="Maximum number of macros to output",
    )
    parser.add_argument(
        "--max-occurrence-samples",
        type=int,
        default=30,
        help="Max occurrences sampled per macro when extracting parameters",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use LLM to polish macro name/description/code docs",
    )
    parser.add_argument(
        "--llm-provider",
        choices=["anthropic"],
        default="anthropic",
        help="LLM provider for macro polishing",
    )
    parser.add_argument(
        "--llm-model",
        default="claude-sonnet-4-20250514",
        help="LLM model used for macro polishing",
    )
    parser.add_argument(
        "--llm-max-macros",
        type=int,
        default=10,
        help="Only polish this many top macros with LLM",
    )
    return parser.parse_args()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "item"


def output_subfolder_name(trace_run_dir: Path) -> str:
    parts = [p for p in trace_run_dir.parts]
    if "browser_traces" in parts:
        idx = parts.index("browser_traces")
        tail = parts[idx + 1 :]
    else:
        tail = parts[-2:]
    return safe_slug("_".join(tail))


def find_trace_files(trace_run_dir: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in sorted(trace_run_dir.rglob("trace.json")):
        if candidate.parent.name.isdigit() and len(candidate.parent.name) == 3:
            files.append(candidate)
    return files


def normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def extract_action_name_and_input(action_item: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(action_item, dict):
        return None

    if "name" in action_item and isinstance(action_item.get("name"), str):
        raw_input = action_item.get("input")
        return action_item["name"].strip().lower(), raw_input if isinstance(raw_input, dict) else {}

    candidate_keys = [k for k in action_item.keys() if k not in IGNORED_ACTION_KEYS]
    if not candidate_keys:
        return None

    if len(candidate_keys) == 1:
        key = candidate_keys[0]
        value = action_item.get(key)
        if isinstance(value, dict):
            return key.strip().lower(), value
        return key.strip().lower(), {"value": value}

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
            return preferred, value if isinstance(value, dict) else {"value": value}

    key = sorted(candidate_keys)[0]
    value = action_item.get(key)
    return key.strip().lower(), value if isinstance(value, dict) else {"value": value}


def action_token(action_name: str, action_input: dict[str, Any]) -> str:
    keys = sorted(str(k) for k in action_input.keys())
    key_part = ",".join(keys[:6])
    return f"{action_name}({key_part})" if key_part else f"{action_name}()"


def load_corpus(trace_run_dir: Path) -> tuple[list[list[str]], list[list[ActionStep]], list[str]]:
    episodes_tokens: list[list[str]] = []
    episodes_steps: list[list[ActionStep]] = []
    episode_ids: list[str] = []

    for trace_file in find_trace_files(trace_run_dir):
        payload = json.loads(trace_file.read_text(encoding="utf-8"))
        result = payload.get("result") or {}
        browser_use = result.get("browser_use") or {}
        model_actions = browser_use.get("model_actions") or []
        if not isinstance(model_actions, list):
            continue

        episode_id = str(payload.get("taskId") or trace_file.parent.name)
        steps: list[ActionStep] = []
        tokens: list[str] = []

        for idx, item in enumerate(model_actions):
            parsed = extract_action_name_and_input(item)
            if not parsed:
                continue
            name, inp = parsed
            token = action_token(name, inp)
            steps.append(
                ActionStep(
                    episode_id=episode_id,
                    step_index=idx,
                    action_name=name,
                    action_input=inp,
                    token=token,
                )
            )
            tokens.append(token)

        if tokens:
            episodes_tokens.append(tokens)
            episodes_steps.append(steps)
            episode_ids.append(episode_id)

    return episodes_tokens, episodes_steps, episode_ids


def pair_counts(sequences: list[list[str]]) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for seq in sequences:
        for i in range(len(seq) - 1):
            counts[(seq[i], seq[i + 1])] += 1
    return counts


def apply_merge_to_sequence(seq: list[str], left: str, right: str, merged: str) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(seq)
    while i < n:
        if i + 1 < n and seq[i] == left and seq[i + 1] == right:
            out.append(merged)
            i += 2
        else:
            out.append(seq[i])
            i += 1
    return out


def decompress_symbol(symbol: str, rules: dict[str, tuple[str, str]]) -> list[str]:
    if symbol not in rules:
        return [symbol]
    left, right = rules[symbol]
    return decompress_symbol(left, rules) + decompress_symbol(right, rules)


def run_bpe(
    sequences: list[list[str]],
    *,
    num_merges: int,
    min_pair_support: int,
) -> tuple[list[list[str]], dict[str, tuple[str, str]], list[dict[str, Any]], list[dict[str, Any]]]:
    working = [list(seq) for seq in sequences]
    rules: dict[str, tuple[str, str]] = {}
    merge_stats: list[dict[str, Any]] = []
    compression_curve: list[dict[str, Any]] = []

    def total_length() -> int:
        return sum(len(s) for s in working)

    initial_len = total_length() or 1
    compression_curve.append(
        {
            "step": 0,
            "total_tokens": initial_len,
            "compression_ratio": 1.0,
        }
    )

    for merge_idx in range(1, num_merges + 1):
        counts = pair_counts(working)
        if not counts:
            break
        (best_left, best_right), best_count = counts.most_common(1)[0]
        if best_count < min_pair_support:
            break

        merged_symbol = f"@M{merge_idx:04d}"
        rules[merged_symbol] = (best_left, best_right)

        for i, seq in enumerate(working):
            working[i] = apply_merge_to_sequence(seq, best_left, best_right, merged_symbol)

        merge_stats.append(
            {
                "merge_index": merge_idx,
                "merged_symbol": merged_symbol,
                "left": best_left,
                "right": best_right,
                "pair_count": best_count,
                "expanded_sequence": decompress_symbol(merged_symbol, rules),
            }
        )

        current_len = total_length() or 1
        compression_curve.append(
            {
                "step": merge_idx,
                "total_tokens": current_len,
                "compression_ratio": round(initial_len / current_len, 6),
            }
        )

    return working, rules, merge_stats, compression_curve


def symbol_support(sequences: list[list[str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for seq in sequences:
        counts.update(seq)
    return counts


def find_occurrences(sequence: list[str], pattern: list[str]) -> list[int]:
    starts: list[int] = []
    if not pattern or len(pattern) > len(sequence):
        return starts
    m = len(pattern)
    for i in range(len(sequence) - m + 1):
        if sequence[i : i + m] == pattern:
            starts.append(i)
    return starts


def flatten_dict(data: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten_dict(value, child_key))
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            child_key = f"{prefix}[{idx}]"
            out.update(flatten_dict(value, child_key))
    else:
        out[prefix] = data
    return out


def set_by_path(root: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".") if path else []
    cur: Any = root
    for idx, part in enumerate(parts):
        arr_match = re.fullmatch(r"(.+)\[(\d+)\]", part)
        is_last = idx == len(parts) - 1

        if arr_match:
            key = arr_match.group(1)
            arr_index = int(arr_match.group(2))
            if key not in cur or not isinstance(cur[key], list):
                cur[key] = []
            while len(cur[key]) <= arr_index:
                cur[key].append({})
            if is_last:
                cur[key][arr_index] = value
                return
            if not isinstance(cur[key][arr_index], dict):
                cur[key][arr_index] = {}
            cur = cur[key][arr_index]
            continue

        if is_last:
            cur[part] = value
            return
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]


def infer_macro_schema(
    sequence_steps: list[list[ActionStep]],
    occurrence_refs: list[tuple[int, int]],
    pattern_len: int,
    max_samples: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sampled_refs = occurrence_refs[:max_samples]
    by_position: list[list[dict[str, Any]]] = [[] for _ in range(pattern_len)]

    for episode_idx, start_idx in sampled_refs:
        steps = sequence_steps[episode_idx]
        window = steps[start_idx : start_idx + pattern_len]
        if len(window) != pattern_len:
            continue
        for pos, step in enumerate(window):
            by_position[pos].append(step.action_input)

    parameters: list[dict[str, Any]] = []
    code_steps: list[dict[str, Any]] = []

    for pos, inputs in enumerate(by_position):
        if not inputs:
            continue
        action_name = sequence_steps[sampled_refs[0][0]][sampled_refs[0][1] + pos].action_name
        flat_inputs = [flatten_dict(inp) for inp in inputs]
        all_paths: set[str] = set()
        for flat in flat_inputs:
            all_paths.update(flat.keys())

        template_input: dict[str, Any] = {}

        for path in sorted(all_paths):
            values = [normalize_scalar(flat.get(path)) for flat in flat_inputs if path in flat]
            if not values:
                continue

            unique_values = list(dict.fromkeys(values))
            if len(unique_values) == 1:
                set_by_path(template_input, path, unique_values[0])
                continue

            param_name = safe_slug(f"p{pos + 1}_{path}").lower()
            placeholder = f"{{{{{param_name}}}}}"
            set_by_path(template_input, path, placeholder)
            parameters.append(
                {
                    "name": param_name,
                    "action_index": pos,
                    "path": path,
                    "required": True,
                    "observed_values": unique_values[:10],
                    "description": f"Value for action {pos + 1} ({action_name}) field '{path}'.",
                }
            )

        code_steps.append(
            {
                "action": action_name,
                "input": template_input,
            }
        )

    return parameters, code_steps


def heuristic_macro_name(sequence_tokens: list[str], macro_idx: int) -> str:
    names = [t.split("(")[0] for t in sequence_tokens]
    head = "_".join(names[:3])
    return safe_slug(f"macro_{macro_idx:03d}_{head}").lower()


def heuristic_description(sequence_tokens: list[str], support: int, saved_calls: int) -> str:
    names = [t.split("(")[0] for t in sequence_tokens]
    chain = " -> ".join(names)
    return (
        f"Reusable action chain ({chain}) observed {support} times. "
        f"Estimated primitive-call reduction if used as one macro: {saved_calls}."
    )


def extract_json_blob(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return None
    return None


def polish_macro_with_claude(
    macro: MacroCandidate,
    *,
    model: str,
) -> MacroCandidate:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return macro

    try:
        from anthropic import Anthropic
    except Exception:
        return macro

    client = Anthropic(api_key=api_key)

    payload = {
        "name": macro.name,
        "description": macro.description,
        "parameters": macro.parameters,
        "code_steps": macro.code_steps,
        "sequence_tokens": macro.sequence_tokens,
        "support": macro.support,
        "estimated_saved_calls": macro.estimated_saved_calls,
    }

    prompt = (
        "Polish this browser macro draft for readability and API usability. "
        "Return strict JSON only with keys: name, description, parameters, code_steps. "
        "Do not invent actions not present in code_steps. Keep placeholders unchanged.\n\n"
        f"Draft:\n{json.dumps(payload, indent=2)}"
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1200,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return macro

    text = "".join(
        part.text
        for part in getattr(response, "content", []) or []
        if getattr(part, "type", "") == "text"
    ).strip()
    parsed = extract_json_blob(text)
    if not isinstance(parsed, dict):
        return macro

    new_name = str(parsed.get("name") or macro.name)
    new_desc = str(parsed.get("description") or macro.description)
    new_params = parsed.get("parameters")
    new_steps = parsed.get("code_steps")

    if isinstance(new_params, list):
        macro.parameters = new_params
    if isinstance(new_steps, list):
        macro.code_steps = new_steps

    macro.name = safe_slug(new_name).lower()
    macro.description = new_desc
    return macro


def write_svg_bar_chart(
    output_path: Path,
    title: str,
    labels: list[str],
    values: list[float],
) -> None:
    width = 1280
    height = 720
    margin_left = 80
    margin_right = 40
    margin_top = 60
    margin_bottom = 220

    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    max_value = max(values) if values else 1.0
    if max_value <= 0:
        max_value = 1.0

    bars = []
    n = max(len(values), 1)
    bar_w = plot_w / n

    for i, value in enumerate(values):
        x = margin_left + i * bar_w
        h = (value / max_value) * plot_h
        y = margin_top + plot_h - h
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(bar_w - 6, 2):.2f}" height="{h:.2f}" fill="#2f6fed" />'
        )

    x_labels = []
    for i, label in enumerate(labels):
        x = margin_left + i * bar_w + max(bar_w - 6, 2) / 2
        safe_label = (
            label.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        x_labels.append(
            f'<text x="{x:.2f}" y="{height - margin_bottom + 20}" font-size="11" '
            f'text-anchor="end" transform="rotate(-45 {x:.2f},{height - margin_bottom + 20})">{safe_label}</text>'
        )

    y_ticks = []
    for i in range(6):
        frac = i / 5
        v = max_value * frac
        y = margin_top + plot_h - frac * plot_h
        y_ticks.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" stroke="#d0d7de"/>')
        y_ticks.append(
            f'<text x="{margin_left - 10}" y="{y + 4:.2f}" font-size="11" text-anchor="end">{v:.1f}</text>'
        )

    title_safe = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2:.1f}" y="30" font-size="20" text-anchor="middle" font-family="Arial">{title_safe}</text>',
        *y_ticks,
        f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{width - margin_right}" y2="{margin_top + plot_h}" stroke="#333"/>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#333"/>',
        *bars,
        *x_labels,
        "</svg>",
    ]
    output_path.write_text("\n".join(svg), encoding="utf-8")


def build_markdown_summary(
    *,
    trace_run_dir: Path,
    macro_candidates: list[MacroCandidate],
    merge_stats: list[dict[str, Any]],
    compression_curve: list[dict[str, Any]],
    episodes: int,
    primitive_tokens: int,
) -> str:
    lines = [
        "# BPE Macro Mining Summary",
        "",
        f"- Trace run dir: `{trace_run_dir}`",
        f"- Episodes parsed: `{episodes}`",
        f"- Primitive action tokens: `{primitive_tokens}`",
        f"- BPE merges applied: `{len(merge_stats)}`",
        f"- Macros emitted: `{len(macro_candidates)}`",
        "",
        "## Top macros",
        "",
    ]

    if not macro_candidates:
        lines.append("No macros met thresholds.")
        return "\n".join(lines)

    for macro in macro_candidates[:10]:
        lines.extend(
            [
                f"### {macro.name}",
                f"- Support: `{macro.support}`",
                f"- Sequence length: `{macro.length}`",
                f"- Estimated saved calls: `{macro.estimated_saved_calls}`",
                f"- Description: {macro.description}",
                f"- Sequence: `{' -> '.join(macro.sequence_tokens)}`",
                "",
            ]
        )

    if compression_curve:
        start = compression_curve[0]["total_tokens"]
        end = compression_curve[-1]["total_tokens"]
        lines.extend(
            [
                "## Compression",
                "",
                f"- Tokens before merges: `{start}`",
                f"- Tokens after merges: `{end}`",
                f"- Compression ratio: `{(start / end) if end else 1.0:.3f}`",
                "",
            ]
        )

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    trace_run_dir = Path(args.trace_run_dir).resolve()
    if not trace_run_dir.exists():
        raise SystemExit(f"Trace run directory not found: {trace_run_dir}")

    episodes_tokens, episodes_steps, episode_ids = load_corpus(trace_run_dir)
    if not episodes_tokens:
        raise SystemExit("No model_actions found under the provided trace run directory.")

    primitive_token_count = sum(len(seq) for seq in episodes_tokens)

    print(f"Loaded episodes: {len(episodes_tokens)}", flush=True)
    print(f"Primitive action tokens: {primitive_token_count}", flush=True)

    merged_sequences, rules, merge_stats, compression_curve = run_bpe(
        episodes_tokens,
        num_merges=args.num_merges,
        min_pair_support=args.min_pair_support,
    )

    print(f"BPE merges applied: {len(merge_stats)}", flush=True)

    support = symbol_support(merged_sequences)

    macro_rows: list[MacroCandidate] = []

    for symbol, count in support.items():
        if symbol not in rules:
            continue
        expanded = decompress_symbol(symbol, rules)
        if len(expanded) < args.min_macro_length:
            continue
        if count < args.min_macro_support:
            continue

        occurrences: list[tuple[int, int]] = []
        for epi_idx, sequence in enumerate(episodes_tokens):
            for start in find_occurrences(sequence, expanded):
                occurrences.append((epi_idx, start))

        occ_count = len(occurrences)
        if occ_count < args.min_macro_support:
            continue

        estimated_saved = occ_count * max(len(expanded) - 1, 0)
        macro_idx = len(macro_rows) + 1

        parameters, code_steps = infer_macro_schema(
            episodes_steps,
            occurrences,
            len(expanded),
            args.max_occurrence_samples,
        )

        sample_episode_ids = []
        for epi_idx, _ in occurrences[:8]:
            eid = episode_ids[epi_idx]
            if eid not in sample_episode_ids:
                sample_episode_ids.append(eid)

        macro_rows.append(
            MacroCandidate(
                macro_id=f"M{macro_idx:03d}",
                symbol=symbol,
                length=len(expanded),
                support=occ_count,
                estimated_saved_calls=estimated_saved,
                sequence_tokens=expanded,
                occurrences=occurrences,
                name=heuristic_macro_name(expanded, macro_idx),
                description=heuristic_description(expanded, occ_count, estimated_saved),
                parameters=parameters,
                code_steps=code_steps,
                sample_episode_ids=sample_episode_ids,
            )
        )

    macro_rows.sort(key=lambda x: (x.estimated_saved_calls, x.support, x.length), reverse=True)
    macro_rows = macro_rows[: args.max_macros]

    if args.use_llm and macro_rows:
        print("Polishing top macros with Claude...", flush=True)
        for i, macro in enumerate(macro_rows[: args.llm_max_macros], start=1):
            print(f"  LLM polish {i}/{min(len(macro_rows), args.llm_max_macros)}: {macro.name}", flush=True)
            polished = polish_macro_with_claude(macro, model=args.llm_model)
            macro_rows[i - 1] = polished

    out_root = Path(args.output_root).resolve()
    out_dir = out_root / output_subfolder_name(trace_run_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    macros_json_path = out_dir / "bpe_tokenizer_macros.json"
    merges_json_path = out_dir / "bpe_merges.json"
    summary_path = out_dir / "summary.md"

    macros_payload = {
        "generated_at": now_iso(),
        "trace_run_dir": str(trace_run_dir),
        "settings": {
            "num_merges": args.num_merges,
            "min_pair_support": args.min_pair_support,
            "min_macro_support": args.min_macro_support,
            "min_macro_length": args.min_macro_length,
            "max_macros": args.max_macros,
            "use_llm": bool(args.use_llm),
            "llm_provider": args.llm_provider,
            "llm_model": args.llm_model,
        },
        "stats": {
            "episodes": len(episodes_tokens),
            "primitive_tokens": primitive_token_count,
            "bpe_merges_applied": len(merge_stats),
            "compression_ratio": compression_curve[-1]["compression_ratio"] if compression_curve else 1.0,
        },
        "macros": [
            {
                "id": macro.macro_id,
                "bpe_symbol": macro.symbol,
                "name": macro.name,
                "description": macro.description,
                "support": macro.support,
                "length": macro.length,
                "estimated_saved_calls": macro.estimated_saved_calls,
                "parameters": macro.parameters,
                "sequence_tokens": macro.sequence_tokens,
                "code": {
                    "language": "browser-use-actions-json",
                    "steps": macro.code_steps,
                },
                "sample_episode_ids": macro.sample_episode_ids,
            }
            for macro in macro_rows
        ],
    }

    merges_payload = {
        "generated_at": now_iso(),
        "trace_run_dir": str(trace_run_dir),
        "merge_stats": merge_stats,
        "compression_curve": compression_curve,
    }

    macros_json_path.write_text(json.dumps(macros_payload, indent=2), encoding="utf-8")
    merges_json_path.write_text(json.dumps(merges_payload, indent=2), encoding="utf-8")

    # Visual 1: top merge pair frequencies
    top_merges = merge_stats[:20]
    write_svg_bar_chart(
        out_dir / "bpe_top_merges.svg",
        "Top BPE merge supports",
        [f"{m['merge_index']}:{m['left']}+{m['right']}" for m in top_merges],
        [float(m["pair_count"]) for m in top_merges],
    )

    # Visual 2: macro estimated savings
    top_macros = macro_rows[:20]
    write_svg_bar_chart(
        out_dir / "macro_estimated_savings.svg",
        "Macro estimated saved primitive calls",
        [m.name for m in top_macros],
        [float(m.estimated_saved_calls) for m in top_macros] if top_macros else [0.0],
    )

    # Visual 3: compression curve over merge steps
    write_svg_bar_chart(
        out_dir / "bpe_compression_curve.svg",
        "Compression ratio over BPE merges",
        [str(point["step"]) for point in compression_curve],
        [float(point["compression_ratio"]) for point in compression_curve],
    )

    summary_path.write_text(
        build_markdown_summary(
            trace_run_dir=trace_run_dir,
            macro_candidates=macro_rows,
            merge_stats=merge_stats,
            compression_curve=compression_curve,
            episodes=len(episodes_tokens),
            primitive_tokens=primitive_token_count,
        ),
        encoding="utf-8",
    )

    print(f"Wrote macro JSON: {macros_json_path}", flush=True)
    print(f"Wrote merge details: {merges_json_path}", flush=True)
    print(f"Wrote visuals + summary in: {out_dir}", flush=True)


if __name__ == "__main__":
    main()
