---
name: reviewer-model
description: Show or change the model and reasoning effort used by reviewer-agent-codex child reviews. Use when the user asks which reviewer model is active, invokes reviewerModel, or asks to set, clear, or change the reviewer model or effort.
---

# Reviewer Model

Run the bundled `scripts/toggle.py`, passing the user's arguments unchanged:

```bash
python3 <skill-directory>/scripts/toggle.py [arguments]
```

Supported forms:

- No arguments or `status`: show the effective model, effort, and their sources.
- `<model> [<effort>]`: set the model and optionally the effort.
- `effort <level>`: set only the effort.
- `clear`: clear the command-written model override.
- `effort clear`: clear the command-written effort override.

Use the absolute path of this skill's directory. Relay the script's one-line result verbatim.
Settings apply to fresh and resumed child reviews from the next hook invocation.
