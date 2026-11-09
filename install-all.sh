#!/usr/bin/env bash
# Install ai-spend hooks in every git repository directly under a directory
# (default: the current directory). Mode per repository (ADR 001):
#   private on GitHub (yours)           -> trailers
#   public, other remotes, no remote    -> notes (local; never pushed by default)
#   GitHub repositories owned by others -> skipped
# Existing commit-msg/post-commit hooks are never replaced: those repositories
# are reported and left alone (re-run `ai_spend.py install --force` there to chain).
#   ./install-all.sh [DIR] [--dry-run]
set -uo pipefail
DIR="${1:-.}"
DRY="${2:-}"
# Your GitHub login (repositories of other owners are skipped); needs `gh`.
OWNER="${AI_SPEND_OWNER:-$(gh api user -q .login 2>/dev/null)}"
[ -n "$OWNER" ] || { echo "set AI_SPEND_OWNER or log in with gh"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
for d in "$DIR"/*/; do
  d="${d%/}"; [ -d "$d/.git" ] || continue
  url=$(git -C "$d" remote get-url origin 2>/dev/null)
  mode=notes
  case "$url" in
    *github.com*)
      slug=$(echo "$url" | sed -E 's#.*github.com[:/]##; s#\.git$##')
      if [ "${slug%%/*}" != "$OWNER" ]; then echo "skip     $(basename "$d")  (not ours: $slug)"; continue; fi
      [ "$(gh repo view "$slug" --json visibility -q .visibility 2>/dev/null)" = PRIVATE ] && mode=trailers ;;
  esac
  if [ "$DRY" = "--dry-run" ]; then echo "$mode $(basename "$d")"; continue; fi
  res=$(cd "$d" && python3 "$HERE/ai_spend.py" install --mode "$mode" 2>&1 | tail -1)
  case "$res" in *installed*) echo "$mode $(basename "$d")";; *) echo "LEFT     $(basename "$d")  ($res)";; esac
done
