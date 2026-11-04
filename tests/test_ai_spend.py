"""ai-spend against synthetic transcripts and throwaway git repositories."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "ai_spend.py")
sys.path.insert(0, os.path.join(HERE, ".."))
import ai_spend as a  # noqa: E402


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def line(mid, t, cwd, model="claude-opus-5-5", out=1000, cr=1_000_000, w5=0, w1=0, extra=""):
    return json.dumps({
        "type": "assistant", "timestamp": iso(t), "cwd": cwd, "sessionId": "s1",
        "message": {"id": mid, "model": model, "content": [{"type": "text", "text": extra}],
                    "usage": {"input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": cr,
                              "cache_creation_input_tokens": w5 + w1,
                              "cache_creation": {"ephemeral_5m_input_tokens": w5,
                                                 "ephemeral_1h_input_tokens": w1}}},
    })


def write_transcript(root, lines, name="t.jsonl"):
    d = os.path.join(root, "proj")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "a") as fh:
        fh.write("\n".join(lines) + "\n")


def test_pricing_counts_cache_reads_and_both_cache_writes():
    s = a.Spend()
    s.add("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                              "cache_read_input_tokens": 1_000_000,
                              "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000,
                                                 "ephemeral_1h_input_tokens": 1_000_000}})
    # 4 + 20 + 0.20 + 4*1.25 + 4*2
    assert s.cost == pytest.approx(37.2)
    s.add("gpt-oss:20b", {"input_tokens": 5, "output_tokens": 5})
    assert s.unpriced == 1 and s.model == "claude-opus-5-5"


def test_window_dedupe_and_attribution(tmp_path):
    repo, other = str(tmp_path / "repo"), str(tmp_path / "other")
    root = str(tmp_path / "t")
    now = time.time()
    write_transcript(root, [
        line("m1", now - 50, repo),
        line("m1", now - 50, repo),                       # same response, second block
        line("m2", now - 40, os.path.dirname(repo), extra=f"edit {repo}/README.md"),  # path named
        line("m3", now - 30, other),                      # another repository
        line("m4", now - 500, repo),                      # before the window
    ])
    s = a.spend_between(os.path.realpath(repo) if os.path.exists(repo) else repo, now - 100, now, root)
    assert s.responses == 2 and s.output == 2000


def test_trailer_text_round_trips():
    s = a.Spend()
    s.add("claude-opus-5-5", {"input_tokens": 1200, "output_tokens": 48000,
                              "cache_read_input_tokens": 310_000_000, "cache_creation_input_tokens": 3_100_000})
    text = "\n".join(f"{k}: {v}" for k, v in a.trailers(s))
    r = a.parse_record(text)
    assert r["model"] == "claude-opus-5-5" and r["output"] == 48000
    assert r["cache_read"] == 310_000_000 and r["input"] == 1200
    assert r["cost"] == pytest.approx(round(s.cost, 2))


def test_adr_totals_overlap_and_unlabelled():
    rows = [{"adr": "74", "cost": 2.0, "output": 1, "cache_read": 1},
            {"adr": "73 74", "cost": 1.0, "output": 1, "cache_read": 1},
            {"adr": "", "cost": 0.5, "output": 1, "cache_read": 1}]
    t = {x["adr"]: x for x in a.adr_totals(rows)}
    assert t["74"]["cost"] == 3.0 and t["74"]["commits"] == 2
    assert t["73"]["cost"] == 1.0 and t["-"]["cost"] == 0.5
    assert a.ADR_RE.findall("feat: x (ADR 074), see ADR-73 and adr #5") == ["74", "73", "5"]  # "ADR 074" and "ADR 74" are one ADR


# ── end to end through real git hooks ───────────────────────────────────────

def run(*cmd, cwd, env=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    env = dict(os.environ, AI_SPEND_TRANSCRIPTS=str(tmp_path / "t"),
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    run("git", "init", "-q", "-b", "main", cwd=r, env=env)
    run("git", "config", "commit.gpgsign", "false", cwd=r, env=env)
    past = dict(env, GIT_COMMITTER_DATE="2000-01-01T00:00:00Z", GIT_AUTHOR_DATE="2000-01-01T00:00:00Z")
    (r / "a.txt").write_text("a")
    run("git", "add", "a.txt", cwd=r, env=past)
    run("git", "commit", "-q", "-m", "init", cwd=r, env=past)
    return r, env, str(tmp_path / "t")


def commit(r, env, msg, name):
    (r / name).write_text(name)
    run("git", "add", name, cwd=r, env=env)
    run("git", "commit", "-q", "-m", msg, cwd=r, env=env)


def test_trailers_mode_stamps_the_commit(repo):
    r, env, root = repo
    run(sys.executable, SCRIPT, "install", "--mode", "trailers", cwd=r, env=env)
    write_transcript(root, [line("x1", time.time() - 5, os.path.realpath(r))])
    commit(r, env, "feat: thing (ADR 001)", "b.txt")
    msg = run("git", "log", "-1", "--format=%B", cwd=r, env=env).stdout
    assert "AI-Model: claude-opus-5-5" in msg and "AI-Cost:" in msg
    out = run(sys.executable, SCRIPT, "csv", cwd=r, env=env).stdout
    assert out.splitlines()[0].startswith("commit,date,author,adr,model")
    assert ",1,claude-opus-5-5," in out.splitlines()[1]


def test_notes_mode_is_the_default_and_leaves_the_message_alone(repo):
    r, env, root = repo
    run(sys.executable, SCRIPT, "install", cwd=r, env=env)
    write_transcript(root, [line("n1", time.time() - 5, os.path.realpath(r))])
    commit(r, env, "fix: other (ADR 2)", "c.txt")
    assert "AI-Cost" not in run("git", "log", "-1", "--format=%B", cwd=r, env=env).stdout
    note = run("git", "notes", "--ref=refs/notes/ai-spend", "show", "HEAD", cwd=r, env=env).stdout
    assert "AI-Cost:" in note
    adr = run(sys.executable, SCRIPT, "adr", cwd=r, env=env).stdout
    assert "    2       1" in adr and "total" in adr


def test_commit_without_ai_activity_gets_nothing(repo):
    r, env, _ = repo
    run(sys.executable, SCRIPT, "install", "--mode", "trailers", cwd=r, env=env)
    commit(r, env, "chore: by hand", "d.txt")
    assert "AI-" not in run("git", "log", "-1", "--format=%B", cwd=r, env=env).stdout


def test_hook_never_blocks_a_commit_on_broken_transcripts(repo):
    r, env, root = repo
    run(sys.executable, SCRIPT, "install", "--mode", "trailers", cwd=r, env=env)
    os.makedirs(os.path.join(root, "proj"), exist_ok=True)
    with open(os.path.join(root, "proj", "bad.jsonl"), "w") as fh:
        fh.write('{"usage": not json\n')
    commit(r, env, "still commits", "e.txt")
    assert run("git", "log", "-1", "--format=%s", cwd=r, env=env).stdout.strip() == "still commits"


def test_install_refuses_then_chains_an_existing_hook(repo):
    r, env, _ = repo
    hooks = r / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "commit-msg").write_text("#!/bin/sh\necho mine >> \"$1\"\n")
    os.chmod(hooks / "commit-msg", 0o755)
    res = run(sys.executable, SCRIPT, "install", cwd=r, env=env, check=False)
    assert res.returncode != 0 and "--force" in (res.stderr + res.stdout)
    run(sys.executable, SCRIPT, "install", "--force", cwd=r, env=env)
    commit(r, env, "chained", "f.txt")
    assert "mine" in run("git", "log", "-1", "--format=%B", cwd=r, env=env).stdout


def test_backfill_skips_other_authors_and_merges(repo):
    r, env, root = repo
    t0 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc).timestamp()
    bot = dict(env, GIT_AUTHOR_EMAIL="bot@example.com",
               GIT_COMMITTER_DATE="2000-01-02T00:00:00Z", GIT_AUTHOR_DATE="2000-01-02T00:00:00Z")
    (r / "h.txt").write_text("h")
    run("git", "add", "h.txt", cwd=r, env=bot)
    run("git", "commit", "-q", "-m", "report: daily snapshot", cwd=r, env=bot)
    mine = dict(env, GIT_COMMITTER_DATE="2000-01-03T00:00:00Z", GIT_AUTHOR_DATE="2000-01-03T00:00:00Z")
    (r / "i.txt").write_text("i")
    run("git", "add", "i.txt", cwd=r, env=mine)
    run("git", "commit", "-q", "-m", "feat: mine (ADR 3)", cwd=r, env=mine)
    write_transcript(root, [line("w1", t0, os.path.realpath(r))])  # before the bot's commit
    out = run(sys.executable, SCRIPT, "backfill", cwd=r, env=env).stdout
    assert "added 1 note(s)" in out
    # The work landed on my commit, not on the bot commit that came first.
    assert "AI-Cost" in run("git", "notes", "--ref=refs/notes/ai-spend", "show", "HEAD", cwd=r, env=env).stdout
    assert run("git", "notes", "--ref=refs/notes/ai-spend", "show", "HEAD~1", cwd=r, env=env, check=False).returncode != 0


def test_backfill_adds_notes_without_rewriting_history(repo):
    r, env, root = repo
    head_before = run("git", "rev-parse", "HEAD", cwd=r, env=env).stdout
    past = dict(env, GIT_COMMITTER_DATE="2000-01-02T00:00:00Z", GIT_AUTHOR_DATE="2000-01-02T00:00:00Z")
    (r / "g.txt").write_text("g")
    run("git", "add", "g.txt", cwd=r, env=past)
    run("git", "commit", "-q", "-m", "old work (ADR 9)", cwd=r, env=past)
    t = datetime(2000, 1, 1, 12, tzinfo=timezone.utc).timestamp()
    write_transcript(root, [line("b1", t, os.path.realpath(r))])
    # Transcript files older than the window are skipped by mtime; make it current.
    dry = run(sys.executable, SCRIPT, "backfill", "--dry-run", cwd=r, env=env).stdout
    assert "would add 1 note(s)" in dry
    run(sys.executable, SCRIPT, "backfill", cwd=r, env=env)
    assert "AI-Cost:" in run("git", "notes", "--ref=refs/notes/ai-spend", "show", "HEAD", cwd=r, env=env).stdout
    assert run("git", "rev-parse", "HEAD~1", cwd=r, env=env).stdout == head_before
