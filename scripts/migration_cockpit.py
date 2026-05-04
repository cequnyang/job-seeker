#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = ROOT / "results"
DEFAULT_PROFILE_CONFIG = ROOT / "config" / "migration_profiles.yaml"
DEFAULT_SAMPLE_CANDIDATES_CONFIG = ROOT / "samples" / "candidates.sample.yaml"
DEFAULT_LOCAL_CANDIDATES_CONFIG = ROOT / "local_private" / "config" / "candidates.local.yaml"
DEFAULT_TRACKER_PATH = ROOT / "samples" / "trackers" / "migration_cockpit" / "job_search_tracker.csv"
DEFAULT_LEGACY_RESUME = ROOT / "samples" / "sample_cpp_qt_engineer.md"
DEFAULT_SCORING_CONFIG = ROOT / "config" / "scoring_config.yaml"
DEFAULT_MARKET = "germany"

REAL_REPLY_STATUSES = {
    "recruiter_reply",
    "referral_reply",
    "hr_call",
    "phone_screen",
    "screen",
    "technical_screen",
    "technical_interview",
    "interview",
    "onsite",
    "offer",
}
HR_CALL_STATUSES = {"hr_call", "phone_screen", "screen"}
TECHNICAL_STATUSES = {"technical_screen", "technical_interview", "onsite"}
OFFER_STATUSES = {"offer"}
REJECTION_STATUSES = {"rejected", "rejection"}
AUTO_REJECTION_STATUSES = {"auto_rejected", "automated_rejection"}

WLB_PATTERNS = [
    r"work[- ]life balance",
    r"flexible working",
    r"flexible hours",
    r"remote",
    r"hybrid",
    r"30 days",
    r"30 vacation",
    r"workation",
    r"family",
    r"familie",
    r"vereinbarkeit",
]


@dataclass(frozen=True)
class CandidateMarketContext:
    config: dict[str, Any]
    candidate_key: str
    market_key: str
    candidate: dict[str, Any]
    market: dict[str, Any]


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def repo_path(value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def load_yaml_object(path: Path, missing_ok: bool = False) -> dict[str, Any]:
    if missing_ok and not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Migration profile config must be a YAML object: {path}")
    return payload


def ensure_profile_sections(payload: dict[str, Any]) -> dict[str, Any]:
    payload.setdefault("candidates", {})
    payload.setdefault("markets", {})
    payload.setdefault("campaign_defaults", {})
    payload.setdefault("tracking", {})
    return payload


def merge_profile_config(target: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for section in ("campaign_defaults", "tracking", "markets", "candidates"):
        value = overlay.get(section)
        if isinstance(value, dict):
            target.setdefault(section, {})
            target[section].update(value)
    return target


def default_candidate_keys(config: dict[str, Any]) -> list[str]:
    sources = config.get("_candidate_sources", {})
    local = [key for key in sources.get("local", []) if key in config.get("candidates", {})]
    if local:
        return local
    sample = [key for key in sources.get("sample", []) if key in config.get("candidates", {})]
    if sample:
        return sample
    return list(config.get("candidates", {}).keys())


def load_profiles(path: str | Path | None = None) -> dict[str, Any]:
    if path:
        return ensure_profile_sections(load_yaml_object(repo_path(path)))

    payload = ensure_profile_sections(load_yaml_object(DEFAULT_PROFILE_CONFIG))
    payload["_candidate_sources"] = {"sample": [], "local": []}

    sample_overlay = ensure_profile_sections(load_yaml_object(DEFAULT_SAMPLE_CANDIDATES_CONFIG, missing_ok=True))
    local_overlay = ensure_profile_sections(load_yaml_object(DEFAULT_LOCAL_CANDIDATES_CONFIG, missing_ok=True))
    payload["_candidate_sources"]["sample"] = list(sample_overlay.get("candidates", {}).keys())
    payload["_candidate_sources"]["local"] = list(local_overlay.get("candidates", {}).keys())

    merge_profile_config(payload, sample_overlay)
    merge_profile_config(payload, local_overlay)
    return payload


def resolve_collection_key(collection: dict[str, Any], requested: str | None, fallback: str | None = None) -> str:
    if requested and requested in collection:
        return requested
    normalized = normalize_key(requested)
    if normalized and normalized in collection:
        return normalized
    for key, profile in collection.items():
        aliases = [key, profile.get("name", ""), *profile.get("aliases", [])]
        if normalized and normalized in {normalize_key(alias) for alias in aliases}:
            return key
    if fallback and fallback in collection:
        return fallback
    if collection:
        return next(iter(collection))
    raise RuntimeError("Profile collection is empty.")


def infer_candidate_key(config: dict[str, Any], summary: dict[str, Any] | None = None, resume_path: str | Path | None = None) -> str:
    candidates = config.get("candidates", {})
    path_text = str(resume_path or (summary or {}).get("resume", {}).get("path", "")).lower()
    if path_text:
        resume_name = Path(path_text).name
        for key, profile in candidates.items():
            configured = str(profile.get("resume_path", "")).lower()
            aliases = [key, profile.get("name", ""), *profile.get("aliases", [])]
            if configured and Path(configured).name.lower() == resume_name:
                return key
            if any(normalize_key(alias) and normalize_key(alias) in normalize_key(resume_name) for alias in aliases):
                return key
    return resolve_collection_key(candidates, None, None)


def infer_market_key(config: dict[str, Any], candidate_key: str | None = None, requested: str | None = None) -> str:
    markets = config.get("markets", {})
    if requested:
        return resolve_collection_key(markets, requested, DEFAULT_MARKET)
    if candidate_key:
        candidate = config.get("candidates", {}).get(candidate_key, {})
        primary = candidate.get("primary_markets") or []
        if primary:
            return resolve_collection_key(markets, str(primary[0]), DEFAULT_MARKET)
    return resolve_collection_key(markets, None, DEFAULT_MARKET)


def resolve_candidate_market_context(
    candidate_profile: str | None = None,
    market_profile: str | None = None,
    summary: dict[str, Any] | None = None,
    resume_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> CandidateMarketContext:
    config = load_profiles(config_path)
    candidate_key = (
        resolve_collection_key(config["candidates"], candidate_profile)
        if candidate_profile
        else infer_candidate_key(config, summary, resume_path)
    )
    market_key = infer_market_key(config, candidate_key, market_profile)
    return CandidateMarketContext(
        config=config,
        candidate_key=candidate_key,
        market_key=market_key,
        candidate=config["candidates"][candidate_key],
        market=config["markets"][market_key],
    )


def candidate_market_settings(candidate: dict[str, Any], market_key: str) -> dict[str, Any]:
    return dict((candidate.get("market_settings") or {}).get(market_key, {}))


def profile_default_resume(candidate: dict[str, Any]) -> Path | None:
    value = candidate.get("resume_path")
    return repo_path(value) if value else None


def profile_default_scoring_config(config: dict[str, Any], candidate_key: str, market_key: str) -> Path | None:
    candidate = config.get("candidates", {}).get(candidate_key, {})
    settings = candidate_market_settings(candidate, market_key)
    value = settings.get("scoring_config") or candidate.get("default_scoring_config")
    return repo_path(value) if value else None


def profile_market_search_location(config: dict[str, Any], market_key: str) -> str:
    market = config.get("markets", {}).get(market_key, {})
    return str(market.get("search_location") or market.get("country") or market.get("name") or market_key)


def profile_market_location_keywords(config: dict[str, Any], market_key: str) -> list[str]:
    market = config.get("markets", {}).get(market_key, {})
    values = [
        market.get("country", ""),
        market.get("name", ""),
        *market.get("aliases", []),
        *market.get("location_keywords", []),
    ]
    seen: set[str] = set()
    keywords: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = normalize_key(text)
        if text and key not in seen:
            keywords.append(text)
            seen.add(key)
    return keywords


def apply_market_collection_defaults(args: Any, config: dict[str, Any], market_key: str) -> None:
    if hasattr(args, "search_location") and not getattr(args, "_explicit_search_location", False):
        args.search_location = profile_market_search_location(config, market_key)
    if hasattr(args, "location_keywords") and not getattr(args, "_explicit_location_keywords", False):
        args.location_keywords = profile_market_location_keywords(config, market_key)


def apply_profile_defaults(args: Any) -> Any:
    if not (getattr(args, "candidate_profile", None) or getattr(args, "market_profile", None)):
        return args
    is_batch = bool(getattr(args, "resumes", None) or getattr(args, "resume_dir", None))
    if is_batch and not getattr(args, "candidate_profile", None):
        config = load_profiles()
        args.market_profile = infer_market_key(config, None, getattr(args, "market_profile", None))
        apply_market_collection_defaults(args, config, args.market_profile)
        return args
    context = resolve_candidate_market_context(
        getattr(args, "candidate_profile", None),
        getattr(args, "market_profile", None),
        resume_path=getattr(args, "resume", None),
    )
    args.candidate_profile = context.candidate_key
    args.market_profile = context.market_key

    resume_default = profile_default_resume(context.candidate)
    if resume_default and not is_batch and Path(args.resume).resolve() == DEFAULT_LEGACY_RESUME.resolve():
        args.resume = resume_default

    scoring_default = profile_default_scoring_config(context.config, context.candidate_key, context.market_key)
    if (
        scoring_default
        and not getattr(args, "_explicit_scoring_config", False)
        and Path(args.scoring_config).resolve() == DEFAULT_SCORING_CONFIG.resolve()
    ):
        args.scoring_config = scoring_default
    apply_market_collection_defaults(args, context.config, context.market_key)
    return args


def scoring_config_for_resume(args: Any, resume_path: Path) -> Path:
    current = Path(getattr(args, "scoring_config", DEFAULT_SCORING_CONFIG)).resolve()
    if getattr(args, "_explicit_scoring_config", False):
        return current
    if current != DEFAULT_SCORING_CONFIG.resolve() and not getattr(args, "market_profile", None):
        return current
    try:
        context = resolve_candidate_market_context(
            getattr(args, "candidate_profile", None),
            getattr(args, "market_profile", None),
            resume_path=resume_path,
        )
        default = profile_default_scoring_config(context.config, context.candidate_key, context.market_key)
        return default.resolve() if default else current
    except Exception:
        return current


def normalize_status(value: Any) -> str:
    text = normalize_key(value)
    aliases = {
        "auto_reject": "auto_rejected",
        "automated_reject": "auto_rejected",
        "hr": "hr_call",
        "recruiter": "recruiter_reply",
        "tech": "technical_screen",
        "technical": "technical_screen",
    }
    return aliases.get(text, text)


def infer_country_from_location(location: str, markets: dict[str, Any]) -> str:
    text = str(location or "").lower()
    for key, market in markets.items():
        values = [key, market.get("country", ""), *market.get("location_keywords", [])]
        if any(str(value).lower() and str(value).lower() in text for value in values):
            return key
    return ""


def tracker_paths(config: dict[str, Any]) -> list[Path]:
    values = list(config.get("tracking", {}).get("tracker_paths", []))
    if DEFAULT_TRACKER_PATH not in [repo_path(value) for value in values]:
        values.append(DEFAULT_TRACKER_PATH)
    paths = []
    seen: set[Path] = set()
    for value in values:
        path = repo_path(value)
        if path.exists() and path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def load_tracker_rows(config: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    markets = config.get("markets", {})
    for path in tracker_paths(config):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                cleaned = {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}
                if not any(cleaned.get(key) for key in ("company", "role", "status", "date")):
                    continue
                cleaned["_source_tracker"] = str(path)
                if not cleaned.get("country") and not cleaned.get("market"):
                    cleaned["market"] = infer_country_from_location(cleaned.get("location", ""), markets)
                rows.append(cleaned)
    return rows


def row_matches_candidate(row: dict[str, str], candidate_key: str, candidate: dict[str, Any]) -> bool:
    value = normalize_key(row.get("candidate"))
    aliases = {normalize_key(candidate_key), normalize_key(candidate.get("name", ""))}
    aliases.update(normalize_key(alias) for alias in candidate.get("aliases", []))
    return not value or value in aliases


def row_matches_market(row: dict[str, str], market_key: str, market: dict[str, Any]) -> bool:
    value = normalize_key(row.get("market") or row.get("country"))
    aliases = {normalize_key(market_key), normalize_key(market.get("country", ""))}
    aliases.update(normalize_key(alias) for alias in market.get("aliases", []))
    if value:
        return value in aliases
    location = row.get("location", "").lower()
    return not location or any(str(keyword).lower() in location for keyword in market.get("location_keywords", []))


def row_matches_campaign(row: dict[str, str], campaign: str) -> bool:
    row_campaign = normalize_key(row.get("campaign"))
    return not row_campaign or row_campaign == normalize_key(campaign)


def build_funnel_metrics(
    rows: list[dict[str, str]],
    context: CandidateMarketContext,
    campaign: str,
) -> dict[str, Any]:
    relevant = [
        row
        for row in rows
        if row_matches_candidate(row, context.candidate_key, context.candidate)
        and row_matches_market(row, context.market_key, context.market)
        and row_matches_campaign(row, campaign)
    ]
    statuses = [normalize_status(row.get("status")) for row in relevant]
    applied = len(relevant)
    real_replies = sum(1 for status in statuses if status in REAL_REPLY_STATUSES)
    hr_calls = sum(1 for status in statuses if status in HR_CALL_STATUSES)
    technical = sum(1 for status in statuses if status in TECHNICAL_STATUSES)
    offers = sum(1 for status in statuses if status in OFFER_STATUSES)
    rejected = sum(1 for status in statuses if status in REJECTION_STATUSES)
    auto_rejected = sum(1 for status in statuses if status in AUTO_REJECTION_STATUSES)
    response_rate = real_replies / applied if applied else 0.0

    defaults = context.config.get("campaign_defaults", {})
    target = int(defaults.get("validation_applications", 100))
    healthy_min = int(defaults.get("healthy_reply_min", 3))
    strong_technical = int(defaults.get("strong_technical_screens", 2))

    if applied >= target and real_replies < healthy_min:
        signal = "revise_positioning"
        summary = "100-application validation is weak; revise positioning, CV first page, LinkedIn headline, and query set."
    elif applied >= target and real_replies >= healthy_min:
        signal = "healthy"
        summary = "Market validation is healthy enough to keep volume and deepen recruiter/referral channels."
    elif real_replies >= healthy_min or technical >= strong_technical:
        signal = "early_positive"
        summary = "Early signal is positive; continue toward the 100-application validation threshold."
    else:
        signal = "collecting"
        summary = "Insufficient funnel data; keep tracking targeted applications before changing strategy."

    return {
        "campaign": campaign,
        "applications": applied,
        "real_replies": real_replies,
        "hr_calls": hr_calls,
        "technical_screens": technical,
        "offers": offers,
        "rejections": rejected,
        "auto_rejections": auto_rejected,
        "response_rate": round(response_rate, 4),
        "target_applications": target,
        "healthy_reply_min": healthy_min,
        "strong_technical_screens": strong_technical,
        "signal": signal,
        "summary": summary,
        "recent_rows": relevant[-8:],
    }


def text_for_job(job: dict[str, Any]) -> str:
    parts = [
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("description", ""),
        job.get("description_full", ""),
        " ".join(str(value) for value in job.get("detected_keywords", []) or []),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def language_market_score(job: dict[str, Any], candidate: dict[str, Any], market: dict[str, Any]) -> float:
    requirement = str(job.get("language_requirement") or "").lower()
    candidate_languages = {normalize_key(value) for value in candidate.get("languages", [])}
    if "german b2" in requirement or "deutsch" in requirement:
        return 25.0 if "german_b1" not in candidate_languages and "german_b2" not in candidate_languages else 75.0
    if "german mentioned" in requirement or "german" in requirement:
        return 45.0 if market.get("local_language_risk", "medium") == "high" else 60.0
    if "english" in requirement:
        return 100.0 if any(value.startswith("english") for value in candidate_languages) else 70.0
    return safe_float(market.get("english_access_score"), 65.0)


def keyword_score(text: str, keywords: list[Any], default: float = 55.0) -> float:
    values = [str(value).lower() for value in keywords if str(value).strip()]
    if not values:
        return default
    hits = sum(1 for value in values if value in text)
    if hits <= 0:
        return default
    return clamp(55.0 + min(45.0, hits * 15.0))


def wlb_score(text: str) -> float:
    hits = sum(1 for pattern in WLB_PATTERNS if re.search(pattern, text, re.I))
    return clamp(45.0 + hits * 12.0)


def location_market_score(job: dict[str, Any], market: dict[str, Any]) -> float:
    location = str(job.get("location") or "").lower()
    if not location:
        return 55.0
    keywords = [market.get("country", ""), *market.get("location_keywords", [])]
    return 100.0 if any(str(keyword).lower() and str(keyword).lower() in location for keyword in keywords) else 35.0


def migration_score_job(job: dict[str, Any], context: CandidateMarketContext) -> dict[str, Any]:
    text = text_for_job(job)
    base = safe_float(job.get("score", job.get("base_score")), 0.0)
    breakdown = job.get("score_breakdown", {}) if isinstance(job.get("score_breakdown"), dict) else {}
    visa = safe_float(breakdown.get("visa"), 60.0)
    if "no visa sponsorship" in str(job.get("visa_sponsorship", "")).lower():
        visa = min(visa, 25.0)
    if "relocation" in text or "visa sponsorship" in text or "blue card" in text:
        visa = max(visa, 90.0)

    settings = candidate_market_settings(context.candidate, context.market_key)
    candidate_keywords = context.candidate.get("target_industries", []) + settings.get("target_industries", [])
    market_keywords = context.market.get("priority_industries", [])
    role_keywords = settings.get("target_roles", []) + context.candidate.get("primary_keywords", [])

    components = {
        "base_job_fit": base,
        "location": location_market_score(job, context.market),
        "visa_sponsorship": visa,
        "language": language_market_score(job, context.candidate, context.market),
        "industry": keyword_score(text, candidate_keywords + market_keywords),
        "role_positioning": keyword_score(text, role_keywords, default=60.0),
        "work_life_balance": wlb_score(text),
        "long_term_residence": safe_float(context.market.get("permanence_score"), 60.0),
        "offshore_access": safe_float(context.market.get("offshore_sponsor_score"), 55.0),
    }
    weights = {
        "base_job_fit": 0.46,
        "location": 0.12,
        "visa_sponsorship": 0.10,
        "language": 0.08,
        "industry": 0.08,
        "role_positioning": 0.06,
        "work_life_balance": 0.04,
        "long_term_residence": 0.04,
        "offshore_access": 0.02,
    }
    total = sum(components[key] * weights[key] for key in weights)
    if components["location"] < 50 and context.market_key != DEFAULT_MARKET:
        total = min(total, base * 0.72)

    if total >= 78:
        recommendation = "priority_apply"
        next_action = "Prioritize this role and tailor the first-screen positioning to the selected market."
    elif total >= 65:
        recommendation = "targeted_apply"
        next_action = "Apply if the role matches this week's target volume; tailor the relocation and project evidence."
    elif total >= 52:
        recommendation = "manual_review"
        next_action = "Review manually; only apply if visa/language risk is acceptable or a referral exists."
    else:
        recommendation = "deprioritize"
        next_action = "Skip unless there is a strong referral or unusual sponsor signal."

    return {
        **job,
        "migration_score": round(clamp(total), 1),
        "market_score_breakdown": {key: round(value, 1) for key, value in components.items()},
        "strategic_recommendation": recommendation,
        "next_action": next_action,
    }


def rank_jobs_for_market(jobs: list[dict[str, Any]], context: CandidateMarketContext) -> list[dict[str, Any]]:
    ranked = [migration_score_job(job, context) for job in jobs]
    ranked.sort(key=lambda item: (safe_float(item.get("migration_score")), safe_float(item.get("score"))), reverse=True)
    return ranked


def policy_sources_with_freshness(market: dict[str, Any]) -> list[dict[str, Any]]:
    today = date.today()
    sources = []
    for item in market.get("policy_sources", []):
        copied = dict(item)
        reviewed = str(copied.get("last_reviewed", ""))
        try:
            reviewed_date = datetime.strptime(reviewed, "%Y-%m-%d").date()
            days = (today - reviewed_date).days
        except ValueError:
            days = 9999
        copied["days_since_review"] = days
        copied["stale_warning"] = days > 90
        sources.append(copied)
    return sources


def build_next_actions(
    funnel: dict[str, Any],
    context: CandidateMarketContext,
    ranked_jobs: list[dict[str, Any]],
) -> list[str]:
    actions: list[str] = []
    settings = candidate_market_settings(context.candidate, context.market_key)
    if funnel["signal"] == "revise_positioning":
        actions.append("Pause volume increase and revise CV first page, LinkedIn headline, query set, and outreach wording.")
    elif funnel["signal"] in {"collecting", "early_positive"}:
        actions.append(
            f"Drive toward {funnel['target_applications']} tracked targeted applications before making a market decision."
        )
    else:
        actions.append("Keep the market active and increase recruiter/referral outreach quality.")
    if context.market.get("activation_rule"):
        actions.append(str(context.market["activation_rule"]))
    if settings.get("portfolio_action"):
        actions.append(str(settings["portfolio_action"]))
    if ranked_jobs and ranked_jobs[0].get("migration_score", 0) >= 70:
        top = ranked_jobs[0]
        actions.append(f"Tailor next application for {top.get('company', 'top company')} - {top.get('title', 'top role')}.")
    return actions[:5]


def build_campaign_summary(
    latest: dict[str, Any],
    summary: dict[str, Any],
    candidate_profile: str | None = None,
    market_profile: str | None = None,
    campaign: str | None = None,
    resume_path: str | Path | None = None,
) -> dict[str, Any]:
    context = resolve_candidate_market_context(candidate_profile, market_profile, summary, resume_path)
    campaign_id = campaign or context.config.get("campaign_defaults", {}).get("default_campaign", "online_validation")
    jobs = list(summary.get("top_matches") or summary.get("jobs") or [])
    ranked_jobs = rank_jobs_for_market(jobs, context)
    rows = load_tracker_rows(context.config)
    funnel = build_funnel_metrics(rows, context, str(campaign_id))
    settings = candidate_market_settings(context.candidate, context.market_key)
    scoring_strategy = dict((summary.get("scoring_config") or {}).get("market_strategy") or {})

    campaign_summary = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "campaign": str(campaign_id),
        "candidate_key": context.candidate_key,
        "market_key": context.market_key,
        "candidate": {
            "name": context.candidate.get("name"),
            "positioning": settings.get("positioning") or context.candidate.get("primary_positioning"),
            "languages": context.candidate.get("languages", []),
            "portfolio_project": settings.get("portfolio_project") or context.candidate.get("portfolio_project"),
            "primary_markets": context.candidate.get("primary_markets", []),
            "material_status": context.candidate.get("material_status", {}),
        },
        "market": {
            "name": context.market.get("name"),
            "country": context.market.get("country"),
            "priority": settings.get("priority", context.market.get("default_priority")),
            "immigration_path": context.market.get("immigration_path"),
            "job_search_mode": context.market.get("job_search_mode"),
            "risk_notes": context.market.get("risk_notes", []),
            "policy_sources": policy_sources_with_freshness(context.market),
        },
        "funnel": funnel,
        "market_signal": {
            "jobs_scored": len(ranked_jobs),
            "high_priority_jobs": sum(1 for job in ranked_jobs if safe_float(job.get("migration_score")) >= 78),
            "targeted_jobs": sum(1 for job in ranked_jobs if safe_float(job.get("migration_score")) >= 65),
            "top_migration_score": ranked_jobs[0]["migration_score"] if ranked_jobs else 0,
        },
        "job_market_strategy": {
            "objective": scoring_strategy.get("objective"),
            "positioning": scoring_strategy.get("positioning") or settings.get("positioning") or context.candidate.get("primary_positioning"),
            "rationale": scoring_strategy.get("rationale"),
            "preferred_titles": scoring_strategy.get("preferred_titles", []),
            "preferred_technologies": scoring_strategy.get("preferred_technologies", []),
            "preferred_domains": scoring_strategy.get("preferred_domains", []),
            "demand_signals": scoring_strategy.get("demand_signals", []),
            "risk_terms": scoring_strategy.get("risk_terms", []),
        },
        "top_market_matches": ranked_jobs[:20],
        "next_actions": build_next_actions(funnel, context, ranked_jobs),
        "artifacts": {
            "legacy_summary_json": latest.get("summary_json"),
            "output_dir": summary.get("output_dir") or latest.get("output_dir"),
        },
        "disclaimer": "Policy notes are planning metadata from official sources, not legal advice. Re-check official pages before acting.",
    }
    return campaign_summary


def write_campaign_summary(campaign_summary: dict[str, Any], output_dir: str | Path | None = None) -> Path:
    root = repo_path(output_dir) if output_dir else repo_path(campaign_summary["artifacts"].get("output_dir") or ROOT)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "campaign_summary.json"
    path.write_text(json.dumps(campaign_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))












def update_latest_with_campaign_summary(latest: dict[str, Any], campaign_summary_path: Path) -> dict[str, Any]:
    updated = dict(latest)
    updated["campaign_summary_json"] = str(campaign_summary_path)
    updated.pop("cockpit_html", None)
    latest_path = ROOT / "outputs" / "linkedin_jobs" / "latest.json"
    if latest_path.exists():
        latest_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated
