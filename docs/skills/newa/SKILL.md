---
name: newa
description: Role and Purpose
---

# NEWA (New Errata Workflow Automation) — Usage Guide

This skill provides guidance for using the `newa` CLI tool to manage errata testing workflows. NEWA orchestrates test execution using tmt, Testing Farm, Jira, and ReportPortal.

## Command Aliases

The `newa` command may appear as various aliases: `newa`, `newa-prod`, `newa-stage`, `newa-ctc`, etc. These are bash aliases pointing to different configuration files (`~/.newa` or custom) and use **different state directories**. They are used identically in terms of syntax but **cannot be used interchangeably** — a run started with `newa-stage` must be managed with `newa-stage`.

Default to `newa` unless context indicates otherwise (e.g., the user mentions a specific alias, or the project README/AGENTS.md specifies one).

**Alias consistency rule:** Once a specific alias is established for the current session (by the user specifying it or by a prior command using it), **all subsequent commands must use that same alias** unless the user explicitly asks to switch. For example, if the user says "use `newa-stage`" or a state-dir was created with `newa-stage`, every follow-up command (`list`, `cancel`, `report`, etc.) must also use `newa-stage`.

## Critical Rule: Never Inspect State Directories Directly

**NEVER** use `ls`, `find`, `grep`, `cat`, `Read`, `Glob`, or any filesystem inspection tool on newa state directories (typically under `/var/tmp/newa*/`).

Always use `newa list` and `newa search` subcommands — they provide all necessary information about state-dir contents, Testing Farm request statuses, Jira issues, ReportPortal launches, and artifacts URLs.

## Workflow Overview

NEWA workflow stages: `event` → `jira` → `schedule` → `execute` → `report` → `summarize`

Each stage produces metadata files in a state directory. Not all stages are always needed — you can start from any stage if earlier stages have already been completed.

## Getting Started: Three Scenarios

Before doing anything, determine which scenario applies. Ask the user if unclear.

### Scenario 1: Starting from Scratch

The user wants to schedule tests for an erratum with no prior NEWA state-dir.

**Steps:**
1. Find the issue-config file — check the project's `NEWA/issue-config/` directory, `README.md`, or `AGENTS.md`
2. Run the full pipeline:
```
newa event --erratum <ID> jira --issue-config NEWA/issue-config/errata.yaml schedule execute --no-wait
```
- Do NOT specify `--state-dir` — NEWA creates one automatically
- Always use `--no-wait` to avoid blocking the terminal
- NEWA prints the state-dir path — record it for follow-up

### Scenario 2: Extract from Jenkins Archive

The user has an archive URL from a Jenkins job containing a previously created state-dir.

**Steps:**
1. Ask the user for the archive URL
2. Extract and list:
```
newa-stage -E https://jenkins.example.com/job/.../artifact/newa-state-dir.tar.gz list
```
- NEWA creates a new local state-dir from the archive
- The `-E` flag (short for `--extract-state-dir`) downloads, extracts, and continues with the new state-dir

### Scenario 3: Use Existing Local State Directory

A state-dir for the erratum may already exist locally from a previous run.

**Steps:**
1. Search for it by erratum ID:
```
newa search --erratum <ID>
```
2. There may be **multiple matching state-dirs** — if so, ask the user which one to work with
3. Once identified, use `-D <path>` for all subsequent commands

## Erratum Respins

An erratum can be **respun** — the old build is removed and a newer build is added. This commonly results in **multiple state-dirs for the same advisory**. When multiple state-dirs exist:

- Typically you want to work with the **most recent one** (for the latest build/spin)
- Respins may be indicated in Jira task summaries (e.g., "spin 0", "spin 1") but this is not guaranteed
- Obsoleted Jira issues from older spins should have been closed or updated by NEWA, so you'd normally work only with open issues
- **Always confirm the state-dir with the user** when multiple exist — don't assume which one is current

## Checking Status

Use `newa -D <state-dir> list` to get the full picture of a state-dir.

```
# Show full details (default when -D is specified)
newa -D /var/tmp/newa/run-123 list

# Refresh statuses for in-progress TF requests
newa -D /var/tmp/newa/run-123 list --refresh

# Refresh with scope limited to a specific task
newa -D /var/tmp/newa/run-123 --issue-id-filter SECENGSP-12021 list --refresh
```

**When to use `--refresh`:** only when requests may still be running (state is not complete/error/failed). If all requests are already finished, plain `list` is sufficient.

**Auto-refresh rule:** When a user asks for errata status and the initial `newa list` output shows any Testing Farm requests in a running/pending state, you **must** automatically follow up with `list --refresh` to fetch the current live status from Testing Farm before presenting results to the user. Do not present stale cached statuses for in-progress requests — always refresh them first. The workflow is:
1. Run `newa list` (or `newa -D <path> list`) to get the initial overview
2. If the output contains any TF requests that are not in a terminal state (complete/passed/failed/error/canceled), run `newa list --refresh` (or `newa -D <path> list --refresh`) immediately
3. Present the refreshed results to the user

### Presenting Status to the User

When asked for status, don't just dump raw output. Synthesize a summary covering:

- **Completed & passed** — tasks where all TF requests passed
- **Completed with failures** — tasks with failed/errored requests (note which request ID and architecture)
- **Still running** — tasks with in-progress TF requests
- **Not yet scheduled** — tasks with recipes that haven't been scheduled
- **Manual tasks** — tasks without recipes (e.g., EW checklist items) needing user action
- **Actions needed** — suggest next steps (reschedule failures, schedule pending tasks, etc.)

NEWA does not track Jira issue statuses. If you have access to Jira (via MCP server or other tools), check Jira issue statuses to provide a more complete picture.

## Jira Issue Structure

A typical NEWA state-dir contains:
- **Epic** — top-level tracking issue for the erratum (not mandatory but common)
- **Tasks** — individual parts of the testing matrix, each with an action ID

Tasks fall into three categories:
1. **Automated, auto-scheduled** — have a recipe and `schedule: true`. Scheduled automatically by `newa schedule execute`.
2. **Automated, manually-scheduled** — have a recipe but `schedule: false`. Must be targeted explicitly with a filter to schedule.
3. **Manual** — no recipe. NEWA cannot execute these; the user handles them independently.

## Scheduling Unscheduled Actions

To manually schedule a task that wasn't auto-scheduled, use a filter to target it:

```
# By Jira issue key
newa -D /path/to/state-dir --issue-id-filter SECENGSP-12345 schedule execute --no-wait

# By action ID
newa -D /path/to/state-dir --action-id-filter 'task_interoperability_versions' schedule execute --no-wait

# By action tag
newa -D /path/to/state-dir --action-tag-filter 'tier2' schedule execute --no-wait
```

No `event` or `jira` subcommands needed — the state-dir already has that data.

## Restarting Failed or Errored Requests

Two restart modes:

```
# Bulk restart by result type (error, failed, or passed)
newa -D /path/to/state-dir execute -r failed --no-wait

# Restart specific request(s) by ID
newa -D /path/to/state-dir execute -R REQ-2.4.4 --no-wait

# Multiple specific requests
newa -D /path/to/state-dir execute -R REQ-1.2.1 -R REQ-2.4.4 --no-wait

# Scoped to a specific Jira task
newa -D /path/to/state-dir --issue-id-filter SECENGSP-12021 execute -r failed --no-wait
```

- Both `-r` and `-R` imply `--continue` automatically
- Always use `--no-wait` to avoid blocking
- Consider asking the user about `--rp-purge` (`-X`) to remove previous results from ReportPortal and avoid duplicates

## Reporting Results

The `report` subcommand updates Jira issues with test result comments and finalizes ReportPortal launches.

**Critical: always scope `report` with a filter** to avoid duplicate comments on already-processed Jira issues:

```
# Report for a specific task
newa -D /path/to/state-dir --issue-id-filter SECENGSP-12021 report

# Report for a specific action type
newa -D /path/to/state-dir --action-id-filter 'task_regression_swtpm' report
```

**When unscoped `report` is acceptable:** only on the first report run when all tests have completed and no partial reports have been done.

**Progress reporting:** use `report --progress` to post interim "in progress" updates to Jira without finalizing anything.

## Cancelling Testing Farm Requests

The `cancel` subcommand cancels running Testing Farm requests in a state-dir.

**Critical: always scope `cancel` with a filter or `--request`** to avoid cancelling requests you want to keep running — same rules as `report` and `summarize`:

```
# Cancel requests for a specific Jira task
newa -D /path/to/state-dir --issue-id-filter BASEQESEC-12345 cancel

# Cancel requests for a specific action type
newa -D /path/to/state-dir --action-id-filter 'task_image_mode' cancel

# Cancel specific request(s) by ID
newa -D /path/to/state-dir cancel -R REQ-1.2.1

# Cancel multiple specific requests
newa -D /path/to/state-dir cancel -R REQ-1.2.1 -R REQ-2.2.2

# Cancel all requests scoped to a Jira task
newa -D /path/to/state-dir --issue-id-filter JIRA-12345 cancel
```

**When unscoped `cancel` is acceptable:** only when you intentionally want to cancel **all** TF requests in the state-dir.

## AI Summarization

The `summarize` subcommand generates AI-powered summaries of ReportPortal test results and posts them to Jira.

```
newa -D /path/to/state-dir --issue-id-filter SECENGSP-12021 summarize
```

- **Only run on explicit user request** — never proactively
- **Same scoping rules as `report`** — always use filters to avoid duplicate comments
- Requires `report` to have been run first

## Command Discovery

If unsure about available commands or options:
```
newa --help
newa <subcommand> --help
```

For complex scenarios, consult the official NEWA documentation:
https://raw.githubusercontent.com/RedHatQE/newa/refs/heads/main/README.md

## Quick Reference

| Task | Command |
|------|---------|
| List recent runs | `newa list` |
| List more runs | `newa list --last 20` or `newa list --all` |
| Search by erratum | `newa search --erratum <ID>` |
| Search by text | `newa search --text <pattern>` |
| Show state-dir details | `newa -D <path> list` |
| Refresh in-progress status | `newa -D <path> list --refresh` |
| Start new erratum tests | `newa event --erratum <ID> jira --issue-config <path> schedule execute --no-wait` |
| Extract Jenkins archive | `newa -E <URL> list` |
| Schedule specific task | `newa -D <path> --issue-id-filter <KEY> schedule execute --no-wait` |
| Restart failed requests | `newa -D <path> execute -r failed --no-wait` |
| Restart specific request | `newa -D <path> execute -R <REQ-ID> --no-wait` |
| Report results (scoped) | `newa -D <path> --issue-id-filter <KEY> report` |
| Report progress | `newa -D <path> --issue-id-filter <KEY> report --progress` |
| Summarize (scoped) | `newa -D <path> --issue-id-filter <KEY> summarize` |
| Cancel all requests | `newa -D <path> cancel` |
| Cancel specific request | `newa -D <path> cancel -R <REQ-ID>` |
| Cancel requests (scoped) | `newa -D <path> --issue-id-filter <KEY> cancel` |
