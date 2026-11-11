#!/usr/bin/env python3
"""Does the assistant pick the right lookup? Run before and after any change
to prompts/assistant.md or a tool's description.

Each question below is sent to DeepSeek ONCE, exactly as the chat would send
it (the same prompt and the same tool list), and the script checks which
tool(s) the model asks for. The tools are NEVER run, so no business data is
sent anywhere: only the question, the instructions and the tool
descriptions. Costs a few cents per run.

  python tools/assistant_eval.py            # needs DEEPSEEK_API_KEY
  python tools/assistant_eval.py --list     # just print the questions

A question passes when the model's first step asks for at least one of the
expected tools (or, for NONE, asks for none). Add the questions you actually
ask; that's what makes this useful.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import assistant_tools, deepseek          # noqa: E402
from routers import chat                          # noqa: E402

NONE = frozenset()

QUESTIONS = [
    # Revenue
    ("How much did we make yesterday?", {"get_revenue_summary"}),
    ("How has Red Soil done since 24 October, in KSh and %?", {"get_revenue_history"}),
    ("Which sites make the most money?", {"get_site_performance"}),
    ("Which of our sites are slipping?", {"get_site_performance"}),
    ("What did ACK make last week?", {"get_revenue_summary", "get_revenue_history"}),
    # Sites
    ("Which sites are down right now?", {"get_site_status"}),
    ("Who is the contact for Red Soil?", {"get_site_contact"}),
    ("Which sites had the most downtime this week?", {"get_fleet_report"}),
    ("What was the network uptime yesterday?", {"get_fleet_report"}),
    ("When did ACK go down this week?", {"get_site_history"}),
    ("What are the busiest hours at Phase 3?", {"get_site_history"}),
    # Network (CCR2116)
    ("Is the internet okay right now?", {"get_network_status"}),
    ("How much bandwidth are we using on Airtel?", {"get_network_status"}),
    ("Is Veenet working?", {"get_network_status"}),
    ("What was today's peak bandwidth and when?", {"get_network_status"}),
    ("Who is expired but still connected?", {"get_pppoe_clients"}),
    ("Is JS32 online?", {"get_pppoe_clients", "lookup_customer", "get_customer"}),
    ("When was the last network outage?", {"get_network_events", "get_network_status"}),
    ("Did the core router restart today?", {"get_network_events", "get_network_status"}),
    # Alerts
    ("Were the site-down alerts sent today?", {"get_alert_log"}),
    ("How many alert messages failed this week?", {"get_alert_log"}),
    # Batteries
    ("Where is BAT-003?", {"get_batteries", "get_battery_history"}),
    ("Which batteries are deployed?", {"get_batteries"}),
    ("Where has BAT-001 been?", {"get_battery_history"}),
    ("Are any batteries in transit?", {"get_batteries"}),
    # Stock
    ("How much 24C ADSS cable do we have?", {"get_stock", "get_cable_stock"}),
    ("What needs reordering?", {"get_stock"}),
    ("What was issued to Jeff this month?", {"get_stock_movements"}),
    ("How many cable reels are left?", {"get_cable_stock", "get_stock"}),
    # Customers and packages
    ("When does JS10's account expire?", {"lookup_customer", "get_pppoe_clients", "get_customer"}),
    ("Tell me about JS45.", {"get_customer"}),
    ("Who is Mark Dre?", {"get_customer", "lookup_customer"}),
    ("Why is JS23 not browsing?", {"get_customer", "get_onu_live"}),
    # Fibre / OLT
    ("Which customers are on LOS right now?", {"get_onus"}),
    ("Who has a weak fibre signal?", {"get_onus"}),
    ("Ask the OLT about JS45's ONU.", {"get_onu_live"}),
    ("When did Redsoil's ONU last go down and why?", {"get_onu_live"}),
    ("What does the monthly package cost?", {"get_packages"}),
    # Things it must not look anything up for
    ("Send an SMS to all customers saying the network is down.", NONE),
    ("What's the capital of France?", NONE),
]


def first_step(question):
    """The tools the model asks for first, for one question."""
    body = chat.ChatRequest(messages=[{"role": "user", "content": question}])
    reply = deepseek.chat(chat.build_messages(body), tools=assistant_tools.tools_for({"role": "admin"}), timeout_seconds=60)
    return {(c.get("function") or {}).get("name") for c in reply.get("tool_calls") or []}


def main():
    unknown = {t for _q, expected in QUESTIONS for t in expected} - set(assistant_tools.TOOLS)
    if unknown:
        print(f"ERROR: the questions expect tools that don't exist: {sorted(unknown)}")
        return 2
    if "--list" in sys.argv:
        for q, expected in QUESTIONS:
            print(f"{q}  ->  {', '.join(sorted(expected)) or 'no lookup'}")
        return 0
    if not deepseek.is_configured():
        print("ERROR: DEEPSEEK_API_KEY is not set")
        return 2
    passed = 0
    for q, expected in QUESTIONS:
        try:
            asked = first_step(q)
        except deepseek.DeepSeekError as e:
            print(f"ERROR  {q}  ({e.kind})")
            continue
        ok = (not asked) if expected == NONE else bool(asked & expected)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {q}  ->  {', '.join(sorted(asked)) or 'no lookup'}"
              + ("" if ok else f"   (expected {', '.join(sorted(expected)) or 'no lookup'})"))
    print(f"\n{passed}/{len(QUESTIONS)} picked the right lookup")
    return 0 if passed == len(QUESTIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
