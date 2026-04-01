---
name: add-repo
description: "Add a new repository to the CAVE stack map. Use when: integrating a new submodule or project into summaries/map.md; updating an existing entry; onboarding a new CAVE-adjacent tool. Reads READMEs and source files, determines the project's section, writes a 1-3 sentence entry following the established format."
argument-hint: "Name or path of the repository to add (e.g. 'MyNewService' or 'submodules/MyNewService')"
---

# Add Repo to Stack Map

## Purpose

Integrate a new (or updated) repository into [`summaries/map.md`](../../../summaries/map.md) — the high-level map of every project in the CAVE connectomics stack.

## Context

[`summaries/map.md`](../../../summaries/map.md) documents each project in 1-3 sentences covering:

1. **What it does** — its primary responsibility
2. **What it does NOT do** — explicit scope boundaries (helps readers avoid confusion with related projects)
3. **What it integrates with** — other stack projects it depends on or is consumed by

Entries are grouped into sections that mirror the CAVE profile in [`cave-profile/README.md`](../../../cave-profile/README.md):

| Section                         | Typical members                                                   |
| ------------------------------- | ----------------------------------------------------------------- |
| **Micro-Services**              | Flask/REST API services deployed to Kubernetes                    |
| **Libraries**                   | Python packages shared by multiple services (no HTTP server)      |
| **Access Tools**                | Python client libraries used by end-users/researchers             |
| **CAVE-adjacent Tools**         | External tools that integrate with CAVE but are not owned by CAVE |
| **Deployment / Infrastructure** | Helm charts, Terraform modules, deployment templates              |

## Procedure

### 1. Read the CAVE profile

Read [`cave-profile/README.md`](../../../cave-profile/README.md) to understand where this project fits in the overall architecture. Note which section it belongs to and which other projects it mentions as dependencies or consumers.

### 2. Read the project's documentation

For the target repo (e.g. `submodules/<RepoName>/`), read in priority order:

- `README.md` or `README.rst` — primary source of truth
- If README is absent or very sparse, read `pyproject.toml` / `setup.py` / `setup.cfg` for package description, then look at the main source file(s) to infer purpose

Flag entries where documentation was sparse or absent with an italicised note, e.g.:  
_No README present; purpose inferred from source code._

### 3. Determine the section

Use the table above. When ambiguous, prefer the "lowest" layer — a library that also exposes a REST API should be listed as a Micro-Service if it is deployed independently.

### 4. Draft the entry

Write **1-3 sentences** following this structure:

```
[Project name] does X [and Y]. It does NOT do Z [— that is <OtherProject>'s responsibility].
Relies on / integrates with [ProjectA] and [ProjectB].
```

Rules:

- First sentence: positive description of the primary responsibility.
- Scope boundary sentence: explicitly say what the project does NOT handle. Start with "Does not…" or "Not a…".
- Integration sentence: name other stack projects it depends on _or_ that depend on it. Use the exact section-heading name of the referenced project as it appears in `map.md`.
- Keep it factual and concise — no marketing language.
- If the project is purely infrastructure/tooling with no connectomics logic, say so.

### 5. Insert the entry into map.md

- Place the `### <ProjectName>` heading and body inside the correct section.
- Alphabetical order within a section is preferred but not required; placing it near related projects is acceptable.
- Do NOT reformat or rewrite existing entries.

### 6. Back-fill related entries

After drafting the new entry, scan `map.md` for existing entries that should reference the new project but currently do not. For each such entry, add a cross-link in its integration sentence. Common cases:

- A service that uses the new library for a core operation (e.g. meshing, skeletonization)
- A library that the new project wraps or extends
- A client that provides access to the new project's outputs

Only amend entries where the relationship is direct and materially clarifies how the stack fits together. Do NOT rewrite or reformat the rest of the entry.

### 7. Validate

After editing, quickly re-read the surrounding entries to confirm:

- The new entry is consistent in tone and length with its neighbours.
- Any projects referenced by name exist as headings in `map.md`.
- The section heading is correct.
- All back-filled cross-links use the correct anchor slug.

## Format reference

```markdown
### MyNewService

Short description of what it does and its primary data store / runtime. Does not handle X — that is [OtherService](#otherservice)'s responsibility. Relies on [LibraryA](#librarya) for Y; integrates with [ServiceB](#serviceb) for Z.
```

Cross-links to other entries use standard markdown anchor syntax: `[DisplayName](#lowercased-heading-slug)`.

## Example

The following entries illustrate the expected style:

**Micro-Service (has clear scope boundary):**

> AnnotationEngine is a REST API service for creating, reading, and updating spatial annotations in PostgreSQL/PostGIS, completely independent of segmentation state. Does not handle segmentation-to-annotation linkage or versioning — that responsibility belongs to MaterializationEngine. Relies on DynamicAnnotationDB for database operations and EMAnnotationSchemas for schema definitions; integrates with middle_auth for authorization.

**Library (not a service, shared dependency):**

> EMAnnotationSchemas is a Python library defining the schema types for CAVE annotations (e.g. synapse, cell type, reference annotations). Not a service — it is a shared dependency used by both AnnotationEngine and MaterializationEngine to generate and validate database schemas.

**Sparse documentation (flag it):**

> PCGL2Cache tracks, caches, and serves precomputed summary statistics (e.g. size, shape features) for "level 2" nodes of the PyChunkedGraph to speed up downstream skeletonization and analysis. Has a worker component driven by Google Pub/Sub events from PyChunkedGraph. _The README is very sparse; the exact statistics cached are not well documented beyond the CAVE profile description._
