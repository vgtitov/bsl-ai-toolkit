# Release and Consumer Pin Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed toolkit release guard and a provider-neutral consumer pin helper, publish the first guarded patch release, then prove the contract with one consumer-owned rollout.

**Architecture:** The toolkit owns two independent Python CLIs: `release_guard.py` validates and creates local immutable release tags, while `consumer_pin.py` validates or changes one consumer-owned pin file using only explicit arguments. GitHub Actions repeats immutable release checks and full tests; every consumer owns its URLs, hooks, CI, review, and rollout in its own repository.

**Tech Stack:** Python 3.12 standard library, Git CLI, pytest, GitHub Actions YAML, Markdown.

## Global Constraints

- Toolkit must contain no organization names, internal domains, organization profiles, contours, or credential data.
- Every state-changing command must perform all read-only validation first and fail closed.
- The release guard must never push.
- The consumer pin helper must modify only the requested pin file and must never commit, push, or open a PR/MR.
- Published tags are immutable and are never moved or reused.
- Consumer pins may intentionally lag latest; validity, not freshness, is the mandatory check.

---

### Task 1: Release guard CLI

**Files:**
- Create: `tests/test_release_guard.py`
- Create: `scripts/release_guard.py`

**Interfaces:**
- Produces: `parse_version(value: str) -> tuple[int, int, int]`
- Produces: `preflight_release(root: Path, version: str, remote: str, branch: str, verify_command: list[str]) -> None`
- Produces: `validate_pushed_tag(root: Path, version: str, remote: str, branch: str) -> None`
- Produces CLI: `check`, `tag`, and `validate-tag`

- [ ] **Step 1: Write failing strict-version and CHANGELOG tests**

```python
@pytest.mark.parametrize("value", ["2.3.0", "v2.3", "v2.3.0-rc1", "v02.3.0"])
def test_parse_version_rejects_non_strict_semver(value):
    with pytest.raises(release_guard.ReleaseError):
        release_guard.parse_version(value)

def test_preflight_requires_dated_changelog_section(release_repo):
    with pytest.raises(release_guard.ReleaseError, match="CHANGELOG"):
        release_guard.preflight_release(
            release_repo.work, "v1.0.1", "origin", "main",
            [sys.executable, "-c", "raise SystemExit(0)"])
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_release_guard.py -q`

Expected: import/file-not-found failure because `scripts/release_guard.py` does not exist.

- [ ] **Step 3: Implement strict parsing, Git helpers, clean/synced branch checks, monotonically increasing tag checks, and CHANGELOG validation**

```python
STRICT_VERSION = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

def parse_version(value: str) -> tuple[int, int, int]:
    match = STRICT_VERSION.fullmatch(value)
    if not match:
        raise ReleaseError(f"invalid strict SemVer tag: {value}")
    return tuple(int(part) for part in match.groups())
```

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_release_guard.py -q`

Expected: the initial parsing and CHANGELOG tests pass.

- [ ] **Step 5: Add failing tests for dirty worktree, wrong branch, remote divergence, duplicate/non-incrementing tag, and failed verification command**

```python
def test_preflight_refuses_remote_divergence(release_repo):
    release_repo.commit("local-only.txt", "x", "local only")
    with pytest.raises(release_guard.ReleaseError, match="origin/main"):
        release_repo.preflight("v1.0.1")

def test_preflight_refuses_failed_verification(release_repo):
    with pytest.raises(release_guard.ReleaseError, match="verification"):
        release_repo.preflight(
            "v1.0.1",
            verify_command=[sys.executable, "-c", "raise SystemExit(7)"])
```

- [ ] **Step 6: Run the new tests and verify RED for the missing guards**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_release_guard.py -q`

Expected: assertions fail because the corresponding guards are not implemented.

- [ ] **Step 7: Implement all preflight guards and verification command execution**

Use `subprocess.run([...], cwd=root, text=True, capture_output=True)` without `shell=True`. Fetch `remote branch --tags` before comparing `HEAD` with `refs/remotes/<remote>/<branch>`.

- [ ] **Step 8: Add failing tests for annotated local tag creation without push and pushed-tag validation**

```python
def test_tag_creates_annotated_local_tag_without_push(release_repo):
    release_repo.tag("v1.0.1")
    assert release_repo.git("cat-file", "-t", "v1.0.1") == "tag"
    assert release_repo.remote_has_tag("v1.0.1") is False

def test_validate_pushed_tag_requires_tag_commit_on_remote_main(release_repo):
    release_repo.create_tag_on_unpublished_commit("v1.0.1")
    with pytest.raises(release_guard.ReleaseError, match="remote main"):
        release_guard.validate_pushed_tag(
            release_repo.work, "v1.0.1", "origin", "main")
```

- [ ] **Step 9: Run the new tests and verify RED**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_release_guard.py -q`

Expected: failures for missing tag creation and pushed-tag validation.

- [ ] **Step 10: Implement `tag` and `validate-tag` CLI behavior**

`tag` runs full preflight, then:

```python
run_git(root, "tag", "-a", version, "-m", message)
```

`validate-tag` verifies exact tag-to-HEAD identity, CHANGELOG, and that the commit is an ancestor of fetched remote release branch.

- [ ] **Step 11: Run Task 1 tests and commit**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_release_guard.py -q`

Commit:

```bash
git add tests/test_release_guard.py scripts/release_guard.py
git commit -m "feat(release): add fail-closed release guard"
```

---

### Task 2: Generic consumer pin CLI

**Files:**
- Create: `tests/test_consumer_pin.py`
- Create: `scripts/consumer_pin.py`

**Interfaces:**
- Consumes: strict SemVer grammar from the release contract; code stays independent to keep the distributed helper standalone.
- Produces: `read_pin(pin_file: Path) -> str`
- Produces: `upstream_has_tag(repo: str, tag: str) -> bool`
- Produces: `check_pin(repo: str, pin_file: Path) -> str`
- Produces: `bump_pin(repo: str, pin_file: Path, tag: str) -> None`
- Produces CLI: `check --repo URL --pin-file PATH` and `bump --repo URL --pin-file PATH --tag VERSION`

- [ ] **Step 1: Write failing tests for valid, malformed, multi-line, and unknown pins**

```python
def test_check_accepts_existing_annotated_tag(upstream, tmp_path):
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v1.2.3\n", encoding="utf-8")
    assert consumer_pin.check_pin(str(upstream), pin) == "v1.2.3"

@pytest.mark.parametrize("content", ["main\n", "v1.2\n", "v1.2.3\nextra\n"])
def test_check_rejects_invalid_pin(upstream, tmp_path, content):
    pin = tmp_path / "toolkit.ref"
    pin.write_text(content, encoding="utf-8")
    with pytest.raises(consumer_pin.PinError):
        consumer_pin.check_pin(str(upstream), pin)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_consumer_pin.py -q`

Expected: import/file-not-found failure because `scripts/consumer_pin.py` does not exist.

- [ ] **Step 3: Implement strict pin reading and exact remote tag lookup**

Use:

```python
git ls-remote --exit-code --tags <repo> refs/tags/<tag> refs/tags/<tag>^{}
```

Treat any non-zero status, including network/auth failure, as `PinError`.

- [ ] **Step 4: Add failing tests that bump only the pin and reject an unknown target**

```python
def test_bump_changes_only_pin_file(upstream, tmp_path):
    pin = tmp_path / "toolkit.ref"
    neighbor = tmp_path / "profile.json"
    pin.write_text("v1.2.2\n", encoding="utf-8")
    neighbor.write_text('{"keep": true}\n', encoding="utf-8")
    consumer_pin.bump_pin(str(upstream), pin, "v1.2.3")
    assert pin.read_text(encoding="utf-8") == "v1.2.3\n"
    assert neighbor.read_text(encoding="utf-8") == '{"keep": true}\n'
```

- [ ] **Step 5: Run new tests and verify RED**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_consumer_pin.py -q`

Expected: failure because `bump_pin` is absent.

- [ ] **Step 6: Implement atomic pin replacement and CLI errors**

Write to a sibling temporary file, flush, then `os.replace`. Print `ERROR: <reason>` to stderr and return exit status 2 on `PinError`.

- [ ] **Step 7: Run Task 2 tests and commit**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_consumer_pin.py -q`

Commit:

```bash
git add tests/test_consumer_pin.py scripts/consumer_pin.py
git commit -m "feat(release): add generic consumer pin helper"
```

---

### Task 3: Release workflow, documentation, and patch changelog

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `docs/RELEASING.md`
- Modify: `docs/AI_UPDATE.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_release_guard.py`

**Interfaces:**
- Consumes: `python scripts/release_guard.py validate-tag "$TAG" --remote origin --branch main`
- Produces: guarded GitHub Release workflow and provider-neutral consumer integration runbook.

- [ ] **Step 1: Add a failing structural workflow test**

```python
def test_release_workflow_runs_tests_and_guard_before_release():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in workflow
    assert "pytest tests/ -q" in workflow
    assert "release_guard.py validate-tag" in workflow
    assert workflow.index("pytest tests/ -q") < workflow.index("gh release create")
```

- [ ] **Step 2: Run the workflow test and verify RED**

Run: `uv run --python 3.12 --no-project --with pytest pytest tests/test_release_guard.py::test_release_workflow_runs_tests_and_guard_before_release -q`

Expected: failure because the current workflow has neither full history nor tests/guard.

- [ ] **Step 3: Update the workflow**

Use `actions/checkout@v4` with `fetch-depth: 0`, install uv, run the exact full test command from `.github/workflows/tests.yml`, run `validate-tag`, then package and publish.

- [ ] **Step 4: Write provider-neutral release and consumer docs**

Document exact commands:

```bash
python scripts/release_guard.py tag vX.Y.Z --remote origin --branch main
git push origin vX.Y.Z
python scripts/consumer_pin.py check --repo <toolkit-upstream-url> --pin-file <consumer-repo>/onboard/toolkit.ref
python scripts/consumer_pin.py bump --repo <toolkit-upstream-url> --pin-file <consumer-repo>/onboard/toolkit.ref --tag vX.Y.Z
```

- [ ] **Step 5: Add `2.2.2` CHANGELOG section**

Record the SSH UTF-8 fix, EDT-safe pre-push dispatch, release guard, consumer pin helper, and guarded release workflow. Do not add consumer-specific names.

- [ ] **Step 6: Run structural and scope checks**

Run:

```bash
uv run --python 3.12 --no-project --with pytest pytest tests/test_release_guard.py tests/test_consumer_pin.py -q
rg -n 'askona|Аскона|gitlab\.askona|claude-1c-team' scripts/release_guard.py scripts/consumer_pin.py docs/RELEASING.md docs/AI_UPDATE.md
```

Expected: tests pass; scope search returns no matches in the new generic release surface.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/release.yml docs/RELEASING.md docs/AI_UPDATE.md CHANGELOG.md tests/test_release_guard.py
git commit -m "docs(release): enforce upstream and consumer workflow"
```

---

### Task 4: Toolkit verification and GitHub integration

**Files:**
- Modify: `docs/superpowers/plans/2026-07-31-release-consumer-sync.md` (checkbox progress only)

**Interfaces:**
- Consumes: all Task 1–3 deliverables.
- Produces: merged GitHub PR on the release branch.

- [ ] **Step 1: Run full verification**

```bash
python -m compileall -q scripts
sh -n scripts/git-hooks/pre-push
uv run --python 3.12 --no-project --with "mcp<2" --with pytest --with lxml --with openpyxl --with xlrd pytest tests/ -q
git diff --check origin/main...HEAD
```

- [ ] **Step 2: Push the feature branch and open a ready-for-review PR**

The PR body lists R1–R8 coverage, RED/GREEN evidence, full test count, and explicitly states that the toolkit contains no consumer organization data.

- [ ] **Step 3: Wait for GitHub Actions and inspect the final PR diff**

Required result: all checks successful, mergeable, head SHA unchanged.

- [ ] **Step 4: Merge with exact head-SHA protection and verify `origin/main`**

Use the repository's normal merge-commit strategy.

---

### Task 5: Guarded patch release

**Files:**
- No source changes after the merged release commit.

**Interfaces:**
- Consumes: merged `main`, `CHANGELOG.md` section `2.2.2`, `release_guard.py`.
- Produces: immutable annotated `v2.2.2` and a successful GitHub Release.

- [ ] **Step 1: Fresh-clone or clean-main release preflight**

Run the guard against `origin/main`. Confirm `v2.2.2` is still unused.

- [ ] **Step 2: Create the annotated local tag through the guard**

```bash
python scripts/release_guard.py tag v2.2.2 --remote origin --branch main
```

- [ ] **Step 3: Inspect the tag object and push only the tag**

```bash
git cat-file -t v2.2.2
git show --no-patch --decorate v2.2.2
git push origin v2.2.2
```

- [ ] **Step 4: Wait for the release workflow**

Required result: workflow success and GitHub Release `v2.2.2` attached to the merged release commit.

---

### Task 6: First consumer-owned rollout

**Files:**
- Create in consumer repository: `specs/toolkit-pin-contract.md`
- Create in consumer repository: `docs/superpowers/plans/2026-07-31-toolkit-pin-contract.md`
- Modify in consumer repository: `onboard/toolkit.ref`
- Create/modify consumer-owned hook and CI files according to its existing conventions.

**Interfaces:**
- Consumes: toolkit `v2.2.2`, `scripts/consumer_pin.py`, consumer-owned upstream URL and pin path.
- Produces: reviewed pin bump, local pre-push check, central CI check, and consumer patch tag.

- [ ] **Step 1: Write the consumer-specific spec and plan inside the consumer repository**

Include its actual remote, pin path, hook dispatcher, CI provider, MR policy, and tag policy. These details must not be copied into toolkit.

- [ ] **Step 2: Add a failing consumer contract test/check**

The check must fail when the pin is malformed or references a nonexistent upstream tag.

- [ ] **Step 3: Wire the generic helper into the consumer pre-push and CI**

The consumer wrapper supplies its upstream URL and pin file. It must not duplicate release semantics.

- [ ] **Step 4: Bump the pin to `v2.2.2` using the helper**

Verify only the pin file changes before adding the wrapper/CI files.

- [ ] **Step 5: Run consumer checks, commit, push a branch, and create a merge request**

Wait for its pipeline, inspect the final diff, then merge according to consumer policy.

- [ ] **Step 6: Create and push the consumer's next patch tag**

Derive the next patch from its existing tags; never assume it equals the toolkit version.

- [ ] **Step 7: Verify the merged consumer pin and upstream tag**

Required result: consumer default branch pin equals `v2.2.2`; upstream `v2.2.2` resolves to the guarded toolkit release commit.

---

### Task 7: Requirement-by-requirement review

**Files:**
- Modify in toolkit follow-up docs PR: `specs/release-consumer-sync.md`
- Create in toolkit follow-up docs PR: `docs/superpowers/reviews/2026-07-31-release-consumer-sync-review.md`
- Create in consumer follow-up MR: a consumer-owned rollout review at the path selected by its repository plan.

**Interfaces:**
- Consumes: spec R1–R9, toolkit PR/release evidence, consumer MR/tag evidence.
- Produces: organization-neutral upstream review plus organization-owned rollout evidence.

- [ ] **Step 1: Record R1–R9 evidence**

For each requirement write `ВЫПОЛНЕНО` with file/line, command output, commit SHA, and PR/MR/release URL.

- [ ] **Step 2: Verify edge cases and unrelated-diff absence**

Run repository status/diff checks in both repositories and repeat the organization-name scan on the toolkit release surface.

- [ ] **Step 3: Run fresh final test suites and remote-state checks**

Do not rely on earlier outputs.

- [ ] **Step 4: Mark every DoD checkbox only when its evidence exists**

- [ ] **Step 5: Publish the completed reviews through follow-up documentation changes**

Open and merge one toolkit docs PR containing only the organization-neutral R1–R9
review and checked DoD, and one consumer docs MR containing its actual remote,
pin, pipeline, MR, and tag evidence. Do not copy consumer identifiers into
toolkit.

## Specification coverage

- R1 → Task 1 preflight and verification command.
- R2 → Task 1 fail-closed errors and local-only tag creation.
- R3 → Task 3 guarded GitHub release workflow.
- R4 → Task 2 argument-driven consumer pin CLI.
- R5 → Task 2 strict pin/tag checks and atomic single-file bump.
- R6 → Task 3 scope scan plus Task 7 split upstream/consumer reviews.
- R7 → Task 3 `docs/RELEASING.md` and `docs/AI_UPDATE.md`.
- R8 → Tasks 1–3 RED/GREEN tests and Task 4 full suite.
- R9 → Tasks 5–6 real toolkit release and consumer rollout.
