"""Just enough of the GitHub REST API to open or update one PR."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request


class GitHubClient:
    def __init__(self, slug: str, token: str, api_url: str = "https://api.github.com"):
        self.slug = slug
        self.owner = slug.split("/")[0]
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        url = f"{self.api_url}/repos/{self.slug}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(
            url, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "api-change-agent",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"null")

    def default_branch(self) -> str:
        return self._request("GET", "")["default_branch"]

    def find_open_pr(self, head: str) -> dict | None:
        prs = self._request("GET", "/pulls", query={"state": "open", "head": f"{self.owner}:{head}"})
        return prs[0] if prs else None

    def create_pr(self, *, head: str, base: str, title: str, body: str) -> dict:
        return self._request("POST", "/pulls", {"head": head, "base": base, "title": title, "body": body})

    def update_pr(self, number: int, *, title: str, body: str) -> dict:
        return self._request("PATCH", f"/pulls/{number}", {"title": title, "body": body})
