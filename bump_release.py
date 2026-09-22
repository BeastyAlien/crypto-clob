# -*- coding: utf-8 -*-
"""NOBI release bumper.

Stamps the next version (v1.0 -> v1.1 -> ...) WITH date+time, records it in
RELEASES.md, commits, tags and (by default) pushes to origin. Every tagged
version is a fallback point on GitHub.

Usage (from the repo root, with the bundled runtime):
    _runtime\\python\\python.exe bump_release.py [--inc major|minor|patch] [--note "summary"] [--no-push]

Examples:
    _runtime\\python\\python.exe bump_release.py                          # v1.0 -> v1.1
    _runtime\\python\\python.exe bump_release.py --inc patch              # v1.0 -> v1.0.1
    _runtime\\python\\python.exe bump_release.py --inc major --note "x"   # v1.0 -> v2.0

Fall back to any older version:
    git switch -c hotfix/v1.0 v1.0     # branch from the v1.0 tag (recommended)
    git checkout v1.0                  # detached HEAD, read-only fallback
"""
import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "VERSION"
RELEASES_FILE = ROOT / "RELEASES.md"


def _run(cmd):
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[bump] command failed: {' '.join(cmd)}\n{proc.stderr or proc.stdout}")
        sys.exit(proc.returncode)
    return proc.stdout.strip()


def _now():
    local = _dt.datetime.now().astimezone()
    return (
        local.strftime("%Y-%m-%d %H:%M:%S %Z (%z)"),
        local.strftime("%Y%m%d-%H%M%S"),
    )


def _read_version():
    if not VERSION_FILE.exists():
        return (1, 0, 0)
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = re.search(r"VERSION=v?(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return (1, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _bump(triple, inc):
    major, minor, patch = triple
    if inc == "major":
        return (major + 1, 0, 0)
    if inc == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def main():
    ap = argparse.ArgumentParser(description="NOBI release bumper")
    ap.add_argument("--inc", choices=("major", "minor", "patch"), default="minor")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-push", action="store_true", help="commit + tag locally, no push")
    args = ap.parse_args()

    old = _read_version()
    new = _bump(old, args.inc)
    ver = f"{new[0]}.{new[1]}" if new[2] == 0 else f"{new[0]}.{new[1]}.{new[2]}"
    stamp, stamp_key = _now()
    commit = _run(["git", "rev-parse", "--short", "HEAD"])

    VERSION_FILE.write_text(
        f"VERSION=v{ver}\n"
        f"BUILD_TIME={stamp}\n"
        f"BUILD_KEY={stamp_key}\n"
        f"COMMIT={commit}\n",
        encoding="utf-8",
    )

    if not RELEASES_FILE.exists():
        RELEASES_FILE.write_text("# NOBI releases\n\n", encoding="utf-8")
    with RELEASES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"## v{ver} — {stamp}\n\n")
        fh.write(f"- build key: `{stamp_key}` / commit `{commit}`"
                 + (f" / note: {args.note}" if args.note else "") + "\n\n")

    _run(["git", "add", "VERSION", "RELEASES.md"])
    msg = f"release: v{ver} ({stamp_key})" + (f" - {args.note}" if args.note else "")
    _run(["git", "commit", "-m", msg])
    _run(["git", "tag", "-a", f"v{ver}", "-m", f"v{ver} - {stamp}"])
    print(f"[bump] released v{ver} | {stamp_key} | commit {commit}")

    if not args.no_push:
        _run(["git", "push", "origin", "main", "--tags"])
        print("[bump] pushed origin main + tag v{ver}")
    else:
        print("[bump] not pushed (--no-push)")


if __name__ == "__main__":
    main()