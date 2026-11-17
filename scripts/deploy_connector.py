"""Deploy the read-only MCP connector (supabase/functions/health-mcp) to Supabase.

Steps (all idempotent):
  1. Apply supabase/migrations/0002_health_reader.sql (read-only role).
  2. Create the path token once (kept in data/private/, ignored by Git) and store
     it as the function secret MCP_PATH_TOKEN.
  3. Deploy the Edge Function with verify_jwt = false (the path token is the auth).
  4. Smoke-test the MCP endpoint (initialize, tools/list, data_coverage).
  5. Write the connector URL to data/private/connector_url.txt for claude.ai.

Requires in .env: SUPABASE_ACCESS_TOKEN (personal access token) and
HEALTH_SUPABASE_PROJECT_REF. Uses only the standard library and the Supabase
Management API. Never prints tokens, the connector URL or health values.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.supabase.com/v1"
SLUG = "health-mcp"
FUNCTION_FILE = ROOT / "supabase" / "functions" / SLUG / "index.ts"
MIGRATION = ROOT / "supabase" / "migrations" / "0002_health_reader.sql"
TOKEN_FILE = ROOT / "data" / "private" / "mcp_path_token"
URL_FILE = ROOT / "data" / "private" / "connector_url.txt"
USER_AGENT = "personal-health-pipeline-deploy/1.0"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request(method: str, url: str, *, token: str | None = None, body: bytes | None = None,
            content_type: str | None = None, timeout: int = 60) -> tuple[int, bytes]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def api_json(method: str, path: str, token: str, payload: object) -> tuple[int, bytes]:
    return request(method, f"{API}{path}", token=token, body=json.dumps(payload).encode(),
                   content_type="application/json")


def step(name: str, status: int, expected: tuple[int, ...] = (200, 201)) -> None:
    flag = "ok" if status in expected else "FAILED"
    print(f"  {name}: HTTP {status} {flag}")
    if status not in expected:
        raise SystemExit(1)


def deploy_function(token: str, ref: str) -> int:
    boundary = uuid.uuid4().hex
    metadata = {"name": SLUG, "entrypoint_path": "index.ts", "verify_jwt": False}
    source = FUNCTION_FILE.read_bytes()
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
        f"Content-Type: application/json\r\n\r\n{json.dumps(metadata)}\r\n".encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="index.ts"\r\n'
        f"Content-Type: application/typescript\r\n\r\n".encode() + source + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    status, _ = request("POST", f"{API}/projects/{ref}/functions/deploy?slug={SLUG}", token=token,
                        body=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}",
                        timeout=180)
    return status


def mcp_call(url: str, method: str, params: dict, msg_id: int) -> dict:
    payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
    status, body = request("POST", url, body=json.dumps(payload).encode(), content_type="application/json")
    if status != 200:
        raise RuntimeError(f"{method}: HTTP {status}")
    return json.loads(body)


def main() -> None:
    load_env(ROOT / ".env")
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    ref = os.environ.get("HEALTH_SUPABASE_PROJECT_REF", "").strip()
    if not token or not ref:
        sys.exit("Set SUPABASE_ACCESS_TOKEN and HEALTH_SUPABASE_PROJECT_REF in .env")

    print("Deploying MCP connector:")
    status, _ = api_json("POST", f"/projects/{ref}/database/query", token,
                         {"query": MIGRATION.read_text(encoding="utf-8")})
    step("migration 0002 (health_reader)", status)

    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    path_token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    status, _ = api_json("POST", f"/projects/{ref}/secrets", token,
                         [{"name": "MCP_PATH_TOKEN", "value": path_token}])
    step("secret MCP_PATH_TOKEN", status)

    step("deploy function", deploy_function(token, ref))

    url = f"https://{ref}.supabase.co/functions/v1/{SLUG}/{path_token}"
    URL_FILE.write_text(url + "\n", encoding="utf-8")

    # Smoke test (retries while the new version propagates).
    for attempt in range(6):
        try:
            init = mcp_call(url, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                "clientInfo": {"name": "deploy-check", "version": "1"}}, 1)
            tools = mcp_call(url, "tools/list", {}, 2)["result"]["tools"]
            cov = mcp_call(url, "tools/call", {"name": "data_coverage", "arguments": {}}, 3)["result"]
            break
        except (RuntimeError, KeyError, json.JSONDecodeError) as error:
            if attempt == 5:
                print(f"  smoke test FAILED: {error}")
                raise SystemExit(1)
            time.sleep(5)
    print(f"  initialize: {init['result']['serverInfo']['name']} (protocol {init['result']['protocolVersion']})")
    print(f"  tools: {', '.join(t['name'] for t in tools)}")
    print(f"  data_coverage: {'ERROR' if cov.get('isError') else 'ok'}")
    rogue, _ = request("POST", f"https://{ref}.supabase.co/functions/v1/{SLUG}/wrong-token",
                       body=b"{}", content_type="application/json")
    print(f"  wrong token rejected: {rogue == 404} (HTTP {rogue})")
    print(f"Connector URL saved to {URL_FILE.relative_to(ROOT)} (keep it secret).")


if __name__ == "__main__":
    main()
