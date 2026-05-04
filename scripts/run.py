#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parent
ROOT = SCRIPTS_ROOT.parent
PIPELINE_SCRIPT = SCRIPTS_ROOT / "pipeline" / "linkedin_jobs.py"
OUTPUT_ROOT = ROOT / "outputs" / "linkedin_jobs"
LATEST_POINTER = OUTPUT_ROOT / "latest.json"

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from migration_cockpit import apply_profile_defaults, build_campaign_summary, write_campaign_summary


def output_timestamp_from_name(name: str) -> datetime | None:
    stamp = "_".join(name.split("_")[:2])
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def clean_old_outputs(days_to_keep: int = 14) -> int:
    cutoff = datetime.now() - timedelta(days=days_to_keep)
    cleaned = 0
    if not OUTPUT_ROOT.exists():
        return cleaned
    for run_dir in OUTPUT_ROOT.iterdir():
        if not run_dir.is_dir():
            continue
        run_time = output_timestamp_from_name(run_dir.name)
        if run_time is None or run_time >= cutoff:
            continue
        shutil.rmtree(run_dir)
        cleaned += 1
        print(f"[cleanup] removed {run_dir}", flush=True)
    return cleaned


def to_windows_path(path: str | Path) -> str:
    text = str(path)
    if text.startswith("/mnt/") and len(text) >= 7 and text[5].isalpha() and text[6] == "/":
        drive = text[5].upper()
        rest = text[7:].replace("/", "\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    if len(text) >= 3 and text[1] == ":" and text[2] in {"\\", "/"}:
        drive = text[0].upper()
        rest = text[3:].replace("/", "\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    return text


def find_powershell() -> str:
    ps = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not ps:
        raise RuntimeError("powershell.exe/pwsh not found in PATH")
    return ps


def ps_quote(value: str | Path) -> str:
    return "'" + to_windows_path(value).replace("'", "''") + "'"


def append_pipeline_options(
    parts: list[str],
    args: argparse.Namespace,
    *,
    include_query: bool = False,
    include_location: bool = False,
) -> None:
    if args.scoring_config:
        parts.append(f"--scoring-config {ps_quote(args.scoring_config)}")
    option_map = {
        "technical_skills_weight": "--technical-skills-weight",
        "domain_experience_weight": "--domain-experience-weight",
        "language_requirement_weight": "--language-requirement-weight",
        "visa_sponsorship_weight": "--visa-sponsorship-weight",
        "seniority_weight": "--seniority-weight",
        "min_score_threshold": "--min-score-threshold",
        "max_dynamic_queries": "--max-dynamic-queries",
    }
    for attr, option in option_map.items():
        value = getattr(args, attr, None)
        if value is not None:
            parts.append(f"{option} {value}")
    if getattr(args, "include_student_jobs", False):
        parts.append("--include-student-jobs")
    if getattr(args, "include_3rd_party", False):
        parts.append("--include-3rd-party")
    if include_query:
        for query in getattr(args, "query", None) or []:
            parts.append(f"--query {ps_quote(query)}")
    if include_location:
        if args.search_location:
            parts.append(f"--search-location {ps_quote(args.search_location)}")
        for keyword in getattr(args, "location_keywords", None) or []:
            parts.append(f"--location-keyword {ps_quote(keyword)}")


def append_pipeline_options_argv(
    command: list[str],
    args: argparse.Namespace,
    *,
    include_query: bool = False,
    include_location: bool = False,
) -> None:
    if args.scoring_config:
        command.extend(["--scoring-config", str(args.scoring_config)])
    option_map = {
        "technical_skills_weight": "--technical-skills-weight",
        "domain_experience_weight": "--domain-experience-weight",
        "language_requirement_weight": "--language-requirement-weight",
        "visa_sponsorship_weight": "--visa-sponsorship-weight",
        "seniority_weight": "--seniority-weight",
        "min_score_threshold": "--min-score-threshold",
        "max_dynamic_queries": "--max-dynamic-queries",
    }
    for attr, option in option_map.items():
        value = getattr(args, attr, None)
        if value is not None:
            command.extend([option, str(value)])
    if getattr(args, "include_student_jobs", False):
        command.append("--include-student-jobs")
    if getattr(args, "include_3rd_party", False):
        command.append("--include-3rd-party")
    if include_query:
        for query in getattr(args, "query", None) or []:
            command.extend(["--query", str(query)])
    if include_location:
        if args.search_location:
            command.extend(["--search-location", str(args.search_location)])
        for keyword in getattr(args, "location_keywords", None) or []:
            command.extend(["--location-keyword", str(keyword)])


def build_scrape_command(args: argparse.Namespace) -> list[str]:
    if not PIPELINE_SCRIPT.exists():
        raise FileNotFoundError(f"pipeline script not found: {PIPELINE_SCRIPT}")

    if os.name != "nt":
        command = [
            sys.executable,
            str(PIPELINE_SCRIPT),
            "run",
            "--pages-per-query",
            str(args.pages_per_query),
            "--max-jobs",
            str(args.max_jobs),
            "--delay-seconds",
            str(args.delay_seconds),
            "--recent-days",
            str(args.recent_days),
        ]
        if args.headless:
            command.append("--headless")
        if args.resume:
            command.extend(["--resume", str(args.resume)])
        if args.profile_dir:
            command.extend(["--profile-dir", str(args.profile_dir)])
        append_pipeline_options_argv(command, args, include_query=True, include_location=True)
        return command

    script = to_windows_path(PIPELINE_SCRIPT)
    parts = [f"& python {ps_quote(script)} run"]
    if args.headless:
        parts.append("--headless")
    parts.extend(
        [
            f"--pages-per-query {args.pages_per_query}",
            f"--max-jobs {args.max_jobs}",
            f"--delay-seconds {args.delay_seconds}",
            f"--recent-days {args.recent_days}",
        ]
    )
    if args.resume:
        parts.append(f"--resume {ps_quote(args.resume)}")
    if args.profile_dir:
        parts.append(f"--profile-dir {ps_quote(args.profile_dir)}")
    append_pipeline_options(parts, args, include_query=True, include_location=True)
    command = " ".join(parts)
    return [find_powershell(), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]


def build_login_command(args: argparse.Namespace) -> list[str]:
    if not PIPELINE_SCRIPT.exists():
        raise FileNotFoundError(f"pipeline script not found: {PIPELINE_SCRIPT}")
    if os.name != "nt":
        return [
            sys.executable,
            str(PIPELINE_SCRIPT),
            "login",
            "--profile-dir",
            str(args.profile_dir),
        ]
    script = to_windows_path(PIPELINE_SCRIPT)
    command = f"& python {ps_quote(script)} login --profile-dir {ps_quote(args.profile_dir)}"
    return [find_powershell(), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]


def load_latest_pointer() -> dict[str, Any]:
    if not LATEST_POINTER.exists():
        raise FileNotFoundError(f"latest pointer not found: {LATEST_POINTER}")
    return json.loads(LATEST_POINTER.read_text(encoding="utf-8"))


def load_summary_from_latest(latest: dict[str, Any]) -> dict[str, Any]:
    summary_path = latest.get("summary_json")
    if not summary_path:
        raise KeyError("latest pointer does not contain summary_json")
    path = Path(str(summary_path)).resolve()
    return json.loads(path.read_text(encoding="utf-8"))


def jobs_json_from_latest(latest: dict[str, Any]) -> Path:
    jobs_path = latest.get("jobs_json")
    if not jobs_path:
        raise KeyError("latest pointer does not contain jobs_json")
    return Path(str(jobs_path)).resolve()


def run_scrape(args: argparse.Namespace) -> tuple[int, int]:
    cmd = build_scrape_command(args)
    print("SCRAPE_CMD:")
    print(" ".join(cmd))
    completed = subprocess.run(cmd)
    jobs_count = 0
    if completed.returncode == 0:
        try:
            latest = load_latest_pointer()
            payload = json.loads(jobs_json_from_latest(latest).read_text(encoding="utf-8"))
            jobs_count = len(payload.get("jobs", []))
        except Exception:
            pass
    return completed.returncode, jobs_count


def write_campaign_summary_artifact(args: argparse.Namespace) -> Path:
    latest = load_latest_pointer()
    summary = load_summary_from_latest(latest)
    campaign_summary = build_campaign_summary(
        latest,
        summary,
        candidate_profile=getattr(args, "candidate_profile", None),
        market_profile=getattr(args, "market_profile", None),
        campaign=getattr(args, "campaign", None),
        resume_path=getattr(args, "resume", None),
    )
    output_dir = Path(str(summary.get("output_dir") or latest.get("output_dir") or ROOT)).resolve()
    campaign_summary_path = write_campaign_summary(campaign_summary, output_dir)
    latest["campaign_summary_json"] = str(campaign_summary_path)
    LATEST_POINTER.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    return campaign_summary_path


def command_all(args: argparse.Namespace) -> int:
    cleaned = clean_old_outputs()
    if cleaned > 0:
        print(f"[cleanup] removed {cleaned} old run directories", flush=True)

    code, jobs_count = run_scrape(args)
    if code != 0:
        return code
    if jobs_count == 0:
        return 1

    campaign_summary_path = write_campaign_summary_artifact(args)
    print(f"CAMPAIGN_SUMMARY: {campaign_summary_path}")
    return 0


def command_scrape(args: argparse.Namespace) -> int:
    cleaned = clean_old_outputs()
    if cleaned > 0:
        print(f"[cleanup] removed {cleaned} old run directories", flush=True)
    code, _ = run_scrape(args)
    return code


def command_login(args: argparse.Namespace) -> int:
    cmd = build_login_command(args)
    if args.dry_run:
        print(" ".join(cmd))
        return 0
    print("LOGIN_CMD:")
    print(" ".join(cmd))
    completed = subprocess.run(cmd)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--pages-per-query", type=int, default=2)
    parent.add_argument("--max-jobs", type=int, default=30)
    parent.add_argument("--delay-seconds", type=float, default=0.3)
    parent.add_argument("--recent-days", type=int, default=7, help="Limit LinkedIn collection and detail filtering to jobs posted in the last N days.")
    parent.add_argument("--headless", dest="headless", action="store_true")
    parent.add_argument("--no-headless", dest="headless", action="store_false")
    parent.set_defaults(headless=True)
    parent.add_argument("--resume", type=Path, default=ROOT / "samples" / "sample_cpp_qt_engineer.md")
    parent.add_argument("--scoring-config", type=Path, default=ROOT / "config" / "scoring_config.yaml")
    parent.add_argument(
        "--candidate-profile",
        help="Candidate key from samples/candidates.sample.yaml or local_private/config/candidates.local.yaml.",
    )
    parent.add_argument("--market-profile", help="Market profile key from config/migration_profiles.yaml.")
    parent.add_argument("--campaign", help="Campaign id used for funnel tracking.")
    parent.add_argument("--search-location", help="LinkedIn location value for collection. Defaults from the selected market profile.")
    parent.add_argument(
        "--location-keyword",
        dest="location_keywords",
        action="append",
        help="Accepted job-location keyword for collection filtering. Defaults from the selected market profile.",
    )
    parent.add_argument("--query", action="append", help="Custom LinkedIn keyword query. Repeat for multiple queries.")
    parent.add_argument("--technical-skills-weight", type=float)
    parent.add_argument("--domain-experience-weight", type=float)
    parent.add_argument("--language-requirement-weight", type=float)
    parent.add_argument("--visa-sponsorship-weight", type=float)
    parent.add_argument("--seniority-weight", type=float)
    parent.add_argument("--min-score-threshold", type=float)
    parent.add_argument("--include-student-jobs", action="store_true")
    parent.add_argument("--include-3rd-party", action="store_true")
    parent.add_argument("--max-dynamic-queries", type=int)
    parent.add_argument("--profile-dir", type=Path, default=ROOT / ".linkedin_profile")
    parent.add_argument("--dry-run", action="store_true", help="Print the command that would be run and exit.")

    parser = argparse.ArgumentParser(
        description="Unified entry for LinkedIn collection plus campaign summary generation.",
        parents=[parent],
    )
    sub = parser.add_subparsers(dest="command")

    p_login = sub.add_parser("login", help="Open Edge and create a LinkedIn login session.", parents=[parent])
    p_login.set_defaults(command="login")

    p_all = sub.add_parser("all", help="Collect jobs, analyze them, and write campaign_summary.json.", parents=[parent])
    p_scrape = sub.add_parser("scrape", help="Run only the collection and analysis pipeline.", parents=[parent])
    p_run = sub.add_parser("run", help="Alias for all.", parents=[parent])

    parser.set_defaults(command="all", dry_run=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(argv)
    args._explicit_scoring_config = "--scoring-config" in raw_argv
    args._explicit_search_location = "--search-location" in raw_argv
    args._explicit_location_keywords = "--location-keyword" in raw_argv
    args = apply_profile_defaults(args)

    if args.dry_run:
        if args.command in {"all", "scrape", "run"}:
            print(" ".join(build_scrape_command(args)))
            return 0
        if args.command == "login":
            print(" ".join(build_login_command(args)))
            return 0

    if args.command in {"all", "run"}:
        return command_all(args)
    if args.command == "login":
        return command_login(args)
    if args.command == "scrape":
        return command_scrape(args)
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
