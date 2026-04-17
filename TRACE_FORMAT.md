# Trace Format Guide

This document explains how traces are written by this repo and how to interpret them.

## Folder structure

Traces are grouped by dataset, run, and example:

```text
browser_traces/
  {task}_{num_examples}_{prompt_id}/
    run_001/
      001/
        task.json
        trace.json
      002/
        task.json
        trace.json
    run_002/
      001/
        task.json
        trace.json
```

Meaning:

- Dataset folder: all runs for one dataset file
- Run folder (`run_001`, `run_002`, ...): one invocation of `scripts/run_browser_use.py`
- Example folder (`001`, `002`, ...): one prompt row from the dataset

## `task.json`

`task.json` captures static task metadata.

Top-level keys:

- `schemaVersion`: currently `1`
- `recordedAt`: UTC ISO timestamp when files were written
- `task`: task object (id/type/url/goal/taskPrompt/deviceId/createdAt)
- `goal`: task goal text
- `sessionId`: local session id for this example
- `serverUrl`: currently `null` in this repo
- `deviceId`: copied from CLI/default env

Example:

```json
{
  "schemaVersion": 1,
  "recordedAt": "2026-04-06T01:59:44.498673Z",
  "task": {
    "id": "task_001",
    "type": "start_local_agent_goal",
    "url": "https://www.amazon.com/",
    "goal": "...",
    "taskPrompt": "...",
    "deviceId": "browser-use-local",
    "createdAt": "2026-04-06T01:59:44.498607Z"
  },
  "goal": "...",
  "sessionId": "session_001",
  "serverUrl": null,
  "deviceId": "browser-use-local"
}
```

## `trace.json`

`trace.json` captures dynamic execution output.

Top-level keys:

- `schemaVersion`
- `startedAt`
- `completedAt`
- `status`: `completed` or `failed`
- `result`: structured output when available
- `error`: stack/message when failed
- `taskId`, `taskType`, `goal`, `url`, `sessionId`, `serverUrl`, `deviceId`
- `traceFolder`: example folder name (for example `001`)
- `events`: event timeline
- `messages`: compact user/assistant transcript blocks

## Event timeline (`trace.json.events`)

Events are append-only and each event has:

- `timestamp` (epoch ms)
- `type`
- `payload`

Common lifecycle events:

- `task_received`
- `session_initialized`
- `agent_loop_started`
- `user_prompt`
- `browser_use_run_started`
- `browser_use_run_finished`
- `task_completed`
- `task_failed`
- `agent_loop_error`
- `agent_loop_finished`

Tool call events (important for downstream ingestion):

- `tool_use`
  - payload: `toolUseId`, `name`, `input`
- `tool_result`
  - payload: `toolUseId`, `name`, `durationMs`, `text`, `imageCount`

`tool_use`/`tool_result` pairs are what `toolcalltokenization` ingestion primarily reads.

## Message blocks (`trace.json.messages`)

Each message has:

- `id`, `role`, `timestamp`
- `blocks`: compact blocks

Block types:

- `text`: `{ "type": "text", "text": "..." }`
- `tool_use`: `{ "type": "tool_use", "id": "tool_001", "name": "...", "input": {...} }`
- `tool_result`: `{ "type": "tool_result", "toolUseId": "tool_001", "text": "...", "hasImage": false }`
- `screenshot`: `{ "type": "screenshot", "hasData": bool, "bytes": int }`

## How to interpret status/result

- `status = completed`
  - Run returned normally.
  - Check `result.final_result` (if present) and tool events.
- `status = failed`
  - Agent failed before completion.
  - Inspect `error` and trailing failure events.

Dry-run behavior:

- `--dry-run` does not launch browser-use.
- It still writes realistic-shaped `task.json`/`trace.json` with a simulated tool event pair.

## Compatibility notes

This format is designed to be ingestible by `toolcalltokenization` OttoAuth converter (`convert_ottoauth_traces`) because it provides:

- `task.json` + `trace.json` pair per example folder
- `trace.json.events` with `tool_use` and `tool_result`
- `taskType = start_local_agent_goal`
- task/url metadata needed for website inference

## Quick inspection commands

```bash
# Print high-level status for one example
jq '{status, taskId, taskType, url, eventCount: (.events|length)}' browser_traces/.../trace.json

# List event types in order
jq -r '.events[].type' browser_traces/.../trace.json

# Inspect tool-use events only
jq '.events[] | select(.type=="tool_use")' browser_traces/.../trace.json
```
