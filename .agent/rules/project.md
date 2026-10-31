- Read docs/SPEC.md for product decisions. Follow them, don't revisit.
- Work ONE phase at a time. Stop after each phase and summarize.
- Never run destructive git commands (filter-repo, reset --hard, force push).
  Print them for me instead.
- Never read, print or commit secrets. Use .env.example.
- No DB, no login. Pin all dependency versions.
- Must run on linux/arm64 and linux/amd64.
- Every security function needs pytest tests. Run tests before saying done.
- Keep the existing UI look; improve, don't redesign.