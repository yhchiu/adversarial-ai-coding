"""record_protected_tests: replace mode (stage 4) and append mode (phases)."""

import subprocess

from adversarial_ai_coding.gitops import head_sha
from adversarial_ai_coding.workflow import record_protected_tests


def _commit_file(repo, name, message):
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", message],
        check=True,
        capture_output=True,
    )


def test_replace_then_append_grows_the_list(make_ctx, new_repo):
    ctx = make_ctx()
    (ctx.wf / ".gitignore").write_text("*\n", encoding="utf-8")
    base_one = head_sha(new_repo)
    _commit_file(new_repo, "test_one.py", "phase 1 tests")
    assert record_protected_tests(ctx, base_one) == ["test_one.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_one.py\n"
    assert (ctx.wf / "protected-base.sha").read_text(
        encoding="utf-8"
    ).strip() == head_sha(new_repo)

    base_two = head_sha(new_repo)
    _commit_file(new_repo, "test_two.py", "phase 2 tests")
    assert record_protected_tests(ctx, base_two, append=True) == ["test_two.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_one.py\ntest_two.py\n"
    assert (ctx.wf / "protected-base.sha").read_text(
        encoding="utf-8"
    ).strip() == head_sha(new_repo)


def test_append_dedupes_and_replace_overwrites(make_ctx, new_repo):
    ctx = make_ctx()
    (ctx.wf / ".gitignore").write_text("*\n", encoding="utf-8")
    base = head_sha(new_repo)
    _commit_file(new_repo, "test_one.py", "tests")
    record_protected_tests(ctx, base)
    assert record_protected_tests(ctx, base, append=True) == ["test_one.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_one.py\n"
    base_two = head_sha(new_repo)
    _commit_file(new_repo, "test_two.py", "more tests")
    assert record_protected_tests(ctx, base_two) == ["test_two.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_two.py\n"


def test_spec_dir_files_are_excluded(make_ctx, new_repo):
    ctx = make_ctx()
    (ctx.wf / ".gitignore").write_text("*\n", encoding="utf-8")
    base = head_sha(new_repo)
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    (ctx.spec_dir / "spec.md").write_text("spec\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(new_repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(new_repo), "commit", "-qm", "spec"],
        check=True,
        capture_output=True,
    )
    assert record_protected_tests(ctx, base) == []
