#!/usr/bin/env python3
"""ai-spend: what each commit cost in AI tokens, recorded in git itself.

Claude Code writes every response's model and token usage to local transcripts
(~/.claude/projects/**/*.jsonl). A commit's spend is the usage of responses that
worked on this repository since the repository's previous commit. It is recorded
on the commit as git trailers (private repositories) or as a git note under
refs/notes/ai-spend (the default: notes are not pushed unless asked to), and read
back into CSV or per-ADR totals. No database, no service, no third-party code.

  ai-spend install [--mode notes|trailers] [--force]   # in a repository
  ai-spend csv [REV_RANGE]                             # one row per commit
  ai-spend adr [REV_RANGE]                             # totals per ADR number
  ai-spend backfill [REV_RANGE] [--dry-run]            # notes for past commits
  ai-spend report [--write PATH]                       # markdown summary file

Hooks call `ai-spend hook commit-msg FILE` and `ai-spend hook post-commit`.
Standard library only. ADR 001.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

NOTES_REF = "refs/notes/ai-spend"
TRAILER_KEYS = ("AI-Model", "AI-Tokens", "AI-Cost")
TRANSCRIPTS = os.environ.get("AI_SPEND_TRANSCRIPTS", os.path.expanduser("~/.claude/projects"))

# USD per million tokens: input, output, cache read. Cache writes are priced
# from input: 1.25x for the 5-minute cache, 2x for the 1-hour cache. Anthropic
# list prices; on a subscription plan this is a measure of use, not of billing.
PRICES = {
    "claude-fable-5-1": (10, 50, 0.25), "claude-fable-5": (10, 50, 1.0),
    "claude-mythos": (10, 50, 1.0),
    "claude-opus-5-5": (4, 20, 0.20), "claude-opus-5": (5, 25, 0.50),
    "claude-opus-4": (5, 25, 0.50),
    "claude-sonnet-5-5": (2, 10, 0.20), "claude-sonnet-5": (2, 10, 0.20),
    "claude-sonnet-4": (3, 15, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01), "claude-haiku-4": (1, 5, 0.10),
}


def price_of(model: str) -> tuple[float, float, float] | None:
    for k in sorted(PRICES, key=len, reverse=True):
        if model.startswith(k):
            return PRICES[k]
    return None


# ── usage ────────────────────────────────────────────────────────────────────

@dataclass
class Spend:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cost: float = 0.0
    responses: int = 0
    unpriced: int = 0
    by_model: dict = field(default_factory=dict)  # model -> cost

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    @property
    def tokens(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def add(self, model: str, u: dict) -> None:
        cc = u.get("cache_creation") or {}
        w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
        if w5 is None and w1 is None:
            w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
        t_in, t_out = u.get("input_tokens") or 0, u.get("output_tokens") or 0
        t_cr = u.get("cache_read_input_tokens") or 0
        self.input += t_in
        self.output += t_out
        self.cache_read += t_cr
        self.cache_write_5m += w5 or 0
        self.cache_write_1h += w1 or 0
        self.responses += 1
        p = price_of(model)
        if p is None:
            self.unpriced += 1
            return
        i, o, r = p
        c = (t_in * i + t_out * o + t_cr * r + (w5 or 0) * i * 1.25 + (w1 or 0) * i * 2) / 1e6
        self.cost += c
        self.by_model[model] = self.by_model.get(model, 0.0) + c

    @property
    def model(self) -> str:
        return max(self.by_model, key=self.by_model.get) if self.by_model else ""


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _belongs(entry: dict, raw: str, repo: str) -> bool:
    """A response worked on this repository when its working directory was inside
    it, or when it named a path inside it (a tool call from a parent directory)."""
    cwd = entry.get("cwd") or ""
    return cwd == repo or cwd.startswith(repo + os.sep) or (repo + os.sep) in raw


TS_RE = re.compile(r'"timestamp":\s*"([^"]+)"')


def usage_events(repo: str, start: float, end: float, root: str = TRANSCRIPTS):
    """(time, model, usage) for each response attributed to `repo` in
    (start, end], once per response: streaming writes one line per content block.
    Cheap text checks (usage present, repo path present, time in window) run
    before any JSON is parsed: transcripts run to gigabytes."""
    seen = set()
    files = [f for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
             if os.path.getmtime(f) > start]
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if '"usage"' not in raw or repo not in raw:
                    continue
                m_ts = TS_RE.search(raw)
                t = _ts(m_ts.group(1)) if m_ts else 0.0
                if not (start < t <= end):
                    continue
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                m = e.get("message") or {}
                u, model, mid = m.get("usage"), m.get("model") or "", m.get("id")
                if not u or not model or model.startswith("<") or not _belongs(e, raw, repo):
                    continue
                if mid:
                    if mid in seen:
                        continue
                    seen.add(mid)
                yield t, model, u


def spend_between(repo: str, start: float, end: float, root: str = TRANSCRIPTS) -> Spend:
    s = Spend()
    for _, model, u in usage_events(repo, start, end, root):
        s.add(model, u)
    return s


# ── formatting ───────────────────────────────────────────────────────────────

def _n(x: int) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if x >= div:
            return f"{x / div:.1f}{unit}"
    return str(x)


def trailers(s: Spend) -> list[tuple[str, str]]:
    return [
        ("AI-Model", s.model or "unknown"),
        ("AI-Tokens", f"in={_n(s.input)} out={_n(s.output)} cache-read={_n(s.cache_read)} "
                      f"cache-write={_n(s.cache_write)}"),
        ("AI-Cost", f"{s.cost:.2f} USD (API list price, {s.responses} responses)"),
    ]


def parse_record(text: str) -> dict:
    """Trailer/note text → {model, input, output, cache_read, cache_write, cost}."""
    out: dict = {}
    for key, val in re.findall(r"^(AI-Model|AI-Tokens|AI-Cost):\s*(.+)$", text, re.M):
        if key == "AI-Model":
            out["model"] = val.strip()
        elif key == "AI-Cost":
            m = re.match(r"([\d.]+)", val)
            out["cost"] = float(m.group(1)) if m else 0.0
        else:
            names = {"in": "input", "out": "output", "cache-read": "cache_read", "cache-write": "cache_write"}
            for k, v in re.findall(r"([a-z-]+)=([\d.]+[kMB]?)", val):
                if k in names:
                    out[names[k]] = _unn(v)
    return out


def _unn(v: str) -> int:
    mult = {"k": 1e3, "M": 1e6, "B": 1e9}.get(v[-1], 1)
    return int(round(float(v.rstrip("kMB")) * mult))


# ── git ──────────────────────────────────────────────────────────────────────

def git(*args: str, cwd: str | None = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def toplevel(cwd: str | None = None) -> str:
    return os.path.realpath(git("rev-parse", "--show-toplevel", cwd=cwd).strip())


def mode(repo: str) -> str:
    return git("config", "--get", "ai-spend.mode", cwd=repo, check=False).strip() or "notes"


def commit_time(rev: str, repo: str) -> float:
    out = git("log", "-1", "--format=%ct", rev, cwd=repo, check=False).strip()
    return float(out) if out else 0.0


def me(repo: str) -> str:
    """The effective author email: config, overridden by GIT_AUTHOR_EMAIL."""
    ident = git("var", "GIT_AUTHOR_IDENT", cwd=repo, check=False)
    m = re.search(r"<([^>]*)>", ident)
    return m.group(1).strip().lower() if m else ""


def my_commits(repo: str, email: str) -> list[tuple[str, float, bool]]:
    """(sha, commit time, is_merge) of every commit authored by `email`, oldest first."""
    out = git("log", "--reverse", "--format=%H %ct %ae %P", "HEAD", cwd=repo, check=False)
    rows = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[2].lower() == email:
            rows.append((parts[0], float(parts[1]), len(parts) > 4))
    return rows


def window_start(rev: str | None, repo: str) -> float:
    """A commit's window opens at its author's previous commit here, not at its
    parent: commits by others (bots, colleagues) in between did not use this
    person's tokens. No previous commit = everything that mentions the repo."""
    mine = my_commits(repo, me(repo))
    if rev is None:  # commit in progress
        return mine[-1][1] if mine else 0.0
    sha = git("rev-parse", rev, cwd=repo).strip()
    prev = 0.0
    for c, t, _ in mine:
        if c == sha:
            return prev
        prev = t
    return prev


# ── hooks ────────────────────────────────────────────────────────────────────

HOOK = """#!/bin/sh
# ai-spend (ADR 001): records this commit's AI token spend. Never blocks a commit.
{prev}command -v python3 >/dev/null && python3 "{script}" hook {name} "$@" || true
"""


def install(repo: str, chosen: str, force: bool, report: str = "") -> str:
    if chosen not in ("notes", "trailers", "off"):
        raise SystemExit("mode must be notes, trailers or off")
    hooks = os.path.join(repo, git("rev-parse", "--git-path", "hooks", cwd=repo).strip())
    os.makedirs(hooks, exist_ok=True)
    script = os.path.realpath(__file__)
    for name in ("commit-msg", "post-commit") + (("pre-commit",) if report else ()):
        path = os.path.join(hooks, name)
        prev = ""
        if os.path.exists(path):
            body = open(path, encoding="utf-8").read()
            if "ai-spend" not in body:
                if not force:
                    raise SystemExit(f"{path} exists; re-run with --force to chain it")
                os.rename(path, path + ".pre-ai-spend")
                prev = f'"$(dirname "$0")/{name}.pre-ai-spend" "$@" || exit $?\n'
            elif os.path.exists(path + ".pre-ai-spend"):
                prev = f'"$(dirname "$0")/{name}.pre-ai-spend" "$@" || exit $?\n'
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(HOOK.format(prev=prev, script=script, name=name))
        os.chmod(path, 0o755)
    git("config", "ai-spend.mode", chosen, cwd=repo)
    if report:
        git("config", "ai-spend.report", report, cwd=repo)
    git("config", "notes.displayRef", NOTES_REF, cwd=repo)
    extra = f", report: {report}" if report else ""
    return f"ai-spend installed in {repo} (mode: {chosen}{extra})"


def hook_commit_msg(msg_file: str, repo: str) -> None:
    if mode(repo) != "trailers":
        return
    text = open(msg_file, encoding="utf-8").read()
    merging = os.path.exists(os.path.join(repo, git("rev-parse", "--git-path", "MERGE_HEAD", cwd=repo).strip()))
    if "AI-Cost:" in text or merging:  # amend, written by hand, or a merge
        return
    s = spend_between(repo, window_start(None, repo), datetime.now(timezone.utc).timestamp())
    if s.responses == 0:
        return
    args = []
    for k, v in trailers(s):
        args += ["--trailer", f"{k}: {v}"]
    git("interpret-trailers", "--in-place", *args, msg_file, cwd=repo)


def hook_post_commit(repo: str) -> None:
    if mode(repo) != "notes":
        return
    head = git("rev-parse", "HEAD", cwd=repo).strip()
    if len(git("log", "-1", "--format=%P", head, cwd=repo).split()) > 1:  # merge
        return
    if git("notes", f"--ref={NOTES_REF}", "show", head, cwd=repo, check=False).strip():
        return
    s = spend_between(repo, window_start(head, repo), commit_time(head, repo))
    if s.responses == 0:
        return
    note = "\n".join(f"{k}: {v}" for k, v in trailers(s))
    git("notes", f"--ref={NOTES_REF}", "add", "-m", note, head, cwd=repo)


# ── reading back ─────────────────────────────────────────────────────────────

ADR_RE = re.compile(r"\bADR[\s#-]*0*(\d{1,4})\b", re.I)


def records(repo: str, rev_range: str = "HEAD") -> list[dict]:
    fmt = "%H%x1f%cs%x1f%an%x1f%s%x1f%B%x1f%N%x1e"
    out = git("log", f"--notes={NOTES_REF}", f"--format={fmt}", rev_range, cwd=repo, check=False)
    rows = []
    for chunk in out.split("\x1e"):
        parts = chunk.strip("\n").split("\x1f")
        if len(parts) < 6:
            continue
        sha, date, author, subject, body, note = parts[:6]
        rec = parse_record(body) or parse_record(note)
        if not rec:
            continue
        adrs = sorted({int(n) for n in ADR_RE.findall(subject + "\n" + body)})
        rows.append({"commit": sha[:10], "date": date, "author": author, "subject": subject,
                     "adr": " ".join(str(a) for a in adrs), **rec})
    return rows


CSV_FIELDS = ["commit", "date", "author", "adr", "model", "input", "output",
              "cache_read", "cache_write", "cost", "subject"]


def write_csv(rows: list[dict], out=sys.stdout) -> None:
    w = csv.DictWriter(out, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def adr_totals(rows: list[dict]) -> list[dict]:
    """Per ADR: commits, tokens and cost. A commit naming two ADRs counts in both;
    commits naming none are totalled as '-'."""
    acc: dict[str, dict] = {}
    for r in rows:
        for a in (r["adr"].split() or ["-"]):
            t = acc.setdefault(a, {"adr": a, "commits": 0, "output": 0, "cache_read": 0, "cost": 0.0})
            t["commits"] += 1
            t["output"] += r.get("output", 0)
            t["cache_read"] += r.get("cache_read", 0)
            t["cost"] += r.get("cost", 0.0)
    return sorted(acc.values(), key=lambda t: (t["adr"] == "-", -t["cost"]))


def render_report(repo: str, recs: list[dict], top: int = 10) -> str:
    """Markdown summary of the commits that carry spend: totals, per ADR, per
    month, most expensive commits. Deterministic: the same history gives the same
    file, so regenerating it on every commit does not churn."""
    name = os.path.basename(repo)
    lines = [f"# AI spend — {name}", ""]
    if not recs:
        return "\n".join(lines + ["No commits carry AI spend yet.", ""])
    total = sum(r.get("cost", 0.0) for r in recs)
    out_t = sum(r.get("output", 0) for r in recs)
    cr_t = sum(r.get("cache_read", 0) for r in recs)
    dates = sorted(r["date"] for r in recs)
    lines += [
        f"As of `{recs[0]['commit'][:8]}` · {len(recs)} commits with spend, {dates[0]} to {dates[-1]} · "
        "Claude Code, API list price (on a subscription: a measure of use, not of billing). "
        "Generated by [ai-spend](https://github.com/velesnitski/ai-spend) (ADR 001); "
        "read it per change, not as a per-person target.",
        "",
        f"**Total: {total:,.2f} USD** · output {_n(out_t)} tokens · cache read {_n(cr_t)} tokens",
        "",
        "## Per ADR",
        "",
        "| ADR | Commits | Output | Cache read | USD |",
        "|----:|--------:|-------:|-----------:|----:|",
    ]
    for t in adr_totals(recs):
        label = "no ADR" if t["adr"] == "-" else t["adr"]
        lines.append(f"| {label} | {t['commits']} | {_n(t['output'])} | {_n(t['cache_read'])} | {t['cost']:,.2f} |")
    months: dict[str, list] = {}
    for r in recs:
        months.setdefault(r["date"][:7], []).append(r)
    lines += ["", "## Per month", "", "| Month | Commits | USD |", "|-------|--------:|----:|"]
    for mo in sorted(months, reverse=True):
        lines.append(f"| {mo} | {len(months[mo])} | {sum(x.get('cost', 0.0) for x in months[mo]):,.2f} |")
    lines += ["", f"## Most expensive commits (top {top})", "",
              "| Commit | Date | ADR | USD | Subject |", "|--------|------|-----|----:|---------|"]
    for r in sorted(recs, key=lambda x: -x.get("cost", 0.0))[:top]:
        subj = r["subject"].replace("|", "\\|")[:80]
        lines.append(f"| `{r['commit'][:8]}` | {r['date']} | {r['adr'] or '–'} | {r.get('cost', 0.0):,.2f} | {subj} |")
    return "\n".join(lines) + "\n"


def hook_pre_commit(repo: str) -> None:
    """Refresh the configured report file and stage it with the commit."""
    path = git("config", "--get", "ai-spend.report", cwd=repo, check=False).strip()
    if not path:
        return
    full = os.path.join(repo, path)
    text = render_report(repo, records(repo))
    if os.path.exists(full) and open(full, encoding="utf-8").read() == text:
        return
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    git("add", "--", path, cwd=repo)


def backfill(repo: str, rev_range: str, dry_run: bool) -> list[tuple[str, Spend]]:
    """Notes for this person's past commits in `rev_range`, from the transcripts
    still on disk (Claude Code deletes old ones per its cleanupPeriodDays). Merges
    and other authors' commits are skipped. One pass over the transcripts, split
    into per-commit windows. Never rewrites history."""
    wanted = set(git("rev-list", rev_range, cwd=repo).split())
    windows, prev = [], 0.0
    for sha, t, merge in my_commits(repo, me(repo)):
        if sha in wanted and not merge:
            windows.append((sha, prev, t))
        prev = t
    windows = [w for w in windows
               if not git("notes", f"--ref={NOTES_REF}", "show", w[0], cwd=repo, check=False).strip()
               and "AI-Cost:" not in git("log", "-1", "--format=%B", w[0], cwd=repo)]
    if not windows:
        return []
    spends = {sha: Spend() for sha, _, _ in windows}
    for t, model, u in usage_events(repo, min(w[1] for w in windows), max(w[2] for w in windows)):
        for sha, lo, hi in windows:
            if lo < t <= hi:
                spends[sha].add(model, u)
                break
    done = []
    for sha, _, _ in windows:
        s = spends[sha]
        if s.responses == 0:
            continue
        done.append((sha, s))
        if not dry_run:
            note = "\n".join(f"{k}: {v}" for k, v in trailers(s))
            git("notes", f"--ref={NOTES_REF}", "add", "-m", note, sha, cwd=repo)
    return done


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ai-spend", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install")
    p.add_argument("--mode", default="notes")
    p.add_argument("--force", action="store_true")
    p.add_argument("--report", default="", help="refresh this markdown file on every commit (private repos)")
    p = sub.add_parser("report")
    p.add_argument("--write", default="", help="write to this path instead of stdout")
    p = sub.add_parser("csv")
    p.add_argument("range", nargs="?", default="HEAD")
    p = sub.add_parser("adr")
    p.add_argument("range", nargs="?", default="HEAD")
    p = sub.add_parser("backfill")
    p.add_argument("range", nargs="?", default="HEAD")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("hook")
    p.add_argument("name", choices=["commit-msg", "post-commit", "pre-commit"])
    p.add_argument("args", nargs="*")
    a = ap.parse_args(argv)

    repo = toplevel()
    if a.cmd == "install":
        print(install(repo, a.mode, a.force, a.report))
    elif a.cmd == "hook":
        try:  # a hook must never block or break a commit
            if a.name == "commit-msg" and a.args:
                hook_commit_msg(a.args[0], repo)
            elif a.name == "post-commit":
                hook_post_commit(repo)
            elif a.name == "pre-commit":
                hook_pre_commit(repo)
        except Exception as e:  # noqa: BLE001
            print(f"ai-spend: skipped ({type(e).__name__})", file=sys.stderr)
    elif a.cmd == "report":
        text = render_report(repo, records(repo))
        if a.write:
            os.makedirs(os.path.dirname(os.path.join(repo, a.write)) or ".", exist_ok=True)
            with open(os.path.join(repo, a.write), "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"wrote {a.write}")
        else:
            sys.stdout.write(text)
    elif a.cmd == "csv":
        write_csv(records(repo, a.range))
    elif a.cmd == "adr":
        recs = records(repo, a.range)
        print(f"{'ADR':>5} {'commits':>7} {'output':>8} {'cache rd':>9} {'USD':>9}")
        for t in adr_totals(recs):
            print(f"{t['adr']:>5} {t['commits']:>7} {_n(t['output']):>8} {_n(t['cache_read']):>9} {t['cost']:>9.2f}")
        # Per-ADR rows can overlap (a commit naming two ADRs); the total does not.
        print(f"{'total':>5} {len(recs):>7} {'':>8} {'':>9} {sum(r.get('cost', 0.0) for r in recs):>9.2f}")
    elif a.cmd == "backfill":
        done = backfill(repo, a.range, a.dry_run)
        for sha, s in done:
            print(f"{sha[:10]} {s.model:<20} out={_n(s.output):>6} {s.cost:>8.2f} USD")
        print(f"{'would add' if a.dry_run else 'added'} {len(done)} note(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
