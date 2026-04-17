#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from browser_use_lab.ids import next_prompt_id, slugify
from browser_use_lab.io_utils import ensure_dir, write_json
from browser_use_lab.prompt_generator import generate_prompt_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate browser-use prompt datasets and save to prompt_datasets/",
    )
    parser.add_argument("--task", required=True, help="Task family name (example: amazon_single_item_purchase)")
    parser.add_argument("--n", type=int, required=True, help="Number of prompts to generate")
    parser.add_argument("--prompt-id", help="Optional prompt id (example: p001). Auto-generated if omitted")
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model used for prompt generation",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("BROWSER_USE_PROVIDER", "openai"),
        choices=["openai", "anthropic"],
        help="LLM provider used for prompt generation",
    )
    parser.add_argument(
        "--template-only",
        action="store_true",
        help="Skip LLM generation and use built-in template prompts",
    )
    parser.add_argument(
        "--output-dir",
        default="prompt_datasets",
        help="Directory where dataset JSON files are stored",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    provider = str(args.provider).strip().lower()
    model = args.model or os.getenv("BROWSER_USE_MODEL", "").strip()
    if not model:
        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4.1-mini"

    if args.n <= 0:
        raise SystemExit("--n must be a positive integer")

    output_dir = ensure_dir(Path(args.output_dir).resolve())
    prompt_id = args.prompt_id or next_prompt_id(output_dir)
    task_slug = slugify(args.task)
    file_name = f"{task_slug}_{args.n}_{prompt_id}.json"

    dataset = generate_prompt_dataset(
        task=task_slug,
        n=args.n,
        prompt_id=prompt_id,
        model=model,
        provider=provider,
        force_template=bool(args.template_only),
    )

    output_path = output_dir / file_name
    write_json(output_path, dataset)

    print(f"Saved {len(dataset['prompts'])} prompts to {output_path}")
    print(f"Dataset id: {task_slug}_{args.n}_{prompt_id}")
    print(f"Provider: {provider}")
    print(f"Model: {model}")
    print(f"Generator: {dataset['generator']['type']}")


if __name__ == "__main__":
    main()
