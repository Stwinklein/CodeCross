"""Tests for the dependency-free GitHub comment upsert script."""

from __future__ import annotations

from scripts.github_pr_comment import (
    DEFAULT_MARKER,
    IssueComment,
    find_existing_comment,
    upsert_comment,
)


class FakeCommentClient:
    """In-memory stand-in for GitHubIssueCommentClient."""

    def __init__(self, comments: list[IssueComment] | None = None) -> None:
        self.comments = comments or []
        self.updated: tuple[int, str] | None = None
        self.created: str | None = None

    def list_issue_comments(self, pr_number: int) -> list[IssueComment]:
        """Return preloaded comments and record no network state."""

        return self.comments

    def update_issue_comment(self, comment_id: int, body: str) -> IssueComment:
        """Record update calls so the test can assert the upsert path."""

        self.updated = (comment_id, body)
        return IssueComment(comment_id=comment_id, body=body)

    def create_issue_comment(self, pr_number: int, body: str) -> IssueComment:
        """Record create calls so the test can assert the upsert path."""

        self.created = body
        return IssueComment(comment_id=99, body=body)


def test_find_existing_comment_uses_marker():
    """Only comments with the hidden CodeCross marker should be updated."""

    comments = [
        IssueComment(comment_id=1, body="human note"),
        IssueComment(comment_id=2, body=f"{DEFAULT_MARKER}\nold review"),
    ]

    existing = find_existing_comment(comments)

    assert existing is not None
    assert existing.comment_id == 2


def test_upsert_comment_updates_existing_marker_comment():
    """A synchronize event should patch the old bot comment, not add noise."""

    client = FakeCommentClient([IssueComment(comment_id=5, body=f"{DEFAULT_MARKER}\nold")])

    result = upsert_comment(
        client=client,  # type: ignore[arg-type]
        pr_number=12,
        body=f"{DEFAULT_MARKER}\nnew",
    )

    assert result.comment_id == 5
    assert client.updated == (5, f"{DEFAULT_MARKER}\nnew")
    assert client.created is None


def test_upsert_comment_creates_when_marker_is_missing():
    """The first CodeCross run on a PR should create a new comment."""

    client = FakeCommentClient([IssueComment(comment_id=1, body="human note")])

    result = upsert_comment(
        client=client,  # type: ignore[arg-type]
        pr_number=12,
        body=f"{DEFAULT_MARKER}\nfirst",
    )

    assert result.comment_id == 99
    assert client.created == f"{DEFAULT_MARKER}\nfirst"
    assert client.updated is None
