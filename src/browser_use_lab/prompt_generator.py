from __future__ import annotations

import json
import os
import random
import re
from typing import Any

from .ids import now_iso


FALLBACK_ITEMS = [
    "stainless steel water bottle",
    "wireless ergonomic mouse",
    "portable phone charger",
    "notebook and pen set",
    "ceramic coffee mug",
    "USB-C hub",
    "desk lamp",
    "yoga mat",
    "running socks",
    "noise-reducing earplugs",
]


def _fallback_prompts(task: str, n: int) -> list[str]:
    random.seed(7)
    prompts: list[str] = []
    task_name = task.replace("_", " ").strip()
    for i in range(n):
        item = FALLBACK_ITEMS[i % len(FALLBACK_ITEMS)]
        prompts.append(
            (
                f"Go to https://www.amazon.com/ and find one {item} with Prime shipping and rating >= 4 stars. "
                f"Add exactly one item to cart and stop before checkout confirmation. "
                f"Task theme: {task_name}."
            )
        )
    return prompts


def _generate_with_openai(task: str, n: int, model: str) -> list[str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("openai package is required for LLM prompt generation") from exc

    client = OpenAI(api_key=api_key)
    system = (
        "You generate realistic browser-use task prompts for research experiments. "
        "Return strict JSON only."
    )
    user = (
        f"Generate exactly {n} unique prompts for task category '{task}'. "
        "Each prompt should be one sentence, actionable, and suitable for a browser agent. "
        "Output JSON with key 'prompts' as a string array."
    )

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    text = getattr(response, "output_text", "").strip()
    if not text:
        raise RuntimeError("OpenAI returned empty output")

    parsed = json.loads(text)
    prompts = parsed.get("prompts")
    if not isinstance(prompts, list):
        raise RuntimeError("OpenAI response JSON missing 'prompts' array")
    clean = [str(p).strip() for p in prompts if str(p).strip()]
    if len(clean) != n:
        raise RuntimeError(f"Expected {n} prompts, got {len(clean)}")
    return clean


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _generate_with_anthropic(task: str, n: int, model: str) -> list[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    try:
        from anthropic import Anthropic
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("anthropic package is required for Claude prompt generation") from exc

    client = Anthropic(api_key=api_key)
    system = (
        "You generate realistic browser-use task prompts for research experiments. "
        "Return strict JSON only."
    )
    user = (
        f"Generate exactly {n} unique prompts for task category '{task}'. "
        "Each prompt should be one sentence, actionable, and suitable for a browser agent. "
        "Output JSON with key 'prompts' as a string array."
    )

    response = client.messages.create(
        model=model,
        max_tokens=1200,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(
        block.text
        for block in getattr(response, "content", []) or []
        if getattr(block, "type", "") == "text"
    ).strip()
    if not text:
        raise RuntimeError("Anthropic returned empty output")

    parsed = _extract_json_object(text)
    prompts = parsed.get("prompts")
    if not isinstance(prompts, list):
        raise RuntimeError("Anthropic response JSON missing 'prompts' array")
    clean = [str(p).strip() for p in prompts if str(p).strip()]
    if len(clean) != n:
        raise RuntimeError(f"Expected {n} prompts, got {len(clean)}")
    return clean


def generate_prompt_dataset(
    *,
    task: str,
    n: int,
    prompt_id: str,
    model: str,
    provider: str,
    force_template: bool,
) -> dict[str, Any]:
    prompts: list[str]
    generator_type = "template"
    provider_slug = provider.strip().lower()

    if force_template:
        prompts = _fallback_prompts(task, n)
    else:
        try:
            if provider_slug == "anthropic":
                prompts = _generate_with_anthropic(task, n, model)
                generator_type = "anthropic"
            else:
                prompts = _generate_with_openai(task, n, model)
                generator_type = "openai"
        except Exception:
            prompts = _fallback_prompts(task, n)
            generator_type = "template"

    return {
        "task": task,
        "num_examples": n,
        "prompt_id": prompt_id,
        "created_at": now_iso(),
        "generator": {
            "type": generator_type,
            "model": model if generator_type in {"openai", "anthropic"} else None,
        },
        "prompts": [
            {
                "id": f"{prompt_id}_{index + 1:03d}",
                "text": prompt,
            }
            for index, prompt in enumerate(prompts)
        ],
    }
