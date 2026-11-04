#!/usr/bin/env bash
# Install ai-spend hooks in every git repository directly under a directory
# (default ~/Downloads). Mode per repository (ADR 001):
#   private on GitHub (ours)            -> trailers
#   public, other remotes, no remote    -> notes (local; never pushed by default)
#   GitHub repositories owned by others -> skipped
# Existing commit-msg/post-commit hooks are never replaced: those repositories
# are reported and left alone (re-run `ai_spend.py install --force` there to chain).
#   ./install-all.sh [DIR] [--dry-run]
set -uo pipefail
DIR="${1:-$HOME/Downloads}"
DRY="${2:-}"
OWNER="${AI_SPEND_OWNER:-velesnitski}"
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
