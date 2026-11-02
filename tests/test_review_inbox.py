import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "review_inbox.py"


def test_self_test():
    r = subprocess.run([sys.executable, str(SCRIPT), "--self-test"], capture_output=True, text=True)
    assert r.returncode == 0 and "self-test ok" in r.stdout, r.stderr


def test_gitlab_skipped_without_env():
    r = subprocess.run([sys.executable, str(SCRIPT), "--no-github"], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 2 and "GITLAB_TOKEN" in r.stderr
