#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPTS_ROOT = Path(__file__).resolve().parent
ROOT = SCRIPTS_ROOT.parent
DEFAULT_OUTPUT = ROOT / "local_private" / "config" / "candidates.local.yaml"
DEFAULT_RESUME_COPY_DIR = ROOT / "local_private" / "CV"

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from email_reports import candidate_from_resume, discover_resume_files
from pipeline.linkedin_jobs import build_resume_profile


ROLE_FAMILY_MARKETS = {
    "backend_engineer": ["germany", "canada", "australia"],
    "cpp_qt_engineer": ["germany", "australia", "netherlands"],
}


def normalize_key(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def role_family_from_profile(resume_path: Path) -> str:
    profile = build_resume_profile(resume_path)
    if profile.skill_levels.get("java", 0.0) >= 0.7 and profile.skill_levels.get("spring", 0.0) >= 0.5:
        return "backend_engineer"
    if profile.skill_levels.get("qt", 0.0) >= 0.4 or profile.skill_levels.get("qml", 0.0) >= 0.4:
        return "cpp_qt_engineer"
    if profile.skill_levels.get("c++", 0.0) >= 0.7:
        return "cpp_qt_engineer"
    return "general_engineer"


def role_positioning(role_family: str) -> str:
    mapping = {
        "backend_engineer": "Backend / Platform engineer for services, integration, and distributed systems",
        "cpp_qt_engineer": "C++ / Qt / Linux engineer for desktop software, diagnostics, and industrial tooling",
        "general_engineer": "Software engineer for application delivery and technical problem solving",
    }
    return mapping.get(role_family, mapping["general_engineer"])


def portfolio_placeholder(role_family: str) -> str:
    mapping = {
        "backend_engineer": "ReplaceWithYourBackendProject",
        "cpp_qt_engineer": "ReplaceWithYourDesktopOrQtProject",
        "general_engineer": "ReplaceWithYourProject",
    }
    return mapping.get(role_family, "ReplaceWithYourProject")


def language_hints(profile_text: str, english_level: str) -> list[str]:
    languages = [english_level or "English not clearly stated"]
    lowered = profile_text.lower()
    if "german" in lowered or "deutsch" in lowered:
        languages.append("German mentioned in source resume")
    return languages


def top_keywords(resume_path: Path, limit: int = 8) -> list[str]:
    profile = build_resume_profile(resume_path)
    labels = {
        "c++": "C++",
        "java": "Java",
        "spring": "Spring Boot",
        "kafka": "Kafka",
        "windows": "Windows",
        "linux": "Linux",
        "qt": "Qt",
        "qml": "QML",
        "cmake": "CMake",
        "sdk": "SDK integration",
        "tooling": "engineering tooling",
        "rest": "REST APIs",
        "testing": "testing",
        "docker": "Docker",
        "postgresql": "PostgreSQL",
        "redis": "Redis",
    }
    scored: list[tuple[float, str]] = []
    for key, label in labels.items():
        level = float(profile.skill_levels.get(key, 0.0))
        if level >= 0.35:
            scored.append((level, label))
    scored.sort(key=lambda item: item[0], reverse=True)
    result: list[str] = []
    for _, label in scored:
        if label not in result:
            result.append(label)
    return result[:limit]


def top_industries(resume_path: Path, limit: int = 6) -> list[str]:
    profile = build_resume_profile(resume_path)
    labels = {
        "logistics": "logistics",
        "ecommerce": "ecommerce",
        "event_driven": "distributed systems",
        "reliability": "platform reliability",
        "tooling": "engineering tools",
        "device": "device software",
        "sdk": "sdk integration",
        "desktop": "desktop software",
    }
    scored: list[tuple[float, str]] = []
    for key, label in labels.items():
        level = float(profile.domain_levels.get(key, 0.0))
        if level >= 0.35:
            scored.append((level, label))
    scored.sort(key=lambda item: item[0], reverse=True)
    result: list[str] = []
    for _, label in scored:
        if label not in result:
            result.append(label)
    return result[:limit] or ["software engineering"]


def copy_resume_to_private_store(source: Path, key: str, copy_dir: Path) -> Path:
    copy_dir.mkdir(parents=True, exist_ok=True)
    destination = copy_dir / f"{key}{source.suffix.lower()}"
    shutil.copy2(source, destination)
    return destination


def make_candidate_key(role_family: str, existing_keys: set[str]) -> str:
    base = normalize_key(f"local_{role_family}")
    index = 1
    while True:
        key = f"{base}_{index:02d}"
        if key not in existing_keys:
            existing_keys.add(key)
            return key
        index += 1


def build_redacted_candidate_entry(
    resume_path: Path,
    key: str,
    stored_resume_path: Path,
) -> dict[str, Any]:
    profile = build_resume_profile(resume_path)
    role_family = role_family_from_profile(resume_path)
    redacted_name = "Local Candidate " + key.split("_")[-1]
    try:
        stored_path_text = str(stored_resume_path.resolve().relative_to(ROOT))
    except ValueError:
        stored_path_text = str(stored_resume_path.resolve())
    return {
        "name": redacted_name,
        "aliases": [key],
        "resume_path": stored_path_text,
        "primary_positioning": role_positioning(role_family),
        "portfolio_project": portfolio_placeholder(role_family),
        "primary_keywords": top_keywords(resume_path),
        "target_industries": top_industries(resume_path),
        "languages": language_hints(profile.text, profile.english_level),
        "primary_markets": ROLE_FAMILY_MARKETS.get(role_family, ["germany"]),
        "material_status": {
            "english_cv": "ready",
            "linkedin": "needs_review",
            "portfolio": "replace_placeholder_project",
        },
    }


def load_candidate_overlay(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"candidates": {}}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Candidate config must be a YAML object: {path}")
    payload.setdefault("candidates", {})
    return payload


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a local redacted candidate config from one or more resumes.")
    parser.add_argument("--resume", dest="resumes", action="append", type=Path, help="Resume file path. Repeat to add multiple resumes.")
    parser.add_argument("--resume-dir", type=Path, help="Directory containing resumes to register.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output YAML path. Defaults to local_private/config/candidates.local.yaml.")
    parser.add_argument("--resume-copy-dir", type=Path, default=DEFAULT_RESUME_COPY_DIR, help="Where to store sanitized local resume copies.")
    parser.add_argument("--keep-resume-paths", action="store_true", help="Do not copy resumes into local_private/CV; keep the provided paths.")
    parser.add_argument("--replace", action="store_true", help="Replace the existing local candidate file instead of merging into it.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    resume_files = discover_resume_files(resumes=args.resumes, resume_dir=args.resume_dir)
    if not resume_files:
        raise RuntimeError("No resume files were provided.")

    payload = {"candidates": {}} if args.replace else load_candidate_overlay(args.output.resolve())
    existing_keys = set(payload.get("candidates", {}).keys())

    for resume_path in resume_files:
        candidate = candidate_from_resume(resume_path, require_email=False)
        role_family = role_family_from_profile(resume_path)
        key = make_candidate_key(role_family, existing_keys)
        stored_resume_path = resume_path.resolve()
        if not args.keep_resume_paths:
            stored_resume_path = copy_resume_to_private_store(resume_path.resolve(), key, args.resume_copy_dir.resolve())
        payload["candidates"][key] = build_redacted_candidate_entry(candidate.resume_path, key, stored_resume_path)
        print(f"GENERATED_CANDIDATE: {key} -> {payload['candidates'][key]['resume_path']}")

    write_yaml(args.output.resolve(), payload)
    print(f"LOCAL_CANDIDATE_CONFIG: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
