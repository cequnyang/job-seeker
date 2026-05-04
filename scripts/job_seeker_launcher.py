#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience dependency
    load_dotenv = None

SCRIPTS_ROOT = Path(__file__).resolve().parent
ROOT = SCRIPTS_ROOT.parent
RUNNER = SCRIPTS_ROOT / "run.py"
RESULTS_ROOT = ROOT / "results"
TASK_ROOT = RESULTS_ROOT / "tasks"
LINKEDIN_OUTPUT_ROOT = ROOT / "outputs" / "linkedin_jobs"
RAW_TASK_ROOT = ROOT / "outputs" / "tasks"

if load_dotenv:
    load_dotenv(ROOT / ".env")

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from candidate_market_report import sorted_visible_jobs, success_score, write_candidate_market_report
from email_reports import (
    Candidate,
    EmailConfig,
    candidate_from_resume,
    env_bool,
    package_html_report,
    send_email_report,
    send_token_expiry_notification,
)
from migration_cockpit import (
    default_candidate_keys,
    load_profiles,
    policy_sources_with_freshness,
    profile_default_resume,
    profile_default_scoring_config,
    profile_market_location_keywords,
    profile_market_search_location,
    repo_path,
    resolve_collection_key,
)


@dataclass(frozen=True)
class CandidateRunTarget:
    key: str
    candidate: Candidate
    profile: dict[str, Any]


@dataclass(frozen=True)
class MarketRunTarget:
    key: str
    profile: dict[str, Any]


def split_values(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in str(value).split(",") if part.strip())
    return result


def prompt_csv(label: str, choices: list[str], default: list[str]) -> list[str]:
    print(f"\n{label}")
    print("可选值: " + ", ".join(choices))
    print("默认值: " + ", ".join(default))
    raw = input("> ").strip()
    return split_values([raw]) if raw else default


def prompt_int(label: str, default: int) -> int:
    raw = input(f"\n{label} [{default}]\n> ").strip()
    return default if not raw else int(raw)


def prompt_float(label: str, default: float) -> float:
    raw = input(f"\n{label} [{default}]\n> ").strip()
    return default if not raw else float(raw)


def prompt_yes_no(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"\n{label} [{suffix}]\n> ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "1", "true", "是"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Linux-friendly launcher for multi-candidate, multi-country job reports.",
    )
    parser.add_argument(
        "--candidates",
        nargs="*",
        help="Candidate keys from samples/candidates.sample.yaml or local_private/config/candidates.local.yaml. Comma-separated values are accepted.",
    )
    parser.add_argument(
        "--target-countries",
        "--countries",
        nargs="*",
        dest="target_countries",
        help="Target market keys/countries from config/migration_profiles.yaml.",
    )
    parser.add_argument("--recent-days", type=int, default=7, help="Only collect LinkedIn jobs posted in the last N days.")
    parser.add_argument("--score-threshold", type=float, default=50.0, help="Only include jobs with success score above this threshold in the final report.")
    parser.add_argument("--max-jobs-per-country", type=int, default=20, help="Collection and final report limit per country.")
    parser.add_argument("--pages-per-query", type=int, default=2)
    parser.add_argument("--delay-seconds", type=float, default=0.3)
    parser.add_argument("--campaign", help="Campaign id. Defaults to config campaign_defaults.default_campaign.")
    parser.add_argument("--profile-dir", type=Path, default=ROOT / ".linkedin_profile")
    parser.add_argument("--task-root", type=Path, default=TASK_ROOT)
    parser.add_argument("--keep-days", type=int, default=14, help="Remove task/linkedin output directories older than this before starting.")
    parser.add_argument("--headless", dest="headless", action="store_true")
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.set_defaults(headless=True)
    parser.add_argument("--dry-run", action="store_true", help="Print planned runs without scraping, report generation, or email sending.")
    parser.add_argument("--email-dry-run", action="store_true", help="Build packages and show recipients without opening SMTP.")
    parser.add_argument("--no-email", dest="email", action="store_false", help="Do not send final reports or LinkedIn-login failure notifications.")
    parser.set_defaults(email=True)
    parser.add_argument("--email-from", help="Sender email address. Defaults to SMTP_FROM or SMTP_USERNAME.")
    parser.add_argument("--email-subject-prefix", default=os.getenv("EMAIL_SUBJECT_PREFIX", "LinkedIn 多国家岗位报告"))
    parser.add_argument("--smtp-host", help="SMTP host. Defaults to SMTP_HOST.")
    parser.add_argument("--smtp-port", type=int, help="SMTP port. Defaults to SMTP_PORT or 587.")
    parser.add_argument("--smtp-username", help="SMTP username. Defaults to SMTP_USERNAME.")
    parser.add_argument("--smtp-password", help="SMTP password/app password. Defaults to SMTP_PASSWORD.")
    parser.add_argument("--smtp-timeout", type=float, default=float(os.getenv("SMTP_TIMEOUT", "30")))
    parser.add_argument("--smtp-starttls", dest="smtp_starttls", action="store_true")
    parser.add_argument("--no-smtp-starttls", dest="smtp_starttls", action="store_false")
    parser.set_defaults(smtp_starttls=env_bool("SMTP_STARTTLS", True))
    parser.add_argument("--smtp-ssl", action="store_true", default=env_bool("SMTP_SSL", False))
    return parser


def interactive_fill(args: argparse.Namespace, config: dict[str, Any]) -> argparse.Namespace:
    candidate_keys = list(config.get("candidates", {}).keys())
    market_keys = list(config.get("markets", {}).keys())
    default_candidates = default_candidate_keys(config) or candidate_keys[:]
    default_markets = ["germany"] if "germany" in config.get("markets", {}) else market_keys[:1]
    args.candidates = prompt_csv("候选人 list（使用配置中的 candidate key）", candidate_keys, default_candidates)
    args.target_countries = prompt_csv("目标国家 list（使用配置中的 market key/country）", market_keys, default_markets)
    args.recent_days = prompt_int("最近 n 天岗位限制", args.recent_days)
    args.score_threshold = prompt_float("最终报告收录的成功率/打分阈值", args.score_threshold)
    args.email_dry_run = prompt_yes_no("是否只打包并检查收件人，不真实发送邮件", default=False)
    return args


def candidate_key_for_value(config: dict[str, Any], value: str) -> str:
    candidates = config.get("candidates", {})
    try:
        return resolve_collection_key(candidates, value)
    except Exception:
        candidate_path = repo_path(value).resolve()
        for key, profile in candidates.items():
            resume = profile_default_resume(profile)
            if resume and resume.resolve() == candidate_path:
                return key
        raise


def resolve_candidates(config: dict[str, Any], selected: list[str]) -> list[CandidateRunTarget]:
    values = selected or default_candidate_keys(config)
    targets: list[CandidateRunTarget] = []
    seen: set[str] = set()
    for value in values:
        key = candidate_key_for_value(config, value)
        if key in seen:
            continue
        profile = config["candidates"][key]
        resume = profile_default_resume(profile)
        if not resume or not resume.exists():
            raise FileNotFoundError(f"Configured resume does not exist for candidate {key}: {resume}")
        candidate = candidate_from_resume(resume, require_email=True)
        targets.append(CandidateRunTarget(key=key, candidate=candidate, profile=profile))
        seen.add(key)
    if not targets:
        raise RuntimeError("No candidates were selected.")
    return targets


def resolve_markets(config: dict[str, Any], selected: list[str], candidates: list[CandidateRunTarget]) -> list[MarketRunTarget]:
    if selected:
        values = selected
    else:
        values = []
        for target in candidates:
            values.extend(str(item) for item in target.profile.get("primary_markets", []))
    targets: list[MarketRunTarget] = []
    seen: set[str] = set()
    for value in values:
        key = resolve_collection_key(config.get("markets", {}), value)
        if key in seen:
            continue
        targets.append(MarketRunTarget(key=key, profile=config["markets"][key]))
        seen.add(key)
    if not targets:
        raise RuntimeError("No target countries were selected.")
    return targets


def parse_output_timestamp(name: str) -> datetime | None:
    stamp = "_".join(name.split("_")[:2])
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def cleanup_old_dirs(root: Path, keep_days: int) -> int:
    cutoff = datetime.now() - timedelta(days=max(1, int(keep_days)))
    if not root.exists():
        return 0
    cleaned = 0
    for item in root.iterdir():
        if not item.is_dir():
            continue
        stamp = parse_output_timestamp(item.name)
        if stamp is None or stamp >= cutoff:
            continue
        shutil.rmtree(item)
        cleaned += 1
        print(f"[cleanup] removed {item}")
    return cleaned


def profile_has_local_session(profile_dir: Path) -> bool:
    return profile_dir.exists() and any(profile_dir.rglob("*"))


def email_config_from_args(args: argparse.Namespace) -> EmailConfig:
    username = args.smtp_username if args.smtp_username is not None else os.getenv("SMTP_USERNAME", "")
    password = args.smtp_password if args.smtp_password is not None else os.getenv("SMTP_PASSWORD", "")
    sender = args.email_from if args.email_from is not None else (os.getenv("SMTP_FROM", "") or username)
    port = args.smtp_port if args.smtp_port is not None else int(os.getenv("SMTP_PORT", "587"))
    return EmailConfig(
        enabled=bool(args.email or args.email_dry_run),
        dry_run=bool(args.email_dry_run),
        host=args.smtp_host if args.smtp_host is not None else os.getenv("SMTP_HOST", ""),
        port=port,
        sender=sender,
        username=username,
        password=password,
        use_starttls=bool(args.smtp_starttls),
        use_ssl=bool(args.smtp_ssl),
        timeout_seconds=float(args.smtp_timeout),
        subject_prefix=args.email_subject_prefix,
    )


def missing_email_config(config: EmailConfig) -> list[str]:
    missing = []
    if not config.host:
        missing.append("SMTP_HOST / --smtp-host")
    if not config.sender:
        missing.append("SMTP_FROM or SMTP_USERNAME / --email-from")
    if not config.username and not config.password:
        missing.append("SMTP_USERNAME / SMTP_PASSWORD")
    return missing


def notify_linkedin_login_unavailable(
    args: argparse.Namespace,
    candidates: list[CandidateRunTarget],
    reason: str,
) -> bool:
    if not (args.email or args.email_dry_run):
        print(f"LINKEDIN_LOGIN_UNAVAILABLE: {reason}")
        print("EMAIL_SKIPPED: email delivery was disabled, so candidates were not notified.")
        return False

    config = email_config_from_args(args)
    missing = [] if config.dry_run else missing_email_config(config)
    if missing:
        print(f"LINKEDIN_LOGIN_UNAVAILABLE: {reason}")
        print("EMAIL_FAILED: missing email configuration: " + ", ".join(missing))
        return False

    ok = True
    for target in candidates:
        result = send_token_expiry_notification(config, target.candidate.resume_path)
        status = result.get("status")
        print(f"LOGIN_NOTIFICATION_{str(status).upper()}: {target.candidate.email} ({reason})")
        if status not in {"sent", "dry-run"}:
            ok = False
    return ok


def build_pair_command(
    args: argparse.Namespace,
    config: dict[str, Any],
    candidate: CandidateRunTarget,
    market: MarketRunTarget,
    campaign: str,
) -> list[str]:
    command = [
        sys.executable,
        str(RUNNER),
        "all",
        "--candidate-profile",
        candidate.key,
        "--market-profile",
        market.key,
        "--campaign",
        campaign,
        "--resume",
        str(candidate.candidate.resume_path),
        "--search-location",
        profile_market_search_location(config, market.key),
        "--max-jobs",
        str(args.max_jobs_per_country),
        "--pages-per-query",
        str(args.pages_per_query),
        "--delay-seconds",
        str(args.delay_seconds),
        "--recent-days",
        str(args.recent_days),
        "--min-score-threshold",
        str(args.score_threshold),
        "--profile-dir",
        str(args.profile_dir),
    ]
    scoring_config = profile_default_scoring_config(config, candidate.key, market.key)
    if scoring_config:
        command.extend(["--scoring-config", str(scoring_config)])
    for keyword in profile_market_location_keywords(config, market.key):
        command.extend(["--location-keyword", keyword])
    command.append("--headless" if args.headless else "--no-headless")
    if args.dry_run:
        command.append("--dry-run")
    return command


def parse_artifacts(output: str) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized = key.strip().lower()
        if normalized == "campaign_summary":
            artifacts[normalized] = value.strip()
    return artifacts


def output_indicates_linkedin_login_failure(output: str) -> bool:
    lowered = output.lower()
    markers = [
        "linkedin login session was not found",
        "linkedin session is no longer valid",
        "/login",
        "checkpoint",
        "token_expiry_notification",
        "run `login` first",
        "run the login command again",
    ]
    return any(marker in lowered for marker in markers)


def load_market_result(market: MarketRunTarget, campaign_summary_path: Path, threshold: float) -> dict[str, Any]:
    payload = json.loads(campaign_summary_path.read_text(encoding="utf-8"))
    market_payload = payload.get("market", {})
    jobs = sorted_visible_jobs(payload.get("top_market_matches", []), threshold)
    return {
        "market_key": market.key,
        "market_name": market_payload.get("name") or market.profile.get("name") or market.key,
        "country": market_payload.get("country") or market.profile.get("country") or market.key,
        "immigration_path": market_payload.get("immigration_path") or market.profile.get("immigration_path", ""),
        "job_search_mode": market_payload.get("job_search_mode") or market.profile.get("job_search_mode", ""),
        "risk_notes": market_payload.get("risk_notes") or market.profile.get("risk_notes", []),
        "policy_sources": market_payload.get("policy_sources") or policy_sources_with_freshness(market.profile),
        "jobs": jobs,
        "campaign_summary": payload,
        "campaign_summary_path": str(campaign_summary_path),
    }


def archive_raw_artifacts(
    raw_task_dir: Path,
    candidate: CandidateRunTarget,
    market: MarketRunTarget,
    campaign_summary: dict[str, Any],
) -> Path | None:
    source_dir = campaign_summary.get("artifacts", {}).get("output_dir") or campaign_summary.get("output_dir")
    if not source_dir:
        return None
    source = Path(str(source_dir)).resolve()
    if not source.exists() or not source.is_dir():
        return None
    destination = raw_task_dir / "raw" / candidate.candidate.slug / market.key
    if source == destination.resolve():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def build_email_summary(candidate: Candidate, market_results: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    combined_jobs: list[dict[str, Any]] = []
    for market in market_results:
        for job in market.get("jobs", []):
            combined = dict(job)
            combined["country"] = market.get("country")
            combined_jobs.append(combined)
    combined_jobs.sort(key=success_score, reverse=True)
    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "output_dir": str(output_dir),
        "jobs_analyzed": len(combined_jobs),
        "top_matches": combined_jobs[:5],
    }


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_profiles()
    if not raw_argv:
        args = interactive_fill(args, config)

    selected_candidates = resolve_candidates(config, split_values(args.candidates))
    selected_markets = resolve_markets(config, split_values(args.target_countries), selected_candidates)
    campaign = args.campaign or config.get("campaign_defaults", {}).get("default_campaign", "online_validation")

    if args.dry_run:
        print(f"DRY_RUN: {len(selected_candidates)} candidates x {len(selected_markets)} countries")
        for candidate in selected_candidates:
            for market in selected_markets:
                print(" ".join(build_pair_command(args, config, candidate, market, str(campaign))))
        return 0

    cleaned_tasks = cleanup_old_dirs(repo_path(args.task_root), args.keep_days)
    cleaned_linkedin = cleanup_old_dirs(LINKEDIN_OUTPUT_ROOT, args.keep_days)
    cleaned_raw_tasks = cleanup_old_dirs(RAW_TASK_ROOT, args.keep_days)
    if cleaned_tasks or cleaned_linkedin or cleaned_raw_tasks:
        print(
            f"[cleanup] tasks={cleaned_tasks}, raw_tasks={cleaned_raw_tasks}, "
            f"linkedin_outputs={cleaned_linkedin}"
        )

    if not profile_has_local_session(args.profile_dir):
        notified = notify_linkedin_login_unavailable(args, selected_candidates, f"profile directory missing or empty: {args.profile_dir}")
        return 1 if not notified else 2

    task_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    task_dir = repo_path(args.task_root) / task_id
    raw_task_dir = RAW_TASK_ROOT / task_id
    report_dir = task_dir / "reports"
    manifest_path = task_dir / "manifest.json"
    manifest: dict[str, Any] = {
        "task_id": task_id,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "campaign": campaign,
        "recent_days": args.recent_days,
        "score_threshold": args.score_threshold,
        "max_jobs_per_country": args.max_jobs_per_country,
        "candidates": [target.key for target in selected_candidates],
        "target_countries": [target.key for target in selected_markets],
        "status": "running",
        "runs": [],
        "reports": [],
    }
    write_manifest(manifest_path, manifest)

    failures = 0
    results_by_candidate: dict[str, list[dict[str, Any]]] = {target.key: [] for target in selected_candidates}
    for candidate in selected_candidates:
        for market in selected_markets:
            command = build_pair_command(args, config, candidate, market, str(campaign))
            print(f"\nRUN: {candidate.key} -> {market.key}")
            print(" ".join(command))
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            output = (completed.stdout or "") + "\n" + (completed.stderr or "")
            if completed.stdout:
                print(completed.stdout.rstrip())
            if completed.stderr:
                print(completed.stderr.rstrip(), file=sys.stderr)

            artifacts = parse_artifacts(completed.stdout or "")
            run_payload = {
                "candidate": candidate.key,
                "country": market.key,
                "returncode": completed.returncode,
                "artifacts": artifacts,
            }
            manifest["runs"].append(run_payload)
            write_manifest(manifest_path, manifest)

            if completed.returncode != 0:
                failures += 1
                if output_indicates_linkedin_login_failure(output):
                    notify_linkedin_login_unavailable(args, selected_candidates, "LinkedIn login session is unavailable or expired")
                    manifest["status"] = "failed_linkedin_login"
                    manifest["failures"] = failures
                    write_manifest(manifest_path, manifest)
                    return 1
                continue

            campaign_summary = artifacts.get("campaign_summary")
            if not campaign_summary:
                failures += 1
                run_payload["error"] = "campaign_summary artifact was not printed"
                write_manifest(manifest_path, manifest)
                continue
            market_result = load_market_result(market, Path(campaign_summary), args.score_threshold)
            raw_archive = archive_raw_artifacts(raw_task_dir, candidate, market, market_result["campaign_summary"])
            if raw_archive:
                market_result["raw_archive_dir"] = str(raw_archive)
            results_by_candidate[candidate.key].append(market_result)

    email_config = email_config_from_args(args)
    for candidate in selected_candidates:
        market_results = results_by_candidate[candidate.key]
        candidate_dir = report_dir / candidate.candidate.slug
        report_path = candidate_dir / f"{candidate.candidate.slug}_multi_country_report.html"
        report = write_candidate_market_report(
            candidate.candidate,
            market_results,
            report_path,
            args.score_threshold,
            args.recent_days,
            task_id,
        )
        package = package_html_report(candidate.candidate, candidate_dir, report, [manifest_path])
        summary = build_email_summary(candidate.candidate, market_results, candidate_dir)
        email_result: dict[str, Any] | None = None
        if args.email or args.email_dry_run:
            missing = [] if email_config.dry_run else missing_email_config(email_config)
            if missing:
                failures += 1
                email_result = {"status": "failed", "reason": "missing email config: " + ", ".join(missing)}
                print(f"EMAIL_FAILED: {candidate.candidate.email}: {email_result['reason']}", file=sys.stderr)
            else:
                email_result = send_email_report(email_config, candidate.candidate, package, summary)
                print(f"EMAIL_{str(email_result['status']).upper()}: {candidate.candidate.email} <- {package}")

        manifest["reports"].append(
            {
                "candidate": candidate.key,
                "candidate_email": candidate.candidate.email,
                "report_html": str(report),
                "package_zip": str(package),
                "email": email_result,
            }
        )
        write_manifest(manifest_path, manifest)

    manifest["status"] = "completed" if failures == 0 else "completed_with_failures"
    manifest["failures"] = failures
    manifest["completed_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    write_manifest(manifest_path, manifest)
    print(f"\nTASK_MANIFEST: {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
