# Development requirements

Use test-driven development for every behavioral change:

1. Add the smallest test that expresses the missing behavior and run it to confirm red.
2. Make the minimum production change needed to reach green.
3. Refactor only while the full suite remains green.

Run Python tooling in the `amygdala` conda environment. The canonical test command is:

```bash
conda run --no-capture-output -n amygdala python -m pytest -p no:cacheprovider
```

Keep the runtime standard-library-only and compatible with Python 3.9+. Never parse Codex rollout
JSONL; build behavior from documented hook payloads and lifecycle events. Commit review remains
fail-closed, while stop supervision remains fail-open and bounded.
