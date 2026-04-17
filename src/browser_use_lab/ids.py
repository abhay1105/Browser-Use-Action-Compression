from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "item"


def next_prompt_id(prompt_datasets_dir: Path) -> str:
    max_seen = 0
    for path in prompt_datasets_dir.glob("*.json"):
        parts = path.stem.rsplit("_", 2)
        if len(parts) != 3:
            continue
        prompt_id = parts[-1]
        if not prompt_id.startswith("p"):
            continue
        suffix = prompt_id[1:]
        if suffix.isdigit():
            max_seen = max(max_seen, int(suffix))
    return f"p{max_seen + 1:03d}"


def next_run_index(browser_traces_dir: Path, dataset_prefix: str) -> int:
    max_seen = 0
    for path in browser_traces_dir.iterdir() if browser_traces_dir.exists() else []:
        if not path.is_dir():
            continue
        name = path.name
        if not name.startswith(f"{dataset_prefix}_"):
            continue
        run_id = name[len(dataset_prefix) + 1 :]
        if run_id.startswith("r") and run_id[1:].isdigit():
            max_seen = max(max_seen, int(run_id[1:]))
    return max_seen + 1


def run_id_from_index(index: int) -> str:
    return f"r{index:03d}"
