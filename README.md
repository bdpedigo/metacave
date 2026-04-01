# metacave

A meta-repo attepting to keep track of and summarize high level knowledge about the CAVE
and related connectomics tools and projects.

Very much a work in progress and an experiment with getting LLMs to summarize this
architecture well at a high level and iteratively improve its understanding based on
Q and A with a user.

## Usage

I currently don't have anything fancy set up for this, but I have just had this repo
(and its submodules) cloned locally, and have been asking questions about the architecture
via an LLM harness that picks up the [q-and-a skill](.agents/skills/q-and-a.md). This answers my questions about how
something works, but also keeps track what needs to be updated in the high level summary
in [summaries/map.md](summaries/map.md). A record of previous questions and answers is kept in [summaries/q-and-a.md](summaries/q-and-a.md).

### To add a repo:

Make a shallow clone of the new repo under `submodules/`:

```bash
git submodule add --depth 1 https://github.com/<user>/<package>.git submodules/<package>
```

Then point the `add-repo` skill at the new repo to add it to the stack map.