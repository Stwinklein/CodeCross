from __future__ import annotations

"""Create or update the CodeCross PR comment through the GitHub REST API.

This script is intentionally dependency-free so it can run in GitHub Actions
right after ``actions/setup-python``. It reads the Markdown produced by
``python -m codecross.ci``, looks for an existing comment containing the stable
CodeCross marker, and updates that comment instead of adding duplicate comments
for every pushed commit.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MARKER = "<!-- codecross-pr-review -->"
GITHUB_API_VERSION = "2022-11-28"


class GitHubApiError(RuntimeError):
    """Raised when GitHub returns a non-2xx response."""


@dataclass(slots=True)
class IssueComment:
    """Minimal shape of a GitHub issue comment used by the upsert logic."""

    comment_id: int
    body: str


class GitHubIssueCommentClient:
    """Tiny GitHub REST client for issue comments.

    Pull request conversation comments are implemented by GitHub as "issue
    comments". That is why this client uses ``/issues/{number}/comments`` even
    though CodeCross is commenting on a PR.
    """

    def __init__(self, *, token: str, repository: str, api_url: str = "https://api.github.com") -> None:
        self.token = token
        self.repository = repository
        self.api_url = api_url.rstrip("/")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        """Send one JSON request to GitHub and decode the response."""

        url = f"{self.api_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "CodeCross-PR-Review",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="replace")
            raise GitHubApiError(f"GitHub API {method} {url} failed: {exc.code} {error_text}") from exc
        return json.loads(text) if text else None

    def list_issue_comments(self, pr_number: int) -> list[IssueComment]:
        """Fetch all top-level PR conversation comments.

        GitHub paginates comments. We request 100 at a time and stop when a page
        returns fewer than 100 records, which is enough for stable CI upserts.
        """

        comments: list[IssueComment] = []
        for page in range(1, 51):
            query = urllib.parse.urlencode({"per_page": 100, "page": page})
            path = f"/repos/{self.repository}/issues/{pr_number}/comments?{query}"
            payload = self._request("GET", path)
            page_items = [
                IssueComment(comment_id=int(item["id"]), body=str(item.get("body") or ""))
                for item in payload
            ]
            comments.extend(page_items)
            if len(page_items) < 100:
                break
        return comments

    def create_issue_comment(self, pr_number: int, body: str) -> IssueComment:
        """Create the first CodeCross comment on a PR."""

        payload = self._request(
            "POST",
            f"/repos/{self.repository}/issues/{pr_number}/comments",
            {"body": body},
        )
        return IssueComment(comment_id=int(payload["id"]), body=str(payload.get("body") or ""))

    def update_issue_comment(self, comment_id: int, body: str) -> IssueComment:
        """Replace the existing CodeCross comment body."""

        payload = self._request(
            "PATCH",
            f"/repos/{self.repository}/issues/comments/{comment_id}",
            {"body": body},
        )
        return IssueComment(comment_id=int(payload["id"]), body=str(payload.get("body") or ""))


def _read_pr_number_from_event(event_path: str | None) -> int | None:
    """Infer the PR number from the GitHub Actions event payload."""

    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    pull_request = payload.get("pull_request") or {}
    number = pull_request.get("number") or payload.get("number")
    return int(number) if number else None


def find_existing_comment(comments: list[IssueComment], marker: str = DEFAULT_MARKER) -> IssueComment | None:
    """Return the first existing CodeCross comment, if any."""

    for comment in comments:
        if marker in comment.body:
            return comment
    return None


def upsert_comment(
    *,
    client: GitHubIssueCommentClient,
    pr_number: int,
    body: str,
    marker: str = DEFAULT_MARKER,
) -> IssueComment:
    """Create or update the stable CodeCross PR comment."""

    existing = find_existing_comment(client.list_issue_comments(pr_number), marker)
    if existing:
        return client.update_issue_comment(existing.comment_id, body)
    return client.create_issue_comment(pr_number, body)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the GitHub comment command arguments."""

    parser = argparse.ArgumentParser(description="Upsert a CodeCross PR comment.")
    parser.add_argument("--comment-file", required=True, help="Markdown file to post.")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""), help="owner/name repository.")
    parser.add_argument("--pr-number", type=int, help="Pull request number.")
    parser.add_argument("--event-path", default=os.getenv("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the comment body and skip the GitHub API write.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point used by the GitHub Actions workflow."""

    args = parse_args(argv)
    body = Path(args.comment_file).read_text(encoding="utf-8")
    if args.marker not in body:
        raise SystemExit(f"Comment body is missing required marker: {args.marker}")

    pr_number = args.pr_number or _read_pr_number_from_event(args.event_path)
    if not pr_number:
        raise SystemExit("Could not determine pull request number.")
    if not args.repo:
        raise SystemExit("Repository is required; set GITHUB_REPOSITORY or pass --repo.")

    if args.dry_run:
        print(body)
        return

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN or GH_TOKEN is required to post the PR comment.")

    client = GitHubIssueCommentClient(token=token, repository=args.repo)
    result = upsert_comment(
        client=client,
        pr_number=pr_number,
        body=body,
        marker=args.marker,
    )
    print(f"CodeCross comment upserted: {result.comment_id}")


if __name__ == "__main__":
    try:
        main()
    except GitHubApiError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
