
# EasyCanvas - Claude Code Instructions

Before making any code changes:

1. Read `AGENTS.md` and follow all repository rules.
2. Read `docs/STABILITY_LOG.md` before fixing any bug or regression.
3. Check whether the issue matches a previously fixed issue before creating a new patch.
4. Diagnose the exact root cause before changing code.
5. Prefer the smallest general fix.
6. Do not hardcode course IDs, assignment names, filenames, or user-specific data.
7. Preserve already-working behavior.
8. Add regression tests for every bug fix.
9. Run relevant tests before considering a change complete.
10. Explain what changed, why it changed, and what remains unverified.

For EasyCanvas specifically:
- Canvas and confirmed user data are the source of truth.
- Do not use the LLM as the source of truth for deadlines, points, submission status, or course facts.
- Do not bypass assignment AI-use restrictions.
- Do not weaken retrieval, citation, policy, conversation, or caching behavior to fix a single example.
- Before fixing a recurring issue, compare it against `docs/STABILITY_LOG.md`.
