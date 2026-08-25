from __future__ import annotations

import argparse
import json
import sys
import time
import logging

from .config import Settings
from .diagnostics import collect, run_llmfit
from .logging_utils import configure_logging
from .runtime import AgentRuntime
from .benchmark import run_local_benchmark


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog="agent-windows",description="Lightweight personal Windows AI agent")
    parser.add_argument("--env",default=".env")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("chat"); sub.add_parser("voice"); sub.add_parser("status")
    doctor=sub.add_parser("doctor"); doctor.add_argument("--llmfit",action="store_true")
    sub.add_parser("benchmark")
    args=parser.parse_args(argv); settings=Settings.from_env(args.env); configure_logging(settings.log_level); runtime=AgentRuntime(settings)
    if runtime.relay and args.command in {"chat","voice"}:
        try: runtime.recover_audio()
        except Exception as exc: logging.getLogger(__name__).warning("offline audio recovery deferred: %s",exc)
    if args.command in {"status","doctor"}:
        print(json.dumps(collect(runtime),indent=2,ensure_ascii=False,default=str))
        if args.command=="doctor" and args.llmfit: print(run_llmfit())
        return 0
    if args.command=="benchmark":
        print(json.dumps(run_local_benchmark(),indent=2))
        return 0
    if args.command=="voice":
        try:
            text=runtime.voice.listen(); print("You:",text); answer=runtime.handle_text(text); print("Agent:",answer); runtime.voice.speak(answer); return 0
        except Exception as exc: print(f"Voice unavailable: {exc}",file=sys.stderr); return 2
    print("agent-Windows chat. /tool current_time, /memory QUERY, or exit")
    while True:
        try: text=input("You> ").strip()
        except (EOFError,KeyboardInterrupt): print(); return 0
        if text.casefold() in {"exit","quit"}: return 0
        if text: print("Agent>",runtime.handle_text(text))


if __name__ == "__main__": raise SystemExit(main())
