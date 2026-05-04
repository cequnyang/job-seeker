#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
PROFILE_DIR = WORKSPACE_ROOT / ".linkedin_profile"
OUTPUT_ROOT = WORKSPACE_ROOT / "outputs" / "linkedin_jobs"
RESULTS_ROOT = WORKSPACE_ROOT / "results"
CONFIG_ROOT = WORKSPACE_ROOT / "config"
DEFAULT_SCORING_CONFIG_PATH = CONFIG_ROOT / "scoring_config.yaml"
DEFAULT_QUERIES = ['"C++"', '"C++" Windows', '"C++" Linux', '"C++" Qt', '"C++" QML']
DEFAULT_PAGES_PER_QUERY = 2
DEFAULT_MAX_JOBS = 30
DEFAULT_DYNAMIC_QUERY_LIMIT = 8
DEFAULT_SEARCH_LOCATION = "Germany"
EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
BROWSER_COMMAND_CANDIDATES = [
    "microsoft-edge",
    "microsoft-edge-stable",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
]
DEFAULT_RESUME_CANDIDATES = [
    WORKSPACE_ROOT / "samples" / "sample_cpp_qt_engineer.md",
    WORKSPACE_ROOT / "samples" / "sample_backend_engineer.md",
    WORKSPACE_ROOT / "sample_cpp_qt_engineer.md",
    WORKSPACE_ROOT / "sample_backend_engineer.md",
]
DEFAULT_RESUME = next((path for path in DEFAULT_RESUME_CANDIDATES if path.exists()), DEFAULT_RESUME_CANDIDATES[0])
DEFAULT_TOP_MATCHES_IN_HTML = 20

# Browser automation is only needed for LinkedIn login/collection. Keeping the
# import lazy lets report-only and local config bootstrap commands run in a
# lightweight Python environment.
PlaywrightTimeoutError = TimeoutError


def require_playwright_sync_api():
    global PlaywrightTimeoutError
    try:
        from playwright.sync_api import TimeoutError as ImportedPlaywrightTimeoutError
        from playwright.sync_api import sync_playwright as imported_sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is required for LinkedIn browser automation commands. "
            "Install dependencies with `python -m pip install -r requirements.txt` "
            "and run `python -m playwright install chromium` before using login or collect."
        ) from exc
    PlaywrightTimeoutError = ImportedPlaywrightTimeoutError
    return imported_sync_playwright

TECH_FEATURES = {
    "c++": {
        "patterns": [r"c\+\+", r"\bcpp\b", r"modern c\+\+"],
        "weight": 20.0,
        "label": "C++",
    },
    "java": {
        "patterns": [r"\bjava\b", r"\bjdk\b", r"java\s*(?:17|21)"],
        "weight": 20.0,
        "label": "Java",
    },
    "spring": {
        "patterns": [r"spring boot", r"spring framework", r"spring security"],
        "weight": 12.0,
        "label": "Spring Boot",
    },
    "kafka": {
        "patterns": [r"\bkafka\b", r"event[- ]driven", r"asynchronous messaging", r"message queue"],
        "weight": 10.0,
        "label": "Kafka / Event-Driven Systems",
    },
    "mysql": {
        "patterns": [r"\bmysql\b", r"\bmariadb\b"],
        "weight": 7.0,
        "label": "MySQL",
    },
    "postgresql": {
        "patterns": [r"\bpostgresql\b", r"\bpostgres\b"],
        "weight": 6.0,
        "label": "PostgreSQL",
    },
    "redis": {
        "patterns": [r"\bredis\b"],
        "weight": 7.0,
        "label": "Redis",
    },
    "elasticsearch": {
        "patterns": [r"\belasticsearch\b", r"\belastic search\b"],
        "weight": 5.0,
        "label": "Elasticsearch",
    },
    "kubernetes": {
        "patterns": [r"\bkubernetes\b", r"\bk8s\b"],
        "weight": 6.0,
        "label": "Kubernetes",
    },
    "docker": {
        "patterns": [r"\bdocker\b", r"\bcontainer"],
        "weight": 4.0,
        "label": "Docker",
    },
    "rest": {
        "patterns": [r"\brestful?\b", r"\brest api", r"\bapi\b"],
        "weight": 6.0,
        "label": "REST APIs",
    },
    "rpc": {
        "patterns": [r"\brpc\b", r"\bdubbo\b", r"\bgrpc\b"],
        "weight": 5.0,
        "label": "RPC / Service Integration",
    },
    "testing": {
        "patterns": [r"\bjunit\b", r"testcontainers", r"integration test", r"\bunit test"],
        "weight": 5.0,
        "label": "Testing",
    },
    "windows": {
        "patterns": [r"\bwindows\b", r"win32", r"windows sdk"],
        "weight": 8.0,
        "label": "Windows",
    },
    "linux": {
        "patterns": [r"\blinux\b", r"\bposix\b"],
        "weight": 8.0,
        "label": "Linux",
    },
    "qt": {
        "patterns": [r"\bqt\b", r"qt6", r"qt5"],
        "weight": 10.0,
        "label": "Qt",
    },
    "qml": {
        "patterns": [r"\bqml\b", r"qt quick"],
        "weight": 6.0,
        "label": "QML",
    },
    "python": {
        "patterns": [r"\bpython\b"],
        "weight": 4.0,
        "label": "Python",
    },
    "cmake": {
        "patterns": [r"\bcmake\b"],
        "weight": 4.0,
        "label": "CMake",
    },
    "sdk": {
        "patterns": [r"\bsdk\b", r"software development kit"],
        "weight": 7.0,
        "label": "SDK",
    },
    "driver": {
        "patterns": [r"\bdriver\b", r"kernel", r"os internals", r"low[- ]level"],
        "weight": 7.0,
        "label": "Driver / OS Internals",
    },
    "desktop": {
        "patterns": [r"\bdesktop\b", r"pc software", r"desktop application"],
        "weight": 5.0,
        "label": "Desktop Applications",
    },
    "client": {
        "patterns": [r"\bclient\b", r"workbench", r"frontend client"],
        "weight": 5.0,
        "label": "Client Applications",
    },
    "tooling": {
        "patterns": [r"\btooling\b", r"\btools\b", r"diagnostic", r"troubleshoot"],
        "weight": 5.0,
        "label": "Tooling / Diagnostics",
    },
    "system": {
        "patterns": [r"system[- ]level", r"\bsystem\b", r"device information", r"collector"],
        "weight": 5.0,
        "label": "System-Level Work",
    },
}

DOMAIN_FEATURES = {
    "enterprise": {
        "patterns": [r"enterprise", r"merchant", r"platform", r"b2b"],
        "label": "Enterprise Platforms",
    },
    "messaging": {
        "patterns": [r"chat", r"messaging", r"notification", r"message history"],
        "label": "Messaging / Realtime Features",
    },
    "logistics": {
        "patterns": [r"logistics", r"fulfillment", r"parcel", r"inbound", r"outbound", r"supply chain"],
        "label": "Logistics / Fulfillment",
    },
    "ecommerce": {
        "patterns": [r"e[- ]commerce", r"marketplace", r"merchant", r"promotion", r"checkout"],
        "label": "E-commerce",
    },
    "event_driven": {
        "patterns": [r"event[- ]driven", r"asynchronous", r"kafka", r"outbox", r"event publishing"],
        "label": "Event-Driven Architecture",
    },
    "reliability": {
        "patterns": [r"idempotenc", r"retry", r"exception recovery", r"fault tolerance", r"delayed message"],
        "label": "Reliability Engineering",
    },
    "tooling": {
        "patterns": [r"\btools\b", r"diagnostic", r"troubleshoot", r"log analysis"],
        "label": "Diagnostics / Tooling",
    },
    "device": {
        "patterns": [r"device", r"embedded", r"hardware", r"driver", r"sensor"],
        "label": "Device / Integration Work",
    },
    "sdk": {
        "patterns": [r"\bsdk\b", r"api integration", r"driver[- ]level"],
        "label": "SDK / Integration",
    },
    "desktop": {
        "patterns": [r"desktop", r"windows app", r"client side", r"gui"],
        "label": "Desktop / GUI",
    },
    "scale": {
        "patterns": [r"millions? of users", r"large scale", r"high[- ]performance", r"at scale"],
        "label": "Scale / Performance",
    },
}

MISMATCH_PENALTIES = [
    {
        "patterns": [r"\bfpga\b", r"\bverilog\b", r"\bvhdl\b", r"\bpcb\b", r"\baltium\b", r"schaltplan", r"leiterplatte"],
        "penalty": 10.0,
        "flag": "Hardware-design-heavy role",
    },
    {
        "patterns": [r"\bunreal\b", r"\bmultiplayer\b", r"\bgameplay\b", r"\bgame engine\b"],
        "penalty": 8.0,
        "flag": "Game-engine-specialist role",
    },
]

SENIORITY_PATTERNS = {
    "principal": [r"\bprincipal\b", r"\bstaff\b", r"\barchitect\b"],
    "lead": [r"\blead\b", r"\btech lead\b"],
    "senior": [r"\bsenior\b", r"\bsr\.?\b"],
    "mid": [r"\bsoftware engineer\b", r"\bdeveloper\b", r"\bentwickler\b"],
}

GERMAN_PATTERNS = [
    re.compile(r"\bgerman\b.*\b(c1|c2|b2|fluent|native|business fluent|very good)\b", re.I),
    re.compile(r"\b(c1|c2|b2)\b.*\bgerman\b", re.I),
    re.compile(r"\bdeutsch\b.*\b(c1|c2|b2|flie[ßs]end|verhandlungssicher|sehr gut)\b", re.I),
    re.compile(r"\bdeutsch\w*\b.*\b(c1|c2|b2|flie[ßs]end|verhandlungssicher|sehr gut)\b", re.I),
    re.compile(r"\bverhandlungssicher(e)? deutsch\b", re.I),
    re.compile(r"\bfluent german\b", re.I),
    re.compile(r"\bstilsicher\w*\s+deutsch\w*\b", re.I),
]

ENGLISH_PATTERNS = [
    re.compile(r"\benglish\b", re.I),
    re.compile(r"\benglisch\w*\b", re.I),
    re.compile(r"\bie(l)?ts\b", re.I),
    re.compile(r"\bcefr\b", re.I),
]

DESCRIPTION_START_MARKERS = {
    "about the job",
    "about the role",
    "job details",
    "职位描述",
    "关于职位",
    "关于此职位",
}

DESCRIPTION_STOP_MARKERS = {
    "set alert for similar jobs",
    "订阅相似职位",
    "about the company",
    "company overview",
    "company photos",
    "公司简介",
    "公司照片",
    "über das unternehmen",
    "unternehmensbeschreibung",
    "looking for talent?",
    "interested in working with us in the future?",
    "将来是否有意向与我们合作？",
    "在招人？",
    "need help?",
    "欢迎访问帮助中心。",
}

DESCRIPTION_SKIP_LINES = {
    "more",
    "… more",
    "... more",
    "apply",
    "save",
    "申请",
    "保存",
    "快速申请",
    "显示全部",
    "发消息",
    "我有意向",
}

GERMANY_LOCATION_PATTERNS = [
    re.compile(r"\b(germany|deutschland)\b", re.I),
    re.compile(r"德国"),
    re.compile(r"\b(baden[- ]württemberg|bavaria|berlin|brandenburg|bremen|hamburg|hessen|hesse|lower saxony|niedersachsen|mecklenburg[- ]vorpommern|north rhine[- ]westphalia|nordrhein[- ]westfalen|rheinland[- ]pfalz|rhineland[- ]palatinate|saarland|sachsen[- ]anhalt|saxony[- ]anhalt|saxony|sachsen|schleswig[- ]holstein|thüringen|thuringia)\b", re.I),
    re.compile(r"(巴登-符腾堡|巴伐利亚|拜恩|柏林|勃兰登堡|不来梅|汉堡|黑森|下萨克森|梅克伦堡|北莱茵|莱茵兰|萨尔兰|萨克森-安哈尔特|萨克森|石勒苏益格|图林根|慕尼黑|法兰克福|卡尔斯鲁厄|斯图加特|德累斯顿|因哥尔斯塔特|达姆施塔特|巴登巴登|柏林)"),
]

NON_GERMANY_LOCATION_PATTERNS = [
    re.compile(r"\b(emea|europe,?\s*middle east|worldwide|global)\b", re.I),
    re.compile(r"\b(european union|europe)\b", re.I),
    re.compile(r"欧盟|欧洲、中东和非洲|全球|世界范围"),
]

FEATURE_LABELS_ZH = {
    "c++": "C++",
    "java": "Java",
    "spring": "Spring Boot",
    "kafka": "Kafka / 事件驱动",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "redis": "Redis",
    "elasticsearch": "Elasticsearch",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "rest": "REST API",
    "rpc": "RPC / 服务集成",
    "testing": "测试",
    "windows": "Windows",
    "linux": "Linux",
    "qt": "Qt",
    "qml": "QML",
    "python": "Python",
    "cmake": "CMake",
    "sdk": "SDK / 集成",
    "driver": "驱动 / OS 内部机制",
    "desktop": "桌面应用",
    "client": "客户端",
    "tooling": "工具 / 诊断",
    "system": "系统级开发",
}

SEARCH_TERMS = {
    "c++": "C++",
    "java": "Java",
    "spring": "Spring Boot",
    "kafka": "Kafka",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "redis": "Redis",
    "elasticsearch": "Elasticsearch",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "rest": "REST API",
    "rpc": "RPC",
    "testing": "Testcontainers",
    "windows": "Windows",
    "linux": "Linux",
    "qt": "Qt",
    "qml": "QML",
    "python": "Python",
    "cmake": "CMake",
    "sdk": "SDK",
    "driver": "Driver",
    "desktop": "Desktop",
    "client": "Client",
    "tooling": "Tooling",
    "system": "System",
}

DOMAIN_LABELS_ZH = {
    "enterprise": "企业平台 / B2B 业务",
    "messaging": "IM / 实时消息",
    "logistics": "物流 / 履约系统",
    "ecommerce": "电商 / 市场平台",
    "event_driven": "事件驱动架构",
    "reliability": "可靠性工程",
    "tooling": "工具链 / 故障诊断",
    "device": "设备 / 硬件集成",
    "sdk": "SDK / 集成开发",
    "desktop": "桌面 GUI / PC 客户端",
    "scale": "大规模终端 / 性能场景",
}

SOFT_SKILL_RULES = {
    "ownership": {
        "patterns": [r"\bownership\b", r"\bown\b", r"self[- ]starter", r"eigenverantwort", r"独立负责"],
        "label_zh": "自主负责 / Owner 意识",
    },
    "communication": {
        "patterns": [r"communication", r"stakeholder", r"cross[- ]functional", r"teamwork", r"collaboration", r"沟通"],
        "label_zh": "跨团队沟通协作",
    },
    "problem_solving": {
        "patterns": [r"problem[- ]solving", r"troubleshoot", r"debug", r"analytical", r"解决问题"],
        "label_zh": "问题定位与排障能力",
    },
    "agile": {
        "patterns": [r"\bagile\b", r"\bscrum\b", r"iterative", r"敏捷"],
        "label_zh": "敏捷迭代 / 团队协作",
    },
}

REQUIRED_MARKERS = [
    "required",
    "must have",
    "must-have",
    "you have",
    "what you bring",
    "qualifications",
    "requirements",
    "solid experience",
    "strong experience",
    "hands-on",
    "proficient in",
    "experience with",
    "experience in",
    "required skills",
    "must",
    "anforderungen",
    "erfahrung mit",
    "kenntnisse in",
    "voraussetzung",
]

PREFERRED_MARKERS = [
    "nice to have",
    "bonus",
    "plus",
    "preferred",
    "would be a plus",
    "ideally",
    "desirable",
    "good to have",
    "wünschenswert",
    "von vorteil",
]

RED_FLAG_RULES = [
    {
        "patterns": [r"wear many hats", r"hit the ground running", r"ambiguous", r"fast[- ]paced environment"],
        "label_zh": "职责边界偏模糊，入职后可能需要快速承接杂项与多线程任务",
    },
    {
        "patterns": [r"rockstar", r"ninja", r"guru", r"work hard, play hard", r"like a family"],
        "label_zh": "招聘表述偏口号化，团队文化可能不够清晰",
    },
    {
        "patterns": [r"competitive salary", r"\bdoe\b", r"equity[- ]heavy", r"commission[- ]based"],
        "label_zh": "薪酬表达不透明，后续沟通时需要尽早确认范围",
    },
]

CULTURE_SIGNAL_RULES = [
    {
        "patterns": [r"cross[- ]functional", r"stakeholder", r"collaboration", r"team player"],
        "label_zh": "团队协作和跨职能沟通权重较高",
    },
    {
        "patterns": [r"ownership", r"self[- ]starter", r"independent", r"eigenverantwort"],
        "label_zh": "偏好能独立推进模块的工程师",
    },
    {
        "patterns": [r"agile", r"scrum", r"iteration", r"sprint"],
        "label_zh": "工作方式偏敏捷迭代",
    },
    {
        "patterns": [r"enterprise", r"platform", r"b2b"],
        "label_zh": "业务语境更偏企业级平台或工具型产品",
    },
]

FLAG_TRANSLATIONS_ZH = {
    "Explicit German language requirement": "明确要求德语工作能力",
    "German language mentioned": "JD 提到了德语要求",
    "Student / internship role": "学生岗 / 实习岗，不适合你当前阶段",
    "Hardware-design-heavy role": "岗位重心偏硬件设计，不是你的主战场",
    "Game-engine-specialist role": "岗位偏游戏引擎方向，与你当前经验主线不一致",
}


@dataclass
class ResumeProfile:
    path: str
    text: str
    years_experience: float
    skill_levels: dict[str, float]
    domain_levels: dict[str, float]
    strengths: list[str]
    english_level: str


@dataclass
class ScoringConfig:
    scoring_weights: dict[str, float]
    filters: dict[str, Any]
    language_preferences: dict[str, list[str]]
    search: dict[str, Any]
    market_strategy: dict[str, Any]


@dataclass
class ResumeTailoringConfig:
    tailored_count: int = 3


DEFAULT_SCORING_CONFIG: dict[str, Any] = {
    "scoring_weights": {
        "language_requirement": 0.15,
        "visa_sponsorship": 0.10,
        "seniority": 0.10,
        "technical_skills": 0.40,
        "domain_experience": 0.25,
    },
    "filters": {
        "exclude_student_jobs": True,
        "exclude_3rd_party": True,
        "min_score_threshold": 50,
    },
    "language_preferences": {
        "preferred": ["English", "German"],
        "acceptable": ["English"],
        "hard_block": ["German B2+ required"],
    },
    "search": {
        "custom_queries": [],
        "max_dynamic_queries": DEFAULT_DYNAMIC_QUERY_LIMIT,
    },
    "market_strategy": {
        "strategy_weight": 0.0,
        "preferred_titles": [],
        "preferred_technologies": [],
        "preferred_domains": [],
        "demand_signals": [],
        "risk_terms": [],
    },
}


def compile_feature_patterns(feature_map: dict[str, dict[str, Any]]) -> None:
    for meta in feature_map.values():
        meta["_compiled_patterns"] = [re.compile(pattern, re.I) for pattern in meta.get("patterns", [])]


for _feature_map in (TECH_FEATURES, DOMAIN_FEATURES, SOFT_SKILL_RULES):
    compile_feature_patterns(_feature_map)
for _rule_set in (MISMATCH_PENALTIES, RED_FLAG_RULES, CULTURE_SIGNAL_RULES):
    for _rule in _rule_set:
        _rule["_compiled_patterns"] = [re.compile(pattern, re.I) for pattern in _rule.get("patterns", [])]

SENIORITY_COMPILED_PATTERNS = {
    level: [re.compile(pattern, re.I) for pattern in patterns]
    for level, patterns in SENIORITY_PATTERNS.items()
}
CANONICAL_JOB_URL_PATTERN = re.compile(r"/jobs/view/(\d+)")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
MARKDOWN_HEADER_PATTERN = re.compile(r"^##\s+", re.M)
NORMALIZE_SPACE_PATTERN = re.compile(r"\s+")
STUDENT_ROLE_PATTERN = re.compile(r"working student|werkstudent|intern|praktik|trainee", re.I)
THIRD_PARTY_PATTERN = re.compile(
    r"\b(recruitment agency|staffing|headhunter|personaldienstleister|"
    r"personalvermittlung|zeitarbeit|projektvermittlung|arbeitnehmer[ -]?überlassung|"
    r"temporary employment|contractor|freelance recruiter|external supplier|outsourcing)\b",
    re.I,
)
VISA_POSITIVE_PATTERN = re.compile(r"\b(visa sponsorship|work permit sponsorship|relocation support|blue card support)\b", re.I)
VISA_NEGATIVE_PATTERN = re.compile(
    r"\b(no visa sponsorship|must already (?:have|hold) (?:a )?(?:valid )?work (?:permit|authorization)|"
    r"valid work permit required|existing work authorization required)\b",
    re.I,
)


def clone_default_config() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_SCORING_CONFIG))


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        elif value is not None:
            merged[key] = value
    return merged


def normalize_scoring_weights(weights: dict[str, Any]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for key, default in DEFAULT_SCORING_CONFIG["scoring_weights"].items():
        try:
            normalized[key] = max(0.0, float(weights.get(key, default)))
        except (TypeError, ValueError):
            normalized[key] = float(default)
    total = sum(normalized.values())
    if total <= 0:
        return dict(DEFAULT_SCORING_CONFIG["scoring_weights"])
    return {key: value / total for key, value in normalized.items()}


def load_scoring_config(path: Path | None = None) -> ScoringConfig:
    config_path = path or DEFAULT_SCORING_CONFIG_PATH
    payload = clone_default_config()
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise RuntimeError(f"Scoring config must be a YAML object: {config_path}")
        payload = merge_config(payload, loaded)
    payload["scoring_weights"] = normalize_scoring_weights(payload.get("scoring_weights", {}))
    payload["filters"] = merge_config(DEFAULT_SCORING_CONFIG["filters"], payload.get("filters", {}))
    payload["language_preferences"] = merge_config(
        DEFAULT_SCORING_CONFIG["language_preferences"],
        payload.get("language_preferences", {}),
    )
    payload["search"] = merge_config(DEFAULT_SCORING_CONFIG["search"], payload.get("search", {}))
    payload["market_strategy"] = merge_config(
        DEFAULT_SCORING_CONFIG["market_strategy"],
        payload.get("market_strategy", {}),
    )
    return ScoringConfig(
        scoring_weights=payload["scoring_weights"],
        filters=payload["filters"],
        language_preferences=payload["language_preferences"],
        search=payload["search"],
        market_strategy=payload["market_strategy"],
    )


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path) -> str:
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8")

    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - exercised in Windows runtime
        raise RuntimeError(
            "PDF resume support requires pypdf. Run `python3 -m pip install -r requirements.txt` "
            "in Linux, or install the same requirements in the active Python environment."
        ) from exc

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\\n".join(pages)
    text = clean_resume_text(text)
    lines = []
    section_headings = {
        "EDUCATION",
        "SKILLS",
        "EXPERIENCE",
        "CERTIFICATIONS",
        "PROJECTS",
        "SUMMARY",
    }
    for raw_line in text.splitlines():
        line = normalize_text(raw_line)
        if not line:
            lines.append("")
            continue
        normalized_heading = re.sub(r"\s+", " ", line).strip().rstrip(":")
        if normalized_heading.upper() in section_headings:
            lines.append(f"## {normalized_heading.title()}")
        else:
            lines.append(raw_line.rstrip())
    return "\\n".join(lines).strip()


def normalize_text(text: str) -> str:
    return NORMALIZE_SPACE_PATTERN.sub(" ", text).strip()


def log_progress(message: str) -> None:
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((str(message) + "\n").encode(encoding, errors="replace"))
        sys.stdout.flush()


def detect_features(text: str, feature_map: dict[str, dict[str, Any]]) -> dict[str, bool]:
    found: dict[str, bool] = {}
    for key, meta in feature_map.items():
        patterns = meta.get("_compiled_patterns") or []
        found[key] = any(pattern.search(text) for pattern in patterns)
    return found


def detect_language_requirement(text: str) -> str:
    if any(pattern.search(text) for pattern in GERMAN_PATTERNS):
        return "German B2+ required or strongly preferred"
    if re.search(r"\bgerman\b|\bdeutsch\w*\b", text, re.I):
        return "German mentioned"
    if any(pattern.search(text) for pattern in ENGLISH_PATTERNS):
        return "English mentioned"
    return "Not stated"


def infer_years_experience(text: str) -> float:
    month_index = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    current = datetime.now()
    lowered = normalize_text(text).lower()

    explicit_patterns = [
        r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp|work experience|professional experience)?",
        r"(\d+(?:\.\d+)?)\s*\+?\s*years?\b",
        r"(\d+(?:\.\d+)?)\s*\+?\s*yrs?\b",
        r"(\d+(?:\.\d+)?)\s*\+?\s*年",
    ]
    explicit_hits: list[float] = []
    for pattern in explicit_patterns:
        for match in re.finditer(pattern, lowered, re.I):
            try:
                explicit_hits.append(float(match.group(1)))
            except Exception:
                continue
    if explicit_hits:
        return round(max(explicit_hits), 1)

    employment_match = re.search(r"## Employment History(.*?)(## |\Z)", text, re.S | re.I)
    scope = employment_match.group(1) if employment_match else text
    ranges: list[tuple[datetime, datetime]] = []
    pattern = re.compile(
        r"(?P<start_month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"[-\s]*(?P<start_year>20\d{2})\s*~\s*"
        r"(?:(?P<end_month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"[-\s]*(?P<end_year>20\d{2})|(?P<present>Present))",
        re.I,
    )
    for match in pattern.finditer(scope):
        start = datetime(
            year=int(match.group("start_year")),
            month=month_index[match.group("start_month").lower()],
            day=1,
        )
        if match.group("present"):
            end = current
        else:
            end = datetime(
                year=int(match.group("end_year")),
                month=month_index[match.group("end_month").lower()],
                day=1,
            )
        ranges.append((start, end))
    if not ranges:
        return 0.0
    start = min(item[0] for item in ranges)
    end = max(item[1] for item in ranges)
    return round(max(0.0, (end.year - start.year) + (end.month - start.month) / 12.0), 1)


def build_resume_profile(resume_path: Path) -> ResumeProfile:
    text = read_text(resume_path)
    lowered = text.lower()
    skill_levels = {
        "c++": 1.0 if re.search(r"c\+\+", lowered) else 0.0,
        "java": 1.0 if re.search(r"\bjava\b", lowered) else 0.0,
        "spring": 0.95 if "spring boot" in lowered else (0.75 if "spring" in lowered else 0.0),
        "kafka": 0.9 if "kafka" in lowered else 0.0,
        "mysql": 0.85 if "mysql" in lowered else 0.0,
        "postgresql": 0.7 if "postgresql" in lowered or "postgres" in lowered else 0.0,
        "redis": 0.85 if "redis" in lowered else 0.0,
        "elasticsearch": 0.7 if "elasticsearch" in lowered else 0.0,
        "kubernetes": 0.65 if "kubernetes" in lowered or "k8s" in lowered else 0.0,
        "docker": 0.6 if "docker" in lowered else 0.0,
        "rest": 0.8 if "restful" in lowered or "rest api" in lowered or "apis" in lowered else 0.0,
        "rpc": 0.75 if "rpc" in lowered or "dubbo" in lowered or "grpc" in lowered else 0.0,
        "testing": 0.7 if "testcontainers" in lowered or "junit" in lowered else 0.0,
        "windows": 1.0 if "windows" in lowered else 0.0,
        "linux": 0.6 if "linux" in lowered else 0.0,
        "qt": 0.45 if "qt" in lowered and "entry level" in lowered else (0.65 if "qt" in lowered else 0.0),
        "qml": 0.45 if "qml" in lowered and "entry level" in lowered else (0.65 if "qml" in lowered else 0.0),
        "python": 0.7 if "python" in lowered else 0.0,
        "cmake": 0.0,
        "sdk": 0.9 if "sdk" in lowered else 0.0,
        "driver": 0.85 if "driver" in lowered else 0.0,
        "desktop": 0.95 if "pc software developer" in lowered or "workbench" in lowered else 0.0,
        "client": 0.95 if "client" in lowered or "workbench" in lowered else 0.0,
        "tooling": 0.9 if "tool" in lowered or "troubleshooting" in lowered else 0.0,
        "system": 0.85 if "system-level" in lowered or "device information" in lowered else 0.0,
    }
    domain_levels = {
        "enterprise": 0.9 if "merchant" in lowered or "platform" in lowered else 0.0,
        "messaging": 0.95 if "im chat" in lowered or "message" in lowered else 0.0,
        "logistics": 0.95 if "logistics" in lowered or "parcel" in lowered or "fulfillment" in lowered else 0.0,
        "ecommerce": 0.85 if "e-commerce" in lowered or "ecommerce" in lowered or "pinduoduo" in lowered else 0.0,
        "event_driven": 0.9 if "kafka" in lowered or "asynchronous" in lowered or "transactional outbox" in lowered else 0.0,
        "reliability": 0.85 if "idempotency" in lowered or "retry" in lowered or "exception recovery" in lowered else 0.0,
        "tooling": 0.9 if "tool" in lowered or "troubleshooting" in lowered else 0.0,
        "device": 0.85 if "device" in lowered or "uav" in lowered else 0.0,
        "sdk": 0.9 if "sdk" in lowered else 0.0,
        "desktop": 0.95 if "pc software developer" in lowered or "windows" in lowered else 0.0,
        "scale": 0.95 if "qps" in lowered or "100 million" in lowered or "high-concurrency" in lowered else (0.8 if "millions" in lowered else 0.0),
    }
    if skill_levels["java"] >= 0.7 and skill_levels["spring"] >= 0.7:
        strengths = [
            "4.5+ 年 Java / Spring Boot 后端开发经验",
            "Kafka 异步消息与事件驱动履约系统经验",
            "高并发物流 / 包裹履约生产系统经验",
            "MySQL、Redis、Elasticsearch 数据与缓存实践",
            "Kubernetes 发布、Kibana 排障与可靠性改进经验",
        ]
    else:
        strengths = [
            "8+ 年 C++ 桌面 / 客户端开发经验",
            "Windows 应用与系统集成背景",
            "工具链、问题定位与稳定性维护经验",
            "SDK / 驱动级集成相关经历",
            "大规模企业产品与终端场景经验",
        ]
    if "english (professional)" in lowered:
        english_level = "Professional English"
    elif "ielts" in lowered:
        english_level = "IELTS 6.5 / B2"
    elif "english (b2)" in lowered or re.search(r"\bb2\b", lowered):
        english_level = "English B2"
    else:
        english_level = "简历未明确写出"
    return ResumeProfile(
        path=str(resume_path),
        text=text,
        years_experience=infer_years_experience(text),
        skill_levels=skill_levels,
        domain_levels=domain_levels,
        strengths=strengths,
        english_level=english_level,
    )


def query_token(term: str) -> str:
    cleaned = normalize_text(term)
    if not cleaned:
        return ""
    if cleaned.startswith('"') and cleaned.endswith('"'):
        return cleaned
    if "+" in cleaned or " " in cleaned:
        return f'"{cleaned}"'
    return cleaned


def extract_resume_skill_tags(resume: ResumeProfile, min_level: float = 0.35) -> list[str]:
    ranked: list[tuple[float, str]] = []
    for key, level in resume.skill_levels.items():
        if level < min_level:
            continue
        term = SEARCH_TERMS.get(key)
        if not term:
            continue
        weight = float(TECH_FEATURES.get(key, {}).get("weight", 1.0))
        ranked.append((weight * level, term))
    ranked.sort(key=lambda item: item[0], reverse=True)
    tags: list[str] = []
    for _, term in ranked:
        if term not in tags:
            tags.append(term)
    return tags


def normalize_query_list(values: list[Any] | None) -> list[str]:
    queries: list[str] = []
    for value in values or []:
        query = normalize_text(str(value))
        if query and query not in queries:
            queries.append(query)
    return queries


def build_dynamic_search_queries(
    resume: ResumeProfile | None,
    scoring_config: ScoringConfig,
    custom_queries: list[str] | None = None,
) -> list[str]:
    queries = normalize_query_list(custom_queries)
    queries.extend(normalize_query_list(scoring_config.search.get("custom_queries", [])))
    if queries:
        return queries

    if resume is None:
        return list(DEFAULT_QUERIES)

    tags = extract_resume_skill_tags(resume)
    if not tags:
        return list(DEFAULT_QUERIES)

    max_queries = int(scoring_config.search.get("max_dynamic_queries", DEFAULT_DYNAMIC_QUERY_LIMIT) or DEFAULT_DYNAMIC_QUERY_LIMIT)
    max_queries = max(1, max_queries)
    anchor = "C++" if "C++" in tags else tags[0]
    secondary_tags = [tag for tag in tags if tag != anchor]

    generated = [query_token(anchor)]
    for tag in secondary_tags:
        generated.append(f"{query_token(anchor)} {query_token(tag)}")
        if len(generated) >= max_queries:
            break

    for tag in secondary_tags:
        if len(generated) >= max_queries:
            break
        generated.append(query_token(tag))

    return normalize_query_list(generated[:max_queries]) or list(DEFAULT_QUERIES)


INVISIBLE_CHAR_PATTERN = re.compile(r"[\u200b\u200c\u200d\u2060\uFEFF\u034F]")


def strip_html_tags(text: str) -> str:
    return normalize_text(HTML_TAG_PATTERN.sub(" ", text))


def clean_resume_text(text: str) -> str:
    return INVISIBLE_CHAR_PATTERN.sub("", text or "").strip()


def extract_markdown_section(text: str, header: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(header)}\s*$", re.M | re.I)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = MARKDOWN_HEADER_PATTERN.search(text[start:])
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


def slugify_filename(value: str, max_length: int = 80) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", normalize_text(value).lower()).strip("_")
    return slug[:max_length].strip("_") or "resume"


def parse_resume_source_sections(text: str) -> dict[str, Any]:
    cleaned = clean_resume_text(text)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    non_empty_lines = [line for line in lines if normalize_text(line)]
    name = strip_html_tags(non_empty_lines[0]) if non_empty_lines else "Sample Candidate"
    contact = strip_html_tags(non_empty_lines[1]) if len(non_empty_lines) > 1 else ""
    contact = re.sub(r"\bMale\b\s*\|\s*Birth:[^|]+\|\s*", "", contact, flags=re.I)
    contact = re.sub(r"^\s*📧\s*", "", contact)
    contact = normalize_text(contact.strip(" |"))
    if not contact and len(non_empty_lines) > 1:
        contact = strip_html_tags(non_empty_lines[1])

    skills_section = extract_markdown_section(cleaned, "Skills")
    skills: list[str] = []
    for line in skills_section.splitlines():
        normalized = normalize_text(re.sub(r"^\s*[-*]\s*", "", line))
        if normalized:
            skills.append(strip_html_tags(normalized))

    role_pattern = re.compile(
        r"<big>\*\*\[(?P<company>[^\]]+)\]\([^)]+\)\*\*\s*\*(?P<title>[^*]+)\*</big>\s*"
        r"<p align=\"right\"><small>(?P<dates>[^<]+)</small>(?:</p>|<p>)\s*"
        r"(?P<body>.*?)(?=\n<big>|\n## Education|\Z)",
        re.S,
    )
    roles: list[dict[str, str]] = []
    for match in role_pattern.finditer(cleaned):
        body = normalize_text(strip_html_tags(match.group("body")))
        roles.append(
            {
                "company": normalize_text(match.group("company")),
                "title": normalize_text(match.group("title")),
                "dates": normalize_text(match.group("dates")),
                "body": body,
            }
        )

    education_section = extract_markdown_section(cleaned, "Education")
    education_lines = [
        strip_html_tags(normalize_text(line))
        for line in education_section.splitlines()
        if normalize_text(line)
    ]

    return {
        "name": name,
        "contact": contact,
        "skills": skills,
        "roles": roles,
        "education_lines": education_lines,
        "raw_text": cleaned,
    }


def simplify_skill_phrase(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"^(proficient in|experience in|experience with|familiar with|skills?:)\s*", "", text, flags=re.I)
    text = re.sub(r"^(develope? on|developed on|developing on)\s*", "", text, flags=re.I)
    return normalize_text(text)


def normalize_list_items(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = simplify_skill_phrase(strip_html_tags(str(value)))
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def find_role_suggestion(
    role_suggestions: list[dict[str, Any]],
    company: str,
    title: str,
) -> dict[str, Any] | None:
    for role in role_suggestions:
        if normalize_text(role.get("company", "")).lower() == normalize_text(company).lower():
            return role
    for role in role_suggestions:
        if normalize_text(role.get("title", "")).lower() == normalize_text(title).lower():
            return role
    return None


def split_into_sentences(text: str) -> list[str]:
    return [
        normalize_text(part)
        for part in re.split(r"(?<=[.!?。；;])\s+|\n+", text)
        if normalize_text(part)
    ]


def fit_status_from_level(level: float) -> tuple[str, str]:
    if level >= 0.75:
        return "已具备", "match"
    if level >= 0.45:
        return "部分具备", "partial"
    if level > 0.0:
        return "证据较弱", "weak"
    return "未体现", "missing"


def translate_recommendation_zh(recommendation: str) -> str:
    mapping = {
        "Strong fit": "强匹配，建议优先投递",
        "Good fit": "较匹配，定制后可投",
        "Stretch": "冲刺岗，选择性投递",
        "Skip": "暂不建议投入时间",
    }
    return mapping.get(recommendation, recommendation or "待判断")


def recommendation_short_zh(recommendation: str) -> str:
    mapping = {
        "Strong fit": "强匹配",
        "Good fit": "较匹配",
        "Stretch": "冲刺岗",
        "Skip": "暂不建议",
    }
    return mapping.get(recommendation, "待判断")


def translate_language_requirement_zh(value: str) -> str:
    mapping = {
        "German B2+ required or strongly preferred": "德语 B2+ 明确要求或强偏好",
        "German mentioned": "提到德语要求",
        "English mentioned": "提到英语环境",
        "Not stated": "未明确说明",
    }
    return mapping.get(value, value or "未明确说明")


def translate_flag_zh(flag: str) -> str:
    return FLAG_TRANSLATIONS_ZH.get(flag, flag)


def resume_evidence_for_feature_zh(feature_key: str) -> str:
    mapping = {
        "c++": "简历主线就是 8+ 年 C++17/20 开发。",
        "java": "简历主线是 Java / Spring Boot 后端服务开发。",
        "spring": "简历明确覆盖 Spring Boot、Spring Security 与后端服务交付。",
        "kafka": "简历有 Kafka 异步消息、下游通知解耦与 Transactional Outbox 项目证据。",
        "mysql": "简历有 MySQL / MyBatis 生产系统数据访问经验。",
        "postgresql": "示例后端项目使用 PostgreSQL，可作为现代后端项目证据。",
        "redis": "简历写明使用 Redis 做缓存与数据库负载降低。",
        "elasticsearch": "简历写明使用 Elasticsearch 支撑检索场景。",
        "kubernetes": "简历有 Kubernetes 发布、金丝雀发布和生产运维接触。",
        "docker": "简历有 Docker / 容器化工具链接触。",
        "rest": "简历主线覆盖 RESTful API 与核心业务接口。",
        "rpc": "简历有 Dubbo RPC 后端服务集成经验。",
        "testing": "示例后端项目写明 JUnit 5、Testcontainers 与 CI 流程。",
        "windows": "拼多多商家工作台和风控采集工具都在 Windows 侧落地。",
        "linux": "技能区已写 Linux 开发，但项目级证据还偏少。",
        "qt": "技能区已补充 Qt/QML，当前强度更接近入门到过渡阶段。",
        "qml": "QML 已写进技能区，但还缺直接项目成果描述。",
        "python": "技能区明确写了 Python，可以作为辅助能力补充。",
        "cmake": "简历里还没有 CMake 的直接证据。",
        "sdk": "你有 SDK / 驱动级集成和终端侧采集相关经验。",
        "driver": "你的经历里有 driver-level integration exposure，可对齐底层集成诉求。",
        "desktop": "两段核心经历都属于 PC / 桌面客户端方向。",
        "client": "连续多年在客户端场景负责功能开发、维护和排障。",
        "tooling": "你做过生产工具、数据采集工具和故障定位相关工作。",
        "system": "安全风控数据采集工具可作为系统级 / 终端侧证据。",
    }
    return mapping.get(feature_key, "简历中有一定相关性，但证据还需要按岗位进一步重写。")


def strength_pitch_for_feature_zh(feature_key: str) -> str:
    mapping = {
        "c++": "把 `8+ 年 C++17/20` 放在摘要第一句，先让招聘方确认你是主语言深耕型候选人。",
        "java": "把 `Java / Spring Boot backend` 与物流履约生产系统放在摘要第一句，避免被看成泛 Java 候选人。",
        "spring": "把 Spring Boot 写成核心生产服务交付经验，并和 REST/RPC/API 边界绑定。",
        "kafka": "强调 Kafka、异步通知、Transactional Outbox 与一致性/可靠性，而不只是写会用消息队列。",
        "mysql": "把 MySQL/MyBatis/事务/索引/负载降低写成数据层可靠性经验。",
        "redis": "把 Redis 放到缓存、吞吐、数据库减压和高峰流量支撑语境里。",
        "kubernetes": "把 Kubernetes 经验写成发布、金丝雀、生产排障支持，而不是只放在技能列表。",
        "windows": "把 Windows 客户端和终端侧工具开发前置，直接对齐 JD 的平台要求。",
        "linux": "如果 JD 要 Linux，把你已有 Linux 开发经历从技能区提到项目经历里。",
        "qt": "JD 强调 Qt 时，把 Qt/QML 放到摘要和技能首屏，并补一条实际界面/模块证据。",
        "qml": "QML 不是你的主卖点，但可以作为转型信号出现，不要写得过重。",
        "sdk": "系统集成类岗位里，优先强调 SDK / driver / data collection 这条经验线。",
        "desktop": "桌面 GUI / PC 客户端岗位里，拼多多和 Hikrobot 的 PC 产品经验是最强卖点。",
        "tooling": "把 troubleshooting、问题定位和工具链建设写成结果导向的 bullet。",
        "system": "系统级岗位里，要突出终端信息采集、稳定性和跨模块排障经验。",
    }
    return mapping.get(feature_key, f"把 {FEATURE_LABELS_ZH.get(feature_key, feature_key)} 相关经历写成更具体的项目证据。")


def gap_advice_for_feature_zh(feature_key: str) -> str:
    mapping = {
        "java": "如果岗位以 Java 为主，应把 Java/Spring Boot、接口吞吐和生产排障证据放到简历前半页。",
        "spring": "补充具体 Spring Boot 服务边界、认证/权限、测试和部署方式。",
        "kafka": "补充消息语义、失败恢复、幂等、延迟消息或 Outbox 的项目级证据。",
        "postgresql": "如果岗位要求 PostgreSQL，把后端示例项目放到项目区并说明数据模型和测试方式。",
        "testing": "补充 JUnit/Testcontainers/CI 的运行方式，证明项目不是只写业务代码。",
        "linux": "把 Linux 从“会用”改成“做过什么”，至少补一条构建、调试或部署证据。",
        "qt": "如果你决定投 Qt 岗，不能只写技能标签，必须补界面、信号槽、线程或模块实现细节。",
        "qml": "QML 当前更像补充项，不建议在简历里夸大；最好补一个真实 UI/交互例子。",
        "cmake": "德国 Qt / Linux 岗常看 CMake，建议尽快补一条实际使用证据。",
        "driver": "如果 JD 明写底层驱动/OS internals，需要把你做过的 driver / SDK 集成写得更具体。",
        "python": "Python 作为辅助能力问题不大，但不应抢走 C++ 主叙事。",
    }
    return mapping.get(feature_key, f"补强 {FEATURE_LABELS_ZH.get(feature_key, feature_key)} 的项目级证据。")


def parse_posted_age_days(posted_text: str) -> float | None:
    text = (posted_text or "").lower()
    if not text:
        return None
    match = re.search(r"(\d+)\s*(minute|minutes|hour|hours|hr|hrs|day|days|week|weeks)", text)
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        if unit.startswith("minute"):
            return value / 1440.0
        if unit.startswith("hour") or unit.startswith("hr"):
            return value / 24.0
        if unit.startswith("day"):
            return value
        if unit.startswith("week"):
            return value * 7.0
    match = re.search(r"vor\s+(\d+)\s*(min|minute|minuten|stunde|stunden|tag|tagen|tage|woche|wochen)", text)
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        if unit.startswith("min"):
            return value / 1440.0
        if unit.startswith("stunde"):
            return value / 24.0
        if unit.startswith("tag"):
            return value
        if unit.startswith("woche"):
            return value * 7.0
    match = re.search(r"(\d+)\s*(分钟|小时|天|周)前", posted_text or "")
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        if unit == "分钟":
            return value / 1440.0
        if unit == "小时":
            return value / 24.0
        if unit == "天":
            return value
        if unit == "周":
            return value * 7.0
    return None


def detect_years_requirement(text: str) -> int | None:
    values = []
    for match in re.finditer(r"\b([1-9]|1[0-5])\+?\s*(?:\+|plus)?\s*(?:years?|yrs?)\b", text, re.I):
        values.append(int(match.group(1)))
    return max(values) if values else None


def summarize_requirement_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"total": len(items), "match": 0, "partial": 0, "weak": 0, "missing": 0}
    for item in items:
        status_key = item.get("status_key", "missing")
        summary[status_key] = summary.get(status_key, 0) + 1
    return summary


def build_requirement_status_line(summary: dict[str, int]) -> str:
    if summary.get("total", 0) == 0:
        return "未从 JD 中稳定提取到该类要求。"
    return (
        f"共 {summary['total']} 项，其中已具备 {summary['match']} 项，"
        f"部分具备 {summary['partial']} 项，证据较弱 {summary['weak']} 项，"
        f"未体现 {summary['missing']} 项。"
    )


def extract_job_requirements_zh(job: dict[str, Any], resume: ResumeProfile) -> dict[str, Any]:
    title = job.get("title", "")
    description = job.get("description", "")
    text = normalize_text(" ".join([title, description, job.get("company", "")]))
    sentences = split_into_sentences(description)

    required_items: list[dict[str, Any]] = []
    preferred_items: list[dict[str, Any]] = []
    soft_items: list[dict[str, Any]] = []

    detected_tech = detect_features(text, TECH_FEATURES)
    for key, detected in detected_tech.items():
        if not detected:
            continue
        matching_sentences = [
            sentence
            for sentence in sentences
            if any(pattern.search(sentence) for pattern in TECH_FEATURES[key].get("_compiled_patterns", []))
        ]
        sentence_blob = " ".join(matching_sentences).lower()
        title_hit = any(pattern.search(title) for pattern in TECH_FEATURES[key].get("_compiled_patterns", []))
        is_preferred = any(marker in sentence_blob for marker in PREFERRED_MARKERS)
        is_required = key in {"c++", "java"} or title_hit or any(marker in sentence_blob for marker in REQUIRED_MARKERS)
        bucket = preferred_items if is_preferred and not is_required else required_items
        level = resume.skill_levels.get(key, 0.0)
        status_zh, status_key = fit_status_from_level(level)
        bucket.append(
            {
                "key": key,
                "label": FEATURE_LABELS_ZH.get(key, TECH_FEATURES[key]["label"]),
                "status_zh": status_zh,
                "status_key": status_key,
                "evidence_zh": resume_evidence_for_feature_zh(key),
                "importance": TECH_FEATURES[key]["weight"],
            }
        )

    years_required = detect_years_requirement(text)
    if years_required is not None:
        years_ok = resume.years_experience >= years_required
        required_items.append(
            {
                "key": "years_experience",
                "label": f"{years_required}+ 年相关经验",
                "status_zh": "已具备" if years_ok else "证据较弱",
                "status_key": "match" if years_ok else "weak",
                "evidence_zh": f"你的简历按时间跨度约 {resume.years_experience:.1f} 年。",
                "importance": 9.0,
            }
        )

    language_requirement = job.get("language_requirement", "Not stated")
    if language_requirement != "Not stated":
        if language_requirement == "German B2+ required or strongly preferred":
            status_key = "missing"
            status_zh = "当前不满足"
        elif language_requirement == "German mentioned":
            status_key = "weak"
            status_zh = "存在风险"
        else:
            status_key = "match"
            status_zh = "基本可接受"
        required_items.append(
            {
                "key": "language",
                "label": "语言要求",
                "status_zh": status_zh,
                "status_key": status_key,
                "evidence_zh": f"JD 语言要求：{translate_language_requirement_zh(language_requirement)}；你的英语证据：{resume.english_level}。",
                "importance": 8.0,
            }
        )

    detected_soft = detect_features(text, SOFT_SKILL_RULES)
    for key, detected in detected_soft.items():
        if not detected:
            continue
        if key == "communication":
            level = 0.8
            evidence = "你有长期跨业务沟通、线上问题排查和维护协作背景。"
        elif key == "problem_solving":
            level = 0.9
            evidence = "你在简历里明确写了 troubleshooting 和 problem solving。"
        elif key == "ownership":
            level = 0.75
            evidence = "你持续负责核心模块维护与替换，具备 owner 倾向。"
        else:
            level = 0.65
            evidence = "从长期客户端开发经历看，这项能力具备一定支撑。"
        status_zh, status_key = fit_status_from_level(level)
        soft_items.append(
            {
                "key": key,
                "label": SOFT_SKILL_RULES[key]["label_zh"],
                "status_zh": status_zh,
                "status_key": status_key,
                "evidence_zh": evidence,
            }
        )

    required_items.sort(key=lambda item: item.get("importance", 0.0), reverse=True)
    preferred_items.sort(key=lambda item: item.get("importance", 0.0), reverse=True)
    return {
        "required": required_items[:8],
        "preferred": preferred_items[:6],
        "soft_skills": soft_items[:4],
        "required_summary": summarize_requirement_counts(required_items),
        "preferred_summary": summarize_requirement_counts(preferred_items),
        "soft_summary": summarize_requirement_counts(soft_items),
    }


def detect_red_flags_zh(job: dict[str, Any]) -> list[str]:
    text = normalize_text(" ".join([job.get("title", ""), job.get("description", ""), job.get("company", "")]))
    flags = [translate_flag_zh(flag) for flag in job.get("flags", []) if translate_flag_zh(flag)]
    for rule in RED_FLAG_RULES:
        if any(pattern.search(text) for pattern in rule.get("_compiled_patterns", [])):
            flags.append(rule["label_zh"])
    return sorted(set(flags))[:5]


def detect_culture_signals_zh(job: dict[str, Any]) -> list[str]:
    text = normalize_text(" ".join([job.get("title", ""), job.get("description", ""), job.get("company", "")]))
    signals = []
    for rule in CULTURE_SIGNAL_RULES:
        if any(pattern.search(text) for pattern in rule.get("_compiled_patterns", [])):
            signals.append(rule["label_zh"])
    return signals[:4]


def build_strengths_to_emphasize_zh(job: dict[str, Any], requirements: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for item in requirements["required"]:
        if item["status_key"] == "match" and item["key"] in TECH_FEATURES:
            selected.append(strength_pitch_for_feature_zh(item["key"]))
    if "Enterprise Platforms" in job.get("strengths", []) or "enterprise" in job.get("query_sources", []):
        selected.append("强调你做过面向大量商家终端的企业级产品，而不是单点 Demo。")
    if not selected:
        selected.append("先把拼多多 Windows 商家工作台和风控采集工具写到摘要前两句，建立主叙事。")
    return selected[:3]


def build_gap_buckets_zh(job: dict[str, Any], requirements: dict[str, Any]) -> dict[str, list[str]]:
    critical: list[str] = []
    major: list[str] = []
    minor: list[str] = []

    for item in requirements["required"]:
        key = item["key"]
        if item["status_key"] == "missing":
            if key in {"language", "c++"}:
                critical.append(f"{item['label']} 当前属于硬风险：{item['evidence_zh']}")
            else:
                major.append(f"{item['label']} 证据不足：{gap_advice_for_feature_zh(key)}")
        elif item["status_key"] == "weak":
            major.append(f"{item['label']} 需要补强：{item['evidence_zh']}")
        elif item["status_key"] == "partial":
            minor.append(f"{item['label']} 可通过改写简历强化：{item['evidence_zh']}")

    for item in requirements["preferred"]:
        if item["status_key"] in {"missing", "weak"}:
            minor.append(f"{item['label']} 属于加分项，可后置处理。")

    for flag in job.get("flags", []):
        if flag == "Hardware-design-heavy role":
            major.append("岗位重心偏硬件设计，和你当前 PC / 客户端主线不完全一致。")
        elif flag == "Student / internship role":
            critical.append("这是学生岗或实习岗，不适合作为当前主攻方向。")

    return {
        "critical": critical[:3],
        "major": major[:4],
        "minor": minor[:4],
    }


def build_tailoring_strategy_zh(job: dict[str, Any], requirements: dict[str, Any], gaps: dict[str, list[str]]) -> list[str]:
    actions = [
        "摘要第一句先讲 `8+ 年 C++ 桌面 / 客户端开发`，不要让 Qt/QML 抢主叙事。",
    ]
    keys = {item["key"] for item in requirements["required"] if item["key"] in TECH_FEATURES}
    if {"windows", "desktop", "client"} & keys:
        actions.append("把拼多多 Windows 商家工作台、Hikrobot PC 客户端写成首屏案例，突出桌面端闭环经验。")
    if {"sdk", "driver", "system"} & keys:
        actions.append("把风控数据采集工具、SDK / driver 集成、终端侧排障写成单独卖点。")
    if {"qt", "qml"} & keys:
        actions.append("如果投 Qt/QML 岗，必须在项目经历里补至少一条真实模块或界面实现细节。")
    if "language" in {item["key"] for item in requirements["required"]} and job.get("language_requirement") != "English mentioned":
        actions.append("语言不占优时，不要硬碰德语岗位；用英文环境协作和问题定位能力兜底。")
    if gaps["major"] or gaps["critical"]:
        actions.append("把主要缺口放在 Cover Letter 或投递备注里解释迁移路径，不要在简历里硬装已经熟练。")
    return actions[:4]


def build_cover_letter_points_zh(job: dict[str, Any], requirements: dict[str, Any]) -> list[str]:
    points = [
        "我长期在 Windows PC / 客户端场景用 C++ 负责核心功能、维护与线上问题排查，能较快进入角色。",
    ]
    keys = {item["key"] for item in requirements["required"] if item["status_key"] in {"match", "partial"}}
    if {"sdk", "driver", "system"} & keys:
        points.append("我做过终端侧数据采集、SDK / driver 集成和复杂问题定位，对系统相关岗位迁移成本较低。")
    if {"qt", "qml", "linux"} & keys:
        points.append("我已经在补强 Qt/QML 与 Linux 方向，愿意把现有客户端经验迁移到更偏跨平台的工程环境。")
    points.append("我更适合需要扎实 C++ 基础、桌面端经验和工程稳定性意识的岗位。")
    return points[:3]


def estimate_tailoring_time_zh(gaps: dict[str, list[str]]) -> str:
    if gaps["critical"]:
        return "60-90 分钟，且需要先判断是否值得投"
    if len(gaps["major"]) >= 3:
        return "45-60 分钟"
    if len(gaps["major"]) >= 1 or len(gaps["minor"]) >= 2:
        return "30-45 分钟"
    return "20-30 分钟"


def application_priority_zh(score: float, posted_text: str) -> str:
    age_days = parse_posted_age_days(posted_text)
    fresh_bonus = age_days is not None and age_days <= 2
    if score >= 80:
        return "高优先级" if fresh_bonus else "较高优先级"
    if score >= 65:
        return "中高优先级" if fresh_bonus else "中优先级"
    if score >= 50:
        return "低到中优先级"
    return "低优先级"


def build_overall_verdict_zh(job: dict[str, Any], requirements: dict[str, Any], gaps: dict[str, list[str]]) -> str:
    recommendation = job.get("recommendation", "")
    score = float(job.get("score", 0.0))
    matched_required = requirements["required_summary"].get("match", 0)
    total_required = requirements["required_summary"].get("total", 0)
    base = {
        "Strong fit": "这份岗位与你当前履历主线高度贴合，值得优先投递。",
        "Good fit": "这份岗位和你的经验有明显交集，经过定制后值得投递。",
        "Stretch": "这份岗位存在一定迁移空间，但需要你主动解释可转移能力。",
        "Skip": "这份岗位与当前履历的硬匹配度偏低，投入产出比不高。",
    }.get(recommendation, "这份岗位需要结合 JD 细节再做判断。")
    detail = f"当前必备项命中 {matched_required}/{total_required}，综合分 {score:.1f}。"
    if gaps["critical"]:
        detail += " 但存在硬风险，需要先确认是否接受。"
    elif gaps["major"]:
        detail += " 主要问题集中在简历证据还不够具体。"
    return base + " " + detail


def build_job_analysis_zh(job: dict[str, Any], resume: ResumeProfile) -> dict[str, Any]:
    requirements = extract_job_requirements_zh(job, resume)
    gaps = build_gap_buckets_zh(job, requirements)
    red_flags = detect_red_flags_zh(job)
    culture_signals = detect_culture_signals_zh(job)
    overall_verdict = build_overall_verdict_zh(job, requirements, gaps)
    strengths = build_strengths_to_emphasize_zh(job, requirements)
    strategy = build_tailoring_strategy_zh(job, requirements, gaps)
    cover_letter_points = build_cover_letter_points_zh(job, requirements)
    return {
        "fit_label_zh": recommendation_short_zh(job.get("recommendation", "")),
        "recommendation_zh": translate_recommendation_zh(job.get("recommendation", "")),
        "application_priority_zh": application_priority_zh(float(job.get("score", 0.0)), job.get("posted_text", "")),
        "estimated_tailoring_time_zh": estimate_tailoring_time_zh(gaps),
        "overall_verdict_zh": overall_verdict,
        "language_requirement_zh": translate_language_requirement_zh(job.get("language_requirement", "Not stated")),
        "requirement_breakdown": requirements,
        "requirements_overview_zh": {
            "required": build_requirement_status_line(requirements["required_summary"]),
            "preferred": build_requirement_status_line(requirements["preferred_summary"]),
            "soft_skills": build_requirement_status_line(requirements["soft_summary"]),
        },
        "strengths_to_emphasize_zh": strengths,
        "gaps_zh": gaps,
        "red_flags_zh": red_flags,
        "tailoring_strategy_zh": strategy,
        "cover_letter_points_zh": cover_letter_points,
        "culture_signals_zh": culture_signals,
    }


def build_resume_tailoring_cluster_metrics(
    top_jobs: list[dict[str, Any]],
    resume: ResumeProfile,
) -> dict[str, Any]:
    if not top_jobs:
        return {
            "cluster_score": 0.0,
            "keyword_focus_en": [],
            "keyword_focus_zh": [],
            "missing_keywords_en": [],
            "missing_keywords_zh": [],
            "formatting_checks_en": [],
            "formatting_checks_zh": [],
        }

    detected_keys: list[str] = []
    for job in top_jobs:
        text = normalize_text(" ".join([job.get("title", ""), job.get("description", ""), job.get("company", "")]))
        for key, hit in detect_features(text, TECH_FEATURES).items():
            if hit and key not in detected_keys:
                detected_keys.append(key)
        for key, hit in detect_features(text, DOMAIN_FEATURES).items():
            if hit and key not in detected_keys:
                detected_keys.append(key)

    keyword_focus_en: list[str] = []
    keyword_focus_zh: list[str] = []
    missing_keywords_en: list[str] = []
    missing_keywords_zh: list[str] = []

    for key in detected_keys:
        label_en = TECH_FEATURES.get(key, DOMAIN_FEATURES.get(key, {})).get("label") or key
        label_zh = FEATURE_LABELS_ZH.get(key) or DOMAIN_LABELS_ZH.get(key) or label_en
        keyword_focus_en.append(label_en)
        keyword_focus_zh.append(label_zh)
        level = resume.skill_levels.get(key, resume.domain_levels.get(key, 0.0))
        if level < 0.45:
            missing_keywords_en.append(label_en)
            missing_keywords_zh.append(label_zh)

    formatting_checks_en: list[str] = []
    formatting_checks_zh: list[str] = []
    source_text = resume.text
    if "<center>" in source_text or "<big>" in source_text:
        formatting_checks_en.append("Remove HTML tags and centering before exporting to PDF.")
        formatting_checks_zh.append("导出前应移除 HTML 标签和居中排版，改成纯文本 / 单栏 ATS 版式。")
    if "## Skills" in source_text:
        formatting_checks_en.append("The resume already has a standard Skills section.")
        formatting_checks_zh.append("已有标准 Skills 章节，可继续保留。")
    if "## Employment History" in source_text:
        formatting_checks_en.append("Experience section is present, but paragraph-based content should be converted into bullets.")
        formatting_checks_zh.append("已有 Experience 章节，但当前还是段落式描述，建议改成 bullet 形式。")
    if "## Education" in source_text:
        formatting_checks_en.append("Education section is present.")
        formatting_checks_zh.append("已有 Education 章节。")
    if "LinkedIn" not in source_text and "github" not in source_text.lower():
        formatting_checks_en.append("Add LinkedIn and GitHub/portfolio links if available.")
        formatting_checks_zh.append("如果有 LinkedIn / GitHub / Portfolio，建议补到页首，增强技术岗位可信度。")

    cluster_score = clamp_score(sum(float(job.get("score", job.get("base_score", 0.0))) for job in top_jobs) / len(top_jobs))
    return {
        "cluster_score": cluster_score,
        "keyword_focus_en": keyword_focus_en,
        "keyword_focus_zh": keyword_focus_zh,
        "missing_keywords_en": missing_keywords_en,
        "missing_keywords_zh": missing_keywords_zh,
        "formatting_checks_en": formatting_checks_en,
        "formatting_checks_zh": formatting_checks_zh,
    }


def build_keyword_summary_for_job(job: dict[str, Any], resume: ResumeProfile) -> dict[str, list[str]]:
    text = normalize_text(" ".join([job.get("title", ""), job.get("description", ""), job.get("company", "")]))
    detected_keys: list[str] = []
    for feature_map in (TECH_FEATURES, DOMAIN_FEATURES):
        for key, meta in feature_map.items():
            if any(pattern.search(text) for pattern in meta.get("_compiled_patterns", [])):
                if key not in detected_keys:
                    detected_keys.append(key)

    focus_en: list[str] = []
    focus_zh: list[str] = []
    missing_en: list[str] = []
    missing_zh: list[str] = []
    for key in detected_keys:
        feature = TECH_FEATURES.get(key) or DOMAIN_FEATURES.get(key) or {}
        label_en = feature.get("label") or key
        label_zh = FEATURE_LABELS_ZH.get(key) or DOMAIN_LABELS_ZH.get(key) or label_en
        focus_en.append(label_en)
        focus_zh.append(label_zh)
        level = resume.skill_levels.get(key, resume.domain_levels.get(key, 0.0))
        if level < 0.45:
            missing_en.append(label_en)
            missing_zh.append(label_zh)

    return {
        "focus_en": normalize_list_items(focus_en),
        "focus_zh": normalize_list_items(focus_zh),
        "missing_en": normalize_list_items(missing_en),
        "missing_zh": normalize_list_items(missing_zh),
    }


def build_resume_role_suggestions(
    resume_source: dict[str, Any],
    version: dict[str, Any],
    target_job: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    keyword_focus = " ".join(normalize_list_items(version.get("keyword_focus_en", []))).lower()
    target_keywords = normalize_list_items((target_job or {}).get("detected_keywords", []))
    keyword_focus += " " + " ".join(item.lower() for item in target_keywords)

    suggestions: list[dict[str, Any]] = []
    for role in resume_source.get("roles", []):
        company = normalize_text(role.get("company", ""))
        title = normalize_text(role.get("title", ""))
        dates = normalize_text(role.get("dates", ""))
        body = normalize_text(role.get("body", ""))

        if company.lower() == "pinduoduo":
            bullets_en = [
                "Developed and maintained the Windows-based IM chat tool for merchants and consumers using C++17/20 and duilib.",
                "Owned daily troubleshooting and problem solving for IM and business functions across the merchant workspace.",
                "Led the replacement of the client's main data parsing library and supported Windows-side security and risk-control data collection tools.",
            ]
            bullets_zh = [
                "使用 C++17/20 和 duilib 开发并维护面向商家和消费者的 Windows 端 IM Chat Tool。",
                "负责 IM 与业务功能的日常排障和问题定位，保障商家工作台稳定运行。",
                "主导客户端主数据解析库替换，并支持 Windows 端安全与风控数据采集工具。",
            ]
            notes_en = [
                "Lead with the Windows client and C++ stack because that is the strongest ATS signal.",
                "Keep troubleshooting and library replacement close to the top of the role section.",
            ]
            notes_zh = [
                "这段经历最强的 ATS 信号是 Windows 客户端和 C++ 主线，建议放在最前面。",
                "把排障、库替换和风控采集工具放在同一段，形成完整的客户端交付叙事。",
            ]
            prompts_en = [
                "How many merchant devices or active users did this client support?",
                "How many troubleshooting tickets or incidents did you handle in a typical week?",
                "What measurable improvement came from replacing the data parsing library?",
            ]
            prompts_zh = [
                "这套客户端支撑了多少商家设备或活跃用户？",
                "你平均每周处理多少个排障工单或线上问题？",
                "替换数据解析库后，具体带来了什么可量化的改进？",
            ]
        elif company.lower() == "hikrobot":
            bullets_en = [
                "Built the client side of the UAV monitoring platform from 0 to 1 using C++ and MFC.",
                "Designed and implemented functional modules, UI optimization, and debugging for the desktop client.",
                "Contributed to production tooling development for the PC software stack.",
            ]
            bullets_zh = [
                "使用 C++ 和 MFC 从 0 到 1 开发无人机监控平台客户端。",
                "负责桌面客户端的功能模块设计、界面优化和调试。",
                "参与生产工具开发，服务于 PC 软件栈。",
            ]
            notes_en = [
                "Keep the from-scratch client build as the leading bullet.",
                "Highlight module design and UI/debugging because they map well to desktop engineering roles.",
            ]
            notes_zh = [
                "从 0 到 1 的客户端开发是最强证据，建议放在第一条。",
                "模块设计、界面优化和调试要保留，因为它们和桌面工程岗高度相关。",
            ]
            prompts_en = [
                "How many modules or screens did you own in the UAV monitoring client?",
                "Did the tooling or client reduce debugging time, setup time, or support effort?",
                "What scale did the platform support (devices, operators, deployments)?",
            ]
            prompts_zh = [
                "无人机监控客户端里你具体负责了多少个模块或界面？",
                "这些工具或客户端是否减少了调试时间、部署时间或支持成本？",
                "平台大概支撑了多少设备、操作员或部署场景？",
            ]
        else:
            sentences = [sentence for sentence in split_into_sentences(body) if sentence]
            bullets_en = sentences[:3] or [body]
            bullets_zh = sentences[:3] or [body]
            notes_en = [
                "Rewrite the most relevant sentence as a verb-led bullet.",
                "Add a number if you can recover a believable metric.",
            ]
            notes_zh = [
                "把最相关的句子改成动词开头的 bullet。",
                "如果能回忆起可信数字，优先补到这一段里。",
            ]
            prompts_en = ["Which number best captures the scale of this work?"]
            prompts_zh = ["这段经历最能量化的指标是什么？"]

        if "qt" in keyword_focus or "qml" in keyword_focus:
            notes_en.append("For Qt/QML jobs, surface any UI, signal-slot, or module boundary work in this role.")
            notes_zh.append("如果投 Qt/QML 岗，把 UI、信号槽或模块边界经验往前提。")
        if "linux" in keyword_focus:
            notes_en.append("If Linux is a target keyword, keep the Linux evidence visible but do not overstate depth.")
            notes_zh.append("如果岗位强调 Linux，把已有 Linux 证据前置，但不要夸大深度。")
        if target_job and target_job.get("jd_analysis_zh", {}).get("language_requirement_zh", "").startswith("德语"):
            notes_en.append("German remains a risk note; do not claim language ability you do not have.")
            notes_zh.append("如果岗位硬性要求德语，不要在简历里虚构语言能力。")

        suggestions.append(
            {
                "company": company,
                "title": title,
                "dates": dates,
                "source_context_en": body[:260],
                "source_context_zh": notes_zh[0] if notes_zh else body[:120],
                "suggested_bullets_en": normalize_list_items(bullets_en)[:4],
                "suggested_bullets_zh": normalize_list_items(bullets_zh)[:4],
                "bullet_notes_en": normalize_list_items(notes_en)[:4],
                "bullet_notes_zh": normalize_list_items(notes_zh)[:4],
                "quantification_prompts_en": normalize_list_items(prompts_en)[:4],
                "quantification_prompts_zh": normalize_list_items(prompts_zh)[:4],
            }
        )

    return suggestions


def build_tailoring_recommendations_en(
    job: dict[str, Any],
    job_analysis: dict[str, Any],
    keyword_focus_en: list[str],
) -> list[str]:
    recommendations = [
        "Lead with the strongest Windows/C++ client story and keep the summary tight.",
        "Mirror the JD keywords only where they are truthfully supported by the source resume.",
    ]
    focus_blob = " ".join(keyword_focus_en).lower()
    if "qt" in focus_blob or "qml" in focus_blob:
        recommendations.append("Surface Qt/QML only if the role actually asks for it; keep it secondary otherwise.")
    if "linux" in focus_blob:
        recommendations.append("Keep Linux evidence visible but do not overstate depth beyond the source resume.")
    if job_analysis.get("gaps_zh", {}).get("critical"):
        recommendations.append("Explain the main gap in the cover letter instead of stretching the resume.")
    if job.get("recommendation") == "Strong fit":
        recommendations.append("This role looks like a strong fit, so prioritize direct evidence over heavy rewriting.")
    return normalize_list_items(recommendations)[:4]


def build_fallback_master_tailoring_version(
    resume_source: dict[str, Any],
    cluster_metrics: dict[str, Any],
    top_jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    keyword_focus_en = normalize_list_items(cluster_metrics.get("keyword_focus_en", []))[:10]
    keyword_focus_zh = normalize_list_items(cluster_metrics.get("keyword_focus_zh", []))[:10]
    missing_en = normalize_list_items(cluster_metrics.get("missing_keywords_en", []))[:10]
    missing_zh = normalize_list_items(cluster_metrics.get("missing_keywords_zh", []))[:10]
    formatting_en = normalize_list_items(cluster_metrics.get("formatting_checks_en", []))[:8]
    formatting_zh = normalize_list_items(cluster_metrics.get("formatting_checks_zh", []))[:8]
    skills = normalize_list_items(resume_source.get("skills", []))
    skill_order_en = normalize_list_items(keyword_focus_en + skills)[:12]
    if not skill_order_en:
        skill_order_en = skills[:12]
    skill_order_zh = normalize_list_items(keyword_focus_zh + keyword_focus_zh)[:12]

    top_job = top_jobs[0] if top_jobs else {}
    top_job_title = normalize_text(top_job.get("title", "Top matching role"))
    top_job_company = normalize_text(top_job.get("company", "target company"))
    top_focus = ", ".join(keyword_focus_en[:3]) or "C++ and desktop client engineering"

    return {
        "version_name_en": "ATS Baseline Resume",
        "version_name_zh": "ATS 基础版简历",
        "summary_en": (
            f"ATS-focused baseline tailored for German C++ software roles. "
            f"Lead with verifiable impact in {top_focus}, and keep claims strictly grounded in the source resume."
        ),
        "summary_zh": (
            f"规则化简历定制包围绕 {top_job_title} @ {top_job_company} 的核心关键词组织简历，"
            "确保所有表述都可被现有经历直接证明。"
        ),
        "keyword_focus_en": keyword_focus_en,
        "keyword_focus_zh": keyword_focus_zh,
        "missing_keywords_en": missing_en,
        "missing_keywords_zh": missing_zh,
        "formatting_notes_en": formatting_en,
        "formatting_notes_zh": formatting_zh,
        "skill_order_en": skill_order_en,
        "skill_order_zh": skill_order_zh,
        "role_suggestions": [],
        "tailoring_recommendations_en": [
            "Lead with the strongest Windows/C++ client and troubleshooting outcomes.",
            "Mirror top JD keywords only where evidence exists in the source resume.",
            "Keep Linux/Qt as growth areas unless the role explicitly demands depth.",
            "Use bullet points with concrete scope or impact where metrics are known.",
        ],
        "tailoring_recommendations_zh": [
            "先突出 Windows/C++ 客户端和排障交付主线。",
            "只在有证据时对齐 JD 关键词，避免过度承诺。",
            "Linux/Qt 作为成长项展示，不夸大深度。",
            "经历改成结果导向 bullet，并补充可量化范围。",
        ],
    }


def validate_resume_tailoring_version(
    version: dict[str, Any],
    kind: str,
) -> None:
    required_strings = ["version_name_en", "version_name_zh", "summary_en", "summary_zh"]
    for field in required_strings:
        if not normalize_text(str(version.get(field, ""))):
            raise RuntimeError(f"Resume tailoring {kind} output is missing required field: {field}")

    for field in ["keyword_focus_en", "keyword_focus_zh", "formatting_notes_en", "formatting_notes_zh", "skill_order_en", "skill_order_zh", "tailoring_recommendations_en", "tailoring_recommendations_zh"]:
        value = version.get(field)
        if not isinstance(value, list):
            raise RuntimeError(f"Resume tailoring {kind} output field is not a list: {field}")


def render_resume_version_markdown(
    version: dict[str, Any],
    resume: ResumeProfile,
    source: dict[str, Any],
    cluster_metrics: dict[str, Any],
    kind: str,
) -> str:
    output_roles: list[str] = []
    for role in source.get("roles", []):
        suggestion = find_role_suggestion(version.get("role_suggestions", []), role.get("company", ""), role.get("title", ""))
        bullets = suggestion.get("suggested_bullets_en", []) if suggestion else [role.get("body", "")]
        output_roles.append(
            "\n".join(
                [
                    f"### {role.get('company', 'Unknown')} | {role.get('title', 'Unknown role')}",
                    f"{role.get('dates', '')}",
                    "",
                    *[f"- {normalize_text(str(bullet))}" for bullet in bullets if normalize_text(str(bullet))],
                ]
            ).strip()
        )

    skills_en = version.get("skill_order_en") or source.get("skills", [])
    title = version.get("version_name_en", kind)
    return "\n".join(
        [
            f"# {title}",
            "",
            f"{source.get('name', 'Sample Candidate')}",
            f"{source.get('contact', '')}",
            "",
            "### Professional Summary",
            version.get("summary_en", ""),
            "",
            "### Technical Skills",
            ", ".join(normalize_list_items(skills_en)),
            "",
            "### Professional Experience",
            "",
            *output_roles,
            "",
            "### Education",
            "",
            *[line for line in source.get("education_lines", [])],
        ]
    ).strip() + "\n"


def render_resume_tailoring_markdown(
    tailoring: dict[str, Any],
) -> str:
    lines = [
        "# 简历定制包 / Resume Tailoring Pack",
        "",
        f"- 生成时间：`{tailoring.get('generated_at', '')}`",
        f"- 规则引擎：`{tailoring.get('engine', tailoring.get('model', ''))}`",
        f"- ATS 集群分：`{tailoring.get('cluster_metrics', {}).get('cluster_score', 0.0)}`",
        f"- Tailored 版本数：`{len(tailoring.get('tailored_versions', []))}`",
        "",
        "## 中文概览",
        "",
        tailoring.get("summary_zh", ""),
        "",
        "### 关键词焦点",
    ]
    keyword_focus = tailoring.get("cluster_metrics", {}).get("keyword_focus_zh", [])
    if keyword_focus:
        for keyword in keyword_focus:
            lines.append(f"- {keyword}")
    else:
        lines.append("- 无明显关键词焦点")
    lines.extend(
        [
            "",
            "### 主要缺口",
        ]
    )
    missing_keywords = tailoring.get("cluster_metrics", {}).get("missing_keywords_zh", [])
    if missing_keywords:
        for keyword in missing_keywords:
            lines.append(f"- {keyword}")
    else:
        lines.append("- 无明显缺口")
    lines.extend(["", "### 文件清单", ""])
    if tailoring.get("master_version"):
        master_name = Path(tailoring["master_version"].get("markdown_path", "master_resume.md")).name
        lines.append(f"- [ATS 基础版简历]({master_name})")
    for version in tailoring.get("tailored_versions", []):
        label = f"{version.get('job_title', '版本')} | {version.get('company', '')}"
        rel_path = Path(version.get("markdown_path", "version.md")).name
        lines.append(f"- [{label}]({rel_path})")
    lines.extend(["", "## English Summary", ""])
    lines.append(tailoring.get("summary_en", ""))
    return "\n".join(lines).strip() + "\n"


def build_resume_tailoring_bundle(
    analysis: dict[str, Any],
    resume: ResumeProfile,
    config: ResumeTailoringConfig,
    output_dir: Path,
) -> dict[str, Any]:
    top_jobs = analysis.get("top_matches", [])[: max(0, config.tailored_count)]
    resume_source = parse_resume_source_sections(resume.text)
    cluster_metrics = build_resume_tailoring_cluster_metrics(top_jobs, resume)
    tailoring_dir = ensure_dir(output_dir / "resume")

    if not top_jobs:
        return {
            "enabled": True,
            "engine": "resume-tailoring-rules",
            "api_url": "",
            "tailored_count": 0,
            "summary_en": "No top matches were available for resume tailoring.",
            "summary_zh": "没有可用于简历改写的匹配岗位。",
            "cluster_metrics": build_resume_tailoring_cluster_metrics([], resume),
            "master_version": {},
            "tailored_versions": [],
            "output_dir": str(tailoring_dir),
        }

    log_progress(f"[resume] generating rules-based ATS baseline for {len(top_jobs)} top jobs")
    master_version = build_fallback_master_tailoring_version(
        resume_source=resume_source,
        cluster_metrics=cluster_metrics,
        top_jobs=top_jobs,
    )
    validate_resume_tailoring_version(master_version, "master-fallback")
    master_version["raw_usage"] = {"fallback": True, "engine": "resume-tailoring-rules"}
    master_version["role_suggestions"] = build_resume_role_suggestions(resume_source, master_version)
    master_version["cluster_score"] = cluster_metrics["cluster_score"]
    master_version["markdown_path"] = str(tailoring_dir / "master_resume.md")
    master_markdown = render_resume_version_markdown(master_version, resume, resume_source, cluster_metrics, "master")
    Path(master_version["markdown_path"]).write_text(master_markdown, encoding="utf-8")

    tailored_versions: list[dict[str, Any]] = []
    for index, job in enumerate(top_jobs, start=1):
        log_progress(
            f"[resume] tailoring version {index}/{len(top_jobs)}: {job.get('title', 'Untitled role')} | {job.get('company', 'Unknown company')}"
        )
        job_analysis = job.get("jd_analysis_zh", {}) if isinstance(job.get("jd_analysis_zh", {}), dict) else {}
        keyword_summary = build_keyword_summary_for_job(job, resume)
        skill_order_en = normalize_list_items(
            keyword_summary["focus_en"] + master_version.get("skill_order_en", []) + resume_source.get("skills", [])
        )
        skill_order_zh = normalize_list_items(
            keyword_summary["focus_zh"] + master_version.get("skill_order_zh", []) + keyword_summary["focus_zh"]
        )
        version = {
            "version_name_en": f"Tailored Resume - {job.get('title', 'Role')} | {job.get('company', 'Company')}",
            "version_name_zh": f"{job.get('title', '岗位')} | {job.get('company', '公司')} 定制版",
            "summary_en": (
                f"Tailored for {job.get('title', 'the role')} at {job.get('company', 'the company')}. "
                f"Lead with the strongest Windows/C++ client evidence and keep "
                f"{', '.join(keyword_summary['focus_en'][:3]) or 'the most relevant keywords'} visible only where truthful."
            ),
            "summary_zh": job_analysis.get("overall_verdict_zh", "该版本围绕最匹配的 JD 重点重新排列。"),
            "keyword_focus_en": keyword_summary["focus_en"] or master_version.get("keyword_focus_en", []),
            "keyword_focus_zh": keyword_summary["focus_zh"] or master_version.get("keyword_focus_zh", []),
            "missing_keywords_en": keyword_summary["missing_en"] or cluster_metrics["missing_keywords_en"],
            "missing_keywords_zh": keyword_summary["missing_zh"] or cluster_metrics["missing_keywords_zh"],
            "formatting_notes_en": cluster_metrics["formatting_checks_en"],
            "formatting_notes_zh": cluster_metrics["formatting_checks_zh"],
            "skill_order_en": skill_order_en[:10],
            "skill_order_zh": skill_order_zh[:10],
            "tailoring_recommendations_en": build_tailoring_recommendations_en(
                job,
                job_analysis,
                keyword_summary["focus_en"],
            ),
            "tailoring_recommendations_zh": (job_analysis.get("tailoring_strategy_zh", []) or job_analysis.get("cover_letter_points_zh", []))[:4],
        }
        version["role_suggestions"] = build_resume_role_suggestions(resume_source, version, target_job=job)
        validate_resume_tailoring_version(version, "tailored")
        version["job_id"] = job.get("job_id")
        version["job_title"] = job.get("title", "")
        version["company"] = job.get("company", "")
        version["location"] = job.get("location", "")
        version["job_url"] = job.get("job_url", "")
        version["score"] = job.get("score", 0.0)
        version["recommendation"] = job.get("recommendation", "")
        slug_source = f"{job.get('title', '')}_{job.get('company', '')}"
        version["markdown_path"] = str(tailoring_dir / f"version_{index}_{slugify_filename(slug_source)}.md")
        version_markdown = render_resume_version_markdown(version, resume, resume_source, cluster_metrics, "tailored")
        Path(version["markdown_path"]).write_text(version_markdown, encoding="utf-8")
        tailored_versions.append(version)

    manifest = {
        "enabled": True,
        "engine": "resume-tailoring-rules",
        "api_url": "",
        "tailored_count": len(tailored_versions),
        "cluster_metrics": cluster_metrics,
        "summary_en": "The resume tailoring pack produced one ATS baseline draft plus tailored drafts for the top matching jobs.",
        "summary_zh": "简历定制包已为当前最匹配的岗位生成 1 份 ATS 基础版和若干定制版草稿，同时保留了中文分析、英文原始字段和量化待补提示。",
        "master_version": master_version,
        "tailored_versions": tailored_versions,
        "source_resume": {
            "path": resume.path,
            "name": resume_source.get("name", ""),
            "contact": resume_source.get("contact", ""),
            "skills": resume_source.get("skills", []),
            "roles": resume_source.get("roles", []),
            "education_lines": resume_source.get("education_lines", []),
        },
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "output_dir": str(tailoring_dir),
    }

    manifest_path = tailoring_dir / "manifest.json"
    tailoring_md_path = tailoring_dir / "tailoring.md"
    manifest["manifest_json"] = str(manifest_path)
    manifest["tailoring_markdown"] = str(tailoring_md_path)
    # Keep the legacy key so older tooling can still open previously generated manifests.
    manifest["workbench_markdown"] = str(tailoring_md_path)
    write_json(manifest_path, manifest)
    tailoring_md_path.write_text(render_resume_tailoring_markdown(manifest), encoding="utf-8")
    return manifest


def resolve_browser_path() -> Path | None:
    configured = os.getenv("LINKEDIN_BROWSER_EXECUTABLE", "").strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return path
        raise RuntimeError(f"LINKEDIN_BROWSER_EXECUTABLE does not exist: {configured}")

    for candidate in EDGE_CANDIDATES:
        if candidate.exists():
            return candidate
    for command in BROWSER_COMMAND_CANDIDATES:
        found = shutil.which(command)
        if found:
            return Path(found)
    return None


def launch_context(profile_dir: Path, headless: bool):
    sync_playwright = require_playwright_sync_api()
    playwright = sync_playwright().start()
    try:
        browser_path = resolve_browser_path()
        launch_kwargs: dict[str, Any] = {}
        if browser_path:
            launch_kwargs["executable_path"] = str(browser_path)
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            reduced_motion="reduce",
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
            **launch_kwargs,
        )
        browser.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            """
        )
        return playwright, browser
    except Exception:
        playwright.stop()
        raise


def linkedin_cookie_present(context) -> bool:
    cookies = context.cookies("https://www.linkedin.com")
    return any(cookie.get("name") == "li_at" for cookie in cookies)


def get_token_expiry_info(context) -> dict[str, Any]:
    cookies = context.cookies("https://www.linkedin.com")
    for cookie in cookies:
        if cookie.get("name") == "li_at":
            expires = cookie.get("expires")
            if expires:
                from datetime import datetime
                expiry_date = datetime.fromtimestamp(expires)
                days_left = (expiry_date - datetime.now()).days
                return {
                    "present": True,
                    "expiry_timestamp": expires,
                    "expiry_date": expiry_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "days_remaining": days_left,
                    "warning": days_left <= 3,
                    "expired": days_left < 0,
                }
            return {"present": True, "expiry_timestamp": None, "expiry_date": None, "days_remaining": None, "warning": False, "expired": False}
    return {"present": False, "expiry_timestamp": None, "expiry_date": None, "days_remaining": None, "warning": False, "expired": False}


def canonical_job_url(href: str) -> str:
    match = CANONICAL_JOB_URL_PATTERN.search(href)
    if match:
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}/"
    return href.split("?")[0]


def extract_job_id(url: str) -> str:
    match = CANONICAL_JOB_URL_PATTERN.search(url)
    return match.group(1) if match else ""


def first_text(page, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                text = normalize_text(locator.first.inner_text(timeout=1500))
                if text:
                    return text
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return ""


def first_href(page, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                href = locator.first.get_attribute("href", timeout=1500)
                if href:
                    return href
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return None


def expand_job_sections(page) -> None:
    selectors = [
        "button[data-testid='expandable-text-button']",
        "button:has-text('more')",
        "button:has-text('More')",
    ]
    for selector in selectors:
        try:
            page.eval_on_selector_all(selector, "els => els.forEach(el => el.click())")
        except Exception:
            continue
    try:
        page.wait_for_timeout(600)
    except Exception:
        pass


def extract_main_lines(page) -> list[str]:
    try:
        text = page.locator("main").inner_text(timeout=5000)
    except Exception:
        return []
    return [normalize_text(line) for line in text.splitlines() if normalize_text(line)]


def parse_meta_line(meta_line: str) -> tuple[str, str]:
    posted_match = re.search(
        r"((?:reposted|posted)\s+)?"
        r"(\d+\s+(?:minutes?|hours?|hrs?|days?|weeks?)\s+ago|"
        r"vor\s+\d+\s+(?:minuten?|stunden?|tagen?|wochen?)|"
        r"\d+\s*分钟前|\d+\s*小时前|\d+\s*天前|\d+\s*周前|"
        r"today|yesterday|heute|gestern|今天|昨天)",
        meta_line,
        re.I,
    )
    posted_text = ""
    location = meta_line
    if posted_match:
        posted_text = normalize_text(posted_match.group(0))
        location = normalize_text(meta_line[: posted_match.start()])
    location = re.sub(
        r"\s+\d+\s*(?:people clicked apply|applicants?|bewerber.*?|位申请者|人申请|位点击申请.*)$",
        "",
        location,
        flags=re.I,
    ).strip(" ·|")
    location = re.sub(r"\s*·\s*(?:的时间|转发的时间)[:：]?\s*$", "", location).strip(" ·|")
    return location, posted_text


def extract_description_from_lines(lines: list[str]) -> str:
    start_idx = None
    for idx, line in enumerate(lines):
        if line.lower() in DESCRIPTION_START_MARKERS:
            start_idx = idx + 1
            break
    if start_idx is None:
        return ""
    description_lines: list[str] = []
    for line in lines[start_idx:]:
        lowered = line.lower()
        if lowered in DESCRIPTION_STOP_MARKERS:
            break
        if lowered in DESCRIPTION_SKIP_LINES:
            continue
        if lowered.startswith("linkedin corporation ©"):
            break
        if lowered.startswith("选择语言") or lowered.startswith("select language"):
            break
        if lowered.startswith("about") and len(description_lines) > 10:
            break
        description_lines.append(line)
    return "\n".join(description_lines).strip()


def parse_job_page(page) -> dict[str, str]:
    expand_job_sections(page)
    lines = extract_main_lines(page)
    page_title = page.title()
    title = ""
    company = ""
    location = ""
    posted_text = ""
    description = extract_description_from_lines(lines)

    if len(lines) >= 2:
        company = lines[0]
        title = lines[1]
    if len(lines) >= 3:
        location, posted_text = parse_meta_line(lines[2])

    if not title or not company:
        title_parts = [part.strip() for part in page_title.split("|") if part.strip()]
        if len(title_parts) >= 2:
            title = title or title_parts[0]
            company = company or title_parts[1]

    if not description and lines:
        fallback_lines = [line for line in lines[3:80] if line.lower() not in DESCRIPTION_SKIP_LINES]
        description = "\n".join(fallback_lines).strip()

    return {
        "title": title,
        "company": company,
        "location": location,
        "posted_text": posted_text,
        "description": description,
    }


def is_germany_location(location: str) -> bool:
    normalized = normalize_text(location)
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in NON_GERMANY_LOCATION_PATTERNS):
        return False
    return any(pattern.search(normalized) for pattern in GERMANY_LOCATION_PATTERNS)


def is_default_germany_search(search_location: str) -> bool:
    return normalize_text(search_location).strip().lower() in {"germany", "deutschland", "de"}


def is_target_location(
    location: str,
    search_location: str = DEFAULT_SEARCH_LOCATION,
    location_keywords: list[str] | None = None,
) -> bool:
    if is_default_germany_search(search_location):
        return is_germany_location(location)

    normalized = normalize_text(location).lower()
    if not normalized:
        return False
    if any(pattern.search(normalized) for pattern in NON_GERMANY_LOCATION_PATTERNS):
        return False

    terms = [search_location, *(location_keywords or [])]
    normalized_terms = {
        normalize_text(str(term)).lower()
        for term in terms
        if normalize_text(str(term)).strip()
    }
    return any(term in normalized for term in normalized_terms)


def extract_search_links(page) -> list[str]:
    try:
        page.wait_for_selector("a[href*='/jobs/view/']", timeout=15000)
    except PlaywrightTimeoutError:
        return []
    hrefs = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href*="/jobs/view/"]'))
          .map(anchor => anchor.href)
          .filter(Boolean)
        """
    )
    links: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        job_url = canonical_job_url(href)
        if "/jobs/view/" not in job_url or job_url in seen:
            continue
        seen.add(job_url)
        links.append(job_url)
    return links


def recent_days_to_linkedin_tpr(recent_days: int | float | None) -> str:
    try:
        days = max(1, int(float(recent_days or 7)))
    except (TypeError, ValueError):
        days = 7
    return f"r{days * 86400}"


def build_search_url(
    query: str,
    start: int,
    search_location: str = DEFAULT_SEARCH_LOCATION,
    recent_days: int | float | None = 7,
) -> str:
    return (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={quote_plus(query)}"
        f"&location={quote_plus(search_location or DEFAULT_SEARCH_LOCATION)}"
        f"&f_TPR={recent_days_to_linkedin_tpr(recent_days)}"
        "&sortBy=DD"
        f"&start={start}"
    )


def parse_posted_age_days(posted_text: str) -> int | None:
    text = posted_text.lower()
    patterns = [
        (r"(\d+)\s+minute", 0),
        (r"(\d+)\s+hour", 0),
        (r"(\d+)\s+hr", 0),
        (r"(\d+)\s+day", 1),
        (r"(\d+)\s+week", 7),
        (r"(\d+)\s+monat", 30),
        (r"(\d+)\s*分钟前", 0),
        (r"(\d+)\s*小时前", 0),
        (r"(\d+)\s*天前", 1),
        (r"(\d+)\s*周前", 7),
        (r"vor\s+(\d+)\s+stund", 0),
        (r"vor\s+(\d+)\s+tag", 1),
        (r"vor\s+(\d+)\s+woche", 7),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            value = int(match.group(1))
            return value * multiplier
    if "today" in text or "heute" in text or "just now" in text or "minutes ago" in text or "今天" in posted_text:
        return 0
    if "yesterday" in text or "gestern" in text or "昨天" in posted_text:
        return 1
    return None


def is_within_recent_days(posted_text: str, recent_days: int | float | None = 7) -> bool:
    days = parse_posted_age_days(posted_text)
    if days is None:
        return True
    try:
        limit = max(1, int(float(recent_days or 7)))
    except (TypeError, ValueError):
        limit = 7
    return days <= limit


def within_last_week(posted_text: str) -> bool:
    return is_within_recent_days(posted_text, 7)


def detect_job_keywords(text: str) -> list[str]:
    found = []
    feature_hits = detect_features(text, TECH_FEATURES)
    for key, hit in feature_hits.items():
        if hit:
            found.append(TECH_FEATURES[key]["label"])
    return found


def detect_seniority(title_and_description: str) -> str:
    for level, patterns in SENIORITY_COMPILED_PATTERNS.items():
        if any(pattern.search(title_and_description) for pattern in patterns):
            return level
    return "mid"


def collect_job_detail(
    page,
    url: str,
    query_sources: list[str],
    search_location: str = DEFAULT_SEARCH_LOCATION,
    location_keywords: list[str] | None = None,
    recent_days: int | float | None = 7,
) -> dict[str, Any] | None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if "/login" in page.url or "checkpoint" in page.url:
        raise RuntimeError("LinkedIn session is no longer valid. Run the login command again.")
    try:
        page.wait_for_load_state("networkidle", timeout=7000)
    except PlaywrightTimeoutError:
        pass

    parsed = parse_job_page(page)
    title = parsed["title"]
    company = parsed["company"]
    meta_block = " ".join(part for part in [parsed["location"], parsed["posted_text"]] if part).strip()
    description = parsed["description"]
    apply_url = first_href(
        page,
        [
            "a[data-control-name*='jobdetails_topcard_inapply']",
            "a.jobs-apply-button",
            "a:has-text('Apply')",
        ],
    )
    full_text = normalize_text(" ".join(part for part in [title, company, meta_block, description] if part))
    location = parsed["location"]
    posted_text = parsed["posted_text"]
    if not title or not description:
        return None
    if not is_target_location(location, search_location, location_keywords):
        return None
    if posted_text and not is_within_recent_days(posted_text, recent_days):
        return None
    return {
        "job_id": extract_job_id(page.url or url),
        "title": title,
        "company": company,
        "location": location,
        "posted_text": posted_text,
        "job_url": canonical_job_url(page.url or url),
        "apply_url": apply_url,
        "description": description,
        "query_sources": sorted(query_sources),
        "detected_keywords": detect_job_keywords(full_text),
        "language_requirement": detect_language_requirement(full_text),
        "seniority": detect_seniority(full_text),
        "collected_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def save_incremental_jobs(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    ensure_dir(path.parent)
    write_json(path, payload)


def collect_jobs(
    profile_dir: Path,
    headless: bool,
    pages_per_query: int,
    max_jobs: int,
    delay_seconds: float,
    queries: list[str] | None = None,
    incremental_path: Path | None = None,
    search_location: str = DEFAULT_SEARCH_LOCATION,
    location_keywords: list[str] | None = None,
    recent_days: int | float | None = 7,
) -> dict[str, Any]:
    active_queries = normalize_query_list(queries) or list(DEFAULT_QUERIES)
    active_location = normalize_text(search_location or DEFAULT_SEARCH_LOCATION)
    active_location_keywords = normalize_query_list(location_keywords)
    pages_per_query = max(1, int(pages_per_query))
    max_jobs = max(1, int(max_jobs))
    try:
        active_recent_days = max(1, int(float(recent_days or 7)))
    except (TypeError, ValueError):
        active_recent_days = 7
    payload: dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "location": active_location,
        "location_keywords": active_location_keywords,
        "posted_within": f"{active_recent_days}d",
        "queries": active_queries,
        "jobs": [],
        "status": "running",
    }
    save_incremental_jobs(incremental_path, payload)

    playwright, context = launch_context(profile_dir, headless=headless)
    try:
        token_info = get_token_expiry_info(context)
        if not linkedin_cookie_present(context):
            raise RuntimeError("LinkedIn login session was not found. Run `login` first.")
        page = context.new_page()
        search_index: dict[str, set[str]] = {}

        payload["token_info"] = token_info
        if token_info.get("warning") or token_info.get("expired"):
            log_progress(f"[collect] WARNING: LinkedIn token expires in {token_info.get('days_remaining', '?')} days ({token_info.get('expiry_date', 'unknown')})")

        for query_index, query in enumerate(active_queries, start=1):
            log_progress(f"[collect] search query {query_index}/{len(active_queries)}: {query}")
            for page_num in range(pages_per_query):
                start = page_num * 25
                url = build_search_url(
                    query,
                    start=start,
                    search_location=active_location,
                    recent_days=active_recent_days,
                )
                log_progress(f"[collect] loading page {page_num + 1}/{pages_per_query} for {query}")
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if "/login" in page.url or "checkpoint" in page.url:
                    raise RuntimeError("LinkedIn session is no longer valid. Run the login command again.")
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PlaywrightTimeoutError:
                    pass
                links = extract_search_links(page)
                log_progress(f"[collect] found {len(links)} candidate links on this page")
                if not links:
                    continue
                for link in links:
                    search_index.setdefault(link, set()).add(query)
                payload["candidate_count"] = len(search_index)
                payload["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                save_incremental_jobs(incremental_path, payload)
                time.sleep(delay_seconds)

        all_candidates = list(search_index.items())
        log_progress(f"[collect] deduped to {len(all_candidates)} candidate job detail pages")
        for detail_index, (link, query_sources) in enumerate(all_candidates, start=1):
            log_progress(f"[collect] inspecting job detail {detail_index}/{len(all_candidates)}")
            job = collect_job_detail(
                page,
                link,
                sorted(query_sources),
                search_location=active_location,
                location_keywords=active_location_keywords,
                recent_days=active_recent_days,
            )
            if job is None:
                time.sleep(delay_seconds)
                continue
            payload["jobs"].append(job)
            payload["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            save_incremental_jobs(incremental_path, payload)
            log_progress(f"[collect] kept {len(payload['jobs'])} jobs so far")
            if len(payload["jobs"]) >= max_jobs:
                break
            time.sleep(delay_seconds)

        payload["status"] = "complete"
        payload["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        save_incremental_jobs(incremental_path, payload)
        log_progress(f"[collect] finished with {len(payload['jobs'])} jobs")
        return payload
    finally:
        context.close()
        playwright.stop()


def latest_jobs_json() -> Path:
    candidates = sorted(OUTPUT_ROOT.glob("*/jobs.json"))
    if not candidates:
        raise RuntimeError("No jobs.json file was found under outputs/linkedin_jobs. Run collect first.")
    return candidates[-1]


def load_jobs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(read_text(path))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        return payload["jobs"]
    raise RuntimeError(f"Unsupported jobs JSON shape in {path}.")


def recommendation_from_score(score: float) -> str:
    if score >= 80:
        return "Strong fit"
    if score >= 65:
        return "Good fit"
    if score >= 50:
        return "Stretch"
    return "Skip"


def clamp_score(score: float) -> float:
    return max(0.0, min(100.0, round(score, 1)))


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def requirement_status_weight(status_key: str) -> float:
    return {
        "match": 1.0,
        "partial": 0.62,
        "weak": 0.28,
        "missing": 0.0,
    }.get(status_key, 0.0)


def weighted_requirement_coverage(items: list[dict[str, Any]]) -> tuple[float, float]:
    total_importance = sum(float(item.get("importance", 1.0)) for item in items)
    if total_importance <= 0:
        return 0.0, 0.0
    covered = sum(float(item.get("importance", 1.0)) * requirement_status_weight(str(item.get("status_key", "missing"))) for item in items)
    ratio = covered / total_importance
    return ratio, total_importance


def weighted_requirement_score(items: list[dict[str, Any]], max_points: float) -> float:
    ratio, _ = weighted_requirement_coverage(items)
    return max_points * ratio


def trim_excerpt(text: str, max_chars: int = 1200) -> str:
    normalized = normalize_text(text)
    return normalized[:max_chars]


def language_requirement_score(job: dict[str, Any], scoring_config: ScoringConfig) -> tuple[float, list[str]]:
    language_requirement = job.get("language_requirement", "Not stated")
    flags: list[str] = []
    hard_blocks = [normalize_text(value).lower() for value in scoring_config.language_preferences.get("hard_block", [])]
    if any(block and block in language_requirement.lower() for block in hard_blocks):
        flags.append("Explicit German language requirement")
        return 0.0, flags
    if "German B2+" in language_requirement:
        flags.append("Explicit German language requirement")
        return 0.0, flags
    if language_requirement == "German mentioned":
        flags.append("German language mentioned")
        return 35.0, flags
    if language_requirement == "English mentioned":
        preferred = {value.lower() for value in scoring_config.language_preferences.get("preferred", [])}
        return (100.0 if "english" in preferred else 90.0), flags
    return 70.0, flags


def visa_sponsorship_score(text: str) -> tuple[float, str]:
    if VISA_NEGATIVE_PATTERN.search(text):
        return 20.0, "No visa sponsorship or existing work authorization required"
    if VISA_POSITIVE_PATTERN.search(text):
        return 100.0, "Visa sponsorship or relocation support mentioned"
    return 60.0, "Visa sponsorship not stated"


def seniority_fit_score(seniority: str, years: float) -> float:
    if seniority == "principal":
        return 100.0 if years >= 10 else (65.0 if years >= 8 else 35.0)
    if seniority == "lead":
        return 100.0 if years >= 8 else (70.0 if years >= 6 else 40.0)
    if seniority == "senior":
        return 100.0 if years >= 7 else (75.0 if years >= 5 else 45.0)
    return 100.0 if years >= 4 else 55.0


def weighted_component_score(items: list[dict[str, Any]]) -> float:
    ratio, _ = weighted_requirement_coverage(items)
    return 100.0 * ratio


def normalized_terms(values: list[Any] | None) -> list[str]:
    terms: list[str] = []
    for value in values or []:
        term = normalize_text(str(value)).strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def matched_strategy_terms(text: str, terms: list[Any] | None) -> list[str]:
    normalized = normalize_text(text).lower()
    matches: list[str] = []
    for term in normalized_terms(terms):
        if term.lower() in normalized:
            matches.append(term)
    return matches


def strategy_component_score(matches: list[str], terms: list[Any] | None, neutral: float = 60.0) -> float:
    total = len(normalized_terms(terms))
    if total <= 0:
        return neutral
    ratio = len(matches) / total
    return 45.0 + min(55.0, ratio * 90.0)


def market_strategy_score(job: dict[str, Any], scoring_config: ScoringConfig) -> dict[str, Any]:
    strategy = scoring_config.market_strategy or {}
    title_text = normalize_text(str(job.get("title", "")))
    full_text = normalize_text(" ".join([job.get("title", ""), job.get("company", ""), job.get("description", "")]))
    matched_titles = matched_strategy_terms(title_text, strategy.get("preferred_titles"))
    matched_technologies = matched_strategy_terms(full_text, strategy.get("preferred_technologies"))
    matched_domains = matched_strategy_terms(full_text, strategy.get("preferred_domains"))
    matched_demand = matched_strategy_terms(full_text, strategy.get("demand_signals"))
    matched_risks = matched_strategy_terms(full_text, strategy.get("risk_terms"))

    title_score = strategy_component_score(matched_titles, strategy.get("preferred_titles"), neutral=58.0)
    technology_score = strategy_component_score(matched_technologies, strategy.get("preferred_technologies"), neutral=62.0)
    domain_score = strategy_component_score(matched_domains, strategy.get("preferred_domains"), neutral=58.0)
    demand_score = strategy_component_score(matched_demand, strategy.get("demand_signals"), neutral=55.0)
    risk_penalty = min(18.0, 6.0 * len(matched_risks))
    score = clamp_score(
        title_score * 0.34
        + technology_score * 0.30
        + domain_score * 0.23
        + demand_score * 0.13
        - risk_penalty
    )
    try:
        strategy_weight = float(strategy.get("strategy_weight", 0.0) or 0.0)
    except (TypeError, ValueError):
        strategy_weight = 0.0
    strategy_weight = max(0.0, min(0.35, strategy_weight))

    return {
        "score": score,
        "weight": strategy_weight,
        "positioning": strategy.get("positioning", ""),
        "objective": strategy.get("objective", ""),
        "rationale": strategy.get("rationale", ""),
        "matched_titles": matched_titles,
        "matched_technologies": matched_technologies,
        "matched_domains": matched_domains,
        "matched_demand_signals": matched_demand,
        "matched_risk_terms": matched_risks,
        "risk_penalty": round(risk_penalty, 1),
    }


def score_job(job: dict[str, Any], resume: ResumeProfile, scoring_config: ScoringConfig | None = None) -> dict[str, Any]:
    scoring_config = scoring_config or load_scoring_config()
    text = normalize_text(" ".join([job.get("title", ""), job.get("description", ""), job.get("company", "")]))
    requirements = extract_job_requirements_zh(job, resume)
    required_items = list(requirements.get("required", []))
    preferred_items = list(requirements.get("preferred", []))
    soft_items = list(requirements.get("soft_skills", []))

    required_ratio, required_importance = weighted_requirement_coverage(required_items)
    preferred_ratio, _ = weighted_requirement_coverage(preferred_items)
    soft_ratio, _ = weighted_requirement_coverage(soft_items)

    technical_items = [item for item in required_items + preferred_items if item.get("key") in TECH_FEATURES]
    if not technical_items:
        detected_tech = detect_features(text, TECH_FEATURES)
        detected_keys = [key for key, hit in detected_tech.items() if hit]
        if detected_keys:
            fallback_level = sum(resume.skill_levels.get(key, 0.0) for key in detected_keys) / len(detected_keys)
            tech_component = 100.0 * fallback_level
        else:
            tech_component = 35.0
    else:
        tech_component = weighted_component_score(technical_items)
    soft_component = weighted_component_score(soft_items) if soft_items else 70.0
    coverage_gate = 0.22 + (0.78 * required_ratio)
    tech_score = (tech_component * 0.92 + soft_component * 0.08) * coverage_gate

    domain_hits = detect_features(text, DOMAIN_FEATURES)
    detected_domain = [key for key, hit in domain_hits.items() if hit]
    if detected_domain:
        domain_levels = [resume.domain_levels.get(key, 0.0) for key in detected_domain]
        domain_score = 100.0 * (sum(domain_levels) / len(domain_levels))
    else:
        domain_score = 50.0

    seniority = job.get("seniority", "mid")
    years = resume.years_experience
    seniority_score = seniority_fit_score(seniority, years)

    language_score, flags = language_requirement_score(job, scoring_config)
    visa_score, visa_status = visa_sponsorship_score(text)
    title_lower = job.get("title", "").lower()

    if STUDENT_ROLE_PATTERN.search(title_lower):
        seniority_score = min(seniority_score, 20.0)
        flags.append("Student / internship role")

    mismatch_penalty = 0.0
    for penalty_rule in MISMATCH_PENALTIES:
        if any(pattern.search(text) for pattern in penalty_rule.get("_compiled_patterns", [])):
            mismatch_penalty += penalty_rule["penalty"]
            flags.append(penalty_rule["flag"])
    if THIRD_PARTY_PATTERN.search(text):
        flags.append("Third-party recruiter / staffing role")

    missing_required_importance = sum(float(item.get("importance", 1.0)) for item in required_items if item.get("status_key") == "missing")
    weak_required_importance = sum(float(item.get("importance", 1.0)) for item in required_items if item.get("status_key") == "weak")
    partial_required_importance = sum(float(item.get("importance", 1.0)) for item in required_items if item.get("status_key") == "partial")
    hard_requirement_penalty = min(40.0, (missing_required_importance * 1.15) + (weak_required_importance * 0.55) + (partial_required_importance * 0.2))
    if required_items and required_ratio < 0.55:
        hard_requirement_penalty += 6.0

    weights = scoring_config.scoring_weights
    base_weighted_total = (
        tech_score * weights["technical_skills"]
        + domain_score * weights["domain_experience"]
        + seniority_score * weights["seniority"]
        + language_score * weights["language_requirement"]
        + visa_score * weights["visa_sponsorship"]
    )
    penalty_total = min(45.0, mismatch_penalty + hard_requirement_penalty * 0.55)
    market_strategy = market_strategy_score(job, scoring_config)
    strategy_weight = float(market_strategy.get("weight", 0.0) or 0.0)
    weighted_total = (base_weighted_total * (1.0 - strategy_weight)) + (market_strategy["score"] * strategy_weight)
    total_score = clamp_score(weighted_total - penalty_total)
    if language_score == 0.0:
        total_score = min(total_score, 45.0)
    if required_ratio < 0.25:
        total_score = min(total_score, 35.0)
    elif required_ratio < 0.45:
        total_score = min(total_score, 55.0)
    elif required_ratio < 0.60:
        total_score = min(total_score, 70.0)

    all_required_satisfied = all(item.get("status_key") in ("match", "partial") for item in required_items) and len(required_items) > 0
    no_preferred_satisfied = len(preferred_items) > 0 and all(item.get("status_key") in ("missing", "weak") for item in preferred_items)

    if not all_required_satisfied:
        total_score = min(total_score, 65.0)
    if no_preferred_satisfied:
        total_score = min(total_score, 75.0)

    recommendation = recommendation_from_score(total_score)

    detected_domain_labels = [DOMAIN_FEATURES[key]["label"] for key in detected_domain if resume.domain_levels.get(key, 0.0) >= 0.75]
    strengths = [
        item["label"]
        for item in required_items
        if item.get("status_key") == "match" and item.get("key") in TECH_FEATURES
    ]
    strengths = sorted(set(strengths + detected_domain_labels))[:6]
    gaps = [
        item["label"]
        for item in required_items
        if item.get("status_key") in {"weak", "missing"}
    ]
    gaps.extend(item["label"] for item in preferred_items if item.get("status_key") in {"weak", "missing"})
    gaps = sorted(set(gaps))[:6]

    return {
        "score": total_score,
        "recommendation": recommendation,
        "score_breakdown": {
            "technical": round(tech_score, 1),
            "domain": round(domain_score, 1),
            "seniority": round(seniority_score, 1),
            "language": round(language_score, 1),
            "visa": round(visa_score, 1),
            "market_strategy": round(market_strategy["score"], 1),
            "market_strategy_weight": round(strategy_weight, 3),
            "logistics": round(language_score * weights["language_requirement"] + visa_score * weights["visa_sponsorship"], 1),
            "mismatch_penalty": round(penalty_total, 1),
            "required_ratio": round(required_ratio, 3),
            "preferred_ratio": round(preferred_ratio, 3),
            "weights": {key: round(value, 3) for key, value in weights.items()},
        },
        "strengths": strengths,
        "gaps": gaps,
        "flags": flags,
        "visa_sponsorship": visa_status,
        "market_strategy_fit": market_strategy,
    }


def filter_reasons_for_job(job: dict[str, Any], scoring_config: ScoringConfig) -> list[str]:
    reasons: list[str] = []
    filters = scoring_config.filters
    title = normalize_text(str(job.get("title", ""))).lower()
    text = normalize_text(" ".join([job.get("title", ""), job.get("company", ""), job.get("description", "")]))
    if filters.get("exclude_student_jobs", True) and (
        "Student / internship role" in job.get("flags", []) or STUDENT_ROLE_PATTERN.search(title)
    ):
        reasons.append("student_or_internship")
    if filters.get("exclude_3rd_party", True) and (
        "Third-party recruiter / staffing role" in job.get("flags", []) or THIRD_PARTY_PATTERN.search(text)
    ):
        reasons.append("third_party_or_staffing")
    try:
        min_score = float(filters.get("min_score_threshold", 0) or 0)
    except (TypeError, ValueError):
        min_score = 0.0
    if float(job.get("score", 0.0) or 0.0) < min_score:
        reasons.append("below_min_score_threshold")
    return reasons


def build_analysis(
    jobs: list[dict[str, Any]],
    resume: ResumeProfile,
    scoring_config: ScoringConfig | None = None,
    resume_tailoring_config: ResumeTailoringConfig | None = None,
    output_dir: Path | None = None,
    token_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoring_config = scoring_config or load_scoring_config()
    analyzed_jobs = []
    for job in jobs:
        result = score_job(job, resume, scoring_config)
        analyzed_jobs.append({**job, **result, "base_score": result["score"]})
    analyzed_jobs.sort(key=lambda item: item["score"], reverse=True)

    for job in analyzed_jobs:
        job["jd_analysis_zh"] = build_job_analysis_zh(job, resume)
        job["filter_reasons"] = filter_reasons_for_job(job, scoring_config)

    visible_jobs = [job for job in analyzed_jobs if not job.get("filter_reasons")]
    filtered_jobs = [job for job in analyzed_jobs if job.get("filter_reasons")]

    top_matches = visible_jobs[:20]
    analysis = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "output_dir": str(output_dir) if output_dir is not None else None,
        "analysis_engine": "job-match-scoring-rules",
        "resume": {
            "path": resume.path,
            "years_experience": resume.years_experience,
            "strengths": resume.strengths,
            "english_level": resume.english_level,
        },
        "jobs_collected": len(analyzed_jobs),
        "jobs_analyzed": len(visible_jobs),
        "jobs_filtered_out": len(filtered_jobs),
        "scoring_config": {
            "scoring_weights": scoring_config.scoring_weights,
            "filters": scoring_config.filters,
            "language_preferences": scoring_config.language_preferences,
            "market_strategy": scoring_config.market_strategy,
        },
        "top_matches": top_matches,
        "jobs": visible_jobs,
        "filtered_jobs": filtered_jobs,
        "resume_tailoring_cluster": build_resume_tailoring_cluster_metrics(
            top_matches[: min(3, len(top_matches))],
            resume,
        ),
        "token_info": token_info,
    }

    if resume_tailoring_config is not None:
        if output_dir is None:
            raise RuntimeError("Resume tailoring output was requested without an output directory.")
        analysis["resume_tailoring"] = build_resume_tailoring_bundle(
            analysis,
            resume,
            resume_tailoring_config,
            output_dir,
        )
    else:
        analysis["resume_tailoring"] = {
            "enabled": False,
            "engine": "resume-tailoring-rules",
            "tailored_count": 0,
            "summary_en": "Resume tailoring output was not requested for this run.",
            "summary_zh": "本次运行未生成简历定制包产物。",
        }

    return analysis


def render_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# 德国 LinkedIn C++ 岗位匹配报告",
        "",
        f"- 生成时间：`{analysis['generated_at']}`",
        f"- 简历来源：`{analysis['resume']['path']}`",
        f"- 分析岗位数：`{analysis['jobs_analyzed']}`",
        "",
        "## 简历画像",
        "",
    ]
    for strength in analysis["resume"]["strengths"]:
        lines.append(f"- {strength}")

    # Read the legacy key for older summaries that were written before the tailoring rename.
    tailoring = analysis.get("resume_tailoring") or analysis.get("resume_workbench") or {}
    if tailoring:
        lines.extend(
            [
                "",
                "## 简历定制包",
                "",
                f"- 规则引擎：`{tailoring.get('engine', tailoring.get('model', ''))}`",
                f"- ATS 集群分：`{tailoring.get('cluster_metrics', {}).get('cluster_score', 0.0)}`",
                f"- Tailored 版本数：`{len(tailoring.get('tailored_versions', []))}`",
                f"- 基础版文件：[{Path(tailoring.get('master_version', {}).get('markdown_path', '')).name if tailoring.get('master_version') else 'N/A'}](resume/{Path(tailoring.get('master_version', {}).get('markdown_path', '')).name if tailoring.get('master_version') else 'master_resume.md'})",
            ]
        )
        for version in tailoring.get("tailored_versions", []):
            version_name = Path(version.get("markdown_path", "")).name
            lines.append(f"- `{version.get('job_title', '版本')} | {version.get('company', '')}` -> [{version_name}](resume/{version_name})")

    lines.extend(["", "## Top 匹配岗位", ""])
    for index, job in enumerate(analysis["top_matches"], start=1):
        job_analysis = job.get("jd_analysis_zh", {})
        lines.append(f"### {index}. {job['title']} | {job.get('company', '未知公司')}")
        lines.append("")
        lines.append(f"- 综合分：`{job['score']}`")
        lines.append(f"- 建议：`{job_analysis.get('recommendation_zh', translate_recommendation_zh(job.get('recommendation', '')) )}`")
        lines.append(f"- 投递优先级：`{job_analysis.get('application_priority_zh', '待判断')}`")
        lines.append(f"- 地点：`{job.get('location', '未知')}`")
        lines.append(f"- 发布时间：`{job.get('posted_text', '未抓取到') or '未抓取到'}`")
        lines.append(f"- 语言要求：`{job_analysis.get('language_requirement_zh', '未明确说明')}`")
        lines.append(f"- 链接：{job['job_url']}")
        lines.append(f"- 总体判断：{job_analysis.get('overall_verdict_zh', '')}")
        if job_analysis.get("strengths_to_emphasize_zh"):
            lines.append(f"- 优先强调：`{'；'.join(job_analysis['strengths_to_emphasize_zh'])}`")
        gap_items = []
        for group in ("critical", "major", "minor"):
            gap_items.extend(job_analysis.get("gaps_zh", {}).get(group, []))
        if gap_items:
            lines.append(f"- 主要缺口：`{'；'.join(gap_items[:4])}`")
        if job_analysis.get("red_flags_zh"):
            lines.append(f"- 风险提示：`{'；'.join(job_analysis['red_flags_zh'])}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def html_text(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def recommendation_badge_class(recommendation: str) -> str:
    mapping = {
        "Strong fit": "strong",
        "Good fit": "good",
        "Stretch": "stretch",
        "Skip": "skip",
    }
    return mapping.get(recommendation, "neutral")


def score_tone_class(score: float) -> str:
    if score >= 80:
        return "strong"
    if score >= 65:
        return "good"
    if score >= 50:
        return "stretch"
    return "skip"


def build_html_list(items: list[str], empty_text: str = "无") -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return f"<div class='empty'>{html_text(empty_text)}</div>"
    return "<ul>" + "".join(f"<li>{html_text(item)}</li>" for item in cleaned) + "</ul>"


def requirement_status_class(status_key: str) -> str:
    mapping = {
        "match": "match",
        "partial": "partial",
        "weak": "weak",
        "missing": "missing",
    }
    return mapping.get(status_key, "missing")


def build_requirement_cards(items: list[dict[str, Any]], empty_text: str) -> str:
    if not items:
        return f"<div class='empty'>{html_text(empty_text)}</div>"
    blocks = []
    for item in items:
        blocks.append(
            f"""
            <div class="req-card {requirement_status_class(item.get('status_key', 'missing'))}">
              <div class="req-top">
                <strong>{html_text(item.get('label', '未命名要求'))}</strong>
                <span class="status-pill {requirement_status_class(item.get('status_key', 'missing'))}">
                  {html_text(item.get('status_zh', '未判断'))}
                </span>
              </div>
              <p>{html_text(item.get('evidence_zh', ''))}</p>
            </div>
            """
        )
    return "".join(blocks)




def write_latest_pointer(output_dir: Path, artifacts: dict[str, Any]) -> None:
    payload = {
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "output_dir": str(output_dir),
        **artifacts,
    }
    write_json(OUTPUT_ROOT / "latest.json", payload)


def write_analysis_outputs(output_dir: Path, analysis: dict[str, Any]) -> dict[str, Path]:
    ensure_dir(output_dir)
    summary_path = output_dir / "summary.json"
    analysis_md_path = output_dir / "analysis.md"

    write_json(summary_path, analysis)
    analysis_md_path.write_text(render_markdown(analysis), encoding="utf-8")
    legacy_semantic_path = output_dir / "semantic_rerank.json"
    if legacy_semantic_path.exists():
        legacy_semantic_path.unlink()

    artifacts = {
        "summary_json": str(summary_path),
        "analysis_markdown": str(analysis_md_path),
    }
    # Read the legacy key for older summaries that were written before the tailoring rename.
    tailoring = analysis.get("resume_tailoring") or analysis.get("resume_workbench") or {}
    if tailoring:
        artifacts["resume_tailoring_json"] = tailoring.get("manifest_json")
        artifacts["resume_tailoring_markdown"] = tailoring.get("tailoring_markdown") or tailoring.get("workbench_markdown")
        if tailoring.get("master_version", {}).get("markdown_path"):
            artifacts["resume_master_resume_markdown"] = tailoring["master_version"]["markdown_path"]
        tailored_paths = [
            version.get("markdown_path")
            for version in tailoring.get("tailored_versions", [])
            if version.get("markdown_path")
        ]
        if tailored_paths:
            artifacts["resume_tailored_resume_markdowns"] = tailored_paths
    write_latest_pointer(output_dir, artifacts)

    return {
        "summary": summary_path,
        "markdown": analysis_md_path,
    }


def create_run_dir() -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return ensure_dir(OUTPUT_ROOT / stamp)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def scoring_config_from_args(args: argparse.Namespace) -> ScoringConfig:
    config = load_scoring_config(getattr(args, "scoring_config", DEFAULT_SCORING_CONFIG_PATH))
    weights = dict(config.scoring_weights)
    weight_arg_map = {
        "technical_skills_weight": "technical_skills",
        "domain_experience_weight": "domain_experience",
        "language_requirement_weight": "language_requirement",
        "visa_sponsorship_weight": "visa_sponsorship",
        "seniority_weight": "seniority",
    }
    for arg_name, weight_key in weight_arg_map.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            weights[weight_key] = float(value)

    filters = dict(config.filters)
    if getattr(args, "min_score_threshold", None) is not None:
        filters["min_score_threshold"] = float(args.min_score_threshold)
    if getattr(args, "include_student_jobs", False):
        filters["exclude_student_jobs"] = False
    if getattr(args, "include_3rd_party", False):
        filters["exclude_3rd_party"] = False

    search = dict(config.search)
    if getattr(args, "max_dynamic_queries", None) is not None:
        search["max_dynamic_queries"] = int(args.max_dynamic_queries)

    return ScoringConfig(
        scoring_weights=normalize_scoring_weights(weights),
        filters=filters,
        language_preferences=config.language_preferences,
        search=search,
        market_strategy=dict(config.market_strategy),
    )


def custom_queries_from_args(args: argparse.Namespace) -> list[str]:
    return normalize_query_list(getattr(args, "query", None))


def resume_tailoring_config_from_args(args: argparse.Namespace) -> ResumeTailoringConfig | None:
    if not getattr(args, "resume_tailoring", False):
        return None
    tailored_count = max(1, int(getattr(args, "resume_tailored_count", 3)))
    return ResumeTailoringConfig(tailored_count=tailored_count)


def command_login(args: argparse.Namespace) -> int:
    ensure_dir(args.profile_dir)
    playwright, context = launch_context(args.profile_dir, headless=False)
    try:
        page = context.new_page()
        page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=45000)
        print(f"LinkedIn profile directory: {args.profile_dir}")
        print("Finish login / 2FA / captcha in the opened Edge window, then press Enter here.")
        input()
        if linkedin_cookie_present(context):
            print("LinkedIn session detected and saved.")
            return 0
        print("Login session was not detected. Try again.", file=sys.stderr)
        return 1
    finally:
        context.close()
        playwright.stop()


def command_collect(args: argparse.Namespace) -> int:
    log_progress("[run] starting collection")
    scoring_config = scoring_config_from_args(args)
    resume = build_resume_profile(args.resume.resolve()) if getattr(args, "resume", None) else None
    queries = build_dynamic_search_queries(resume, scoring_config, custom_queries_from_args(args))
    run_dir = create_run_dir()
    jobs_path = run_dir / "jobs.json"
    write_latest_pointer(run_dir, {"jobs_json": str(jobs_path)})
    payload = collect_jobs(
        profile_dir=args.profile_dir,
        headless=args.headless,
        pages_per_query=args.pages_per_query,
        max_jobs=args.max_jobs,
        delay_seconds=args.delay_seconds,
        queries=queries,
        incremental_path=jobs_path,
        search_location=args.search_location,
        location_keywords=args.location_keywords,
        recent_days=args.recent_days,
    )
    write_json(jobs_path, payload)
    write_latest_pointer(run_dir, {"jobs_json": str(jobs_path)})
    log_progress(f"[run] saved {len(payload['jobs'])} jobs to {jobs_path}")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    log_progress("[run] starting analysis")
    resume_path = args.resume.resolve()
    jobs_path = args.jobs_json.resolve() if args.jobs_json else latest_jobs_json()
    jobs = load_jobs(jobs_path)
    output_dir = jobs_path.parent if args.output_dir is None else ensure_dir(args.output_dir.resolve())
    scoring_config = scoring_config_from_args(args)
    if getattr(args, "semantic_rerank", False):
        log_progress("[run] --semantic-rerank is deprecated and ignored; scoring is rules-only.")
    analysis = build_analysis(
        jobs,
        build_resume_profile(resume_path),
        scoring_config=scoring_config,
        resume_tailoring_config=resume_tailoring_config_from_args(args),
        output_dir=output_dir,
    )
    artifacts = write_analysis_outputs(output_dir, analysis)
    log_progress(f"[run] wrote analysis to {artifacts['markdown']}")
    log_progress(f"[run] wrote summary to {artifacts['summary']}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    log_progress("[run] starting collection")
    scoring_config = scoring_config_from_args(args)
    resume = build_resume_profile(args.resume.resolve())
    queries = build_dynamic_search_queries(resume, scoring_config, custom_queries_from_args(args))
    run_dir = create_run_dir()
    jobs_path = run_dir / "jobs.json"
    write_latest_pointer(run_dir, {"jobs_json": str(jobs_path)})
    payload = collect_jobs(
        profile_dir=args.profile_dir,
        headless=args.headless,
        pages_per_query=args.pages_per_query,
        max_jobs=args.max_jobs,
        delay_seconds=args.delay_seconds,
        queries=queries,
        incremental_path=jobs_path,
        search_location=args.search_location,
        location_keywords=args.location_keywords,
        recent_days=args.recent_days,
    )
    write_json(jobs_path, payload)
    write_latest_pointer(run_dir, {"jobs_json": str(jobs_path)})
    log_progress(f"[run] collection complete, saved jobs to {jobs_path}")
    log_progress("[run] starting analysis")
    if getattr(args, "semantic_rerank", False):
        log_progress("[run] --semantic-rerank is deprecated and ignored; scoring is rules-only.")
    output_dir = run_dir
    token_info = payload.get("token_info")
    analysis = build_analysis(
        payload["jobs"],
        resume,
        scoring_config=scoring_config,
        resume_tailoring_config=resume_tailoring_config_from_args(args),
        output_dir=output_dir,
        token_info=token_info,
    )
    artifacts = write_analysis_outputs(output_dir, analysis)
    log_progress(f"[run] wrote analysis to {artifacts['markdown']}")
    log_progress(f"[run] wrote summary to {artifacts['summary']}")
    return 0


def add_scoring_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scoring-config", type=Path, default=DEFAULT_SCORING_CONFIG_PATH)
    parser.add_argument("--technical-skills-weight", type=float)
    parser.add_argument("--domain-experience-weight", type=float)
    parser.add_argument("--language-requirement-weight", type=float)
    parser.add_argument("--visa-sponsorship-weight", type=float)
    parser.add_argument("--seniority-weight", type=float)
    parser.add_argument("--min-score-threshold", type=float)
    parser.add_argument("--include-student-jobs", action="store_true")
    parser.add_argument("--include-3rd-party", action="store_true")
    parser.add_argument("--max-dynamic-queries", type=int)


def add_query_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--query",
        action="append",
        help="Custom LinkedIn keyword query. Repeat to provide multiple queries and bypass resume-derived queries.",
    )


def add_location_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--search-location",
        default=DEFAULT_SEARCH_LOCATION,
        help="LinkedIn location value used in the job search URL.",
    )
    parser.add_argument(
        "--location-keyword",
        dest="location_keywords",
        action="append",
        help="Accepted job-location keyword. Repeat for city/country aliases from a market profile.",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=7,
        help="Only collect and keep LinkedIn jobs posted within the last N days.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect LinkedIn jobs for a target market and rank them against a resume.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Open Edge with a dedicated LinkedIn profile for manual login.")
    login_parser.add_argument("--profile-dir", type=Path, default=PROFILE_DIR)
    login_parser.set_defaults(func=command_login)

    collect_parser = subparsers.add_parser("collect", help="Collect LinkedIn Germany jobs from a saved session.")
    collect_parser.add_argument("--profile-dir", type=Path, default=PROFILE_DIR)
    collect_parser.add_argument("--resume", type=Path, default=DEFAULT_RESUME)
    collect_parser.add_argument("--pages-per-query", type=int, default=DEFAULT_PAGES_PER_QUERY)
    collect_parser.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS)
    collect_parser.add_argument("--delay-seconds", type=float, default=1.0)
    collect_parser.add_argument("--headless", action="store_true")
    add_query_cli_args(collect_parser)
    add_location_cli_args(collect_parser)
    add_scoring_cli_args(collect_parser)
    collect_parser.set_defaults(func=command_collect)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze collected jobs against the resume.")
    analyze_parser.add_argument("--resume", type=Path, default=DEFAULT_RESUME)
    analyze_parser.add_argument("--jobs-json", type=Path)
    analyze_parser.add_argument("--output-dir", type=Path)
    analyze_parser.add_argument("--semantic-rerank", action="store_true", help="Deprecated no-op; scoring is rules-only.")
    analyze_parser.add_argument("--resume-tailoring", "--resume-workbench", dest="resume_tailoring", action="store_true")
    analyze_parser.add_argument(
        "--resume-tailored-count",
        "--resume-workbench-tailored-count",
        dest="resume_tailored_count",
        type=int,
        default=3,
    )
    analyze_parser.add_argument("--semantic-top-n", type=int, default=10, help="Deprecated no-op.")
    add_scoring_cli_args(analyze_parser)
    analyze_parser.set_defaults(func=command_analyze)

    run_parser = subparsers.add_parser("run", help="Collect jobs and analyze them in one command.")
    run_parser.add_argument("--resume", type=Path, default=DEFAULT_RESUME)
    run_parser.add_argument("--profile-dir", type=Path, default=PROFILE_DIR)
    run_parser.add_argument("--pages-per-query", type=int, default=DEFAULT_PAGES_PER_QUERY)
    run_parser.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS)
    run_parser.add_argument("--delay-seconds", type=float, default=1.0)
    run_parser.add_argument("--headless", action="store_true")
    run_parser.add_argument("--semantic-rerank", action="store_true", help="Deprecated no-op; scoring is rules-only.")
    run_parser.add_argument("--resume-tailoring", "--resume-workbench", dest="resume_tailoring", action="store_true")
    run_parser.add_argument(
        "--resume-tailored-count",
        "--resume-workbench-tailored-count",
        dest="resume_tailored_count",
        type=int,
        default=3,
    )
    run_parser.add_argument("--semantic-top-n", type=int, default=10, help="Deprecated no-op.")
    add_query_cli_args(run_parser)
    add_location_cli_args(run_parser)
    add_scoring_cli_args(run_parser)
    run_parser.set_defaults(func=command_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
