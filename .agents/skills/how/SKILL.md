---
name: how
description: "Investigate how a specific operation or feature works in the CAVE stack and record the findings. Use when: asked 'how does X work?', 'how is X implemented?', 'how does service Y handle Z?', 'how does the stack perform X?', 'what is X?', 'what does X use?', 'what stores/handles/serves X?', 'what is the backend/database/storage for X?', 'what library does X depend on?'. Researches relevant code, synthesizes a high-level answer, writes to q-and-a.md and submaps/, and flags missing documentation. Do not use for implementation
questions, e.g. 'how do I add a new API endpoint to service Y?' or 'how do I set up service Z in production?' — those are architecture and implementation questions, not "how does it work?" questions."
argument-hint: "The 'how' question to investigate (e.g. 'how does MaterializationEngine update root IDs?')"
---

# How — CAVE Stack Investigation

> **LOADING REQUIREMENT:** Read this file in a **standalone tool call** before taking any other action — do not parallelize the skill load with repo exploration or directory listing. The procedure below must be read in full before any research begins.

## Purpose

Answer a "how does X work?" question by triaging `summaries/map.md`, reading targeted source code, and recording the findings in `summaries/q-and-a.md` (and `summaries/submaps/` for mechanical detail). Each invocation incrementally builds up the team's shared understanding of the codebase.

## Output Files

| File                           | What goes in it                                                                                                                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `summaries/q-and-a.md`         | Service- or topic-indexed Q&A. Max 2-3 sentences per entry; links to a submap when one was written.                                                                             |
| `summaries/submaps/{topic}.md` | Detailed mechanics when the answer requires more than 3 sentences. Flat by default; split into `summaries/submaps/{topic}/` sub-files only when a single file becomes unwieldy. |
| `summaries/map.md`             | Updated via the `update-map` skill when the investigation reveals missing or incorrect service descriptions.                                                                    |

## Procedure

### Phase A — Triage

1. Read `summaries/map.md` in full.
2. Identify 2–4 services or libraries most likely relevant to the question, based on their descriptions and integration sentences.
3. Check `summaries/submaps/` for any existing file on this topic. If found, read it before diving into code — it may already answer the question or narrow the scope.

### Phase B — Research

Run Explore subagents in parallel, one per candidate repo, each with:

- Read the README first.
- Read only the source files directly relevant to the specific operation — do not crawl the entire repo.
- Note inter-service calls, data flows, and key implementation details found.

**Early-exit rule — apply this before and during research:**

If a critical piece of the answer depends on something that is undocumented or absent from the codebase (e.g., how segmentation data is physically stored, what a specific external API returns), **stop immediately** and ask the user:

> "I can't determine [X] from the codebase — do you have context on this?"

Do NOT guess, infer beyond what the code shows, or proceed past the gap. If the user provides context, treat it as ground truth and note it as "user-provided" in the output. If the user says "continue anyway", note the gap explicitly in the output and move on.

### Phase C — Synthesize

Compose a high-level answer covering:

- What happens, and in what order
- Which services and libraries are involved at each step
- Key data flows or state transitions
- Any gaps that remained unresolved

The answer must be grounded in what was actually found in the code or stated by the user. Do not speculate.

### Phase D — Write Outputs

Perform these steps in order.

#### 1. Update `summaries/q-and-a.md`

Locate or create the appropriate section heading:

- Primary service involved → `## ServiceName`
- Cross-cutting question (3+ services equally involved) → `## TopicName` with the services named in the entry body

Append the entry under that heading. Do not remove or rewrite existing entries.

```markdown
**Q: {original question as asked}**
{2-3 sentence answer}. See [submaps/{topic}.md](submaps/{topic}.md) for mechanics.
```

The 2-3 sentence answer must stand alone as a useful summary. The submap link is only present when a submap was written in step 2.

#### 2. Write or update a submap (when needed)

Create or update `summaries/submaps/{topic}.md` when the mechanical answer requires more than 3 sentences.

Submap format:

```markdown
# {Topic Title}

> Last investigated: {date}

## {Aspect or Step 1}

...

## {Aspect or Step 2}

...
```

If a submap file is already long and this investigation adds another substantial section, consider splitting into `summaries/submaps/{topic}/` sub-files. Only split when it meaningfully helps navigation.

If user-provided context was used, include a callout:

```markdown
> **Note:** The following is based on user-provided context, not inspected source code.
```

#### 3. Update `map.md` (when needed)

If the investigation found anything missing or incorrect in existing `map.md` descriptions, invoke the `update-map` skill to make the change. Do not edit `map.md` directly.

#### 4. Audit Mermaid diagram for missing nodes and edges

After Phase B, explicitly enumerate every **physical resource** discovered during research:

- Databases (Cloud Datastore, Cloud SQL, BigTable, Redis, PostgreSQL, etc.)
- Storage buckets (GCS, S3, local filesystem)
- Message queues / Pub/Sub topics
- External APIs or services called at runtime

For each resource, check the `map.md` Mermaid diagram for:

1. A **node** representing that resource (storage nodes use `[(...)]` syntax)
2. An **edge** from the relevant service or library to that node

If either is missing, do NOT edit the diagram. Ask the user for each gap:

> "I found that [X] uses [resource Y] — this [node / edge] isn't in the `map.md` Mermaid diagram. Should I add it?"

Wait for confirmation, then invoke `update-map` to make the change. Do not batch-ask — present each gap separately so the user can confirm or decline individually.

## What This Skill Does NOT Do

- Does not answer "why was it designed this way?" — that is architecture discussion.
- Does not perform root-cause debugging of bugs or runtime errors.
- Does not crawl repos exhaustively — it targets the specific operation.
- Does not edit `map.md` directly — always delegates to `update-map`.

## Completion Checklist

Before sending a final response to the user, confirm each item is done:

- [ ] Phase A complete: `summaries/map.md` read, relevant services identified, existing submaps checked
- [ ] Phase B complete: Explore subagent(s) run, code findings grounded (no speculation)
- [ ] Phase C complete: Synthesized answer covers what/order/services/data-flow/gaps
- [ ] Phase D-1 complete: Entry written to `summaries/q-and-a.md` under correct heading
- [ ] Phase D-2 complete: Submap written (or confirmed unnecessary because answer fits in ≤3 sentences)
- [ ] Phase D-3 complete: `map.md` updated via `update-map` skill if anything was wrong/missing, or confirmed no change needed
- [ ] Phase D-4 complete: All physical resources (databases, buckets, queues, APIs) enumerated; each missing Mermaid node or edge surfaced to user individually (or confirmed diagram is complete)
