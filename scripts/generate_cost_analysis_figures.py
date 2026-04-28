#!/usr/bin/env python3
import json
import math
import os
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE_DIR = ROOT / "awo_browser_traces"
OUT_DIR = ROOT / "cost_analysis"

FILES = {
    "Yelp Search Task": TRACE_DIR / "yelp_search_50_p008.costs.json",
    "Google Flights Task": TRACE_DIR / "google_flights_48_p009.costs.json",
}


def money(x: float) -> str:
    return f"${x:,.2f}"


def load_data():
    rows = []
    for task_name, path in FILES.items():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for trace_id, trace in sorted(data.items()):
            usage = trace.get("usage", {})
            rows.append(
                {
                    "task": task_name,
                    "trace_id": trace_id,
                    "total_cost": float(usage.get("total_cost", 0.0)),
                    "prompt_cost": float(usage.get("total_prompt_cost", 0.0)),
                    "completion_cost": float(usage.get("total_completion_cost", 0.0)),
                    "cached_cost": float(usage.get("total_prompt_cached_cost", 0.0)),
                    "success": bool(trace.get("taskSuccess", False)),
                }
            )
    return rows


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def save_svg(path: Path, content: str):
    path.write_text(content, encoding="utf-8")


def svg_header(width: int, height: int, title: str):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f8fafc"/>\n'
        f'<text x="30" y="42" font-family="Helvetica,Arial,sans-serif" font-size="26" font-weight="700" fill="#0f172a">{title}</text>\n'
    )


def svg_footer():
    return "</svg>\n"


def axis_y_labels(min_v, max_v, left, top, plot_h, ticks=6):
    parts = []
    for i in range(ticks + 1):
        t = i / ticks
        v = min_v + (max_v - min_v) * t
        y = top + plot_h - t * plot_h
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left+760}" y2="{y:.2f}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(
            f'<text x="{left-10}" y="{y+4:.2f}" text-anchor="end" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#334155">{money(v)}</text>'
        )
    return "\n".join(parts)


def figure_average_by_task(rows, out_path: Path):
    groups = {}
    for r in rows:
        groups.setdefault(r["task"], []).append(r["total_cost"])
    groups["All Tasks Combined"] = [r["total_cost"] for r in rows]

    order = ["Yelp Search Task", "Google Flights Task", "All Tasks Combined"]
    stats = {}
    for k in order:
        vals = groups[k]
        stats[k] = {
            "mean": statistics.mean(vals),
            "median": statistics.median(vals),
            "min": min(vals),
            "max": max(vals),
            "n": len(vals),
        }

    width, height = 980, 640
    left, top, plot_w, plot_h = 140, 100, 760, 420
    max_v = max(v["max"] for v in stats.values()) * 1.12
    min_v = 0.0

    def y_of(v):
        return top + plot_h - (v - min_v) / (max_v - min_v) * plot_h

    parts = [svg_header(width, height, "Average Cost Per Browser-Use Task (USD)")]
    parts.append('<text x="30" y="68" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#334155">Each bar is the average dollar cost to complete one task run. Black line shows the full observed range.</text>')
    parts.append(axis_y_labels(min_v, max_v, left, top, plot_h, ticks=6))

    bar_w = 140
    gap = 90
    x0 = left + 70
    colors = ["#2563eb", "#0891b2", "#16a34a"]

    for i, name in enumerate(order):
        s = stats[name]
        x = x0 + i * (bar_w + gap)
        y_mean = y_of(s["mean"])
        y_min = y_of(s["min"])
        y_max = y_of(s["max"])

        parts.append(f'<line x1="{x + bar_w/2}" y1="{y_max:.2f}" x2="{x + bar_w/2}" y2="{y_min:.2f}" stroke="#0f172a" stroke-width="3"/>')
        parts.append(f'<line x1="{x + 25}" y1="{y_max:.2f}" x2="{x + bar_w - 25}" y2="{y_max:.2f}" stroke="#0f172a" stroke-width="3"/>')
        parts.append(f'<line x1="{x + 25}" y1="{y_min:.2f}" x2="{x + bar_w - 25}" y2="{y_min:.2f}" stroke="#0f172a" stroke-width="3"/>')

        parts.append(
            f'<rect x="{x}" y="{y_mean:.2f}" width="{bar_w}" height="{top + plot_h - y_mean:.2f}" fill="{colors[i]}" rx="6"/>'
        )
        parts.append(f'<text x="{x + bar_w/2}" y="{y_mean - 10:.2f}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="14" font-weight="700" fill="#0f172a">{money(s["mean"])} avg</text>')
        parts.append(f'<text x="{x + bar_w/2}" y="{top + plot_h + 28}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#0f172a">{name}</text>')
        parts.append(f'<text x="{x + bar_w/2}" y="{top + plot_h + 46}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#475569">{s["n"]} runs</text>')

    parts.append('<text x="30" y="585" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#334155">Interpretation: most runs cost well under $1, but harder tasks can approach $1 per run.</text>')
    parts.append(svg_footer())
    save_svg(out_path, "\n".join(parts))


def figure_cost_distribution(rows, out_path: Path):
    values = sorted([r["total_cost"] for r in rows])
    width, height = 980, 640
    left, top, plot_w, plot_h = 120, 110, 790, 400
    min_v = math.floor(min(values) * 20) / 20.0
    max_v = math.ceil(max(values) * 20) / 20.0
    bin_w = 0.05
    bins = []
    b = min_v
    while b < max_v + 1e-9:
        bins.append((b, b + bin_w))
        b += bin_w

    counts = []
    for lo, hi in bins:
        c = sum(1 for v in values if (lo <= v < hi) or (hi >= max_v and lo <= v <= hi))
        counts.append(c)

    max_count = max(counts) if counts else 1

    def x_of(v):
        return left + (v - min_v) / (max_v - min_v) * plot_w

    def y_of(c):
        return top + plot_h - c / max_count * plot_h

    p50 = statistics.median(values)
    p90 = sorted(values)[int(0.9 * (len(values) - 1))]

    parts = [svg_header(width, height, "How Often Different Per-Task Costs Occur")]
    parts.append('<text x="30" y="70" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#334155">Histogram of all 80 task runs. Taller bars mean that price range happened more often.</text>')

    for i in range(6):
        y = top + plot_h - i / 5 * plot_h
        c = round(i / 5 * max_count)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left+plot_w}" y2="{y:.2f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{left-10}" y="{y+4:.2f}" text-anchor="end" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#475569">{c}</text>')

    for (lo, hi), c in zip(bins, counts):
        x = x_of(lo) + 1
        w = max(1.0, x_of(hi) - x_of(lo) - 2)
        y = y_of(c)
        h = top + plot_h - y
        parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="#2563eb" opacity="0.80"/>')

    for i in range(9):
        t = i / 8
        v = min_v + t * (max_v - min_v)
        x = x_of(v)
        parts.append(f'<line x1="{x:.2f}" y1="{top+plot_h}" x2="{x:.2f}" y2="{top+plot_h+6}" stroke="#334155"/>')
        parts.append(f'<text x="{x:.2f}" y="{top+plot_h+24}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#334155">{money(v)}</text>')

    for label, val, color in [("Median", p50, "#dc2626"), ("90th percentile", p90, "#7c3aed")]:
        x = x_of(val)
        parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top+plot_h}" stroke="{color}" stroke-width="3" stroke-dasharray="7,5"/>')
        parts.append(f'<text x="{x+8:.2f}" y="{top+24}" font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="700" fill="{color}">{label}: {money(val)}</text>')

    parts.append('<text x="30" y="585" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#334155">Interpretation: most tasks cluster around ~20 to ~60 cents each, with fewer higher-cost runs.</text>')
    parts.append(svg_footer())
    save_svg(out_path, "\n".join(parts))


def figure_scaling(rows, out_path: Path):
    values = sorted([r["total_cost"] for r in rows])
    p25 = values[int(0.25 * (len(values) - 1))]
    p50 = statistics.median(values)
    p90 = values[int(0.9 * (len(values) - 1))]

    task_counts = [10, 25, 50, 100, 250, 500, 1000]
    scenarios = {
        "Lower-cost days (25th percentile)": (p25, "#16a34a"),
        "Typical days (median)": (p50, "#2563eb"),
        "Higher-cost days (90th percentile)": (p90, "#dc2626"),
    }

    width, height = 980, 640
    left, top, plot_w, plot_h = 120, 110, 780, 400
    max_budget = max(task_counts) * p90 * 1.08

    def x_of(n):
        return left + (n - task_counts[0]) / (task_counts[-1] - task_counts[0]) * plot_w

    def y_of(v):
        return top + plot_h - v / max_budget * plot_h

    parts = [svg_header(width, height, "How Total Spend Scales With Number of Tasks")]
    parts.append('<text x="30" y="70" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#334155">If cost per task stays similar, total spend grows almost linearly as you run more tasks.</text>')

    for i in range(6):
        t = i / 5
        v = t * max_budget
        y = y_of(v)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left+plot_w}" y2="{y:.2f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{left-10}" y="{y+4:.2f}" text-anchor="end" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#475569">{money(v)}</text>')

    for n in task_counts:
        x = x_of(n)
        parts.append(f'<line x1="{x:.2f}" y1="{top+plot_h}" x2="{x:.2f}" y2="{top+plot_h+6}" stroke="#334155"/>')
        parts.append(f'<text x="{x:.2f}" y="{top+plot_h+24}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#334155">{n}</text>')

    for label, (unit_cost, color) in scenarios.items():
        points = []
        for n in task_counts:
            x, y = x_of(n), y_of(unit_cost * n)
            points.append((x, y))
        path = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        parts.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="3"/>')
        for x, y in points:
            parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"/>')

        lx, ly = points[-1]
        parts.append(f'<text x="{lx + 10:.2f}" y="{ly+4:.2f}" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="{color}">{label} ({money(unit_cost)}/task)</text>')

    n = 100
    typ_100 = p50 * n
    parts.append(f'<rect x="{left+20}" y="{top+15}" width="340" height="56" rx="8" fill="#ffffff" stroke="#cbd5e1"/>')
    parts.append(f'<text x="{left+34}" y="{top+38}" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#0f172a">Example: {n} typical tasks ≈ {money(typ_100)}</text>')
    parts.append(f'<text x="{left+34}" y="{top+58}" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#475569">(using the median observed cost per task)</text>')

    parts.append('<text x="30" y="585" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#334155">Interpretation: doubling task volume roughly doubles total spend.</text>')
    parts.append(svg_footer())
    save_svg(out_path, "\n".join(parts))


def figure_cost_components(rows, out_path: Path):
    # Note: cached prompt cost is a discount component. We show it as "cache savings".
    by_task = {}
    for r in rows:
        by_task.setdefault(r["task"], []).append(r)

    labels = ["Yelp Search Task", "Google Flights Task", "All Tasks Combined"]
    data = {}
    for label in labels:
        rs = rows if label == "All Tasks Combined" else by_task[label]
        data[label] = {
            "prompt": statistics.mean(r["prompt_cost"] for r in rs),
            "completion": statistics.mean(r["completion_cost"] for r in rs),
            "cache": statistics.mean(r["cached_cost"] for r in rs),
        }

    width, height = 980, 640
    left, top, plot_w, plot_h = 150, 110, 730, 390

    totals = [v["prompt"] + v["completion"] for v in data.values()]
    max_v = max(totals) * 1.18

    def y_of(v):
        return top + plot_h - v / max_v * plot_h

    parts = [svg_header(width, height, "What Makes Up The Cost Per Task")]
    parts.append('<text x="30" y="70" font-family="Helvetica,Arial,sans-serif" font-size="15" fill="#334155">Blue = reading/context cost. Orange = model response cost. Green text shows average cache savings.</text>')

    for i in range(6):
        t = i / 5
        v = t * max_v
        y = y_of(v)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left+plot_w}" y2="{y:.2f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{left-10}" y="{y+4:.2f}" text-anchor="end" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#475569">{money(v)}</text>')

    bar_w = 160
    gap = 75
    x0 = left + 40

    for i, label in enumerate(labels):
        d = data[label]
        x = x0 + i * (bar_w + gap)
        y_prompt = y_of(d["prompt"])
        y_total = y_of(d["prompt"] + d["completion"])

        parts.append(f'<rect x="{x}" y="{y_prompt:.2f}" width="{bar_w}" height="{top+plot_h - y_prompt:.2f}" fill="#3b82f6" rx="6"/>')
        parts.append(f'<rect x="{x}" y="{y_total:.2f}" width="{bar_w}" height="{y_prompt - y_total:.2f}" fill="#f59e0b" rx="6"/>')

        total = d["prompt"] + d["completion"]
        parts.append(f'<text x="{x + bar_w/2}" y="{y_total-9:.2f}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" font-weight="700" fill="#0f172a">{money(total)}</text>')
        parts.append(f'<text x="{x + bar_w/2}" y="{top+plot_h+28}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="13" fill="#0f172a">{label}</text>')
        parts.append(f'<text x="{x + bar_w/2}" y="{top+plot_h+48}" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#16a34a">Avg cache savings: {money(d["cache"])}</text>')

    parts.append('<rect x="705" y="130" width="14" height="14" fill="#3b82f6"/><text x="726" y="142" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#0f172a">Prompt/context cost</text>')
    parts.append('<rect x="705" y="152" width="14" height="14" fill="#f59e0b"/><text x="726" y="164" font-family="Helvetica,Arial,sans-serif" font-size="12" fill="#0f172a">Completion/response cost</text>')

    parts.append('<text x="30" y="585" font-family="Helvetica,Arial,sans-serif" font-size="14" fill="#334155">Interpretation: prompt/context processing drives most of the per-task cost in these traces.</text>')
    parts.append(svg_footer())
    save_svg(out_path, "\n".join(parts))


def write_summary(rows, out_path: Path):
    values = [r["total_cost"] for r in rows]
    yelp = [r["total_cost"] for r in rows if r["task"] == "Yelp Search Task"]
    flights = [r["total_cost"] for r in rows if r["task"] == "Google Flights Task"]

    p10 = sorted(values)[int(0.10 * (len(values) - 1))]
    p50 = statistics.median(values)
    p90 = sorted(values)[int(0.90 * (len(values) - 1))]

    def line_for(n):
        return f"- {n:>4} tasks: low {money(p10*n)}, typical {money(p50*n)}, high {money(p90*n)}"

    text = []
    text.append("# Browser-Use Cost Analysis (Dollar-Focused)")
    text.append("")
    text.append("## Data Used")
    text.append("- Source files: `awo_browser_traces/yelp_search_50_p008.costs.json` and `awo_browser_traces/google_flights_48_p009.costs.json`")
    text.append("- Total runs analyzed: 80 (40 per task type)")
    text.append("")
    text.append("## Key Per-Task Cost Takeaways")
    text.append(f"- Overall average per task: **{money(statistics.mean(values))}**")
    text.append(f"- Overall median per task (typical): **{money(p50)}**")
    text.append(f"- Observed range: **{money(min(values))} to {money(max(values))}**")
    text.append(f"- Yelp average per task: **{money(statistics.mean(yelp))}**")
    text.append(f"- Google Flights average per task: **{money(statistics.mean(flights))}**")
    text.append("")
    text.append("## What This Means For Budgeting")
    text.append("Use these rough planning ranges from observed traces:")
    for n in [10, 50, 100, 500, 1000]:
        text.append(line_for(n))
    text.append("")
    text.append("## Figures")
    text.append("- `01_average_cost_per_task.svg`: average cost with min-max ranges")
    text.append("- `02_cost_distribution_histogram.svg`: frequency of different per-task costs")
    text.append("- `03_budget_scaling_projection.svg`: total spend as task count grows")
    text.append("- `04_cost_components_breakdown.svg`: prompt vs completion cost components")

    out_path.write_text("\n".join(text) + "\n", encoding="utf-8")


def main():
    ensure_dir(OUT_DIR)
    rows = load_data()

    figure_average_by_task(rows, OUT_DIR / "01_average_cost_per_task.svg")
    figure_cost_distribution(rows, OUT_DIR / "02_cost_distribution_histogram.svg")
    figure_scaling(rows, OUT_DIR / "03_budget_scaling_projection.svg")
    figure_cost_components(rows, OUT_DIR / "04_cost_components_breakdown.svg")
    write_summary(rows, OUT_DIR / "README.md")

    print(f"Wrote figures and summary to: {OUT_DIR}")


if __name__ == "__main__":
    main()
