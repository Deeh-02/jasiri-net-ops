"""The one place this app talks to DeepSeek at runtime (ask_deepseek.py is a
separate developer tool, not part of the app). Used by the admin assistant
(routers/chat.py).

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


def _post(url, body, api_key, timeout_seconds):
    """One HTTP attempt. Returns the response, or raises DeepSeekError for a
    timeout / network failure."""
    try:
        return requests.post(url, json=body, timeout=timeout_seconds,
                             headers={"Authorization": f"Bearer {api_key}"})
    except requests.Timeout:
        raise DeepSeekError("timeout")
    except requests.RequestException:
        raise DeepSeekError("network")


def chat(messages, tools=None, timeout_seconds=30, max_tokens=900):
    """One chat completion. Returns the reply message as a dict: 'content'
    (text, may be empty) and, when the model wants data, 'tool_calls'.
    A network blip or a DeepSeek 5xx is retried once; nothing else is."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekError("not_configured")
    url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
    body = {
        "model": os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        # Same setting ask_deepseek.py uses: DeepSeek defaults to slow,
        # costlier "thinking" mode, which short answers don't need.
        "thinking": {"type": "disabled"},
    }
    if tools:
        body["tools"] = tools

    for attempt in (1, 2):
        try:
            res = _post(url, body, api_key, timeout_seconds)
        except DeepSeekError as e:
            if attempt == 2 or e.kind == "timeout":
                raise  # a second 30-second wait is worse than an error
            continue
        if res.status_code >= 500 and attempt == 1:
            continue
        break

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
        message = res.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise DeepSeekError("bad_response")
    if not isinstance(message, dict):
        raise DeepSeekError("bad_response")
    return message
