import base64
import io
import json
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from github_profile_audit import (
    audit,
    contribution_snapshot_claim,
    fetch_graphql,
    fetch_json,
    readme_pr_claims,
)


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode()

    def __iter__(self):
        return iter(())


def fake_opener(request, timeout=15):
    paths = {
        "/users/demo": {"type": "User", "name": "Demo", "bio": "AI", "public_repos": 1, "followers": 2, "html_url": "https://github.com/demo"},
        "/users/demo/repos?per_page=100&type=owner&sort=updated": [{"name": "demo", "private": False, "fork": False, "archived": False, "html_url": "https://github.com/demo/demo", "description": "test", "updated_at": "2026-09-27T00:00:00Z", "topics": ["ai"]}],
        "/repos/demo/demo/readme": {"path": "README.md", "sha": "abc", "size": 4, "content": base64.b64encode(b"test").decode()},
    }
    return Response(paths[request.full_url.removeprefix("https://api.github.com")])


class AuditTests(unittest.TestCase):
    def test_report_uses_public_repositories_and_readme_metadata(self):
        report = audit("demo", fake_opener)
        self.assertTrue(report["checks"]["public_repo_count_matches_profile"])
        self.assertEqual(report["readme"]["sha"], "abc")
        self.assertEqual(report["repositories"][0]["topics"], ["ai"])

    def test_timestamped_contribution_snapshot_is_recognized(self):
        readme = base64.b64encode(
            b"**GitHub snapshot 2026-09-29 16:25 UTC:** 29 public repos including this profile; "
            b"243 commits, 41 pull requests, 2 reviews, 1 issue and 342 total contributions "
            b"in the 365 days ending at the snapshot time, verified against GitHub's public "
            b"profile API and contribution collection. These are dated counters, not live values."
        ).decode()

        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            claim = contribution_snapshot_claim(
                {"content": readme}, {"public_repos": 29}, "demo", fake_opener
            )

        self.assertEqual(claim["verification"], "not_checked_auth_required")
        self.assertEqual(claim["snapshot_date"], "2026-09-29")
        self.assertEqual(claim["reported"]["pull_requests"], 41)
        self.assertEqual(claim["reported"]["total_contributions"], 342)

    def test_current_graphql_snapshot_format_and_repository_breakdown_are_recognized(self):
        readme = base64.b64encode(
            b"**GitHub contribution snapshot (GraphQL, rechecked 2026-10-07 07:33 ICT / "
            b"2026-10-07 00:33 UTC):** 32 owned public repositories total: 15 non-fork "
            b"public repositories including this profile, plus 17 forks; 385 contributions "
            b"in the exact preceding 365 days, including 255 commits, 55 pull-request "
            b"contributions, 12 reviews and 2 issues."
        ).decode()

        def opener(request, timeout=15):
            self.assertEqual(request.full_url, "https://api.github.com/graphql")
            query = json.loads(request.data)
            self.assertEqual(
                query["variables"],
                {
                    "login": "demo",
                    "from": "2025-10-07T00:33:00Z",
                    "to": "2026-10-07T00:33:00Z",
                },
            )
            return Response(
                {
                    "data": {
                        "user": {
                            "contributionsCollection": {
                                "startedAt": "2025-10-07T00:33:00Z",
                                "endedAt": "2026-10-07T00:33:00Z",
                                "totalCommitContributions": 255,
                                "totalPullRequestContributions": 55,
                                "totalPullRequestReviewContributions": 12,
                                "totalIssueContributions": 2,
                                "contributionCalendar": {"totalContributions": 385},
                            }
                        }
                    }
                }
            )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            claim = contribution_snapshot_claim(
                {"content": readme}, {"public_repos": 32}, "demo", opener
            )

        self.assertEqual(claim["verification"], "checked")
        self.assertTrue(claim["matches"])
        self.assertEqual(claim["reported_public_repos"], 32)
        self.assertEqual(
            claim["reported_repo_breakdown"],
            {"non_fork_public_repos": 15, "forks": 17},
        )
        self.assertTrue(claim["repo_breakdown_matches_total"])

    def test_current_snapshot_flags_an_arithmetically_inconsistent_repo_breakdown(self):
        readme = base64.b64encode(
            b"**GitHub contribution snapshot (GraphQL, rechecked 2026-10-07 07:33 ICT / "
            b"2026-10-07 00:33 UTC):** 32 owned public repositories total: 15 non-fork "
            b"public repositories including this profile, plus 16 forks; 385 contributions "
            b"in the exact preceding 365 days, including 255 commits, 55 pull-request "
            b"contributions, 12 reviews and 2 issues."
        ).decode()

        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            claim = contribution_snapshot_claim(
                {"content": readme}, {"public_repos": 32}, "demo", fake_opener
            )

        self.assertEqual(claim["verification"], "not_checked_auth_required")
        self.assertFalse(claim["repo_breakdown_matches_total"])

    def test_snapshot_repo_count_is_not_compared_to_current_profile_count(self):
        readme = base64.b64encode(
            b"**GitHub snapshot 2026-09-29 16:25 UTC:** 28 public repos including this profile; "
            b"241 commits, 37 pull requests, 2 reviews, 1 issue and 333 total contributions "
            b"in the 365 days ending at the snapshot time."
        ).decode()

        def opener(request, timeout=15):
            if request.full_url == "https://api.github.com/graphql":
                query = json.loads(request.data)
                self.assertEqual(
                    query["variables"],
                    {
                        "login": "demo",
                        "from": "2025-09-29T16:25:00Z",
                        "to": "2026-09-29T16:25:00Z",
                    },
                )
                return Response(
                    {
                        "data": {
                            "user": {
                                "contributionsCollection": {
                                    "startedAt": "2025-09-29T16:25:00Z",
                                    "endedAt": "2026-09-29T16:25:00Z",
                                    "totalCommitContributions": 241,
                                    "totalPullRequestContributions": 37,
                                    "totalPullRequestReviewContributions": 2,
                                    "totalIssueContributions": 1,
                                    "contributionCalendar": {"totalContributions": 333},
                                }
                            }
                        }
                    }
                )
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "public_repos": 30})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([{"name": f"repo-{i}", "private": False} for i in range(30)])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            raise AssertionError(path)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            report = audit("demo", opener)
        self.assertTrue(report["checks"]["public_repo_count_matches_profile"])
        self.assertTrue(report["checks"]["profile_contribution_snapshot_matches_live_data"])
        self.assertEqual(report["contribution_snapshot"]["verification"], "checked")
        self.assertEqual(report["contribution_snapshot"]["reported_public_repos"], 28)
        self.assertEqual(
            report["contribution_snapshot"]["historical_public_repos_verification"],
            "not_available",
        )
        self.assertEqual(
            report["contribution_snapshot"]["period_start"], "2025-09-29T16:25:00Z"
        )
        self.assertEqual(
            report["contribution_snapshot"]["period_end"], "2026-09-29T16:25:00Z"
        )
        self.assertNotIn("test-token", json.dumps(report))

    def test_stale_contribution_snapshot_is_reported_as_mismatch(self):
        readme = base64.b64encode(
            b"**GitHub snapshot 2026-09-29 16:25 UTC:** 28 public repos including this profile; "
            b"241 commits, 34 pull requests, 1 review, 1 issue and 327 total contributions "
            b"in the 365 days ending at the snapshot time."
        ).decode()

        def opener(request, timeout=15):
            if request.full_url == "https://api.github.com/graphql":
                return Response(
                    {
                        "data": {
                            "user": {
                                "contributionsCollection": {
                                    "startedAt": "2025-09-29T16:25:00Z",
                                    "endedAt": "2026-09-29T16:25:00Z",
                                    "totalCommitContributions": 241,
                                    "totalPullRequestContributions": 37,
                                    "totalPullRequestReviewContributions": 2,
                                    "totalIssueContributions": 1,
                                    "contributionCalendar": {"totalContributions": 333},
                                }
                            }
                        }
                    }
                )
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "public_repos": 28})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([{"name": f"repo-{i}", "private": False} for i in range(28)])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            raise AssertionError(path)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            report = audit("demo", opener)
        self.assertFalse(report["checks"]["profile_contribution_snapshot_matches_live_data"])
        self.assertEqual(report["contribution_snapshot"]["observed"]["pull_requests"], 37)
        self.assertEqual(
            report["contribution_snapshot"]["period_start"], "2025-09-29T16:25:00Z"
        )

    def test_date_only_snapshot_does_not_guess_the_contribution_window(self):
        readme = base64.b64encode(
            b"**GitHub snapshot 2026-09-29 UTC:** 29 public repos including this profile; "
            b"243 commits, 41 pull requests, 2 reviews, 1 issue and 342 total contributions."
        ).decode()

        def opener(request, timeout=15):
            if request.full_url == "https://api.github.com/graphql":
                raise AssertionError("A date-only snapshot has no exact window to query")
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "public_repos": 29})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            raise AssertionError(path)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            report = audit("demo", opener)
        claim = report["contribution_snapshot"]
        self.assertEqual(claim["verification"], "snapshot_time_missing")
        self.assertIsNone(claim["matches"])

    def test_contribution_snapshot_is_not_claimed_verified_without_token(self):
        readme = base64.b64encode(
            b"**GitHub snapshot 2026-09-28 10:00 UTC:** 28 public repos including this profile; "
            b"241 commits, 37 pull requests, 2 reviews, 1 issue and 333 total contributions "
            b"in the preceding year."
        ).decode()

        def opener(request, timeout=15):
            if request.full_url == "https://api.github.com/graphql":
                raise AssertionError("GraphQL must not run without a token")
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "public_repos": 28})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([{"name": f"repo-{i}", "private": False} for i in range(28)])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            raise AssertionError(path)

        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            report = audit("demo", opener)
        self.assertEqual(
            report["contribution_snapshot"]["verification"],
            "not_checked_auth_required",
        )
        self.assertIsNone(report["checks"]["profile_contribution_snapshot_matches_live_data"])

    def test_http_errors_are_actionable(self):
        errors = []

        def failing_opener(request, timeout=15):
            error = HTTPError(request.full_url, 403, "rate", {}, io.BytesIO())
            errors.append(error)
            raise error

        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            fetch_json("/users/demo", failing_opener)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                fetch_graphql("query { viewer { login } }", {}, failing_opener)
        self.assertTrue(all(error.fp.closed for error in errors))

    def test_profile_pr_claims_are_checked_against_live_state(self):
        pr_url = "https://github.com/example/project/pull/7"
        readme = base64.b64encode(f"Open PR: {pr_url}".encode()).decode()

        def opener(request, timeout=15):
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "name": "Demo", "bio": "AI", "public_repos": 1})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "sha": "abc", "size": len(readme), "content": readme})
            if path == "/repos/example/project/pulls/7":
                return Response({"state": "closed", "merged_at": "2026-09-27T00:00:00Z", "draft": False})
            raise AssertionError(path)

        report = audit("demo", opener)
        self.assertFalse(report["checks"]["profile_pr_claims_are_open"])
        self.assertTrue(report["pull_request_claims"][0]["merged"])

    def test_open_and_merged_pr_sections_have_distinct_expectations(self):
        readme = base64.b64encode(
            b"**Open-source work in review:** "
            b"https://github.com/example/project/pull/7\n\n"
            b"**Merged upstream:** https://github.com/example/project/pull/8"
        ).decode()

        def opener(request, timeout=15):
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "name": "Demo", "public_repos": 0})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            if path == "/repos/example/project/pulls/7":
                return Response({"state": "open", "merged_at": None, "draft": False})
            if path == "/repos/example/project/pulls/8":
                return Response({"state": "closed", "merged_at": "2026-09-27T00:00:00Z", "draft": False})
            raise AssertionError(path)

        report = audit("demo", opener)
        self.assertTrue(report["checks"]["profile_pr_claims_are_open"])
        self.assertTrue(report["checks"]["profile_merged_pr_claims_are_merged"])
        self.assertEqual([claim["kind"] for claim in report["pull_request_claims"]], ["open", "merged"])

    def test_pr_lifecycle_claims_are_local_and_unlabeled_links_are_unasserted(self):
        urls = {
            number: f"https://github.com/example/project/pull/{number}"
            for number in range(7, 14)
        }
        markdown = (
            "**Selected open upstream work, checked today** — status is shown.\n\n"
            f"| [Example #{7}]({urls[7]}) | fix | Open · review required |\n"
            f"| [Example #{8}]({urls[8]}) | docs | Open · checks passing |\n\n"
            f"Haystack [#{9}]({urls[9]}) is not listed as open: the maintainer closed it "
            "without merge.\n\n"
            f"**Merged upstream:** [Example #{10}]({urls[10]}), merged by the maintainer.\n\n"
            "**Review feedback incorporated:**\n\n"
            f"In [Example #{11}]({urls[11]}), I reported the edge case; the maintainer merged it. "
            f"[Example #{12}]({urls[12]}) remains open while review is pending.\n\n"
            "**Technical context:**\n\n"
            f"This evidence link [Example #{13}]({urls[13]}) has no lifecycle claim."
        )
        readme = {"content": base64.b64encode(markdown.encode()).decode()}
        states = {
            "7": ("open", None),
            "8": ("open", None),
            "9": ("closed", None),
            "10": ("closed", "2026-09-30T00:00:00Z"),
            "11": ("closed", "2026-09-29T00:00:00Z"),
            "12": ("open", None),
            "13": ("open", None),
        }

        def opener(request, timeout=15):
            path = request.full_url.removeprefix("https://api.github.com")
            number = path.rsplit("/", 1)[-1]
            state, merged_at = states[number]
            return Response({"state": state, "merged_at": merged_at, "draft": False})

        claims = readme_pr_claims(readme, opener)
        self.assertEqual(
            [claim["kind"] for claim in claims],
            [
                "open",
                "open",
                "closed_unmerged",
                "merged",
                "merged",
                "open",
                "unasserted",
            ],
        )

    def test_profile_repository_claims_are_checked_against_live_state(self):
        readme = base64.b64encode(
            b'<a href="https://github.com/demo/opsdesk">OpsDesk</a> '
            b'[private](https://github.com/demo/secret)'
        ).decode()

        def opener(request, timeout=15):
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "name": "Demo", "public_repos": 0})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            if path == "/repos/demo/opsdesk":
                return Response({"owner": {"login": "demo"}, "private": False, "archived": False, "fork": False})
            if path == "/repos/demo/secret":
                return Response({"owner": {"login": "demo"}, "private": True, "archived": False, "fork": False})
            raise AssertionError(path)

        report = audit("demo", opener)
        self.assertFalse(report["checks"]["profile_repo_claims_are_public"])
        self.assertEqual(len(report["repository_claims"]), 2)

    def test_issue_and_discussion_references_are_not_overclaimed(self):
        readme = base64.b64encode(
            b"Issue https://github.com/demo/project/issues/7 and "
            b"discussion https://github.com/demo/project/discussions/3"
        ).decode()

        def opener(request, timeout=15):
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "public_repos": 0})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            if path == "/repos/demo/project/issues/7":
                return Response({"state": "open", "title": "An issue", "updated_at": "2026-09-28T00:00:00Z", "comments": 0})
            if path == "/repos/demo/project/issues/7/comments?per_page=100&page=1":
                return Response([])
            raise AssertionError(path)

        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            report = audit("demo", opener)
        self.assertTrue(report["checks"]["profile_issue_claims_are_checked"])
        self.assertFalse(report["checks"]["profile_discussion_claims_have_live_metadata"])
        self.assertEqual(
            {claim["verification"] for claim in report["reference_claims"]},
            {"checked", "not_checked_auth_required"},
        )

    def test_discussion_metadata_reads_all_comment_pages_without_comment_bodies(self):
        readme = base64.b64encode(
            b"discussion https://github.com/demo/project/discussions/3"
        ).decode()
        page_calls = []

        def opener(request, timeout=15):
            if request.full_url == "https://api.github.com/graphql":
                body = json.loads(request.data)
                page_calls.append(body["variables"]["after"])
                first_page = body["variables"]["after"] is None
                comments = (
                    [{"author": {"login": "demo"}, "authorAssociation": "NONE"}]
                    if first_page
                    else [{"author": {"login": "maintainer"}, "authorAssociation": "OWNER"}]
                )
                return Response(
                    {
                        "data": {
                            "repository": {
                                "discussion": {
                                    "title": "Thread",
                                    "updatedAt": "2026-09-28T00:00:00Z",
                                    "isAnswered": False,
                                    "locked": False,
                                    "author": {"login": "demo"},
                                    "answer": None,
                                    "comments": {
                                        "totalCount": 2,
                                        "pageInfo": {
                                            "hasNextPage": first_page,
                                            "endCursor": "next" if first_page else None,
                                        },
                                        "nodes": comments,
                                    },
                                }
                            }
                        }
                    }
                )
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "public_repos": 0})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            raise AssertionError(path)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            report = audit("demo", opener)

        claim = report["reference_claims"][0]
        self.assertEqual(page_calls, [None, "next"])
        self.assertTrue(report["checks"]["profile_discussion_claims_have_live_metadata"])
        self.assertEqual(claim["verification"], "checked")
        self.assertEqual(claim["comment_count"], 2)
        self.assertEqual(claim["comment_authors"][1]["association"], "OWNER")
        self.assertNotIn("body", claim["comment_authors"][0])
        self.assertNotIn("test-token", json.dumps(report))

    def test_issue_comment_claim_includes_authors_but_not_comment_bodies(self):
        readme = base64.b64encode(b"issue https://github.com/demo/project/issues/7").decode()

        def opener(request, timeout=15):
            path = request.full_url.removeprefix("https://api.github.com")
            if path == "/users/demo":
                return Response({"type": "User", "public_repos": 0})
            if path == "/users/demo/repos?per_page=100&type=owner&sort=updated":
                return Response([])
            if path == "/repos/demo/demo/readme":
                return Response({"path": "README.md", "content": readme})
            if path == "/repos/demo/project/issues/7":
                return Response({"state": "open", "title": "Thread", "comments": 101})
            if path == "/repos/demo/project/issues/7/comments?per_page=100&page=1":
                return Response([{"user": {"login": f"user-{i}"}, "author_association": "NONE"} for i in range(100)])
            if path == "/repos/demo/project/issues/7/comments?per_page=100&page=2":
                return Response(
                    [
                        {
                            "user": {"login": "maintainer"},
                            "author_association": "OWNER",
                            "body": "not included in the report",
                        }
                    ]
                )
            raise AssertionError(path)

        report = audit("demo", opener)
        claim = report["reference_claims"][0]
        self.assertTrue(report["checks"]["profile_issue_claims_are_checked"])
        self.assertEqual(claim["comment_count"], 101)
        self.assertEqual(claim["comment_authors"][-1], {"login": "maintainer", "association": "OWNER"})
        self.assertNotIn("body", claim)


if __name__ == "__main__":
    unittest.main()
