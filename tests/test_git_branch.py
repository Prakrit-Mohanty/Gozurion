# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

import subprocess

import pytest

from scanner import git_context


def git(path, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=path,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for name in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME", "GITHUB_REF_TYPE"):
        monkeypatch.delenv(name, raising=False)
    git(tmp_path, "init", "-b", "feature/login")
    (tmp_path / "a.txt").write_text("one")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "one")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_checked_out_branch_is_the_branch(repo):
    assert git_context.branch() == "feature/login"


def test_a_detached_head_finds_the_local_branch_that_points_at_it(repo):
    git(repo, "checkout", "--detach")

    assert git_context.branch() == "feature/login"


def test_a_detached_head_finds_a_remote_branch_when_no_local_branch_points_at_it(repo):
    git(repo, "update-ref", "refs/remotes/origin/feature/login", "HEAD")
    git(repo, "checkout", "--detach")
    git(repo, "branch", "-D", "feature/login")

    assert git_context.branch() == "feature/login"


def test_a_pull_request_run_gives_the_head_branch_of_the_pull_request(repo, monkeypatch):
    monkeypatch.setenv("GITHUB_HEAD_REF", "fix/eval")
    monkeypatch.setenv("GITHUB_REF_NAME", "12/merge")

    assert git_context.branch() == "fix/eval"


def test_the_name_of_a_tag_is_never_a_branch(repo, monkeypatch):
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.2.3")
    git(repo, "checkout", "--detach")

    assert git_context.branch() == "feature/login"


def test_a_commit_that_no_branch_points_at_gives_no_branch(repo):
    git(repo, "checkout", "--detach")
    (repo / "b.txt").write_text("two")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "two")
    git(repo, "branch", "-D", "feature/login")

    assert git_context.branch() is None
