import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        token = (query.get("token") or [""])[0]

        if not token or token != os.environ.get("STOP_TOKEN"):
            self.respond(403, {"ok": False, "message": "Invalid stop token."})
            return

        try:
            set_actions_variable("AUTOMATION_ACTIVE", "false")
        except Exception as exc:
            self.respond(500, {"ok": False, "message": str(exc)})
            return

        self.respond(
            200,
            {
                "ok": True,
                "message": "Automation disabled. Future scheduled GitHub Actions runs will be skipped.",
            },
        )

    def respond(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def set_actions_variable(name: str, value: str) -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    github_token = os.environ["GITHUB_TOKEN"]
    base_url = f"https://api.github.com/repos/{repository}/actions/variables"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }

    patch_payload = json.dumps({"name": name, "value": value}).encode("utf-8")
    patch_request = Request(
        f"{base_url}/{name}",
        data=patch_payload,
        headers=headers,
        method="PATCH",
    )
    try:
        with urlopen(patch_request, timeout=20) as response:
            if response.status not in {200, 201, 204}:
                raise RuntimeError(f"GitHub returned HTTP {response.status}")
            return
    except HTTPError as exc:
        if exc.code != 404:
            raise RuntimeError(f"GitHub variable update failed with HTTP {exc.code}") from exc

    create_payload = json.dumps({"name": name, "value": value}).encode("utf-8")
    create_request = Request(base_url, data=create_payload, headers=headers, method="POST")
    with urlopen(create_request, timeout=20) as response:
        if response.status not in {200, 201, 204}:
            raise RuntimeError(f"GitHub variable create returned HTTP {response.status}")
