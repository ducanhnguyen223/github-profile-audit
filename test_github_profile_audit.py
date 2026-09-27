import base64
import io
import json
import unittest
from urllib.error import HTTPError

from github_profile_audit import audit, fetch_json


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

    def test_http_errors_are_actionable(self):
        def failing_opener(request, timeout=15):
            raise HTTPError(request.full_url, 403, "rate", {}, io.BytesIO())

        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            fetch_json("/users/demo", failing_opener)

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


if __name__ == "__main__":
    unittest.main()
