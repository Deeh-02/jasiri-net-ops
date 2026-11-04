#!/usr/bin/env python3
"""Fill the real ingest URL and token into the CCR2116 monitor scripts.

The repo copies of router/ops-live.rsc and router/ops-sessions.rsc hold
placeholders only — the token is never committed. This reads the real
values from the environment and writes ready-to-paste copies into
router-rendered/ (gitignored), then prints the install steps. It never
prints the token.

Usage (from the repo root, with the values in your environment):
  export NETWORK_INGEST_URL="https://YOUR-APP.onrender.com"      # or .../network/ingest
  export NETWORK_INGEST_TOKEN_CCR2116="..."                       # same value as on Render
  python tools/render_router_script.py                # dry-run copies (print, send nothing)
  python tools/render_router_script.py --live         # copies that really send
  python tools/render_router_script.py --new-token    # print a fresh random token, nothing else

Options: --router ccr2116 (default), --gateway-ip a.b.c.d, --internet-target 1.1.1.1,
--out DIR (default router-rendered).
"""
import argparse
import os
import re
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = ("ops-live.rsc", "ops-sessions.rsc")
# RouterOS splits http-header-field on commas, and the value sits inside a
# double-quoted RouterOS string — so none of these may appear in a token.
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_\-]{24,128}$")
IP_SHAPE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def ingest_url(value):
    value = (value or "").strip().rstrip("/")
    if not value.startswith("https://"):
        raise ValueError("NETWORK_INGEST_URL must start with https:// (the token must never travel unencrypted)")
    if '"' in value or "\\" in value or " " in value:
        raise ValueError("NETWORK_INGEST_URL contains a quote, backslash or space")
    return value if value.endswith("/network/ingest") else value + "/network/ingest"


def check_token(token):
    if not token:
        raise ValueError("the token environment variable is not set")
    if not TOKEN_SHAPE.match(token):
        raise ValueError("the token must be 24-128 characters of letters, digits, '-' or '_' "
                         "(no commas, quotes or spaces — RouterOS would split or break the header)")
    return token


def set_local(source, name, value, quoted=True):
    """Replace the value of `:local <name> ...` on its CONFIGURE line."""
    pattern = re.compile(rf'^(:local {name} ).*$', re.MULTILINE)
    if not pattern.search(source):
        raise ValueError(f"':local {name}' not found — has the script's CONFIGURE block changed?")
    replacement = f'"{value}"' if quoted else value
    return pattern.sub(lambda m: m.group(1) + replacement, source, count=1)


def render(source, url, token, slug, live, gateway_ip=None, internet_target=None):
    out = set_local(source, "ingestUrl", url)
    out = set_local(out, "ingestToken", token)
    out = set_local(out, "routerSlug", slug)
    out = set_local(out, "dryRun", "false" if live else "true", quoted=False)
    if gateway_ip is not None and ":local gatewayIp " in out:
        out = set_local(out, "gatewayIp", gateway_ip)
    if internet_target is not None and ":local internetPingTarget " in out:
        out = set_local(out, "internetPingTarget", internet_target)
    return out


INSTALL = """
Written (not committed — router-rendered/ is gitignored):
{files}

Mode: {mode}

Install on the router — see docs/network-monitor-runbook.md for the full
checklist. In short, in Winbox/WebFig (NOT `/system script add source=...`
from the terminal, which mangles the quotes):
  1. System > Scripts > Add New: name=ops-live, policy=read,write,test,
     paste router-rendered/ops-live.rsc. Same for ops-sessions.
  2. /system script run ops-live      and   /system script run ops-sessions
     A dry run prints the message(s). Check them against the runbook.
  3. When happy, render again with --live, replace both script bodies, run each once,
     and check the Network page. Then add the schedulers:
       /system scheduler add name=ops-live interval=15s start-time=startup policy=read,write,test on-event="/system script run ops-live"
       /system scheduler add name=ops-sessions interval=1m start-time=startup policy=read,write,test on-event="/system script run ops-sessions"
Rollback at any time: router/uninstall-ops-monitor.rsc
Delete router-rendered/ when done: it holds the token.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--router", default="ccr2116")
    parser.add_argument("--live", action="store_true", help="copies that really send (default: dry run)")
    parser.add_argument("--gateway-ip")
    parser.add_argument("--internet-target")
    parser.add_argument("--out", default=str(REPO / "router-rendered"))
    parser.add_argument("--new-token", action="store_true", help="print a fresh random token and exit")
    args = parser.parse_args()

    if args.new_token:
        print(secrets.token_urlsafe(32))
        return 0
    try:
        if not re.match(r"^[a-z0-9-]{1,32}$", args.router):
            raise ValueError("--router must be lowercase letters, digits or '-'")
        url = ingest_url(os.environ.get("NETWORK_INGEST_URL"))
        token_var = f"NETWORK_INGEST_TOKEN_{args.router.upper().replace('-', '_')}"
        token = check_token(os.environ.get(token_var))
        for value, name in ((args.gateway_ip, "--gateway-ip"), (args.internet_target, "--internet-target")):
            if value is not None and not IP_SHAPE.match(value):
                raise ValueError(f"{name} must be an IPv4 address")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in SCRIPTS:
        source = (REPO / "router" / name).read_text(encoding="utf-8")
        target = out_dir / name
        target.write_text(render(source, url, token, args.router, args.live, args.gateway_ip, args.internet_target),
                          encoding="utf-8")
        target.chmod(0o600)   # it holds the token
        written.append(f"  {target}")
    print(INSTALL.format(files="\n".join(written),
                         mode="LIVE — these copies send to Ops" if args.live else "DRY RUN — these copies only print"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
