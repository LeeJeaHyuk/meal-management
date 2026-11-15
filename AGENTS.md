# Meal management source rules

- Domain code only: meal/weight journal, nutrition catalog, statistics and offline reports.
- The private orchestrator owns model connection, OCR, messaging, permissions and schedules.
- No actual journals, images, conversations, keys, host profiles, fixtures or reports in Git.
- Cloud/catalog actors must not query a personal journal. A role label grants no filesystem access.
- Preserve source message identity, append-only revisions, missing values and nutrition references.
- Do not convert unknown nutrients to zero, label candidates verified or send reports autonomously.
- The shared private orchestrator consumes a pinned Git submodule; publication does not deploy it.
- Use Python standard library only at runtime.
- Run python3 -m unittest discover -s tests -v after changes, plus public-tree checks.
