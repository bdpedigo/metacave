---
name: add-repo
description: "Add a new repository to the CAVE stack map. Use when: integrating a new submodule or project into summaries/map.md; onboarding a new CAVE-adjacent tool. Reads READMEs and source files, determines the project's section, drafts a 1-3 sentence entry, then delegates the actual map edit to the update-map skill."
argument-hint: "Name or path of the repository to add (e.g. 'MyNewService' or 'submodules/MyNewService')"
---

# Add Repo to Stack Map

## Purpose

Integrate a new (or updated) repository into [`summaries/map.md`](../../../summaries/map.md) — the high-level map of every project in the CAVE connectomics stack.

## Context

[`summaries/map.md`](../../../summaries/map.md) documents each project in 1-3 sentences. Format rules and section definitions are owned by the `update-map` skill, which performs all actual edits to `map.md`. This skill is responsible only for reading and classifying the new repository.

## Procedure

### 1. Read the CAVE profile

Read [`submodules/cave-github/profile/README.md`](../../../submodules/cave-github/profile/README.md) to understand where this project fits in the overall architecture. Note which section it belongs to and which other projects it mentions as dependencies or consumers.

### 2. Read the project's documentation

For the target repo (e.g. `submodules/<RepoName>/`), read in priority order:

- `README.md` or `README.rst` — primary source of truth
- If README is absent or very sparse, read `pyproject.toml` / `setup.py` / `setup.cfg` for package description, then look at the main source file(s) to infer purpose

Flag entries where documentation was sparse or absent with an italicised note, e.g.:  
_No README present; purpose inferred from source code._

### 3. Determine the section

Use the section table in the `update-map` skill. When ambiguous, prefer the "lowest" layer — a library that also exposes a REST API should be listed as a Micro-Service if it is deployed independently.

### 4. Draft the entry

Write **1-3 sentences** following the format defined in the `update-map` skill:

- First sentence: positive description of the primary responsibility.
- Scope boundary sentence: explicitly say what the project does NOT handle.
- Integration sentence: name other stack projects it depends on _or_ that depend on it.

Keep it factual and concise — no marketing language.

### 5. Invoke `update-map` to insert the entry

Pass the drafted entry and its target section to the `update-map` skill. It will handle:

- Placing the entry in the correct section of `map.md`
- Back-filling cross-links in related existing entries
- Mermaid diagram flagging (if new nodes or edges are implied)
- Validation

Do not edit `map.md` directly.
