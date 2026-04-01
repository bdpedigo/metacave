---
name: update-map
description: "Edit summaries/map.md. Use when: correcting or expanding an existing entry's description, scope boundary, or integration sentence; adding a completely new section entry; back-filling cross-links between entries. Called internally by the add-repo and how skills, or invoked directly when a description needs a correction."
argument-hint: "What to change and why (e.g. 'add X to Libraries section' or 'fix ME entry: it also uses Redis')"
---

# Update Stack Map

## Purpose

Make targeted, format-compliant edits to [`summaries/map.md`](../../../summaries/map.md) — the high-level map of every project in the CAVE stack. This is the single source of truth for map edits; both `add-repo` and `how` delegate here rather than editing `map.md` directly.

## Format Rules

Every entry must be **1-3 sentences** following this structure:

1. **Primary responsibility** — what it does and its main data store / runtime.
2. **Scope boundary** — what it does NOT handle. Start with "Does not…" or "Not a…".
3. **Integration sentence** — other stack projects it depends on or that depend on it.

Cross-links use markdown anchor syntax: `[DisplayName](#lowercased-heading-slug)`.

Entries where documentation was sparse or absent must be flagged with an italicised note, e.g.:
_No README present; purpose inferred from source code._

All entries live under one of these section headings (matching the CAVE profile):

| Section                         | Typical members                                                   |
| ------------------------------- | ----------------------------------------------------------------- |
| **Micro-Services**              | Flask/REST API services deployed to Kubernetes                    |
| **Libraries**                   | Python packages shared by multiple services (no HTTP server)      |
| **Access Tools**                | Python client libraries for end-users / researchers               |
| **CAVE-adjacent Tools**         | External tools that integrate with CAVE but are not owned by CAVE |
| **Deployment / Infrastructure** | Helm charts, Terraform modules, deployment templates              |

## Procedure

### 1. Read the current map

Read `summaries/map.md` in full before making any changes. Locate the target entry or the target section for a new entry.

### 2a. Edit an existing entry

- Make the minimum necessary change: fix the specific sentence that is wrong or incomplete.
- Do NOT rewrite or reformat the rest of the entry.
- Do NOT alter entries that are not directly relevant to the change.

### 2b. Add a new entry

- Place the `### <ProjectName>` heading inside the correct section.
- Alphabetical order within a section is preferred; placing near related projects is also acceptable.
- After inserting, scan existing entries for any that should now cross-link to the new entry. Add cross-links only where the relationship is direct and meaningfully clarifies how the stack fits together.

### 3. Mermaid diagram

Do NOT edit the Mermaid diagram automatically.

If the change reveals a new node (project) or edge (relationship) missing from the diagram, stop and ask the user:

> "I found that [X] calls/uses [Y] — this relationship isn't in the `map.md` Mermaid diagram. Should I add it?"

Wait for confirmation before touching the diagram block.

### 4. Validate

After editing, verify:

- New or changed entry is consistent in tone and length with its neighbours.
- All referenced project names exist as `### ` headings in `map.md`.
- Section heading is correct.
- Cross-links use correct anchor slugs (lowercase, spaces replaced with `-`).

## Format Reference

```markdown
### MyNewService

Short description of what it does and its primary data store / runtime. Does not handle X — that is [OtherService](#otherservice)'s responsibility. Relies on [LibraryA](#librarya) for Y; integrates with [ServiceB](#serviceb) for Z.
```
