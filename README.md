# ai-spend

What each commit cost in AI tokens, recorded in git itself: no database, no
service, Python standard library only. Design: [ADR 001](docs/adr/001-token-spend-in-git.md).

```bash
# in a repository
python3 ~/Downloads/ai-spend/ai_spend.py install                  # notes (safe for public repos)
python3 ~/Downloads/ai-spend/ai_spend.py install --mode trailers  # private repos: in the message

python3 ~/Downloads/ai-spend/ai_spend.py csv  > spend.csv         # one row per commit
python3 ~/Downloads/ai-spend/ai_spend.py adr                      # totals per ADR
python3 ~/Downloads/ai-spend/ai_spend.py backfill --dry-run       # past commits, from transcripts on disk
python3 ~/Downloads/ai-spend/ai_spend.py report                    # markdown summary (per ADR, per month, top commits)
```

A stamped commit:

```
AI-Model: claude-opus-5-5
AI-Tokens: in=1.2k out=48.0k cache-read=310.0M cache-write=3.1M
AI-Cost: 98.80 USD (API list price, 214 responses)
```

- Notes live under `refs/notes/ai-spend`. To share them with a (private) remote:
  `git push origin refs/notes/ai-spend`, and fetch with
  `git fetch origin refs/notes/ai-spend:refs/notes/ai-spend`.
- Existing `commit-msg` / `post-commit` hooks: `install --force` chains them.
- Mode later: `git config ai-spend.mode notes|trailers|off`.
- A summary file kept current in the repository (private repos): `install --report docs/ai-spend.md`.
  A pre-commit hook refreshes and stages it, so it always covers history up to the previous commit.
  Not for public repositories: it publishes what the work cost.

Tests: `python3 -m pytest tests`.
