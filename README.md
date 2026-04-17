# browser_use

Standalone browser-use experiment repo for:

- generating prompt datasets
- running browser-use tasks from those datasets
- saving OttoAuth-compatible `task.json` / `trace.json` artifacts

This repo is intentionally small and script-driven.

## Repository layout

- `scripts/generate_prompts.py`: Create dataset JSON files under `prompt_datasets/`
- `scripts/run_browser_use.py`: Execute dataset prompts and write traces under `browser_traces/`
- `src/browser_use_lab/`: Runtime + trace formatting code
- `prompt_datasets/`: Generated datasets
- `browser_traces/`: Per-run trace output
- `TRACE_FORMAT.md`: Detailed trace schema and interpretation guide

## Requirements

- Python `>=3.10`
- OpenAI API key (`OPENAI_API_KEY`) for real LLM calls
- Anthropic API key (`ANTHROPIC_API_KEY`) for Claude runs
- Playwright browser binaries for browser execution

Project dependencies are defined in `pyproject.toml` and installed via `pip install -e .`.

## Setup

```bash
cd /Users/abhayp/Documents/browser_use
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
python -m playwright install
cp .env.example .env
```

Set your key:

```bash
export OPENAI_API_KEY="YOUR_KEY"
```

Optional defaults (already in `.env.example`):

- `BROWSER_USE_PROVIDER` (default: `openai`, also supports `anthropic`)
- `BROWSER_USE_MODEL` (default: `gpt-4.1-mini`)
- `BROWSER_USE_DEVICE_ID` (default: `browser-use-local`)

## Quickstart

### 1) Generate prompt dataset

```bash
python scripts/generate_prompts.py \
  --task amazon_single_item_purchase \
  --n 20
```

Output example:

- `prompt_datasets/amazon_single_item_purchase_20_p001.json`

Notes:

- If `OPENAI_API_KEY` is set, prompt generation uses OpenAI.
- If generation fails or key is missing, it falls back to template prompts.
- Use `--template-only` to force template generation.

### 2) Run browser-use and collect traces

```bash
python scripts/run_browser_use.py \
  --dataset prompt_datasets/amazon_single_item_purchase_20_p001.json
```

Output folders are grouped by dataset, run number, and example number:

- `browser_traces/amazon_single_item_purchase_20_p001/run_001/001/`
- `browser_traces/amazon_single_item_purchase_20_p001/run_001/002/`
- `browser_traces/amazon_single_item_purchase_20_p001/run_002/001/`

Each example folder contains:

- `task.json`
- `trace.json`

## Common script options

### `scripts/generate_prompts.py`

- `--task`: task family name (required)
- `--n`: number of prompts (required)
- `--prompt-id`: explicit prompt id like `p007`
- `--model`: model for generation
- `--provider`: `openai` or `anthropic`
- `--template-only`: skip LLM generation
- `--output-dir`: dataset output directory

### `scripts/run_browser_use.py`

- `--dataset`: input dataset path (required)
- `--traces-dir`: root output directory (default `browser_traces`)
- `--model`: model used by browser-use
- `--provider`: `openai` or `anthropic`
- `--device-id`: written into task/trace metadata
- `--max-prompts`: cap prompts from dataset
- `--show-browser`: run non-headless
- `--dry-run`: skip real browser-use execution, still writes trace files

## Known behavior and troubleshooting

- If browser launch fails with permission errors in restricted environments, try running locally outside sandboxed terminals.
- If you see missing Playwright/browser errors, run:

```bash
python -m playwright install
```

- If model/provider errors appear, confirm:
  - `OPENAI_API_KEY` is set in current shell
  - `ANTHROPIC_API_KEY` is set if using `--provider anthropic`
  - dependencies are installed in the active `.venv`

For more verbose browser-use internals in terminal logs:

```bash
export BROWSER_USE_LOGGING_LEVEL=debug
export ANONYMIZED_TELEMETRY=false
```

## Trace compatibility

The output is structured to remain compatible with OttoAuth-style ingestion used by `toolcalltokenization` (`convert_ottoauth_traces`), including:

- top-level `task.json` and `trace.json` keys
- `tool_use` / `tool_result` events in `trace.json.events`
- compact message blocks in `trace.json.messages`

For the full schema and interpretation details, see:

- `TRACE_FORMAT.md`
