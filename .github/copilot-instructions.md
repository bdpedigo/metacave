# CAVE Stack — Workspace Instructions

## Knowledge Capture Requirement

Whenever you answer a question about how any service, library, or component in this repo works — including "what is X?", "what does X use?", "what stores/handles/serves X?" — you **must** record the answer in `summaries/q-and-a.md` under the appropriate service heading, following the format in that file.

If the question is non-trivial (answer requires more than 3 sentences), also write a submap in `summaries/submaps/`.

This rule applies whether or not the `how` skill was explicitly invoked. The `how` skill provides the full procedure; this instruction enforces the minimum output requirement.

## Skill Invocation

The `how` skill (`.agents/skills/how/SKILL.md`) **must** be loaded as a **standalone tool call before any other action** when the user asks any question about CAVE stack internals. Do not parallelize the skill load with directory listings or code searches.
