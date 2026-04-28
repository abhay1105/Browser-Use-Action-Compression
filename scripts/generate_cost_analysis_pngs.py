#!/usr/bin/env python3
import json
import statistics
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
TRACE_DIR = ROOT / "awo_browser_traces"
OUT_DIR = ROOT / "cost_analysis"

FILES = {
    "Yelp Search Task": TRACE_DIR / "yelp_search_50_p008.costs.json",
    "Google Flights Task": TRACE_DIR / "google_flights_48_p009.costs.json",
}


def money(x: float) -> str:
    return f"${x:,.2f}"


def load_rows():
    rows = []
    for task, path in FILES.items():
        data = json.loads(path.read_text())
        for trace_id, trace in sorted(data.items()):
            usage = trace.get("usage", {})
            rows.append(
                {
                    "task": task,
                    "trace_id": trace_id,
                    "total_cost": float(usage.get("total_cost", 0.0)),
                    "prompt_cost": float(usage.get("total_prompt_cost", 0.0)),
                    "completion_cost": float(usage.get("total_completion_cost", 0.0)),
                    "cached_cost": float(usage.get("total_prompt_cached_cost", 0.0)),
                }
            )
    return rows


def load_font(size, bold=False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def text(draw, x, y, s, font, fill=(15, 23, 42), anchor=None):
    draw.text((x, y), s, font=font, fill=fill, anchor=anchor)


def canvas(title, subtitle):
    w, h = 1800, 1100
    img = Image.new("RGB", (w, h), (248, 250, 252))
    d = ImageDraw.Draw(img)
    text(d, 60, 45, title, load_font(52, bold=True))
    text(d, 60, 110, subtitle, load_font(28), fill=(51, 65, 85))
    return img, d


def draw_axes(d, left, top, width, height, y_max, y_ticks=6):
    d.rectangle((left, top, left + width, top + height), outline=(203, 213, 225), width=2)
    for i in range(y_ticks + 1):
        t = i / y_ticks
        y = top + height - t * height
        val = y_max * t
        d.line((left, y, left + width, y), fill=(226, 232, 240), width=1)
        text(d, left - 15, y, money(val), load_font(18), fill=(71, 85, 105), anchor="rm")


def save(img, name):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img.save(OUT_DIR / name, format="PNG", optimize=True)


def fig1(rows):
    groups = {
        "Yelp Search Task": [r["total_cost"] for r in rows if r["task"] == "Yelp Search Task"],
        "Google Flights Task": [r["total_cost"] for r in rows if r["task"] == "Google Flights Task"],
        "All Tasks Combined": [r["total_cost"] for r in rows],
    }
    order = ["Yelp Search Task", "Google Flights Task", "All Tasks Combined"]
    stats = {k: {"mean": statistics.mean(v), "min": min(v), "max": max(v), "n": len(v)} for k, v in groups.items()}

    img, d = canvas(
        "Average Cost Per Browser-Use Task (USD)",
        "Bar = average cost per run. Black whisker = full observed range.",
    )
    L, T, W, H = 220, 190, 1450, 700
    y_max = max(stats[k]["max"] for k in order) * 1.15
    draw_axes(d, L, T, W, H, y_max)

    colors = [(37, 99, 235), (8, 145, 178), (22, 163, 74)]
    bar_w = 250
    gap = 180
    x0 = L + 130

    def y(v):
        return T + H - (v / y_max) * H

    for i, name in enumerate(order):
        s = stats[name]
        x = x0 + i * (bar_w + gap)
        y_mean, y_min, y_max_p = y(s["mean"]), y(s["min"]), y(s["max"])

        d.line((x + bar_w / 2, y_max_p, x + bar_w / 2, y_min), fill=(15, 23, 42), width=6)
        d.line((x + 45, y_max_p, x + bar_w - 45, y_max_p), fill=(15, 23, 42), width=6)
        d.line((x + 45, y_min, x + bar_w - 45, y_min), fill=(15, 23, 42), width=6)
        d.rounded_rectangle((x, y_mean, x + bar_w, T + H), radius=12, fill=colors[i])

        text(d, x + bar_w / 2, y_mean - 18, f"{money(s['mean'])} avg", load_font(24, bold=True), anchor="ms")
        text(d, x + bar_w / 2, T + H + 45, name, load_font(24), anchor="ms")
        text(d, x + bar_w / 2, T + H + 78, f"{s['n']} runs", load_font(20), fill=(71, 85, 105), anchor="ms")

    text(d, 60, 1015, "Interpretation: runs are usually well under $1, but tougher tasks can approach $1 per run.", load_font(24), fill=(51, 65, 85))
    save(img, "01_average_cost_per_task.png")


def fig2(rows):
    vals = sorted(r["total_cost"] for r in rows)
    mn, mx = min(vals), max(vals)
    bin_w = 0.05
    bins = []
    b = mn - (mn % bin_w)
    while b <= mx + bin_w:
        bins.append((b, b + bin_w))
        b += bin_w
    counts = [sum(1 for v in vals if (lo <= v < hi) or (hi >= bins[-1][1] and lo <= v <= hi)) for lo, hi in bins]

    img, d = canvas(
        "How Often Different Per-Task Costs Occur",
        "Histogram of all 80 runs. Taller bars mean that price range occurred more often.",
    )
    L, T, W, H = 160, 200, 1520, 640
    max_c = max(counts)

    d.rectangle((L, T, L + W, T + H), outline=(203, 213, 225), width=2)
    for i in range(6):
        t = i / 5
        y = T + H - t * H
        c = round(t * max_c)
        d.line((L, y, L + W, y), fill=(226, 232, 240), width=1)
        text(d, L - 14, y, str(c), load_font(18), fill=(71, 85, 105), anchor="rm")

    def x(v):
        return L + (v - bins[0][0]) / (bins[-1][1] - bins[0][0]) * W

    def y(c):
        return T + H - (c / max_c) * H

    for (lo, hi), c in zip(bins, counts):
        x1, x2, yt = x(lo), x(hi), y(c)
        d.rectangle((x1 + 1, yt, x2 - 1, T + H), fill=(37, 99, 235))

    p50 = statistics.median(vals)
    p90 = sorted(vals)[int(0.9 * (len(vals) - 1))]
    for label, v, col in [("Median", p50, (220, 38, 38)), ("90th percentile", p90, (124, 58, 237))]:
        xv = x(v)
        d.line((xv, T, xv, T + H), fill=col, width=5)
        text(d, xv + 10, T + 16, f"{label}: {money(v)}", load_font(22, bold=True), fill=col)

    for i in range(9):
        t = i / 8
        v = bins[0][0] + t * (bins[-1][1] - bins[0][0])
        xv = x(v)
        d.line((xv, T + H, xv, T + H + 8), fill=(51, 65, 85), width=2)
        text(d, xv, T + H + 40, money(v), load_font(18), anchor="ms")

    text(d, 60, 1015, "Interpretation: most tasks cluster around about 20-60 cents each, with fewer high-cost runs.", load_font(24), fill=(51, 65, 85))
    save(img, "02_cost_distribution_histogram.png")


def fig3(rows):
    vals = sorted(r["total_cost"] for r in rows)
    p25 = vals[int(0.25 * (len(vals) - 1))]
    p50 = statistics.median(vals)
    p90 = vals[int(0.9 * (len(vals) - 1))]

    task_counts = [10, 25, 50, 100, 250, 500, 1000]
    scenarios = [
        ("Lower-cost days", p25, (22, 163, 74)),
        ("Typical days", p50, (37, 99, 235)),
        ("Higher-cost days", p90, (220, 38, 38)),
    ]

    img, d = canvas(
        "How Total Spend Scales With Number of Tasks",
        "If cost per task stays similar, total spend increases almost linearly with task count.",
    )
    L, T, W, H = 160, 200, 1480, 640
    y_max = task_counts[-1] * p90 * 1.1

    d.rectangle((L, T, L + W, T + H), outline=(203, 213, 225), width=2)
    for i in range(6):
        t = i / 5
        y = T + H - t * H
        d.line((L, y, L + W, y), fill=(226, 232, 240), width=1)
        text(d, L - 14, y, money(y_max * t), load_font(18), fill=(71, 85, 105), anchor="rm")

    def x(n):
        return L + (n - task_counts[0]) / (task_counts[-1] - task_counts[0]) * W

    def y(v):
        return T + H - (v / y_max) * H

    for n in task_counts:
        xv = x(n)
        d.line((xv, T + H, xv, T + H + 8), fill=(51, 65, 85), width=2)
        text(d, xv, T + H + 40, str(n), load_font(18), anchor="ms")

    for label, unit, col in scenarios:
        pts = [(x(n), y(n * unit)) for n in task_counts]
        d.line(pts, fill=col, width=6)
        for px, py in pts:
            d.ellipse((px - 6, py - 6, px + 6, py + 6), fill=col)
        lx, ly = pts[-1]
        text(d, lx + 14, ly, f"{label} ({money(unit)}/task)", load_font(20, bold=True), fill=col, anchor="lm")

    t100 = p50 * 100
    d.rounded_rectangle((L + 20, T + 20, L + 640, T + 120), radius=12, fill=(255, 255, 255), outline=(203, 213, 225), width=2)
    text(d, L + 40, T + 48, f"Example: 100 typical tasks is about {money(t100)}", load_font(28, bold=True))
    text(d, L + 40, T + 84, "(using the median observed per-task cost)", load_font(22), fill=(71, 85, 105))

    text(d, 60, 1015, "Interpretation: doubling task volume roughly doubles spend.", load_font(24), fill=(51, 65, 85))
    save(img, "03_budget_scaling_projection.png")


def fig4(rows):
    data = {
        "Yelp Search Task": [r for r in rows if r["task"] == "Yelp Search Task"],
        "Google Flights Task": [r for r in rows if r["task"] == "Google Flights Task"],
        "All Tasks Combined": rows,
    }
    order = ["Yelp Search Task", "Google Flights Task", "All Tasks Combined"]
    stats = {
        k: {
            "prompt": statistics.mean(r["prompt_cost"] for r in v),
            "completion": statistics.mean(r["completion_cost"] for r in v),
            "cache": statistics.mean(r["cached_cost"] for r in v),
        }
        for k, v in data.items()
    }

    img, d = canvas(
        "What Makes Up Cost Per Task",
        "Blue = prompt/context cost. Orange = response cost. Green text = average cache savings.",
    )
    L, T, W, H = 240, 210, 1360, 620
    ymax = max(stats[k]["prompt"] + stats[k]["completion"] for k in order) * 1.22

    draw_axes(d, L, T, W, H, ymax)

    bar_w = 280
    gap = 170
    x0 = L + 80

    def y(v):
        return T + H - (v / ymax) * H

    for i, name in enumerate(order):
        s = stats[name]
        x = x0 + i * (bar_w + gap)
        y_prompt = y(s["prompt"])
        y_total = y(s["prompt"] + s["completion"])

        d.rounded_rectangle((x, y_prompt, x + bar_w, T + H), radius=12, fill=(59, 130, 246))
        d.rounded_rectangle((x, y_total, x + bar_w, y_prompt), radius=12, fill=(245, 158, 11))

        total = s["prompt"] + s["completion"]
        text(d, x + bar_w / 2, y_total - 14, money(total), load_font(24, bold=True), anchor="ms")
        text(d, x + bar_w / 2, T + H + 46, name, load_font(24), anchor="ms")
        text(d, x + bar_w / 2, T + H + 82, f"Avg cache savings: {money(s['cache'])}", load_font(20), fill=(22, 163, 74), anchor="ms")

    d.rectangle((1250, 210, 1274, 234), fill=(59, 130, 246))
    text(d, 1288, 222, "Prompt/context", load_font(20), anchor="lm")
    d.rectangle((1250, 248, 1274, 272), fill=(245, 158, 11))
    text(d, 1288, 260, "Completion/response", load_font(20), anchor="lm")

    text(d, 60, 1015, "Interpretation: prompt/context processing is the biggest cost driver in these traces.", load_font(24), fill=(51, 65, 85))
    save(img, "04_cost_components_breakdown.png")


def main():
    rows = load_rows()
    fig1(rows)
    fig2(rows)
    fig3(rows)
    fig4(rows)
    print(f"Wrote PNG charts to: {OUT_DIR}")


if __name__ == "__main__":
    main()
