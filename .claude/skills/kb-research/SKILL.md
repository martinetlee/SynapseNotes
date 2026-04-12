---
name: kb-research
description: Agentic research loop that deeply investigates a topic via web search and saves findings as atomic notes
user_invocable: true
arguments: "Topic to research. Optional flags: --depth=shallow|medium|deep (default: medium), --checkpoint=plan|each|end (default: plan+end)"
---

# /kb-research

Agentic research loop. Given a topic, plan a research agenda, iteratively search the web, follow leads, fill gaps, and produce comprehensive atomic notes.

## Parse Arguments

Extract from $ARGUMENTS:
- **Topic**: the research subject (required)
- **--depth**: `shallow` | `medium` | `deep` (default: `medium`)
- **--checkpoint**: when to pause for user input (default: `plan+end`)
  - `plan` — pause after planning, then run autonomously until synthesis
  - `each` — pause after each sub-question to show findings and ask to continue/redirect
  - `end` — no pauses, full autonomy, show everything at the end
  - Combine with `+`: `plan+end` (default), `plan+each`, etc.

## Depth Levels

| Depth | Follow-up rounds per question | Behavior |
|---|---|---|
| `shallow` | 1 | One round of search per question, follow up on gaps once |
| `medium` | 2 | Follow up on gaps twice per question |
| `deep` | Until diminishing returns, max 10 | Keep researching until new searches add little value, hard cap at 10 rounds total across all questions |

## Phase 1: PLAN

1. **Check existing KB** — Search `notes/` for what the KB already knows about this topic. Read any relevant notes.

2. **Break topic into research questions** — Decompose the topic into 3-7 specific, answerable sub-questions. Consider:
   - What are the core concepts?
   - What are the key debates or trade-offs?
   - What are the practical implications?
   - What does the KB already cover vs. what's missing?

3. **Present research plan** to the user (if `plan` checkpoint is active):

   ```
   ## Research Plan: [Topic]

   Already in KB:
   - [[existing-note-1]] — covers X
   - [[existing-note-2]] — covers Y

   Research questions:
   1. [question 1] — why this matters
   2. [question 2] — why this matters
   ...

   Depth: medium (2 follow-up rounds per question)
   Checkpoints: after plan + at end

   Proceed? (yes / adjust questions / change depth)
   ```

4. Wait for user approval if checkpoint is active. User can adjust questions, depth, or add/remove topics.

## Phase 2: RESEARCH LOOP

For each research question, run the research loop:

1. **Search** — Use WebSearch to find relevant sources for the question.

2. **Read & evaluate** — Use WebFetch to read promising results. Assess:
   - Is this source credible and substantive?
   - Does it answer the question or part of it?
   - Does it raise new questions worth following?

3. **Save useful sources** — Write fetched content to `references/` as markdown files with slugified names. Only save sources that are substantive and worth citing — don't save every search result.

4. **Extract findings** — Note key facts, claims, frameworks, and insights. Track which source each finding comes from.

5. **Identify gaps** — What's still unclear? What follow-up questions emerged?

6. **Follow up** — If depth budget allows, search for gap-filling information. Decrement the follow-up counter.

7. **Checkpoint** — If `each` checkpoint is active, pause after each question:
   ```
   ## Question 1: [question]
   
   Findings so far:
   - [key finding 1] (source: ...)
   - [key finding 2] (source: ...)
   
   Gaps remaining:
   - [gap 1]
   
   Continue to question 2? (yes / redirect / add question / stop here)
   ```

### Concurrency Decision

Claude decides whether to research sub-questions concurrently or sequentially:

- **Concurrent** (via subagents): when questions are independent and answers to one won't inform another. Prefer this for breadth-oriented research.
- **Sequential**: when later questions build on earlier findings, or when the topic is narrow and each answer reshapes the next question. Prefer this for depth-oriented research.

State the choice and reasoning in the plan.

## Phase 3: SYNTHESIS

After all questions are researched:

1. **Compile findings** — Organize all findings across all questions. Identify:
   - Atomic concepts worth their own note
   - Overarching themes or frameworks
   - Contradictions or debates between sources
   - Connections to existing KB notes

2. **Present proposed notes** — Show the user a numbered list of proposed notes:

   ```
   ## Research Complete: [Topic]

   Sources saved to references/:
   - references/source-1.md
   - references/source-2.md

   Proposed notes:
   0. [reference] Research Hub: [Topic] — hub linking all notes from this research
   1. [concept] [Title] — [one-line summary]
   2. [concept] [Title] — [one-line summary]
   3. [insight] [Title] — [one-line summary]
   ...

   Gaps not fully resolved:
   - [gap] — couldn't find definitive answer

   Which notes to save? (all / 1,3,5 / none)
   ```

3. **Wait for user selection** — User picks which notes to create.

4. **Create notes** — For each selected note:
   a. Search existing notes for overlap
   b. If overlap exists, ask user: update or create new?
   c. Create/update following CLAUDE.md format
   d. **Cite all sources inline** — every claim must trace back to a source with `[text](references/filename)` or `[text](url)`
   e. Add `[[wikilinks]]` to the research hub and related notes, in both directions

5. **Create research hub note** — A `reference`-type note that:
   - Summarizes the research topic and key findings
   - Lists all sources in `sources` frontmatter
   - Links to all created notes via `[[wikilinks]]`
   - Notes any unresolved gaps

6. **Update taxonomy** — Add new tags to `.kb/taxonomy.yaml`.

7. **Final summary**:
   - Notes created/updated
   - Sources saved
   - Tags applied
   - Links added
   - Gaps flagged for future research

## Rules

- **Never fabricate sources** — every citation must point to a real source that was actually read
- **Never edit or delete existing files in `references/`** — only write new files
- **Atomic notes** — one concept per note, even from a single source
- **Track provenance** — maintain a clear chain from claim → note → source throughout
- **Respect depth limits** — don't exceed the follow-up budget for the chosen depth level
- **Respect checkpoints** — always pause when the user's checkpoint setting says to
- **Be honest about gaps** — if something couldn't be resolved, say so rather than guessing

## Topic

$ARGUMENTS
