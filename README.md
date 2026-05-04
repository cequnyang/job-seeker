# Job Seeker Delivery Workflow

This repository is a rules-based LinkedIn collection and candidate delivery workflow.

It does four things:

1. collect LinkedIn jobs with a saved browser session
2. score jobs against a resume with local rules
3. build one final multi-country HTML report per candidate
4. package and optionally email that report to the email found in the candidate CV

The current repository intentionally keeps only the main delivery path. Older auxiliary analysis HTML paths were removed to keep the codebase smaller and easier to publish.

## Project layout

- `scripts/pipeline/linkedin_jobs.py` — LinkedIn login, collection, resume scoring, summary generation
- `scripts/run.py` — thin wrapper used by the launcher for one candidate x one market; writes `campaign_summary.json`
- `scripts/job_seeker_launcher.py` / `run/run_job_seeker.sh` — main end-to-end launcher for multi-candidate, multi-country delivery
- `scripts/candidate_market_report.py` — final candidate-facing HTML report renderer
- `scripts/email_reports.py` — CV email extraction, zip packaging, SMTP delivery
- `scripts/migration_cockpit.py` — market scoring, policy-source metadata, campaign summary generation
- `scripts/bootstrap_local_candidate_config.py` — generate a redacted local candidate config from one or more resumes
- `config/` — public scoring and market rules
- `samples/` — public sample resumes and tracker templates for demos and tests
- `outputs/` — raw scrape data and intermediate JSON / Markdown artifacts
- `results/` — final candidate-facing HTML reports, task manifests, and report zip packages

## Main workflow

Linux launcher:

```bash
chmod +x ./run/setup_linux_env.sh ./run/linkedin_login.sh ./run/run_job_seeker.sh

./run/setup_linux_env.sh
./run/linkedin_login.sh

./run/run_job_seeker.sh \
  --candidates sample_backend_engineer sample_cpp_qt_engineer \
  --target-countries germany canada australia \
  --recent-days 7 \
  --score-threshold 60
```

With no arguments, `./run/run_job_seeker.sh` prompts for:

- candidate list
- target country list
- recent-day limit
- final report score threshold
- whether email should be dry-run only

The launcher:

- removes task leftovers older than 14 days
- runs each candidate against each selected market
- archives raw per-run data under `outputs/tasks/<task_id>/raw/`
- writes one final HTML report per candidate under `results/tasks/<task_id>/reports/<candidate>/`
- writes the zip package beside that report
- sends the package to the email extracted from the candidate CV when email sending is enabled

If the LinkedIn profile directory is missing or the saved session is no longer valid, the launcher attempts to notify all selected candidates by email.

## Candidate config

The public repo keeps market rules and candidate data separate:

- `config/migration_profiles.yaml` holds campaign defaults, tracker paths, and market rules
- `samples/candidates.sample.yaml` holds de-identified sample candidates
- `local_private/config/candidates.local.yaml` is the intended local-only candidate overlay

Generate a local redacted candidate file from a real resume:

```bash
python scripts/bootstrap_local_candidate_config.py --resume /path/to/resume.pdf
```

By default the script copies the resume into `local_private/CV/` with a sanitized filename and writes `local_private/config/candidates.local.yaml`.

## Validation examples

Dry-run the whole launcher:

```bash
./run/run_job_seeker.sh --candidates sample_cpp_qt_engineer --target-countries germany --dry-run
```

Build artifacts without sending:

```bash
./run/run_job_seeker.sh \
  --candidates sample_cpp_qt_engineer \
  --target-countries germany netherlands \
  --recent-days 14 \
  --score-threshold 55 \
  --email-dry-run
```

Direct single-market internal run:

```bash
python scripts/run.py all --candidate-profile sample_cpp_qt_engineer --market-profile germany --headless
```

Direct collection only:

```bash
python scripts/run.py scrape --candidate-profile sample_cpp_qt_engineer --market-profile germany --headless
```

## LinkedIn login

Standalone login:

```bash
chmod +x ./run/linkedin_login.sh
./run/linkedin_login.sh
```

This script calls `scripts/pipeline/linkedin_jobs.py login` directly and stores the browser session under `.linkedin_profile/` by default.

To use another profile directory:

```bash
LINKEDIN_PROFILE_DIR=.linkedin_profile_work ./run/linkedin_login.sh
```

## Resume file types

Supported resume formats for email extraction and candidate discovery:

```text
.pdf, .md, .markdown, .txt, .docx
```

## Email configuration

Copy `.env.example` to `.env` and set at least:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM`

Sending is explicit. Without `--email-dry-run` or the launcher default send path, no SMTP connection is opened.

## Docker deployment

This project is packaged as a batch-task container, not a long-running web service.

The image includes all non-user project data needed for a fresh deploy: `config/`, `samples/`, `run/`, `scripts/`, and the documentation. User-specific material stays outside the image and is provided through mounted directories.

Prepare a server checkout:

```bash
git clone https://github.com/cequnyang/job-seeker.git
cd job-seeker
cp .env.example .env
mkdir -p .linkedin_profile local_private/CV local_private/config outputs results
```

Build:

```bash
docker compose build
```

Run a sample task:

```bash
docker compose run --rm job-seeker \
  --candidates sample_backend_engineer sample_cpp_qt_engineer \
  --target-countries germany canada australia \
  --recent-days 7 \
  --score-threshold 60
```

Runtime mounts in `docker-compose.yml`:

- `.env`: SMTP and runtime settings.
- `.linkedin_profile/`: saved LinkedIn browser session.
- `local_private/`: real resumes and `local_private/config/candidates.local.yaml`.
- `outputs/`: raw scrape and intermediate artifacts.
- `results/`: final candidate-facing reports and zip packages.

Real candidates should be placed under `local_private/`, not committed into Git:

```text
local_private/
  CV/
    real_candidate_resume.pdf
  config/
    candidates.local.yaml
```

Recommended login flow for Docker:

1. run `./run/linkedin_login.sh` on a machine with a visible browser
2. copy `.linkedin_profile/` to the server
3. mount that directory into the container

## Notes

- The workflow does not require any AI API key.
- The public repo ships with sample candidate data. Put real local material under `local_private/` or another Git-ignored location before running real tasks.
- Scoring, filtering, report generation, packaging, and email sending are local Python logic.
- Search queries are generated from resume skill tags by default; repeat `--query` to override them.
- Scoring weights and filters are loaded from `config/scoring_config.yaml`.
- Policy links are planning metadata only; re-check official sources before acting on immigration or residence matters.
