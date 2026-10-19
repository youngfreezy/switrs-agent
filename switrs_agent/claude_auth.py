"""Environment for headless `claude -p` runs (UI and evals), billed to a Claude subscription.

Put a subscription token from `claude setup-token` in the project's .env as
CLAUDE_CODE_OAUTH_TOKEN=... and every headless run uses it, leaving the terminal CLI's own login
untouched. Runs refuse to start on API-key billing unless SWITRS_ALLOW_API_BILLING=1.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
DROP = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def load_dotenv(path: pathlib.Path = ROOT / ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def claude_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DROP}
    dot = load_dotenv()
    if dot.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = dot["CLAUDE_CODE_OAUTH_TOKEN"]
    env.update(extra or {})
    return env


def billing_status() -> dict:
    """Which credential headless runs will use. ok is False when it would bill API credits
    and SWITRS_ALLOW_API_BILLING is not set."""
    env = claude_env()
    try:
        out = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, env=env, timeout=30)
        status = json.loads(out.stdout)
    except Exception as e:  # CLI missing or output changed
        return {"ok": False, "method": "unknown", "detail": f"could not read claude auth status: {e}"}
    method = status.get("authMethod", "unknown")
    subscription = method in ("oauth_token", "claude.ai", "claudeai")
    allow_api = load_dotenv().get("SWITRS_ALLOW_API_BILLING", os.environ.get("SWITRS_ALLOW_API_BILLING")) == "1"
    ok = subscription or allow_api
    detail = ("Claude subscription" if subscription else
              f"API key billing ({status.get('apiKeySource', 'key')}). Run `claude setup-token` and put "
              "CLAUDE_CODE_OAUTH_TOKEN=... in .env to use your subscription, or set SWITRS_ALLOW_API_BILLING=1.")
    return {"ok": ok, "method": method, "subscription": subscription, "detail": detail, "raw": status}
