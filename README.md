# ai-spend

**What each commit cost in AI tokens, recorded in git itself.**

ai-spend reads the token usage Claude Code already writes to its local
transcripts and stamps every commit with the spend of the work behind it:

```
feat: add rate limiting to the export endpoint (ADR 12)

AI-Model: claude-opus-5-5
AI-Tokens: in=1.2k out=21.5k cache-read=4.2M cache-write=310.0k
AI-Cost: 4.27 USD (API list price, 37 responses)
```

From there, cost per commit, per change (ADR) and per month is a query on your
history: no database, no service, no account. One Python file, standard library
only. Design: [ADR 001](docs/adr/001-token-spend-in-git.md).

## Quick start

```bash
git clone https://github.com/velesnitski/ai-spend
cd your-repository
python3 /path/to/ai-spend/ai_spend.py install            # notes mode (safe for public repos)
python3 /path/to/ai-spend/ai_spend.py backfill --dry-run # what past commits cost
```

Commit as usual. With Claude Code working in the repository, each commit gets its
spend; commits made without it get nothing.

## Commands

| Command | What it does |
|---|---|
| `install [--mode notes\|trailers\|off] [--report PATH] [--force]` | Install the hooks in the current repository |
| `adr [RANGE]` | Totals per ADR number named in commit messages |
| `csv [RANGE]` | One row per commit: date, author, ADR, model, tokens, cost |
| `report [--write PATH]` | Markdown summary: total, per ADR, per month, most expensive commits |
| `backfill [RANGE] [--dry-run] [--force]` | Add notes to past commits from the transcripts still on disk |

`RANGE` is any git revision range (default: the whole history). `install-all.sh
[DIR] [--dry-run]` installs into every repository under a directory, choosing the
mode from each one's GitHub visibility.

## Modes

| Mode | Where the spend goes | Use for |
|---|---|---|
| `notes` (default) | git notes under `refs/notes/ai-spend` — local, never pushed unless you ask | public repositories |
| `trailers` | the commit message | private repositories, where the history should carry the numbers |
| `off` | nowhere | pausing it |

Share notes with a private remote: `git push origin refs/notes/ai-spend`; fetch
them with `git fetch origin refs/notes/ai-spend:refs/notes/ai-spend`.

`--report docs/ai-spend.md` adds a pre-commit hook that keeps a summary file in the
repository, refreshed with each commit (it covers history up to the previous
commit). For private repositories: a report in a public repository publishes how
much AI went into the work.

## How it attributes spend

- **Source:** Claude Code's transcripts (`~/.claude/projects/**/*.jsonl`); every
  response records its model and token usage. `AI_SPEND_TRANSCRIPTS` points
  elsewhere.
- **Which responses:** those whose working directory was inside the repository, or
  that named a path inside it (tool calls made from a parent directory).
- **Which window:** from the same author's previous commit in the repository.
  Commits by other people and bots in between did not use your tokens; merge
  commits get nothing. If you commit under several emails, list them:
  `git config --global --add ai-spend.email work@example.com`.
- **Counting:** each response once — streamed responses span several transcript
  lines and the most complete one wins.
- **Cost:** Anthropic API list prices, cache reads and writes included. On a
  subscription plan this measures use, not billing. Unknown (local) models are
  counted in tokens and priced at zero.

## Limits

- Only Claude Code on the machine with the hooks installed is counted.
- Work spread over several repositories in one session counts in each repository
  whose path it named: per-repository totals are right, sums across repositories
  overcount.
- Backfill reaches only as far back as the transcripts Claude Code keeps
  (`cleanupPeriodDays`).
- Read cost per change, next to what the change delivered. As a per-commit or
  per-person target it stops measuring anything: commits just get smaller.

Hooks never block a commit: any failure prints one line and the commit goes
through.

## Related

[claude-token-trailers](https://github.com/y-core-engineering/claude-token-trailers)
uses the same trailer names, so its tooling reads these commits too.
[ccusage](https://github.com/ryoppippi/ccusage) reports usage per day and session.

## Development

```bash
python3 -m pip install pytest
python3 -m pytest tests
```

Changes are recorded as ADRs in [docs/adr](docs/adr). License: [MIT](LICENSE).
