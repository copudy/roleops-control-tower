"""Scan exact Git trees before local commit, push or API publication. Stdlib only."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import time
import unicodedata

HERE = Path(__file__).resolve().parent
WINDOWS_SCANNER_SHA = "17157e2ee8b76fc8b1d8bee607a250e34b8a8023c8bc81822d4b5ee4d78fcb7c"
ZERO = "0" * 40
MAX_BLOB = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 20000
MAX_COMMITS = 300
# Keep the public asset fingerprint separate from the source-line formatting.
MODEL_ASSET_FINGERPRINT = "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"
MODEL_HASH_LINE = " 'tokenizer_sha256':'" + MODEL_ASSET_FINGERPRINT + "',  # gitleaks:allow (model file SHA-256)"
HOOKS = ("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit",
         "pre-merge-commit", "prepare-commit-msg", "commit-msg", "post-commit",
         "pre-rebase", "post-checkout", "post-merge", "pre-push",
         "pre-auto-gc", "post-rewrite", "push-to-checkout", "reference-transaction")

class Blocked(Exception):
    pass

def run(args, cwd=None, data=None, check=True):
    try:
        result = subprocess.run(args, cwd=cwd, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("tool unavailable or timeout: " + type(exc).__name__) from None
    if check and result.returncode:
        raise Blocked("command failed: " + Path(str(args[0])).name)
    return result

def git(repo, *args, data=None):
    return run(["git", "-C", str(repo), *args], data=data).stdout

def resolve(repo, ref):
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", ref):
        raise Blocked("exact SHA required")
    return git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()

def root_of(repo):
    return Path(os.fsdecode(git(repo, "rev-parse", "--show-toplevel")).strip()).resolve()

def private_dir(repo):
    value = os.fsdecode(git(repo, "rev-parse", "--git-common-dir")).strip()
    p = Path(value)
    return p if p.is_absolute() else root_of(repo) / p

def installed_config():
    p = HERE / "installed.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def scanner_info(path):
    p = Path(path).resolve()
    if not p.is_file():
        raise Blocked("scanner missing")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    cfg = installed_config()
    expected = cfg.get("scanner_sha256", WINDOWS_SCANNER_SHA)
    if digest != expected:
        raise Blocked("scanner SHA mismatch")
    if run([str(p), "version"]).stdout.decode().strip() != "8.30.1":
        raise Blocked("scanner version mismatch")
    return str(p), digest

def safe_path(raw):
    try:
        name = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise Blocked("non UTF-8 path") from None
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(x in ("", ".", "..") for x in path.parts):
        raise Blocked("unsafe tree path")
    if "\\" in name or ":" in name or any(ord(x) < 32 for x in name):
        raise Blocked("unsafe cross-platform path")
    for part in path.parts:
        if part.rstrip(" .") != part or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise Blocked("unsafe Windows path")
    low = [p.casefold() for p in path.parts]
    leaf = low[-1]
    if any(p in {".git", ".aws", ".ssh", "secrets", "credentials", "runtime", "node_modules", ".venv"} for p in low):
        raise Blocked("sensitive or runtime path")
    if leaf.startswith(".env") and leaf not in {".env.example", ".env.sample"}:
        raise Blocked("environment values prohibited")
    if leaf in {"auth.json", "credentials.json", "id_rsa", "id_ed25519"}:
        raise Blocked("credential path prohibited")
    if any(leaf.endswith(s) for s in (".pem", ".p12", ".pfx", ".key", ".sqlite", ".db", ".zip", ".7z", ".tar", ".gz", ".bak")):
        raise Blocked("credential, database, archive or backup prohibited")
    return name

def entries(repo, tree):
    output = git(repo, "ls-tree", "-rz", "--full-tree", tree)
    rows = []
    seen = set()
    total = 0
    for line in filter(None, output.split(b"\0")):
        meta, raw = line.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        name = safe_path(raw)
        normalized = unicodedata.normalize("NFKC", name).casefold()
        if normalized in seen:
            raise Blocked("cross-platform filename collision")
        seen.add(normalized)
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise Blocked("symlink or submodule requires separate review")
        size = int(git(repo, "cat-file", "-s", oid))
        total += size
        if size > MAX_BLOB or total > MAX_TOTAL or len(rows) >= MAX_FILES:
            raise Blocked("scan size limit; split or review separately")
        rows.append((name, oid))
    return rows

def reviewed_model_finding(finding, payload):
    """Only the reviewed public asset fingerprint in its exact Git-tree path.

    No repository allowlist or inline comment grants an exception. Check the
    original extracted blob; the scanner report is redacted and never printed.
    """
    if not isinstance(finding, dict) or finding.get("RuleID") != "generic-api-key":
        return False
    target = payload / "app/e5_provider.py"
    name = finding.get("File")
    start, end = finding.get("StartLine"), finding.get("EndLine")
    if (not isinstance(name, str) or type(start) is not int or
            type(end) is not int or start != end or start < 1):
        return False
    if Path(name).resolve() != target.resolve():
        return False
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
        return start <= len(lines) and lines[start - 1] == MODEL_HASH_LINE
    except (OSError, UnicodeError):
        return False


def scan_tree(repo, tree, scanner):
    rows = entries(repo, tree)
    if not rows:
        return 0
    with tempfile.TemporaryDirectory(prefix="codex-github-scan-") as tmp:
        folder = Path(tmp)
        payload = folder / "payload"
        payload.mkdir()
        # Prevent repository ignore files, config, and inline allow comments from suppressing findings.
        ignore = folder / "empty.ignore"
        report = folder / "report.json"
        ignore.write_text("", encoding="utf-8")
        for name, oid in rows:
            dest = payload / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(git(repo, "cat-file", "blob", oid))
        env_keys = [k for k in os.environ if k.startswith("GITLEAKS_")]
        old = {k: os.environ.pop(k) for k in env_keys}
        try:
            result = run([scanner, "dir", str(payload), "--config", str(HERE / "gitleaks.toml"),
                "--gitleaks-ignore-path", str(ignore), "--ignore-gitleaks-allow",
                "--max-target-megabytes", "0", "--max-decode-depth", "5",
                "--no-banner", "--no-color", "--redact=100", "--log-level", "error",
                "--report-format", "json", "--report-path", str(report),
                "--timeout", "90"], check=False)
        finally:
            os.environ.update(old)
        if result.returncode not in (0, 1) or not report.is_file():
            raise Blocked("secret scan failed; details suppressed")
        try:
            findings = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Blocked("invalid scanner report; details suppressed") from None
        if not isinstance(findings, list) or (result.returncode == 1 and not findings):
            raise Blocked("inconsistent scanner report; details suppressed")
        if any(not reviewed_model_finding(item, payload) for item in findings):
            raise Blocked("secret scan failed; details suppressed")
    return len(rows)

def scan_commits(repo, commits, scanner):
    if len(commits) > MAX_COMMITS:
        raise Blocked("commit range exceeds bound; review history in controlled batches")
    trees = {}
    for commit in commits:
        tree = git(repo, "rev-parse", commit + "^{tree}").decode().strip()
        if tree not in trees:
            trees[tree] = scan_tree(repo, tree, scanner)
    return trees

def commits_between(repo, base, head):
    if git(repo, "rev-parse", "--is-shallow-repository").decode().strip() != "false":
        raise Blocked("shallow history cannot establish complete outgoing range")
    head = resolve(repo, head)
    if base == ZERO:
        args = ["rev-list", "--reverse", head]
    else:
        base = resolve(repo, base)
        if run(["git", "-C", str(repo), "merge-base", "--is-ancestor", base, head], check=False).returncode:
            raise Blocked("non fast-forward range")
        args = ["rev-list", "--reverse", base + ".." + head]
    return git(repo, *args).decode().splitlines()

def receipt(repo, phase, trees, scanner_sha):
    folder = private_dir(repo) / "codex-safety"
    folder.mkdir(exist_ok=True)
    body = {"schema": 1, "time": int(time.time()), "phase": phase, "repo": str(root_of(repo)),
            "scanner_version": "8.30.1", "scanner_sha256": scanner_sha,
            "guard_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "config_sha256": hashlib.sha256((HERE / "gitleaks.toml").read_bytes()).hexdigest(),
            "exception_contract": "roleops-e5-tokenizer-exact-line-v1",
            "trees": trees, "result": "passed"}
    target = folder / ("latest-" + phase + ".json")
    temp = target.with_suffix(".new")
    temp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)

def scan_command(repo, command, scanner, sha, value=None, base=None, head=None):
    if command == "staged":
        if git(repo, "ls-files", "-u"):
            raise Blocked("unmerged index")
        tree = git(repo, "write-tree").decode().strip()
        trees = {tree: scan_tree(repo, tree, scanner)}
        if git(repo, "write-tree").decode().strip() != tree:
            raise Blocked("index changed during scan")
    elif command == "commit":
        trees = scan_commits(repo, [resolve(repo, value)], scanner)
    else:
        trees = scan_commits(repo, commits_between(repo, base, head), scanner)
    receipt(repo, command, trees, sha)
    return trees

def doctor(repo):
    cfg = installed_config()
    expected = HERE / "hooks"
    result = run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"], check=False)
    actual = result.stdout.decode().strip()
    if not actual or Path(actual).resolve() != expected.resolve():
        raise Blocked("shared hooks not active or repository override present")
    if not cfg:
        raise Blocked("global installation incomplete")
    scanner_info(cfg["scanner"])
    if not (HERE.parent / "AGENTS.md").is_file():
        raise Blocked("global AGENTS missing")
    print("DOCTOR_OK; remote rules and separate publisher identity still require verification")

def chain(repo, hook, args, stdin):
    cfg = installed_config()
    prior = cfg.get("previous_hooks_path")
    old = Path(prior).expanduser() if prior else private_dir(repo) / "hooks"
    if not old.is_absolute():
        old = root_of(repo) / old
    target = old / hook
    if target.is_file() and target.resolve().parent != (HERE / "hooks").resolve():
        result = run([cfg.get("shell", "sh"), str(target), *args], cwd=root_of(repo), data=stdin, check=False)
        if result.returncode:
            raise Blocked("previous hook rejected operation")

def hook_command(repo, name, args, scanner, sha, stdin):
    if name == "pre-commit":
        scan_command(repo, "staged", scanner, sha)
    elif name == "pre-push":
        rows = stdin.decode("utf-8", errors="strict").splitlines()
        if not rows:
            raise Blocked("empty push update list")
        ranges = []
        for row in rows:
            parts = row.split()
            if len(parts) != 4:
                raise Blocked("invalid push update")
            local_ref, local_sha, remote_ref, remote_sha = parts
            if local_sha == ZERO or not remote_ref.startswith("refs/heads/"):
                raise Blocked("remote deletion or tag publication requires separate authorization")
            if remote_ref in {"refs/heads/main", "refs/heads/master"}:
                raise Blocked("publish a work branch and use a checked PR")
            ranges.extend(commits_between(repo, remote_sha, local_sha))
        trees = scan_commits(repo, list(dict.fromkeys(ranges)), scanner)
        receipt(repo, "push", trees, sha)
    chain(repo, name, args, stdin)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=".")
    p.add_argument("--scanner")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("staged")
    c = sub.add_parser("commit"); c.add_argument("sha")
    r = sub.add_parser("range"); r.add_argument("--base", required=True); r.add_argument("--head", required=True)
    sub.add_parser("doctor")
    h = sub.add_parser("hook"); h.add_argument("name", choices=HOOKS); h.add_argument("args", nargs="*")
    a = p.parse_args()
    try:
        repo = root_of(a.repo)
        if a.command == "doctor":
            doctor(repo); return 0
        cfg = installed_config()
        scanner, sha = scanner_info(a.scanner or cfg.get("scanner", HERE / "bin/gitleaks.exe"))
        if a.command == "hook":
            data = sys.stdin.buffer.read() if a.name in {"pre-push", "post-rewrite", "pre-receive", "post-receive", "reference-transaction"} else b""
            hook_command(repo, a.name, a.args, scanner, sha, data)
        else:
            trees = scan_command(repo, a.command, scanner, sha, getattr(a, "sha", None),
                                 getattr(a, "base", None), getattr(a, "head", None))
            print("SAFETY_CHECK_OK trees=" + str(len(trees)))
        return 0
    except (Blocked, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print("SAFETY_BLOCKED: " + str(exc), file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
