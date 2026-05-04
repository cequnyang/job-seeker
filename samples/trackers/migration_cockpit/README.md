# Migration Job Search Cockpit

This folder is the planning and tracking layer for the multi-candidate migration job search.

It exists to answer four questions repeatedly:

- Which candidate and market deserves the next block of application effort?
- Which roles are worth tailoring instead of generic applying?
- Is the funnel signal healthy enough to keep the current positioning?
- Is it too early to spend a limited visa or job-search window?

## Current Workflow

Run the main launcher for multi-candidate delivery:

```bash
./run/run_job_seeker.sh \
  --candidates sample_backend_engineer sample_cpp_qt_engineer \
  --target-countries germany canada australia \
  --recent-days 7 \
  --score-threshold 60
```

Run a single candidate x market analysis directly:

```powershell
python scripts\run.py all --candidate-profile sample_backend_engineer --market-profile germany --campaign online_validation_2026_q2 --max-jobs 50 --headless
python scripts\run.py all --candidate-profile sample_cpp_qt_engineer --market-profile australia --campaign online_validation_2026_q2 --max-jobs 50 --headless
```

The current machine-readable market artifact is:

```text
campaign_summary.json
```

Final candidate-facing reports are written under:

```text
results/tasks/<task_id>/reports/<candidate>/
```

Raw and intermediate per-run artifacts are archived under:

```text
outputs/tasks/<task_id>/raw/<candidate>/<market>/
```

## Tracker Contract

`job_search_tracker.csv` is intentionally simple enough to edit by hand, but structured enough for weekly metrics.

Use these status values where possible:

```text
applied
auto_rejected
rejected
recruiter_reply
hr_call
technical_screen
offer
```

The cockpit treats `recruiter_reply`, `hr_call`, `technical_screen`, and `offer` as real replies. After 100 targeted applications, fewer than 3 real replies means the positioning, CV first screen, LinkedIn headline, query set, or outreach channel needs revision before increasing volume.

## Why This Exists

The migration decision is not the same as a job match. A technically strong role can still be a poor move if the country path is unstable, the language requirement is hard, or the employer has no offshore sponsor signal. This layer keeps that reasoning visible instead of hiding it inside one score.
