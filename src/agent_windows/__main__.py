from __future__ import annotations

import argparse
import json
import sys
import logging

from .config import Settings
from .diagnostics import collect, provider_check_report, realtime_check_report, run_llmfit
from .integrations import integrations_report
from .final_checks import build_final_report, write_final_report
from .logging_utils import configure_logging
from .model_fit import model_fit_report
from .runtime import AgentRuntime
from .realtime import LocalRealtimeSession
from .benchmark import run_local_benchmark


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(prog="agent-windows",description="Lightweight personal Windows AI agent")
    parser.add_argument("--env",default=".env")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("chat"); sub.add_parser("voice"); sub.add_parser("status")
    doctor=sub.add_parser("doctor"); doctor.add_argument("--llmfit",action="store_true")
    sub.add_parser("benchmark"); sub.add_parser("providers-check"); sub.add_parser("realtime-check")
    sub.add_parser("integrations-check"); sub.add_parser("routing-check"); sub.add_parser("model-lab-status")
    final=sub.add_parser("final-check"); final.add_argument("--live",action="store_true"); final.add_argument("--output")
    fit=sub.add_parser("model-fit"); fit.add_argument("--params",type=float); fit.add_argument("--quant",default="q4"); fit.add_argument("--context",type=int,default=8192); fit.add_argument("--model",default="candidate")
    prepare=sub.add_parser("model-lab-prepare")
    prepare.add_argument("--backend",choices=("unsloth","soup"),required=True)
    prepare.add_argument("--dataset",required=True); prepare.add_argument("--model",required=True)
    prepare.add_argument("--approve-dataset",action="store_true")
    run=sub.add_parser("model-lab-run"); run.add_argument("--job",required=True)
    run.add_argument("--execute",action="store_true"); run.add_argument("--approve-run",action="store_true")
    dry=sub.add_parser("soup-dry-run"); dry.add_argument("--job",required=True)
    args=parser.parse_args(argv); settings=Settings.from_env(args.env); configure_logging(settings.log_level)
    with AgentRuntime(settings) as runtime:
        return _run(args, runtime)


def _run(args, runtime) -> int:
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
    if args.command=="model-fit":
        print(json.dumps(model_fit_report(parameter_billions=args.params, quantization=args.quant, context_tokens=args.context, model=args.model, ollama_base_url=runtime.settings.local_llm_url or "http://127.0.0.1:11434/v1"), indent=2, ensure_ascii=False))
        return 0
    if args.command=="model-lab-status":
        print(json.dumps(runtime.model_lab.status().as_dict(), indent=2, ensure_ascii=False))
        return 0
    if args.command=="model-lab-prepare":
        job = runtime.model_lab.prepare(args.backend, args.dataset, args.model, approved_dataset=args.approve_dataset)
        print(json.dumps(job.as_dict(), indent=2, ensure_ascii=False))
        return 0
    if args.command=="model-lab-run":
        report = runtime.model_lab.run(args.job, approved_run=args.approve_run, execute=args.execute)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("status") not in {"FAILED"} else 2
    if args.command=="soup-dry-run":
        report = runtime.model_lab.soup_dry_run(args.job)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("status") in {"OK", "CODE_READY"} else 2
    if args.command=="realtime-check":
        print(json.dumps(realtime_check_report(runtime), indent=2, ensure_ascii=False))
        return 0
    if args.command=="integrations-check":
        report = integrations_report(runtime)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if all(not str(item["status"]).startswith("PENDING") for item in report) else 2
    if args.command=="routing-check":
        print(json.dumps(runtime.provider_manager.routing_snapshot(), indent=2, ensure_ascii=False))
        return 0
    if args.command=="final-check":
        report = build_final_report(runtime, live=args.live)
        if args.output:
            path = write_final_report(report, args.output)
            print(report["summary"])
            print(f"Report: {path}")
        else:
            print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return 0 if report["overall"] in {"PASS", "CODE_READY_EXTERNAL_VALIDATION_REQUIRED"} else 2
    if args.command=="providers-check":
        report = provider_check_report(runtime)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if all(item["status"] in {"OK", "UNCONFIGURED"} for item in report) else 2
    if args.command=="voice":
        try:
            print("Voice conversation active. Press Ctrl+C to exit.")
            LocalRealtimeSession(runtime).run(lambda: True)
            return 0
        except KeyboardInterrupt:
            print()
            return 0
        except Exception as exc:
            print(f"Voice unavailable: {exc}", file=sys.stderr)
            return 2
    print("agent-Windows chat. /tool current_time, /memory QUERY, or exit")
    while True:
        try: text=input("You> ").strip()
        except (EOFError,KeyboardInterrupt): print(); return 0
        if text.casefold() in {"exit","quit"}: return 0
        if text: print("Agent>",runtime.handle_text(text))


if __name__ == "__main__": raise SystemExit(main())
