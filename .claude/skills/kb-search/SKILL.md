---
name: kb-search
description: Search the knowledge base and synthesize an answer from matching notes
user_invocable: true
arguments: The search query
---

# /kb-search

Search the knowledge base for notes relevant to the user's query and synthesize a grounded answer.

## Steps

1. **Rebuild index if stale** — Run `python3 .kb/kb-index.py build` if the index is missing or if notes have been added/modified since last build. Check `.kb/index/metadata.json` timestamp.

2. **Semantic search** — Run `python3 .kb/kb-index.py search "QUERY"` to get ranked results. This uses TF-IDF with contextual metadata prepending (Anthropic's contextual retrieval approach).

   Optionally filter:
   - `--tags tag1,tag2` to restrict by tags
   - `--type concept` to restrict by note type

3. **Check coverage** — Run `python3 .kb/kb-index.py coverage "QUERY"` to assess if the KB has adequate coverage.

   Coverage levels:
   - **well-covered** (top_score > 0.4, 3+ relevant notes): proceed to synthesis
   - **partially-covered** (top_score > 0.2, 1+ note): synthesize what exists, explicitly flag gaps
   - **not-covered** (top_score < 0.2): tell the user the KB doesn't cover this topic and suggest `/kb-question` or `/kb-research`

4. **Read matching notes** — Read the full content of top-scoring notes (up to 5 for synthesis, read more for verification if needed).

5. **Context sufficiency check** — Before synthesizing, assess:
   - Do the retrieved notes actually contain information to answer this query?
   - Are there aspects of the query that NO retrieved note addresses?

   If context is insufficient: **explicitly state what the KB covers and what it doesn't**. Do NOT fill gaps with Claude's own knowledge unless the user asks. This is the #1 hallucination prevention rule — insufficient context causes more hallucination than no context at all.

6. **Synthesize answer** — Present:

   **Answer** at the top — a coherent response assembled from matched notes. Every factual claim must cite `[[note-name]]`. If synthesizing across multiple notes, show how the pieces connect.

   **Coverage assessment** — what the KB covers well vs what's missing or thin.

   **Notes consulted** — list of all matched notes with title, score, and relevance.

7. **Handle edge cases**:
   - **No matches**: "The KB has no coverage on this topic." Suggest `/kb-question <query>` or `/kb-research <topic>`.
   - **Stale/deprecated notes**: Flag if any retrieved notes have `valid_until` dates passed or are deprecated. Suggest updating.
   - **Contradictions**: If retrieved notes contain conflicting information, surface both perspectives and note the conflict.

## NLI-Based Claim Verification (Post-Synthesis)

After drafting the answer, self-check:
- For each factual claim in the answer, verify it is directly supported by a specific note
- If a claim cannot be traced to a specific note, either remove it or explicitly mark it as "Claude's assessment, not from KB"
- If notes contradict each other, present both views with citations rather than choosing one

## Rules

- **Never hallucinate** — only cite notes that exist. If the KB doesn't cover it, say so.
- **Abstain over guess** — saying "I don't know" is better than generating an unsupported answer
- Use `[[note-name]]` (without `.md`) for all citations
- Respect temporal validity — deprioritize expired/deprecated notes
- Keep the synthesized answer concise; point to notes for full detail

## Query

$ARGUMENTS
