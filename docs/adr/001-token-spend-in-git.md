# ADR 001: Record each commit's AI token spend in git

**Status:** Accepted
**Date:** 2026-11-04

## Problem

We want to know what AI-assisted work costs: tokens in and out, cache, and money,
per commit and per change (ADR), across repositories. The organization-wide
usage APIs report per person and per day, not per change, and need an admin
credential. A shared table or a warehouse adds a service to run and a place for
two people to write at once.

## Decision

1. **The spend travels with the commit.** Each commit is stamped with the usage of
   the AI responses that worked on that repository since its author's previous
   commit there:

   ```
   AI-Model: claude-opus-5-5
   AI-Tokens: in=1.2k out=48.0k cache-read=310.0M cache-write=3.1M
   AI-Cost: 98.80 USD (API list price, 214 responses)
   ```

   Trailer names match the open-source `claude-token-trailers` convention, so its
   tooling reads our commits too.
2. **Source: Claude Code's local transcripts** (`~/.claude/projects/**/*.jsonl`).
   Every response records its model and token usage. A response belongs to the
   repository when its working directory was inside it, or when it named a path
   inside it (tool calls made from a parent directory). Each response is counted
   once: streaming writes one line per content block.
3. **The window is the author's, not the parent's.** It opens at the same author's
   previous commit in the repository. Commits by other people and bots in between
   did not use this person's tokens. Merge commits get nothing.
4. **Two modes, chosen per repository:**
   - `notes` (default): a git note under `refs/notes/ai-spend`. Notes are not
     pushed unless explicitly asked to, so a public repository never publishes
     spend or model choice by accident.
   - `trailers`: in the commit message. For private repositories, where the
     numbers should reach everyone who reads the history and the reports built
     on it.
5. **Cost is API list price.** On a subscription plan it measures use, not
   billing. Unknown models (local ones) are counted in tokens and priced at zero.
6. **Reading back is a query, not a store:** `ai-spend csv` (one row per commit)
   and `ai-spend adr` (totals per ADR number named in the commit message). A CSV
   exists only when asked for.
7. **Backfill** adds notes to past commits from the transcripts still on disk. It
   never rewrites history.
8. **A hook never blocks a commit.** Any failure is a one-line notice and the
   commit goes through.

## Consequences

- Only work done through Claude Code on this machine is counted. Other tools and
  other machines need their own source; the trailer format is the contract.
- Work spread over several repositories in one session is attributed to each
  repository whose path it named, so a response can count in two repositories.
  Per-repository totals are accurate; summing across repositories overcounts.
- Old commits can only be backfilled while their transcripts exist: Claude Code
  deletes transcripts older than its `cleanupPeriodDays`.
- Cost per commit must not become a target: commits would shrink and split. Read it
  per change (ADR, merge request) and next to what the change delivered.
