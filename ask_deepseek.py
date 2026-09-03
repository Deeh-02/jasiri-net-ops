#!/usr/bin/env python3
"""
ask_deepseek.py — delegate a code-generation subtask to DeepSeek.

Called by Claude Code (via its Bash tool) as the "junior" in a senior/junior
workflow. Claude remains the orchestrator: it decides *when* to delegate,
runs the build/tests afterward, and re-invokes this script with --patch if
DeepSeek's output fails, feeding back the error.

Usage:
  export DEEPSEEK_API_KEY="sk-..."

  # First pass — generate something
  python3 ask_deepseek.py --prompt-file task.txt --design DESIGN.md --rules RULES.md --out generated.js

  # Patch pass — fix a failure, keep style anchored
  python3 ask_deepseek.py --prompt-file task.txt --design DESIGN.md --rules RULES.md \
      --patch --error-log build_error.txt --previous generated.js --out generated.js

Model tiers (DeepSeek V4 family, as of mid-2026):
  flash  — cheap, default for boilerplate/replication
  pro    — flagship, use for anything schema/algorithm-heavy
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

API_BASE = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-pro"


def strip_code_fences(text):
    """Defensive cleanup: strip a leading/trailing ```lang fence if DeepSeek
    ignored the system prompt's 'no markdown fences' instruction."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.split("\n")
    if lines[-1].strip() == "```":
        lines = lines[1:-1]
    else:
        lines = lines[1:]
    return "\n".join(lines)


def read(path):
    if not path:
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_messages(args):
    system_parts = [
        "You are a code-generation worker in an automated pipeline. "
        "Output ONLY the requested code/content — no chat-style preamble or "
        "postamble outside the code (no 'Here's the function:', no "
        "explanation before or after, no markdown fences). This does NOT "
        "mean omit in-code comments — the code quality standards below "
        "require WHY-comments on non-obvious logic and workarounds; include "
        "those as part of the code itself. Match existing conventions "
        "exactly rather than imposing your own style."
    ]
    design = read(args.design)
    if design:
        system_parts.append("Design/style tokens to follow strictly:\n" + design)

    rules = read(args.rules)
    if rules:
        system_parts.append(
            "Code quality standards to follow strictly — naming, comments, "
            "error handling, isolation:\n" + rules
        )

    task = read(args.prompt_file)

    if args.patch:
        previous = read(args.previous)
        error_log = read(args.error_log)
        user_msg = (
            f"Original task:\n{task}\n\n"
            f"Your previous output:\n{previous}\n\n"
            f"This build/test error resulted:\n{error_log}\n\n"
            "Fix it. Output the complete corrected file, not a diff."
        )
    else:
        user_msg = task

    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": user_msg},
    ]


def call_deepseek(messages):
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        # Deterministic, low-latency output for precisely-specified tasks —
        # DeepSeek's default is thinking ENABLED at high effort, which is
        # slower and costs more per call than this workflow needs.
        "thinking": {"type": "disabled"},
    }).encode("utf-8")

    req = urllib.request.Request(
        API_BASE,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"ERROR: DeepSeek API returned {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    return data["choices"][0]["message"]["content"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt-file", required=True, help="Task description for DeepSeek")
    p.add_argument("--design", help="Path to DESIGN.md or similar style/token reference")
    p.add_argument("--rules", required=True, help="Path to RULES.md — code quality standards to enforce on generated code")
    p.add_argument("--patch", action="store_true", help="Error-correction pass")
    p.add_argument("--previous", help="Path to the file DeepSeek previously generated (--patch mode)")
    p.add_argument("--error-log", help="Path to captured build/test error output (--patch mode)")
    p.add_argument("--out", help="Write result here instead of stdout")
    args = p.parse_args()

    if args.patch and not (args.previous and args.error_log):
        p.error("--patch requires --previous and --error-log")

    messages = build_messages(args)
    result = call_deepseek(messages)
    result = strip_code_fences(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Wrote {len(result)} chars to {args.out}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()