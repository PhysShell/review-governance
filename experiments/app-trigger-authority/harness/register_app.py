#!/usr/bin/env python3
"""Register the Governor GitHub App via GitHub's app-manifest flow.

Happy path:
  1. Serve http://localhost:PORT/ with a page that auto-submits the app
     manifest to https://github.com/settings/apps/new — the owner confirms
     creation in the browser (logged in as the app owner).
  2. GitHub redirects to http://localhost:PORT/callback?code=<one-time code>.
  3. The code is exchanged via POST /app-manifests/{code}/conversions
     (unauthenticated, single-use, expires in 1 hour) for app credentials.
  4. Credentials — including the private key PEM — are written directly to
     --out-dir (default ~/.config/review-governor) with 0600 permissions.
     Secrets are never printed and never enter the repository.
  5. The browser is redirected to the app's installation page.

Fallback if localhost forwarding fails: copy the `code` query parameter from
the browser address bar after GitHub redirects, then rerun with --code.
"""
import argparse
import html
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

API = "https://api.github.com"
BASE_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "review-governor-registration",
}

FORM_PAGE = """<!doctype html><meta charset="utf-8"><title>Governor App registration</title>
<body style="font-family:sans-serif;max-width:40rem;margin:3rem auto">
<h1>Governor App registration</h1>
<p>Submitting the app manifest to GitHub… if nothing happens, press the button.</p>
<form action="https://github.com/settings/apps/new?state={state}" method="post">
  <input type="hidden" name="manifest" value="{manifest}">
  <button type="submit" style="font-size:1.1rem;padding:.5rem 1.5rem">Create GitHub App</button>
</form>
<script>document.forms[0].submit()</script>
"""


def exchange_code(code: str) -> dict:
    req = urllib.request.Request(
        f"{API}/app-manifests/{urllib.parse.quote(code, safe='')}/conversions",
        method="POST", data=b"", headers=BASE_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def store_credentials(creds: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    pem_path = out_dir / "app.pem"
    pem_path.write_text(creds["pem"])
    os.chmod(pem_path, 0o600)
    cred_path = out_dir / "app-credentials.json"
    cred_path.write_text(json.dumps(creds, indent=2) + "\n")
    os.chmod(cred_path, 0o600)
    public = {
        "app_id": creds["id"],
        "slug": creds["slug"],
        "name": creds["name"],
        "html_url": creds["html_url"],
        "client_id": creds.get("client_id"),
        "owner": {"login": creds["owner"]["login"], "id": creds["owner"]["id"]},
        "created_at": creds.get("created_at"),
        "pem_path": str(pem_path),
    }
    pub_path = out_dir / "app-public.json"
    pub_path.write_text(json.dumps(public, indent=2) + "\n")
    os.chmod(pub_path, 0o644)
    return public


def summarize(public: dict, out_dir: Path) -> None:
    print(json.dumps({
        "registered": public["name"],
        "app_id": public["app_id"],
        "slug": public["slug"],
        "owner": public["owner"]["login"],
        "credentials_dir": str(out_dir),
        "secrets_note": "private key and client/webhook secrets written 0600; not printed, not in repo",
        "install_url": f"{public['html_url']}/installations/new",
    }, indent=2))


def run_server(manifest: dict, port: int, timeout_min: int, out_dir: Path) -> int:
    state = secrets.token_urlsafe(16)
    manifest = dict(manifest)
    manifest["redirect_url"] = f"http://localhost:{port}/callback"
    form = FORM_PAGE.format(
        state=state, manifest=html.escape(json.dumps(manifest), quote=True))
    outcome = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep one-time codes out of access logs

        def _send(self, status, body, extra=None):
            data = body.encode()
            self.send_response(status)
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/":
                self._send(200, form)
                return
            if url.path != "/callback":
                self._send(404, "not found")
                return
            query = urllib.parse.parse_qs(url.query)
            code = (query.get("code") or [""])[0]
            got_state = (query.get("state") or [""])[0]
            if got_state != state or not code:
                outcome["soft_error"] = "state mismatch or missing code"
                self._send(400, "<p>State mismatch or missing code — registration "
                                "NOT completed. Rerun the script and try again.</p>")
                return
            try:
                creds = exchange_code(code)
            except urllib.error.HTTPError as e:
                outcome["fatal_error"] = (
                    f"manifest conversion failed: HTTP {e.code}: "
                    f"{e.read().decode()[:300]}")
                self._send(500, "<p>Manifest conversion failed — see terminal.</p>")
                return
            public = store_credentials(creds, out_dir)
            outcome["public"] = public
            install = f"{public['html_url']}/installations/new"
            self._send(302, f'<p>App created. Continue to <a href="{install}">'
                            f'installation</a>.</p>', extra={"Location": install})

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 5
    deadline = time.time() + timeout_min * 60
    print(f"listening on http://localhost:{port}/  (deadline: {timeout_min} min)",
          flush=True)
    while "public" not in outcome and "fatal_error" not in outcome:
        if time.time() >= deadline:
            print("timed out waiting for the browser callback; rerun when ready, "
                  "or rerun with --code <code> from the redirect URL",
                  file=sys.stderr)
            return 2
        server.handle_request()
        if "soft_error" in outcome:
            print(f"warning: {outcome.pop('soft_error')}", file=sys.stderr, flush=True)
    server.server_close()
    if "fatal_error" in outcome:
        print(f"error: {outcome['fatal_error']}", file=sys.stderr)
        return 3
    summarize(outcome["public"], out_dir)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8917)
    ap.add_argument("--manifest", default=str(
        Path(__file__).resolve().parent.parent / "app-manifest.json"))
    ap.add_argument("--out-dir", default=os.path.expanduser("~/.config/review-governor"))
    ap.add_argument("--timeout-min", type=int, default=180)
    ap.add_argument("--code", help="manual fallback: one-time code from the redirect URL")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if args.code:
        summarize(store_credentials(exchange_code(args.code), out_dir), out_dir)
        return 0
    manifest = json.loads(Path(args.manifest).read_text())
    return run_server(manifest, args.port, args.timeout_min, out_dir)


if __name__ == "__main__":
    sys.exit(main())
