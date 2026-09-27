#!/usr/bin/env python3
"""Read-only GitHub profile evidence audit using the public REST API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.github.com"
Opener = Callable[[Request], Any]
PR_LINK = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
REPO_LINK = re.compile(r"https://github\.com/([^/\s?#]+)/([A-Za-z0-9_.-]+)")


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
        hint = "GitHub API rate limit or access policy" if exc.code == 403 else "GitHub API request failed"
        raise RuntimeError(f"{hint}: HTTP {exc.code} for {path}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub API network error for {path}: {exc.reason}") from exc


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
        claims.append(
            {
                "url": url,
                "state": pull.get("state"),
                "merged": pull.get("merged_at") is not None,
                "draft": pull.get("draft"),
            }
        )
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
        "repository_claims": repo_claims,
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
                claim["state"] == "open" and not claim["merged"] for claim in pr_claims
            ),
            "profile_repo_claims_are_public": all(
                claim["private"] is not True for claim in repo_claims
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
