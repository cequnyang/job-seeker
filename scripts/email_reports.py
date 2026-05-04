#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import re
import smtplib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

SUPPORTED_RESUME_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".docx"}
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])",
    re.I,
)
INVISIBLE_CHAR_PATTERN = re.compile(r"[\u200b\u200c\u200d\u2060\uFEFF\u034F]")


@dataclass(frozen=True)
class Candidate:
    resume_path: Path
    name: str
    email: str
    slug: str


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    dry_run: bool
    host: str
    port: int
    sender: str
    username: str = ""
    password: str = ""
    use_starttls: bool = True
    use_ssl: bool = False
    timeout_seconds: float = 30.0
    subject_prefix: str = "LinkedIn JD 匹配报告"

    def validate(self) -> None:
        if not self.enabled or self.dry_run:
            return
        missing = []
        if not self.host:
            missing.append("SMTP_HOST / --smtp-host")
        if not self.sender:
            missing.append("SMTP_FROM / --email-from")
        if missing:
            raise RuntimeError("Email sending is enabled, but missing: " + ", ".join(missing))


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def clean_resume_text(text: str) -> str:
    return INVISIBLE_CHAR_PATTERN.sub("", text or "").strip()


def read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as docx:
        document_xml = docx.read("word/document.xml")
    root = ElementTree.fromstring(document_xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: list[str] = []
    for paragraph in root.findall(".//w:p", ns):
        parts = [node.text or "" for node in paragraph.findall(".//w:t", ns)]
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def read_resume_text(path: Path) -> str:
    path = path.resolve()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:  # pragma: no cover - depends on local runtime
            raise RuntimeError(
                "PDF resume support requires pypdf in the active Python environment. "
                "Run `python3 -m pip install -r requirements.txt` in Linux."
            ) from exc
        reader = PdfReader(str(path))
        return clean_resume_text("\n".join(page.extract_text() or "" for page in reader.pages))
    if suffix == ".docx":
        return clean_resume_text(read_docx_text(path))
    return clean_resume_text(path.read_text(encoding="utf-8", errors="replace"))


def extract_email(text: str) -> str:
    cleaned = clean_resume_text(text)
    match = EMAIL_PATTERN.search(cleaned)
    if match:
        return match.group(1).lower()

    compact = re.sub(r"\s+", "", cleaned)
    match = EMAIL_PATTERN.search(compact)
    return match.group(1).lower() if match else ""


def infer_candidate_name(text: str, resume_path: Path) -> str:
    for raw_line in text.splitlines():
        line = html.unescape(re.sub(r"<[^>]+>", " ", raw_line))
        line = re.sub(r"\s+", " ", line).strip(" |:-\t")
        if not line:
            continue
        if "@" in line or EMAIL_PATTERN.search(line):
            continue
        if re.search(r"\b(phone|mobile|email|github|linkedin|address)\b", line, re.I):
            continue
        if len(line) <= 80:
            return line
    return re.sub(r"[_-]+", " ", resume_path.stem).strip() or "Candidate"


def slugify(value: str, fallback: str = "candidate", max_length: int = 64) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return (slug[:max_length].strip("_") or fallback).lower()


def candidate_from_resume(path: str | Path, require_email: bool = True) -> Candidate:
    resume_path = Path(path).resolve()
    text = read_resume_text(resume_path)
    email = extract_email(text)
    if require_email and not email:
        raise ValueError(f"No email address found in resume: {resume_path}")
    name = infer_candidate_name(text, resume_path)
    return Candidate(
        resume_path=resume_path,
        name=name,
        email=email,
        slug=slugify(f"{name}_{resume_path.stem}", fallback=slugify(resume_path.stem)),
    )


def discover_resume_files(
    single_resume: str | Path | None = None,
    resumes: list[Path] | None = None,
    resume_dir: str | Path | None = None,
) -> list[Path]:
    paths: list[Path] = []
    if resumes:
        paths.extend(Path(path) for path in resumes)
    elif resume_dir:
        root = Path(resume_dir)
        if not root.exists():
            raise FileNotFoundError(f"Resume directory not found: {root}")
        paths.extend(
            path
            for path in sorted(root.iterdir())
            if path.is_file() and path.suffix.lower() in SUPPORTED_RESUME_EXTENSIONS
        )
    elif single_resume:
        paths.append(Path(single_resume))

    resolved: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        item = path.resolve()
        if item in seen:
            continue
        if not item.exists():
            raise FileNotFoundError(f"Resume file not found: {item}")
        if item.suffix.lower() not in SUPPORTED_RESUME_EXTENSIONS:
            raise ValueError(
                f"Unsupported resume file type: {item}. "
                f"Supported: {', '.join(sorted(SUPPORTED_RESUME_EXTENSIONS))}"
            )
        resolved.append(item)
        seen.add(item)
    if not resolved:
        raise RuntimeError("No resume files were found.")
    return resolved


def _existing_files(paths: list[Any]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for value in paths:
        if not value:
            continue
        if isinstance(value, list):
            files.extend(_existing_files(value))
            continue
        path = Path(str(value)).resolve()
        if path.exists() and path.is_file() and path not in seen:
            files.append(path)
            seen.add(path)
    return files


def package_html_report(
    candidate: Candidate,
    output_dir: str | Path,
    report_path: str | Path,
    extra_paths: list[Any] | None = None,
) -> Path:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    package_path = output_dir / f"{candidate.slug}_job_matches.zip"
    report = Path(report_path).resolve()
    files = [report, *_existing_files(extra_paths or [])]

    used_names: set[str] = set()
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in files:
            if not file_path.exists() or not file_path.is_file():
                continue
            if file_path == report:
                arcname = "report.html"
            else:
                arcname = file_path.name
            if arcname in used_names:
                arcname = f"artifacts/{file_path.name}"
            archive.write(file_path, arcname=arcname)
            used_names.add(arcname)
    return package_path


def summarize_top_matches(summary: dict[str, Any], limit: int = 5) -> str:
    matches = summary.get("top_matches", [])
    if not isinstance(matches, list) or not matches:
        return "本次未生成可展示的岗位匹配结果。"
    lines = []
    for idx, job in enumerate(matches[:limit], start=1):
        if not isinstance(job, dict):
            continue
        title = job.get("title") or "Unknown role"
        company = job.get("company") or "Unknown company"
        score = job.get("score", job.get("semantic_score", ""))
        lines.append(f"{idx}. {title} @ {company} - score {score}")
    return "\n".join(lines) if lines else "本次未生成可展示的岗位匹配结果。"


def summarize_token_info(summary: dict[str, Any]) -> str:
    token_info = summary.get("token_info")
    if not token_info:
        return ""
    if token_info.get("expired"):
        return f"⚠️ LinkedIn Token 已过期！请立即运行 `./run/linkedin_login.sh` 重新登录。"
    if token_info.get("warning"):
        days = token_info.get("days_remaining", "?")
        expiry = token_info.get("expiry_date", "unknown")
        return f"⚠️ LinkedIn Token 将在 {days} 天后过期（{expiry}），请尽快运行 `./run/linkedin_login.sh` 刷新会话。"
    days = token_info.get("days_remaining")
    expiry = token_info.get("expiry_date", "unknown")
    return f"✓ LinkedIn Token 有效期正常（剩余 {days} 天，{expiry}）"


def build_email_message(
    config: EmailConfig,
    candidate: Candidate,
    package_path: Path,
    summary: dict[str, Any],
) -> EmailMessage:
    generated_at = summary.get("generated_at") or datetime.utcnow().isoformat(timespec="seconds") + "Z"
    subject = f"{config.subject_prefix} - {candidate.name}"

    token_warning = summarize_token_info(summary)
    token_section = f"\n\n{token_warning}" if token_warning else ""

    body = (
        f"{candidate.name} 你好，\n\n"
        "附件中是根据你的简历生成的 LinkedIn JD 匹配报告压缩包，打开其中的 report.html 即可查看完整结果。\n\n"
        f"生成时间：{generated_at}\n"
        f"分析岗位数：{summary.get('jobs_analyzed', 'N/A')}\n\n"
        "Top matches:\n"
        f"{summarize_top_matches(summary)}\n"
        f"{token_section}\n\n"
        "这封邮件由本地求职报告流程自动发送。"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.sender
    msg["To"] = candidate.email
    msg.set_content(body)
    msg.add_attachment(
        package_path.read_bytes(),
        maintype="application",
        subtype="zip",
        filename=package_path.name,
    )
    return msg


def send_token_expiry_notification(
    config: EmailConfig,
    resume_path: str | Path | None = None,
) -> dict[str, Any]:
    candidate_email = None
    candidate_name = "User"
    if resume_path:
        try:
            candidate_path = Path(resume_path)
            if candidate_path.exists():
                cand = candidate_from_resume(candidate_path, require_email=False)
                candidate_email = cand.email
                candidate_name = cand.name
        except Exception:
            pass

    recipient = candidate_email or config.sender
    if config.dry_run:
        return {"status": "dry-run", "recipient": recipient or "unknown"}
    if not recipient:
        return {"status": "skipped", "reason": "no recipient email"}

    subject = f"{config.subject_prefix} - LinkedIn Token Expired"
    body = (
        f"{candidate_name} 你好，\n\n"
        "你的 LinkedIn 登录会话已失效，无法自动抓取职位信息。\n\n"
        "请运行以下命令重新登录：\n"
        "  ./run/linkedin_login.sh\n\n"
        "登录完成后，你的 LinkedIn 会话将被保存，任务即可继续自动运行。\n\n"
        "—— 求职报告流程自动通知"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.sender
    msg["To"] = recipient
    msg.set_content(body)

    try:
        smtp_cls = smtplib.SMTP_SSL if config.use_ssl else smtplib.SMTP
        with smtp_cls(config.host, config.port, timeout=config.timeout_seconds) as smtp:
            if config.use_starttls and not config.use_ssl:
                smtp.starttls()
            if config.username:
                smtp.login(config.username, config.password)
            smtp.send_message(msg)
        return {"status": "sent", "recipient": recipient}
    except Exception as exc:
        return {"status": "failed", "recipient": recipient, "error": str(exc)}


def send_email_report(
    config: EmailConfig,
    candidate: Candidate,
    package_path: str | Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    config.validate()
    package = Path(package_path).resolve()
    if config.dry_run:
        return {
            "status": "dry-run",
            "recipient": candidate.email,
            "package": str(package),
        }

    message = build_email_message(config, candidate, package, summary)
    smtp_cls = smtplib.SMTP_SSL if config.use_ssl else smtplib.SMTP
    with smtp_cls(config.host, config.port, timeout=config.timeout_seconds) as smtp:
        if config.use_starttls and not config.use_ssl:
            smtp.starttls()
        if config.username:
            smtp.login(config.username, config.password)
        smtp.send_message(message)
    return {
        "status": "sent",
        "recipient": candidate.email,
        "package": str(package),
    }
