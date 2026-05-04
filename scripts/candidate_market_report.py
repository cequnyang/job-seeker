#!/usr/bin/env python3
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from email_reports import Candidate


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def html_id(value: Any) -> str:
    ident = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return ident or "market"


def success_score(job: dict[str, Any]) -> float:
    return safe_float(
        job.get("success_score", job.get("migration_score", job.get("score", job.get("base_score", 0.0))))
    )


def sorted_visible_jobs(jobs: list[dict[str, Any]], threshold: float, limit: int = 20) -> list[dict[str, Any]]:
    visible = [job for job in jobs if success_score(job) >= threshold]
    visible.sort(key=lambda job: (success_score(job), safe_float(job.get("score"))), reverse=True)
    return visible[:limit]


def render_badges(items: list[Any], class_name: str = "") -> str:
    badges = [f'<span class="badge {esc(class_name)}">{esc(item)}</span>' for item in items if str(item).strip()]
    return "".join(badges) or '<span class="muted">None captured</span>'


def render_job_card(job: dict[str, Any], index: int) -> str:
    score = success_score(job)
    base_score = safe_float(job.get("score"))
    breakdown = job.get("market_score_breakdown") if isinstance(job.get("market_score_breakdown"), dict) else {}
    jd_analysis = job.get("jd_analysis_zh") if isinstance(job.get("jd_analysis_zh"), dict) else {}
    rule_analysis = job.get("rule_analysis") if isinstance(job.get("rule_analysis"), dict) else {}
    url = job.get("job_url") or job.get("apply_url") or ""
    strengths = as_list(job.get("strengths")) or as_list(jd_analysis.get("strengths_to_emphasize_zh"))
    gaps = as_list(job.get("gaps")) or as_list(jd_analysis.get("gaps_zh", {}).get("major")) if isinstance(jd_analysis.get("gaps_zh"), dict) else as_list(job.get("gaps"))
    action = job.get("next_action") or jd_analysis.get("tailoring_strategy_zh") or job.get("recommendation") or ""
    if isinstance(action, list):
        action = "；".join(str(item) for item in action[:3])

    breakdown_html = "".join(
        f'<span class="mini">{esc(label)} {safe_float(value):.0f}</span>'
        for label, value in breakdown.items()
    )
    return f"""
    <article class="job-card">
      <div class="job-head">
        <div class="rank">#{index}</div>
        <div class="job-title-block">
          <h3>{esc(job.get('title', 'Untitled role'))}</h3>
          <p>{esc(job.get('company', 'Unknown company'))} · {esc(job.get('location', 'Unknown location'))}</p>
        </div>
        <div class="score-box">
          <span>Success</span>
          <strong>{score:.1f}</strong>
          <small>JD {base_score:.1f}</small>
        </div>
      </div>
      <div class="job-meta">
        <span>{esc(job.get('posted_text', 'posted date unavailable'))}</span>
        <span>{esc(job.get('strategic_recommendation', job.get('recommendation', 'review')))}</span>
        <span>{esc(job.get('visa_sponsorship', 'visa signal not stated'))}</span>
        <span>{esc(jd_analysis.get('language_requirement_zh', job.get('language_requirement', 'language not stated')))}</span>
      </div>
      <p class="verdict">{esc(jd_analysis.get('overall_verdict_zh', rule_analysis.get('benchmark_summary_zh', 'Rule-based fit details were not captured for this job.')))}</p>
      <div class="split">
        <div>
          <h4>Fit evidence</h4>
          <div>{render_badges(strengths, 'good')}</div>
        </div>
        <div>
          <h4>Gaps / risks</h4>
          <div>{render_badges(gaps or as_list(job.get('flags')), 'warn')}</div>
        </div>
      </div>
      <p class="action"><strong>Next action:</strong> {esc(action or 'Manual review before applying.')}</p>
      {f'<div class="breakdown">{breakdown_html}</div>' if breakdown_html else ''}
      {f'<a class="job-link" href="{esc(url)}" target="_blank" rel="noreferrer">Open job source</a>' if url else ''}
    </article>
    """


def render_market_panel(market_result: dict[str, Any], threshold: float, index: int) -> str:
    panel_id = f"panel-{html_id(market_result.get('market_key') or index)}"
    jobs = sorted_visible_jobs(as_list(market_result.get("jobs")), threshold)
    cards = "".join(render_job_card(job, idx) for idx, job in enumerate(jobs, start=1))
    return f"""
    <section id="{esc(panel_id)}" class="market-panel">
      <div class="market-head">
        <div>
          <h2>{esc(market_result.get('market_name') or market_result.get('country') or market_result.get('market_key'))}</h2>
          <p>{esc(market_result.get('immigration_path', 'No immigration path metadata configured.'))}</p>
        </div>
        <div class="market-stat">
          <span>Included jobs</span>
          <strong>{len(jobs)}</strong>
        </div>
      </div>
      <div class="jobs">{cards or '<div class="empty">No jobs met the configured score threshold for this country.</div>'}</div>
    </section>
    """


def render_policy_footer(market_results: list[dict[str, Any]]) -> str:
    blocks = []
    for market in market_results:
        sources = []
        for source in as_list(market.get("policy_sources")):
            stale = " stale" if source.get("stale_warning") else ""
            sources.append(
                f"""
                <li>
                  <a href="{esc(source.get('url', '#'))}" target="_blank" rel="noreferrer">{esc(source.get('label', 'Official source'))}</a>
                  <span class="source-date{stale}">reviewed {esc(source.get('last_reviewed', 'unreviewed'))}</span>
                </li>
                """
            )
        risk_notes = "".join(f"<li>{esc(item)}</li>" for item in as_list(market.get("risk_notes")))
        blocks.append(
            f"""
            <section class="policy-block">
              <h3>{esc(market.get('market_name') or market.get('country') or market.get('market_key'))}</h3>
              <p><strong>Residence route:</strong> {esc(market.get('immigration_path', 'Not configured'))}</p>
              <p><strong>Search mode:</strong> {esc(market.get('job_search_mode', 'Not configured'))}</p>
              <div class="policy-grid">
                <div><h4>Risk notes</h4><ul>{risk_notes or '<li>No risk notes configured.</li>'}</ul></div>
                <div><h4>Official sources</h4><ul>{''.join(sources) or '<li>No official source configured.</li>'}</ul></div>
              </div>
            </section>
            """
        )
    return "\n".join(blocks)


def render_candidate_market_report(
    candidate: Candidate,
    market_results: list[dict[str, Any]],
    threshold: float,
    recent_days: int,
    task_id: str,
) -> str:
    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    tab_rules = []
    inputs = []
    labels = []
    panels = []
    for index, market in enumerate(market_results, start=1):
        market_key = html_id(market.get("market_key") or index)
        tab_id = f"tab-{market_key}"
        panel_id = f"panel-{market_key}"
        inputs.append(f'<input type="radio" id="{esc(tab_id)}" name="market-tab" {"checked" if index == 1 else ""}>')
        labels.append(f'<label for="{esc(tab_id)}">{esc(market.get("market_name") or market.get("country") or market.get("market_key"))}</label>')
        panels.append(render_market_panel({**market, "market_key": market_key}, threshold, index))
        tab_rules.append(f"#{tab_id}:checked ~ .market-panels #{panel_id} {{ display: block; }}")
        tab_rules.append(f"#{tab_id}:checked ~ .tab-labels label[for='{tab_id}'] {{ background: #172033; color: #fff; border-color: #172033; }}")

    total_jobs = sum(len(sorted_visible_jobs(as_list(market.get("jobs")), threshold)) for market in market_results)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{esc(candidate.name)} - Multi-Country Job Report</title>
<style>
  :root {{
    --bg: #f5f7fb;
    --panel: #fff;
    --ink: #172033;
    --muted: #647084;
    --line: #d9dee8;
    --good: #1f7a4f;
    --warn: #a15c00;
    --accent: #1d6f8f;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: "Segoe UI", Arial, sans-serif; }}
  a {{ color: var(--accent); text-decoration: none; }}
  .page {{ max-width: 1240px; margin: 0 auto; padding: 28px; }}
  .hero {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 24px; margin-bottom: 18px; }}
  h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: 0; }}
  .subtitle {{ color: var(--muted); line-height: 1.6; }}
  .metric-row {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }}
  .metric {{ border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: #fbfcff; }}
  .metric span, .market-stat span, .score-box span {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
  .metric strong, .market-stat strong, .score-box strong {{ display: block; margin-top: 6px; font-size: 24px; }}
  .tabs input {{ position: absolute; opacity: 0; pointer-events: none; }}
  .tab-labels {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 18px 0; }}
  .tab-labels label {{ border: 1px solid var(--line); background: var(--panel); border-radius: 999px; padding: 9px 14px; cursor: pointer; color: var(--muted); font-weight: 600; }}
  .market-panel {{ display: none; }}
  {" ".join(tab_rules)}
  .market-head, .job-head {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }}
  .market-head {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 14px; }}
  .market-head h2 {{ margin: 0 0 6px; }}
  .market-head p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
  .market-stat, .score-box {{ text-align: right; min-width: 116px; }}
  .job-card, .policy-block {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; margin-bottom: 12px; }}
  .rank {{ width: 38px; height: 38px; border-radius: 8px; display: grid; place-items: center; background: #edf4f8; color: var(--accent); font-weight: 700; flex: none; }}
  .job-title-block {{ flex: 1; min-width: 0; }}
  .job-title-block h3 {{ margin: 0; font-size: 18px; }}
  .job-title-block p, .verdict, .action {{ color: var(--muted); line-height: 1.6; }}
  .job-meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }}
  .job-meta span, .mini, .badge {{ display: inline-flex; border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; font-size: 12px; color: var(--muted); background: #fbfcff; }}
  .badge.good {{ color: var(--good); border-color: rgba(31,122,79,.25); }}
  .badge.warn {{ color: var(--warn); border-color: rgba(161,92,0,.25); }}
  .split, .policy-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  h4 {{ margin: 0 0 8px; }}
  .breakdown {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
  .job-link {{ display: inline-block; margin-top: 12px; font-weight: 700; }}
  .empty {{ background: var(--panel); border: 1px dashed var(--line); border-radius: 8px; padding: 18px; color: var(--muted); }}
  .policies {{ margin-top: 22px; }}
  .policies h2 {{ margin-bottom: 8px; }}
  .policy-block h3 {{ margin: 0 0 10px; }}
  .policy-block p {{ color: var(--muted); line-height: 1.55; }}
  .policy-block li {{ margin-bottom: 8px; line-height: 1.5; }}
  .source-date {{ color: var(--good); margin-left: 8px; font-size: 12px; }}
  .source-date.stale {{ color: var(--warn); }}
  .muted {{ color: var(--muted); }}
  @media (max-width: 860px) {{
    .page {{ padding: 18px; }}
    .metric-row, .split, .policy-grid {{ grid-template-columns: 1fr; }}
    .market-head, .job-head {{ display: block; }}
    .score-box, .market-stat {{ text-align: left; margin-top: 12px; }}
  }}
</style>
</head>
<body>
<main class="page">
  <section class="hero">
    <h1>{esc(candidate.name)} - 多国家岗位成功率报告</h1>
    <p class="subtitle">本报告由本地规则引擎生成，不调用 AI API。岗位按国家分组，每个国家最多收录 20 个高于阈值的岗位，并按规则估算的投递成功率从高到低排列。</p>
    <div class="metric-row">
      <div class="metric"><span>Candidate email</span><strong>{esc(candidate.email or 'missing')}</strong></div>
      <div class="metric"><span>Countries</span><strong>{len(market_results)}</strong></div>
      <div class="metric"><span>Included jobs</span><strong>{total_jobs}</strong></div>
      <div class="metric"><span>Threshold / days</span><strong>{threshold:.1f} / {recent_days}d</strong></div>
    </div>
    <p class="subtitle">生成时间：{esc(generated_at)}；任务：{esc(task_id)}</p>
  </section>

  <section class="tabs">
    {''.join(inputs)}
    <div class="tab-labels">{''.join(labels)}</div>
    <div class="market-panels">{''.join(panels)}</div>
  </section>

  <section class="policies">
    <h2>就业居留 / 移民政策细则与官方来源</h2>
    <p class="muted">这里展示配置中维护的官方政策入口、居留路径和风险提示。它用于投递规划，不构成法律意见；真正行动前应重新打开官方来源核验。</p>
    {render_policy_footer(market_results)}
  </section>
</main>
</body>
</html>"""


def write_candidate_market_report(
    candidate: Candidate,
    market_results: list[dict[str, Any]],
    output_path: str | Path,
    threshold: float,
    recent_days: int,
    task_id: str,
) -> Path:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_candidate_market_report(candidate, market_results, threshold, recent_days, task_id),
        encoding="utf-8",
    )
    return output
