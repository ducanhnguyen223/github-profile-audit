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


if __name__ == "__main__":
    unittest.main()
