# KB Session Log

Append-only record of research sessions, notes created, and gaps identified.
Claude reads the last 20 entries at session start for continuity.

---

## [2026-04-20] research | Knowledge Storage Paradigms for LLMs
- Researched: vector vs graph vs plain-text RAG, GraphRAG variants, benchmarks, production tradeoffs
- Notes created: 11 (research hub + 10 concepts/insights)
- Key finding: no single paradigm wins; task complexity determines best approach
- Gaps: no head-to-head Obsidian vs vector DB benchmark exists

## [2026-04-20] infrastructure | Citation & Reference System Overhaul
- Fixed: 606 orphaned reference files linked to notes (0 → 498 via URL matching + content matching)
- Updated: CLAUDE.md citation rules (notes cite local references, not external URLs)
- Updated: /kb-research skill to enforce local citations
- Fixed: /kb-review regex parsing error (unquoted parentheses in arguments field)

## [2026-04-21] research | SOC 2 & ISO 27001 Compliance
- Researched: SOC 2 (6 questions), ISO 27001 (5 questions), triple compliance, auditor firms, other frameworks
- Notes created: 14 (research hub + 13 concepts/references)
- Key finding: 65-85% control overlap between SOC 2 and ISO 27001; combined audit saves 20-35%

## [2026-04-21] infrastructure | KB Publishing System
- Created: /kb-publish skill + HTML template + build-report.py
- Features: collapsible sections, search, dark mode, footnotes, wikilink navigation, PDF export
- Published: SOC 2 guide, scam detection deep dive

## [2026-04-21] research | Persistent KB Architecture for LLM Retrieval
- Researched: 15 questions across 5 agents (deep depth) — hallucination prevention, scaling, schema, ops, frameworks
- Key findings: hybrid search mandatory (91% vs 58%); HNSW degrades at 10M+; contextual retrieval reduces failures 67%
- Published: interactive research report (persist-kb-for-llm-retrieval.html)

## [2026-04-21] infrastructure | Search Index & Retrieval Improvements
- Created: .kb/kb-index.py (TF-IDF with contextual metadata, 199 notes indexed)
- Updated: /kb-search (semantic search + context sufficiency + NLI verification)
- Updated: /kb-research (contradiction detection + dedup at ingestion)
- Updated: /kb-review (temporal staleness + retrieval quality metrics)
- Updated: /kb-explain (context sufficiency + claim verification)
- Updated: CLAUDE.md (temporal validity fields: valid_from, valid_until, deprecated_by)

## [2026-04-25] ingest | Recursive Language Models (RLM)
- Ingested: arXiv 2512.24601 + GitHub repo
- Notes created: 6 (hub + architecture + performance + complexity insight + emergent strategies + training)
- Key finding: RLM processes inputs 100x beyond context window; 8B model fine-tuned on 1K trajectories gains 28.3%

## [2026-04-25] ingest | Karpathy LLM Wiki (detailed)
- Updated: [[plain-text-knowledge-storage-for-llms]] with operations (ingest/query/lint) and indexing details
- Created: [[mutable-wiki-vs-immutable-zettelkasten-for-llms]] — the mutable vs immutable debate

## [2026-04-26] infrastructure | KB Design Review
- Identified: 8 design considerations (backup, session log, synthesis, embeddings, topic weighting, citations, archival, reference quality)
- Implementing: #8 backup, #5 session log, #4 synthesis persistence, #6 dense embeddings, #1 topic-weighted search
