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


def _is_yelp_task(task: str) -> bool:
    slug = task.strip().lower()
    return "yelp" in slug


def _allocate_yelp_bucket_counts(n: int) -> dict[str, int]:
    # Target distribution for n=50:
    # search=20, search_deep=15, compare=10, review=5
    specs: list[tuple[str, int]] = [
        ("search", 20),
        ("search_deep", 15),
        ("compare", 10),
        ("review", 5),
    ]
    total_weight = sum(weight for _, weight in specs)
    base_counts: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for name, weight in specs:
        exact = (n * weight) / total_weight
        count = int(exact)
        base_counts[name] = count
        assigned += count
        remainders.append((exact - count, name))

    remainders.sort(key=lambda item: item[0], reverse=True)
    remaining = n - assigned
    idx = 0
    while remaining > 0 and remainders:
        _, name = remainders[idx % len(remainders)]
        base_counts[name] += 1
        remaining -= 1
        idx += 1

    return base_counts


def _build_yelp_mixed_prompts(n: int) -> list[str]:
    random.seed(13)
    cities = [
        "Seattle",
        "Austin",
        "Chicago",
        "Boston",
        "San Diego",
        "Denver",
        "Portland",
        "Nashville",
        "San Jose",
        "Phoenix",
        "San Francisco",
        "Los Angeles",
        "New York",
        "Philadelphia",
        "Washington, DC",
        "Miami",
        "Orlando",
        "Atlanta",
        "Charlotte",
        "Raleigh",
        "Dallas",
        "Houston",
        "San Antonio",
        "Las Vegas",
        "Minneapolis",
        "Milwaukee",
        "Detroit",
        "Cleveland",
        "Columbus",
        "Pittsburgh",
        "Kansas City",
        "St. Louis",
        "Indianapolis",
        "Cincinnati",
        "Salt Lake City",
        "Boise",
        "Albuquerque",
        "New Orleans",
        "Tampa",
        "Sacramento",
    ]
    cuisines = [
        "Italian",
        "Thai",
        "Pizza",
        "Mexican",
        "Japanese",
        "Indian",
        "Mediterranean",
        "Korean",
        "Vietnamese",
        "French",
        "Chinese",
        "Greek",
        "Turkish",
        "Spanish",
        "Lebanese",
        "Peruvian",
        "Brazilian",
        "Caribbean",
        "Ethiopian",
        "Middle Eastern",
        "Sushi",
        "Ramen",
        "Steakhouse",
        "Seafood",
        "BBQ",
        "Burgers",
        "Breakfast",
        "Brunch",
        "Vegan",
        "Vegetarian",
    ]
    counts = _allocate_yelp_bucket_counts(n)
    prompts: list[str] = []
    used_prompts: set[str] = set()

    def _add_unique(bucket_count: int, builder: Any, seed: int) -> None:
        nonlocal prompts
        rng = random.Random(seed)
        combinations = [(city, cuisine, city_index, cuisine_index) for city_index, city in enumerate(cities) for cuisine_index, cuisine in enumerate(cuisines)]
        rng.shuffle(combinations)

        generated = 0
        for city, cuisine, city_index, cuisine_index in combinations:
            candidate = str(builder(city, cuisine, city_index, cuisine_index)).strip()
            if not candidate or candidate in used_prompts:
                continue
            prompts.append(candidate)
            used_prompts.add(candidate)
            generated += 1
            if generated >= bucket_count:
                return
        raise RuntimeError(f"Unable to generate {bucket_count} unique Yelp template prompts for a bucket")

    _add_unique(
        counts["search"],
        lambda city, cuisine, city_index, cuisine_index: (
            f"On Yelp, find {cuisine} restaurants in {city}."
        ),
        seed=1301,
    )
    _add_unique(
        counts["search_deep"],
        lambda city, cuisine, city_index, cuisine_index: (
            f"On Yelp, find the best-rated {cuisine} place in {city} and report its hours and whether it takes reservations."
        ),
        seed=1302,
    )
    _add_unique(
        counts["compare"],
        lambda city, cuisine, city_index, cuisine_index: (
            f"On Yelp, find two highly-rated {cuisine} places in {city} and compare their price levels and ratings."
        ),
        seed=1303,
    )
    _add_unique(
        counts["review"],
        lambda city, cuisine, city_index, cuisine_index: (
            f"On Yelp, find the top {cuisine} restaurant in {city} and summarize its three most recent reviews."
        ),
        seed=1304,
    )

    return prompts


def _yelp_bucket_specs() -> dict[str, str]:
    return {
        "search": "Create Yelp search tasks like: Find Italian restaurants in Seattle.",
        "search_deep": (
            "Create Yelp search-then-dig-deeper tasks like: "
            "Find the best-rated Thai place in Austin and tell me their hours and whether they take reservations."
        ),
        "compare": (
            "Create Yelp comparison tasks like: "
            "Find two highly-rated pizza places in Chicago and compare their prices and ratings."
        ),
        "review": (
            "Create Yelp review-reading tasks like: "
            "Find the top Italian restaurant in Boston and summarize the 3 most recent reviews."
        ),
    }


def _generate_yelp_bucket_with_openai(*, bucket: str, count: int, model: str) -> list[str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("openai package is required for LLM prompt generation") from exc

    specs = _yelp_bucket_specs()
    if bucket not in specs:
        raise RuntimeError(f"Unsupported Yelp bucket: {bucket}")

    client = OpenAI(api_key=api_key)
    system = (
        "You generate realistic browser-use task prompts for Yelp-focused research experiments. "
        "Return strict JSON only."
    )
    user = (
        f"Generate exactly {count} unique Yelp prompts for bucket '{bucket}'. "
        f"{specs[bucket]} "
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
    if len(clean) != count:
        raise RuntimeError(f"Expected {count} prompts for bucket '{bucket}', got {len(clean)}")
    return clean


def _generate_yelp_bucket_with_anthropic(*, bucket: str, count: int, model: str) -> list[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    try:
        from anthropic import Anthropic
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("anthropic package is required for Claude prompt generation") from exc

    specs = _yelp_bucket_specs()
    if bucket not in specs:
        raise RuntimeError(f"Unsupported Yelp bucket: {bucket}")

    client = Anthropic(api_key=api_key)
    system = (
        "You generate realistic browser-use task prompts for Yelp-focused research experiments. "
        "Return strict JSON only."
    )
    user = (
        f"Generate exactly {count} unique Yelp prompts for bucket '{bucket}'. "
        f"{specs[bucket]} "
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
    if len(clean) != count:
        raise RuntimeError(f"Expected {count} prompts for bucket '{bucket}', got {len(clean)}")
    return clean


def _generate_yelp_prompts_by_buckets(*, n: int, model: str, provider: str) -> list[str]:
    counts = _allocate_yelp_bucket_counts(n)
    prompts: list[str] = []

    for bucket in ("search", "search_deep", "compare", "review"):
        count = counts[bucket]
        if count <= 0:
            continue
        if provider == "anthropic":
            prompts.extend(_generate_yelp_bucket_with_anthropic(bucket=bucket, count=count, model=model))
        else:
            prompts.extend(_generate_yelp_bucket_with_openai(bucket=bucket, count=count, model=model))

    if len(prompts) != n:
        raise RuntimeError(f"Expected {n} Yelp bucket prompts, got {len(prompts)}")
    return prompts


def _fallback_prompts(task: str, n: int) -> list[str]:
    if _is_yelp_task(task):
        return _build_yelp_mixed_prompts(n)

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

    if _is_yelp_task(task):
        if force_template:
            prompts = _build_yelp_mixed_prompts(n)
            generator_type = "template_yelp_mix"
        else:
            try:
                prompts = _generate_yelp_prompts_by_buckets(n=n, model=model, provider=provider_slug)
                generator_type = f"{provider_slug}_yelp_buckets"
            except Exception:
                prompts = _build_yelp_mixed_prompts(n)
                generator_type = "template_yelp_mix"
    elif force_template:
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
