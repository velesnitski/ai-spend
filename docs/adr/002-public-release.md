# ADR 002: Public release

**Status:** Accepted
**Date:** 2026-11-09

## Context

ai-spend started as a private tool. Nothing in it is specific to one organization:
it reads Claude Code's local transcripts and writes git trailers or notes. The
repository's history carries no organization names, hosts, people or real spend
figures (the README's example amount is invented).

## Decision

1. **The repository is public, MIT-licensed.**
2. **No personal defaults in code or docs.** `install-all.sh` scans the current
   directory unless given one, and takes the owner whose repositories it treats as
   "yours" from `gh` (or `AI_SPEND_OWNER`). The README uses placeholder paths.
3. **CI runs the tests on Python 3.10–3.13** on every push and pull request, plus a
   syntax check of the installer.
4. **`main` is protected:** the four test checks are required (`strict: false`, so a
   fast-forward from `dev` works once `dev` is green), administrators included, no
   force pushes, no deletion. Work lands on `dev` first.
5. **The privacy defaults stay as they are:** notes mode by default, the report file
   documented as private-repository-only. Making the tool public does not make
   anyone's spend public.

## Consequences

- Releases follow: push `dev` → CI green → fast-forward `main`.
- The Python matrix and the required check names move together: changing one
  without the other leaves `main` waiting for a check that never reports.
