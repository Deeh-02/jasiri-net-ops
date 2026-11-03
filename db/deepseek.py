"""The one place this app talks to DeepSeek at runtime (ask_deepseek.py is a
separate developer tool, not part of the app).

Configured from the environment, read at call time so a missing key never
stops the app from starting — it only means features that need the model
show as unavailable:

  DEEPSEEK_API_KEY   required for any call; never logged, never sent to a browser
  DEEPSEEK_BASE_URL  default https://api.deepseek.com
  DEEPSEEK_MODEL     default deepseek-v4-pro (the model ask_deepseek.py already
                     uses successfully); change it here if DeepSeek renames it

Failures are raised as DeepSeekError with a message that is safe to log and
to show: it names the kind of failure, never the upstream response body,
the URL's query or the key.
"""
import logging
import os

import requests

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"


class DeepSeekError(Exception):
    """The call failed. `kind` is one of: not_configured, timeout, network,
    rate_limited, auth, upstream, bad_response."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


def is_configured():
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


def chat_json(messages, timeout_seconds=30, max_tokens=700):
    """One chat completion that must answer in a JSON object. Returns the
    reply text (still a string — the caller validates it)."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekError("not_configured")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    body = {
        "model": os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        # Same setting ask_deepseek.py uses: DeepSeek defaults to slow,
        # costlier "thinking" mode, which a short summary doesn't need.
        "thinking": {"type": "disabled"},
    }
    try:
        res = requests.post(f"{base_url}/chat/completions", json=body, timeout=timeout_seconds,
                            headers={"Authorization": f"Bearer {api_key}"})
    except requests.Timeout:
        raise DeepSeekError("timeout")
    except requests.RequestException:
        raise DeepSeekError("network")

    if res.status_code == 429:
        raise DeepSeekError("rate_limited")
    if res.status_code in (401, 402, 403):
        # Bad key or no balance: the owner has to fix it, so log it loudly
        # (status only — the body is never logged).
        log.error("DeepSeek refused the request (HTTP %s) - check DEEPSEEK_API_KEY and the account balance",
                  res.status_code)
        raise DeepSeekError("auth")
    if res.status_code >= 400:
        log.error("DeepSeek answered HTTP %s", res.status_code)
        raise DeepSeekError("upstream")
    try:
        return res.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise DeepSeekError("bad_response")
