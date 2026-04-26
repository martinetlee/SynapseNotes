---
name: kb-init
description: Create a new knowledge base within the system
user_invocable: true
arguments: "Optional: KB name. If omitted, Claude will ask interactively."
---

# /kb-init

Create and configure a new knowledge base.

## Steps

1. **Gather info** — If $ARGUMENTS provides a name, use it. Otherwise, ask the user:
   - **Name**: short, slugified (e.g. `machine-learning`, `zk-security`). Will become the directory name under `kbs/`.
   - **Description**: one line explaining what this KB is for.
   - **Private?**: Should this KB be excluded from MCP server, unified search, and git? (default: no)

2. **Validate** — Read `kbs.yaml` and check:
   - Name doesn't already exist in the registry
   - Name is lowercase, hyphenated, no spaces or special characters
   - If invalid, explain why and ask for a corrected name

3. **Create the KB** — Three steps:

   a. Add entry to `kbs.yaml`:
   ```yaml
   <name>:
     path: kbs/<name>
     description: "<description>"
     # private: true  (only if user requested)
   ```

   b. Create the directory:
   ```bash
   mkdir -p kbs/<name>
   ```

   c. Rebuild the index to register the new KB:
   ```bash
   python3 .kb/kb-index.py build --kb <name>
   ```

4. **Confirm** — Tell the user:
   - KB created: `kbs/<name>/`
   - How to use it: `/kb-question --kb <name> "your question"`
   - How to search it: `/kb-search --kb <name> "query"`
   - Private status (if applicable): excluded from MCP and unified search
   - Suggest a first note: "Want to start with `/kb-question --kb <name>`?"

## Rules

- Never overwrite an existing KB entry in `kbs.yaml`
- Keep the YAML formatting consistent with existing entries
- Don't set `default: true` unless the user explicitly asks — there should only be one default KB

$ARGUMENTS
