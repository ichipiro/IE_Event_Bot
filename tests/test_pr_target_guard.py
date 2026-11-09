import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-target-guard.yml"


def _run_guard(head: str, base: str, directory: Path) -> subprocess.CompletedProcess[str]:
    workflow = WORKFLOW.read_text()
    script = textwrap.dedent(workflow.split("        run: |\n", 1)[1])
    refs = {"head": head, "base": base}
    environment = {"PATH": os.defpath}
    for name, field in re.findall(
        r"^\s+(HEAD|BASE): \$\{\{ github\.event\.pull_request\.(head|base)\.ref \}\}$",
        workflow,
        re.MULTILINE,
    ):
        environment[name] = refs[field]
    # GitHub Actions が run 本文へ行う式展開を再現する。
    for field, value in refs.items():
        script = script.replace("${{ github.event.pull_request." + field + ".ref }}", value)
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("head,base,expected", [
    ("feature/change", "develop", 0),
    ("release/0.7.0", "main", 0),
    ("hotfix/urgent", "main", 0),
    ("release-please--branches--main", "main", 0),
    ("sync/main-to-develop", "develop", 0),
    ("feature/change", "main", 1),
    ("release/0.7.0", "develop", 1),
    ("hotfix/urgent", "develop", 1),
    ("release-please--branches--main", "develop", 1),
    ("sync/main-to-develop", "main", 1),
    ("topic/unsupported", "develop", 1),
])
def test_pr_target_guard_preserves_branch_rules(
    head: str, base: str, expected: int, tmp_path: Path,
) -> None:
    result = _run_guard(head, base, tmp_path)
    assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize("head,expected", [
    ("feature/$(touch${IFS}injected)", 0),
    ("feature/`touch${IFS}injected`", 0),
    ('feature/";touch${IFS}injected;#', 0),
    ("topic/$(touch${IFS}injected)", 1),
])
def test_pr_target_guard_does_not_execute_valid_branch_text(
    head: str, expected: int, tmp_path: Path,
) -> None:
    valid = subprocess.run(
        ["git", "check-ref-format", "--branch", head],
        cwd=tmp_path,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr
    result = _run_guard(head, "develop", tmp_path)
    assert not (tmp_path / "injected").exists()
    assert result.returncode == expected, result.stdout + result.stderr
    assert f"HEAD={head}\n" in result.stdout
