"""Chat with the assistant in the terminal, logged in as a synthetic user.

Interactive:  .venv\\Scripts\\python scripts\\chat.py rahul.sharma
Scripted demo (declines the reschedule, so the database is unchanged):
              .venv\\Scripts\\python scripts\\chat.py rahul.sharma --demo
Users: rahul.sharma, priya.nair, arjun.mehta, sneha.kapoor, nurse.anita (add --patient P1001), admin_01
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.orchestrator.models import Session  # noqa: E402
from app.orchestrator.service import Orchestrator  # noqa: E402
from app.tools.base import build_context  # noqa: E402

DEMO = [
    "Hi",
    "What are the ICU visiting hours?",
    "What is my latest blood sugar reading?",
    "Am I covered for knee replacement under my insurance plan?",
    "Can I proceed with surgery next week?",
    "Can I take ibuprofen with my current medicines?",
    "Show me the lab results of patient P1003",
    "Should I stop taking metformin before my surgery?",
    "Reschedule my cardiology appointment",
    "the first one",
    "no",
    "Do you treat snake bites?",
]


def show(r) -> None:
    print(f"\nAssistant [{r.answer_type} | intent={r.intent} | {r.latency_ms} ms | {r.trace_id}]\n{r.answer}")
    tools = [t["tool"] for t in r.tools_invoked]
    if tools:
        print("  tools:", " -> ".join(tools))
    cites = [c.get("document_id", c.get("system")) + (f" §{c['section']}" if c.get("section") else "")
             for c in r.citations]
    if cites:
        print("  sources:", "; ".join(cites))
    if r.escalation:
        print("  escalate to:", ", ".join(r.escalation["to"]))
    if r.routing_notes:
        print("  routing notes:", "; ".join(r.routing_notes))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("user")
    p.add_argument("--patient", help="patient id (staff only)")
    p.add_argument("--demo", action="store_true")
    args = p.parse_args()
    session = Session(ctx=build_context(args.user, acting_for_patient=args.patient))
    bot = Orchestrator()
    print(f"Logged in as {args.user} (role {session.ctx.role}, patient {session.ctx.patient_id})")
    for message in DEMO if args.demo else iter(lambda: input("\nYou: "), "exit"):
        if args.demo:
            print(f"\n{'-' * 70}\nYou: {message}")
        show(bot.handle(session, message))


if __name__ == "__main__":
    main()
