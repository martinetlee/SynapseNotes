---
name: kb-question
description: Ask a question and Claude saves the answer as an atomic note in the knowledge base
user_invocable: true
arguments: The question to research and note down
---

# /kb-question

You are helping the user build their knowledge base. The user asked a question and you need to:

1. **Search existing notes** — Use Glob and Grep to check if `notes/` already has a note covering this topic.
2. **Research the answer** — Use your own knowledge and optionally WebSearch/WebFetch if the question benefits from current information or specific sources.
3. **Answer the question** — Present a clear, thorough answer to the user in the conversation.
4. **Save as atomic note** — After answering:

   a. If an existing note covers the **same atomic concept**, update it with the new information. Show the user what changed.
   
   b. If no existing note matches (or the topic is distinct enough), create a new note.
   
   c. If creating a new note, check for **related** existing notes and add `[[wikilinks]]` in both directions.

5. **Update taxonomy** — Read `.kb/taxonomy.yaml`. Add any new tags used. Write back.

## Note Type Guidance

This skill defaults to `type: question`, which is one of the most useful types for retrieval. However, consider whether the answer reveals something better captured as:
- **insight** — if the answer surfaces an original observation, non-obvious connection, or tradeoff (e.g., "Why is X hard?" → the answer is really about a fundamental tension)
- **concept** — if the question is really "What is X?" and the answer is a pure explanation with no Q&A structure

Prefer `question` when there's a clear question + answer + takeaways structure. Prefer `insight` when the value is in the observation itself.

## Note Creation Rules

- Filename: slugified title, e.g. `tcp-congestion-control.md`
- Save to: `notes/` directory (flat, no subfolders)
- Follow the note format from CLAUDE.md exactly
- For question-type notes, use this body structure:

```markdown
## Question
{the question}

## Answer
{thorough answer with inline citations like [source name](url) wherever a claim comes from a specific source}

## Key Takeaways
- {bullet points for quick scanning}
```

- Always include `related: [[other-note]]` in frontmatter when connections exist
- Set `created` and `updated` to today's date
- Set `type: question`
- Include `sources` in frontmatter if web search was used
- **Always cite sources inline** — every claim from an external source must have a citation near it in the body, not just in frontmatter. Use `[text](url)` for web sources, `[text](file-path)` for ingested files, or `(Source: Title)` for books/papers/other

## After Saving

Tell the user:
- The note filename and path
- Tags applied
- Any related notes that were linked
- Whether this was a new note or an update to an existing one

The question to answer: $ARGUMENTS
