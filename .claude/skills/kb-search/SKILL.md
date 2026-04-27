---
name: kb-search
description: Search the knowledge base and synthesize an answer from matching notes
user_invocable: true
arguments: "The search query. Optional: --kb name"
---

# /kb-search

Search the knowledge base for notes relevant to the user's query and synthesize a grounded answer.

**KB routing**: Parse $ARGUMENTS for `--kb <name>` flag. If specified, search only that KB. If not specified, **infer the best KB from the query**:

1. Read `kbs.yaml` to get all non-private KBs with their descriptions.
2. Run `python3 .kb/kb-index.py quick "QUERY" --kb <name>` for each non-private KB to see which has the strongest match.
3. Also consider KB descriptions — if the query clearly fits a domain KB (e.g., "reentrancy exploit" fits "Blockchain security, auditing, ZK systems"), prefer that KB.
4. **If one KB has significantly stronger matches** (top result score 2x+ higher than other KBs): search that KB only. Mention which KB was selected in the response.
5. **If no clear winner or query is cross-domain**: search unified index (all non-private KBs).
6. The user can always override with `--kb <name>`.

## Steps

1. **Assess query complexity** — Decide the retrieval tier:
   - **Quick lookup** (query is a specific term, name, or concept): Run `python3 .kb/kb-index.py quick "QUERY"` against the selected KB. If it returns a clear match (score > 3.0), read that note and answer directly — skip full search.
   - **Full search** (query needs synthesis across multiple notes): proceed to step 2.

2. **Multi-query search** — Generate 2-3 alternative phrasings of the user's query to overcome vocabulary mismatch. The original query may use different words than the notes.

   For example, if the user asks "How do pig butchering scams work?", also search:
   - "pig butchering compound infrastructure fraud operations"
   - "romance baiting investment scam detection"

   Run with multi-query fusion:
   ```
   python3 .kb/kb-index.py search "ORIGINAL QUERY" --multi "REFORMULATION 1" "REFORMULATION 2" [--kb <name>]
   ```

   **How to reformulate**: Use synonyms, technical terms, related concepts, and different phrasings of the same intent. Think about what words the note *titles* and *tags* would use, not just how a human would phrase the question. Check `.kb/taxonomy.yaml` for relevant tags.

   If the query is already very specific and technical (e.g., "BM25 vs TF-IDF"), a single query is fine — skip `--multi`.

   Optionally filter:
   - `--tags tag1,tag2` to restrict by tags
   - `--type concept` to restrict by note type

3. **Check coverage** — Run `python3 .kb/kb-index.py coverage "QUERY" [--kb <name>]` to assess if the KB has adequate coverage.

   Coverage levels (thresholds configured in `.kb/config.yaml` under `coverage:`):
   - **well-covered**: proceed to synthesis
   - **partially-covered**: synthesize what exists, explicitly flag gaps
   - **not-covered**: tell the user the KB doesn't cover this topic and suggest `/kb-question` or `/kb-research`

4. **Read matching notes** — Read the full content of top-scoring notes (up to 5 for synthesis, read more for verification if needed).

5. **Context sufficiency check** — Before synthesizing, assess:
   - Do the retrieved notes actually contain information to answer this query?
   - Are there aspects of the query that NO retrieved note addresses?

   If context is insufficient: **explicitly state what the KB covers and what it doesn't**. Do NOT fill gaps with Claude's own knowledge unless the user asks. This is the #1 hallucination prevention rule — insufficient context causes more hallucination than no context at all.

6. **Synthesize answer** — Present:

   **Answer** at the top — a coherent response assembled from matched notes. Every factual claim must cite `[[note-name]]`. If synthesizing across multiple notes, show how the pieces connect.

   **Epistemic transparency** — when synthesizing, surface the confidence level:
   - For claims from `verified` notes: present as established facts
   - For claims from `likely` notes: present with source attribution
   - For claims from `speculative` or `opinion` notes: explicitly flag as analysis/opinion, e.g., "According to Prestwich's analysis [opinion]: ..."
   - For claims from `disputed` notes: present both sides

   **Coverage assessment** — what the KB covers well vs what's missing or thin.

   **Notes consulted** — list of all matched notes with title, score, epistemic status, and relevance.

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

## Feedback

If the user indicates the results were wrong or incomplete, log feedback:
```
python3 .kb/kb-index.py feedback log "QUERY" "FAILURE_TYPE" "expected_slug1,slug2" "notes" [--kb <name>]
```
Failure types: `missed` (relevant note not retrieved), `wrong` (irrelevant note ranked high), `stale` (outdated note returned), `irrelevant` (answer didn't address the question).

## Query

$ARGUMENTS
