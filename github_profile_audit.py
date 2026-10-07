#!/usr/bin/env python3
"""Read-only GitHub profile evidence audit using REST and optional GraphQL."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.github.com"
GRAPHQL_API = f"{API}/graphql"
Opener = Callable[[Request], Any]
PR_LINK = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
REFERENCE_LINK = re.compile(r"https://github\.com/([^/\s?#]+)/([^/\s?#]+)/(issues|discussions)/(\d+)")
REPO_LINK = re.compile(r"https://github\.com/([^/\s?#]+)/([A-Za-z0-9_.-]+)")
OPEN_PR_MARKER = re.compile(
    r"(?im)^(?:\*\*)?(?:Open-source work in review|Open PR|Selected open upstream work)\b"
)
MERGED_PR_MARKER = re.compile(r"(?im)^\*\*Merged upstream:")
PR_CLOSED_UNMERGED = re.compile(
    r"(?i)\bclosed\b[^.!?\n]{0,100}\bwithout\s+(?:being\s+)?merg(?:e|ed|ing|er)\b"
)
PR_OPEN_STATEMENT = re.compile(
    r"(?i)(?:\bopen\s*[·|—-]|\bremains?\s+open\b|\bstill\s+open\b|\bwas\s+reopened\b|"
    r"\breopened\s+(?:it|the\s+pr)\b)"
)
PR_MERGED_STATEMENT = re.compile(
    r"(?i)(?:\b(?:maintainer|author|upstream)\s+(?:has\s+)?merged\b|"
    r"\bmerged\s+(?:the\s+)?(?:pr|pull\s+request|it)\b|\bpr\s+was\s+merged\b)"
)
SECTION_HEADER = re.compile(r"(?m)^(?:#{1,6}\s+.+|\*\*[^*\n]+\*\*.*)$")
CONTRIBUTION_SNAPSHOT = re.compile(
    r"\*\*GitHub snapshot (?P<date>\d{4}-\d{2}-\d{2})(?: (?P<time>\d{2}:\d{2}))? UTC:\*\*\s+"
    r"(?P<public_repos>[\d,]+) public repos including this profile;\s+"
    r"(?P<commits>[\d,]+) commits,\s+(?P<pull_requests>[\d,]+) pull requests,\s+"
    r"(?P<reviews>[\d,]+) reviews?,\s+(?P<issues>[\d,]+) issues? and\s+"
    r"(?P<total_contributions>[\d,]+) total contributions\b"
)
CURRENT_CONTRIBUTION_SNAPSHOT = re.compile(
    r"\*\*GitHub contribution snapshot \(GraphQL, rechecked "
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} ICT / "
    r"(?P<date>\d{4}-\d{2}-\d{2}) (?P<time>\d{2}:\d{2}) UTC\):\*\*\s+"
    r"(?P<public_repos>[\d,]+) owned public repositories total: "
    r"(?P<non_fork_public_repos>[\d,]+) non-fork(?: public)? repositories including this profile, plus "
    r"(?P<forks>[\d,]+) forks;\s+"
    r"(?P<total_contributions>[\d,]+) contributions in the exact preceding 365 days, including "
    r"(?P<commits>[\d,]+) commits, (?P<pull_requests>[\d,]+) pull-request contributions, "
    r"(?P<reviews>[\d,]+) reviews and (?P<issues>[\d,]+) issues\."
)
DISCUSSION_QUERY = """
query DiscussionAudit($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    discussion(number: $number) {
      title
      updatedAt
      isAnswered
      locked
      author { login }
      answer { author { login } authorAssociation }
      comments(first: 100, after: $after) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes { author { login } authorAssociation }
      }
    }
  }
}
"""
CONTRIBUTION_QUERY = """
query ContributionSnapshotAudit($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      startedAt
      endedAt
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      contributionCalendar { totalContributions }
    }
  }
}
"""


def fetch_json(path: str, opener: Opener = urlopen) -> Any:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "soukyu-profile-audit",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        API + path,
        headers=headers,
    )
    try:
        with opener(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        exc.close()
        hint = "GitHub API rate limit or access policy" if exc.code == 403 else "GitHub API request failed"
        raise RuntimeError(f"{hint}: HTTP {exc.code} for {path}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub API network error for {path}: {exc.reason}") from exc


def fetch_graphql(
    query: str,
    variables: dict[str, Any],
    opener: Opener = urlopen,
) -> dict[str, Any]:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for authenticated GraphQL checks")
    request = Request(
        GRAPHQL_API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "soukyu-profile-audit",
        },
    )
    try:
        with opener(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as exc:
        exc.close()
        hint = "GitHub GraphQL access policy" if exc.code == 403 else "GitHub GraphQL request failed"
        raise RuntimeError(f"{hint}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub GraphQL network error: {exc.reason}") from exc
    if result.get("errors"):
        raise RuntimeError("GitHub GraphQL query returned errors")
    return result.get("data", {})


def contribution_snapshot_claim(
    readme: dict[str, Any] | None,
    profile: dict[str, Any],
    username: str,
    opener: Opener,
) -> dict[str, Any]:
    if not readme or not readme.get("content"):
        return {"verification": "not_present", "matches": None}
    try:
        markdown = base64.b64decode(readme["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Profile README content is not valid base64 UTF-8") from exc
    match = CURRENT_CONTRIBUTION_SNAPSHOT.search(markdown) or CONTRIBUTION_SNAPSHOT.search(markdown)
    if not match:
        return {"verification": "not_present", "matches": None}

    parsed_values = {
        key: int(value.replace(",", ""))
        for key, value in match.groupdict().items()
        if value is not None and key not in {"date", "time"}
    }
    reported_public_repos = parsed_values.pop("public_repos")
    reported_non_fork_public_repos = parsed_values.pop("non_fork_public_repos", None)
    reported_forks = parsed_values.pop("forks", None)
    reported = parsed_values
    repo_breakdown_matches_total = None
    if reported_non_fork_public_repos is not None and reported_forks is not None:
        repo_breakdown_matches_total = (
            reported_non_fork_public_repos + reported_forks == reported_public_repos
        )
    try:
        snapshot_date = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
    except ValueError:
        return {
            "verification": "invalid_snapshot_date",
            "reported": reported,
            "reported_public_repos": reported_public_repos,
            "reported_repo_breakdown": (
                {
                    "non_fork_public_repos": reported_non_fork_public_repos,
                    "forks": reported_forks,
                }
                if reported_non_fork_public_repos is not None and reported_forks is not None
                else None
            ),
            "repo_breakdown_matches_total": repo_breakdown_matches_total,
            "historical_public_repos_verification": "not_available",
            "matches": False,
        }

    timestamp = match.group("time")
    claim: dict[str, Any] = {
        "verification": "not_checked_auth_required",
        "snapshot_date": snapshot_date.isoformat(),
        "snapshot_time": timestamp,
        "reported": reported,
        "reported_public_repos": reported_public_repos,
        "reported_repo_breakdown": (
            {
                "non_fork_public_repos": reported_non_fork_public_repos,
                "forks": reported_forks,
            }
            if reported_non_fork_public_repos is not None and reported_forks is not None
            else None
        ),
        "repo_breakdown_matches_total": repo_breakdown_matches_total,
        "historical_public_repos_verification": "not_available",
        "historical_public_repos_reason": (
            "GitHub exposes the current public repository count, not its value at a past snapshot time."
        ),
        "observed": None,
        "matches": None,
    }
    if not timestamp:
        claim.update(
            {
                "verification": "snapshot_time_missing",
                "reason": "An exact UTC time is required to verify a rolling 365-day contribution window.",
            }
        )
        return claim
    try:
        snapshot_end = datetime.strptime(
            f"{match.group('date')} {timestamp}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        claim.update({"verification": "invalid_snapshot_time", "matches": False})
        return claim
    snapshot_start = snapshot_end - timedelta(days=365)
    from_time = snapshot_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_time = snapshot_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    claim.update({"period_start": from_time, "period_end": to_time})
    if not os.environ.get("GITHUB_TOKEN"):
        claim["reason"] = "Set GITHUB_TOKEN to check the dated contribution snapshot via GraphQL."
        return claim

    try:
        data = fetch_graphql(
            CONTRIBUTION_QUERY,
            {"login": username, "from": from_time, "to": to_time},
            opener,
        )
    except RuntimeError as exc:
        claim.update({"verification": "error", "error": str(exc)})
        return claim
    user = data.get("user")
    if not user:
        claim.update({"verification": "not_found"})
        return claim

    collection = user.get("contributionsCollection", {})
    observed = {
        "commits": collection.get("totalCommitContributions"),
        "pull_requests": collection.get("totalPullRequestContributions"),
        "reviews": collection.get("totalPullRequestReviewContributions"),
        "issues": collection.get("totalIssueContributions"),
        "total_contributions": (collection.get("contributionCalendar") or {}).get(
            "totalContributions"
        ),
    }
    claim.update(
        {
            "verification": "checked",
            "period_start": collection.get("startedAt"),
            "period_end": collection.get("endedAt"),
            "observed": observed,
            "matches": reported == observed,
        }
    )
    return claim


def fetch_issue_comments(
    owner: str,
    repo: str,
    number: str,
    opener: Opener,
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = fetch_json(
            f"/repos/{owner}/{repo}/issues/{number}/comments?per_page=100&page={page}",
            opener,
        )
        comments.extend(batch)
        if len(batch) < 100:
            return comments
        page += 1


def readme_pr_claims(readme: dict[str, Any] | None, opener: Opener) -> list[dict[str, Any]]:
    if not readme or not readme.get("content"):
        return []
    try:
        markdown = base64.b64decode(readme["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Profile README content is not valid base64 UTF-8") from exc

    claims = []
    seen: set[str] = set()
    for match in PR_LINK.finditer(markdown):
        owner, repo, number = match.groups()
        url = match.group(0)
        if url in seen:
            continue
        seen.add(url)
        pull = fetch_json(f"/repos/{owner}/{repo}/pulls/{number}", opener)
        line_start = markdown.rfind("\n", 0, match.start()) + 1
        line_end = markdown.find("\n", match.end())
        if line_end == -1:
            line_end = len(markdown)
        line = markdown[line_start:line_end]
        paragraph_break = markdown.rfind("\n\n", 0, match.start())
        paragraph_start = paragraph_break + 2 if paragraph_break >= 0 else 0
        paragraph_end = markdown.find("\n\n", match.end())
        if paragraph_end == -1:
            paragraph_end = len(markdown)
        paragraph = markdown[paragraph_start:paragraph_end]

        # Only an explicit local status or a heading for the same paragraph
        # creates a lifecycle claim. Unlabeled evidence links stay unasserted.
        section_headers = list(SECTION_HEADER.finditer(markdown, 0, match.start()))
        latest_section = section_headers[-1].group(0) if section_headers else ""
        section_is_open = bool(OPEN_PR_MARKER.match(latest_section))
        section_is_merged = bool(MERGED_PR_MARKER.match(latest_section))

        paragraph_pr_links = list(PR_LINK.finditer(paragraph))
        paragraph_pr_numbers = {item.group(3) for item in paragraph_pr_links}
        relative_link_start = match.start() - paragraph_start
        relevant_sentences = []
        sentence_breaks = list(re.finditer(r"(?<=[.!?])\s+", paragraph))
        sentence_spans = []
        cursor = 0
        for boundary in sentence_breaks:
            sentence_spans.append((cursor, boundary.start()))
            cursor = boundary.end()
        sentence_spans.append((cursor, len(paragraph)))
        for start, end in sentence_spans:
            sentence = paragraph[start:end]
            if (
                sentence
                and (
                    start <= relative_link_start < end
                    or re.search(rf"#{re.escape(number)}\b", sentence)
                )
            ):
                relevant_sentences.append(sentence)
        if len(paragraph_pr_numbers) == 1:
            relevant_sentences.append(paragraph)
        line_context = (
            line
            if "|" in line or OPEN_PR_MARKER.match(line) or MERGED_PR_MARKER.match(line)
            else ""
        )
        local_context = "\n".join([line_context, *relevant_sentences])

        kind = "unasserted"
        if PR_CLOSED_UNMERGED.search(local_context):
            kind = "closed_unmerged"
        elif section_is_merged:
            kind = "merged"
        elif (
            section_is_open
            or OPEN_PR_MARKER.search(line)
            or PR_OPEN_STATEMENT.search(local_context)
        ):
            kind = "open"
        elif PR_MERGED_STATEMENT.search(local_context):
            kind = "merged"
        claims.append(
            {
                "url": url,
                "kind": kind,
                "state": pull.get("state"),
                "merged": pull.get("merged_at") is not None,
                "draft": pull.get("draft"),
            }
        )
    return claims


def readme_reference_claims(readme: dict[str, Any] | None, opener: Opener) -> list[dict[str, Any]]:
    """Check issue links and fetch discussion metadata when an API token is available."""
    if not readme or not readme.get("content"):
        return []
    try:
        markdown = base64.b64decode(readme["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Profile README content is not valid base64 UTF-8") from exc

    claims = []
    seen: set[str] = set()
    for owner, repo, kind, number in REFERENCE_LINK.findall(markdown):
        url = f"https://github.com/{owner}/{repo}/{kind}/{number}"
        if url in seen:
            continue
        seen.add(url)
        claim: dict[str, Any] = {"url": url, "kind": kind, "verification": "checked"}
        if kind == "discussions":
            if not os.environ.get("GITHUB_TOKEN"):
                claim.update(
                    {
                        "verification": "not_checked_auth_required",
                        "reason": "Set GITHUB_TOKEN to check discussion metadata via GraphQL.",
                    }
                )
            else:
                comments: list[dict[str, Any]] = []
                cursor = None
                discussion = None
                try:
                    while True:
                        data = fetch_graphql(
                            DISCUSSION_QUERY,
                            {"owner": owner, "repo": repo, "number": int(number), "after": cursor},
                            opener,
                        )
                        discussion = (
                            data.get("repository", {}).get("discussion")
                            if data.get("repository")
                            else None
                        )
                        if discussion is None:
                            claim["verification"] = "not_found"
                            break
                        connection = discussion.get("comments", {})
                        comments.extend(connection.get("nodes", []))
                        page_info = connection.get("pageInfo", {})
                        if not page_info.get("hasNextPage"):
                            if connection.get("totalCount") != len(comments):
                                claim["verification"] = "incomplete"
                            break
                        cursor = page_info.get("endCursor")
                        if not cursor:
                            claim["verification"] = "incomplete"
                            break
                except RuntimeError as exc:
                    claim.update({"verification": "error", "error": str(exc)})
                else:
                    if discussion is not None and claim["verification"] not in {
                        "not_found",
                        "incomplete",
                    }:
                        answer = discussion.get("answer")
                        claim.update(
                            {
                                "verification": "checked",
                                "title": discussion.get("title"),
                                "updated_at": discussion.get("updatedAt"),
                                "is_answered": discussion.get("isAnswered"),
                                "locked": discussion.get("locked"),
                                "author": (discussion.get("author") or {}).get("login"),
                                "comment_count": len(comments),
                                "comment_authors": [
                                    {
                                        "login": (comment.get("author") or {}).get("login"),
                                        "association": comment.get("authorAssociation"),
                                    }
                                    for comment in comments
                                ],
                                "answer_author": {
                                    "login": (answer.get("author") or {}).get("login"),
                                    "association": answer.get("authorAssociation"),
                                }
                                if answer
                                else None,
                            }
                        )
        else:
            try:
                issue = fetch_json(f"/repos/{owner}/{repo}/issues/{number}", opener)
                comments = fetch_issue_comments(owner, repo, number, opener)
            except RuntimeError as exc:
                claim.update({"verification": "error", "error": str(exc)})
            else:
                complete = issue.get("comments") == len(comments)
                claim.update(
                    {
                        "verification": "checked" if complete else "incomplete",
                        "state": issue.get("state"),
                        "title": issue.get("title"),
                        "updated_at": issue.get("updated_at"),
                        "comment_count": len(comments),
                        "comment_authors": [
                            {
                                "login": (comment.get("user") or {}).get("login"),
                                "association": comment.get("author_association"),
                            }
                            for comment in comments
                        ],
                    }
                )
        claims.append(claim)
    return claims


def readme_repo_claims(
    readme: dict[str, Any] | None,
    repos_by_key: dict[tuple[str, str], dict[str, Any]],
    opener: Opener,
) -> list[dict[str, Any]]:
    """Resolve repository links in the README without trusting link text."""
    if not readme or not readme.get("content"):
        return []
    markdown = base64.b64decode(readme["content"]).decode("utf-8")
    claims = []
    seen: set[str] = set()
    for match in REPO_LINK.finditer(markdown):
        owner, raw_repo = match.groups()
        if markdown[match.end() : match.end() + 1] in "/?#":
            continue
        repo = raw_repo.rstrip(".,;:")
        url = f"https://github.com/{owner}/{repo}"
        if url in seen:
            continue
        seen.add(url)
        key = (owner, repo.rstrip(".,;:"))
        record = repos_by_key.get(key)
        if record is None:
            record = fetch_json(f"/repos/{owner}/{repo}", opener)
        claims.append(
            {
                "url": url,
                "owner": record.get("owner", {}).get("login"),
                "private": record.get("private"),
                "archived": record.get("archived"),
                "fork": record.get("fork"),
            }
        )
    return claims


def audit(username: str, opener: Opener = urlopen) -> dict[str, Any]:
    profile = fetch_json(f"/users/{username}", opener)
    repos = fetch_json(f"/users/{username}/repos?per_page=100&type=owner&sort=updated", opener)
    readme: dict[str, Any] | None = None
    if profile.get("type") == "User":
        try:
            readme = fetch_json(f"/repos/{username}/{username}/readme", opener)
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
    pr_claims = readme_pr_claims(readme, opener)
    reference_claims = readme_reference_claims(readme, opener)
    open_pr_claims = [claim for claim in pr_claims if claim["kind"] == "open"]
    merged_pr_claims = [claim for claim in pr_claims if claim["kind"] == "merged"]
    closed_unmerged_pr_claims = [
        claim for claim in pr_claims if claim["kind"] == "closed_unmerged"
    ]
    contribution_snapshot = contribution_snapshot_claim(readme, profile, username, opener)

    public_repos = [repo for repo in repos if not repo.get("private")]
    repos_by_key = {(username, repo.get("name")): repo for repo in repos}
    repo_claims = readme_repo_claims(readme, repos_by_key, opener)
    report = {
        "schema": "github-profile-audit/v1",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "username": username,
        "profile": {
            "name": profile.get("name"),
            "bio": profile.get("bio"),
            "public_repos": profile.get("public_repos"),
            "followers": profile.get("followers"),
            "html_url": profile.get("html_url"),
        },
        "readme": {
            "present": readme is not None,
            "path": readme.get("path") if readme else None,
            "sha": readme.get("sha") if readme else None,
            "size": readme.get("size") if readme else None,
        },
        "pull_request_claims": pr_claims,
        "reference_claims": reference_claims,
        "repository_claims": repo_claims,
        "contribution_snapshot": contribution_snapshot,
        "repositories": [
            {
                "name": repo.get("name"),
                "html_url": repo.get("html_url"),
                "description": repo.get("description"),
                "fork": repo.get("fork"),
                "archived": repo.get("archived"),
                "updated_at": repo.get("updated_at"),
                "topics": repo.get("topics", []),
            }
            for repo in public_repos
        ],
        "checks": {
            "public_repo_count_matches_profile": len(public_repos) == profile.get("public_repos"),
            "private_repos_excluded": all(repo.get("private") is not True for repo in public_repos),
            "archived_repos_listed_explicitly": True,
            "profile_pr_claims_are_open": all(
                claim["state"] == "open" and not claim["merged"] for claim in open_pr_claims
            ),
            "profile_merged_pr_claims_are_merged": all(
                claim["state"] == "closed" and claim["merged"] for claim in merged_pr_claims
            ),
            "profile_closed_unmerged_pr_claims_are_closed_without_merge": all(
                claim["state"] == "closed" and not claim["merged"]
                for claim in closed_unmerged_pr_claims
            ),
            "profile_repo_claims_are_public": all(
                claim["private"] is not True for claim in repo_claims
            ),
            "profile_issue_claims_are_checked": all(
                claim["verification"] == "checked"
                for claim in reference_claims
                if claim["kind"] == "issues"
            ),
            "profile_discussion_claims_have_live_metadata": all(
                claim["verification"] == "checked"
                for claim in reference_claims
                if claim["kind"] == "discussions"
            ),
            "profile_contribution_snapshot_matches_live_data": contribution_snapshot.get("matches"),
            "profile_contribution_repo_breakdown_is_consistent": contribution_snapshot.get(
                "repo_breakdown_matches_total"
            ),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit public GitHub profile metadata (read-only).")
    parser.add_argument("username")
    parser.add_argument("--out", type=Path, help="Write JSON report to this path")
    args = parser.parse_args(argv)
    try:
        report = audit(args.username)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
