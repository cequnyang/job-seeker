# Sample Data

This directory contains the public, de-identified inputs that make the workflow runnable after a fresh clone or Docker build.

It exists so readers can inspect the expected data shape without seeing any real candidate material. Docker images include this directory, while user-specific resumes and candidate overlays stay outside the image under `local_private/`.

## Contents

- `candidates.sample.yaml`: sample candidate profiles used by demos, tests, and documentation.
- `sample_backend_engineer.md`: sample resume for the backend/platform role family.
- `sample_cpp_qt_engineer.md`: sample resume for the C++/Qt/industrial-software role family.
- `trackers/migration_cockpit/job_search_tracker.csv`: sample tracker template for application funnel metrics.

## How It Is Used

Sample candidates can be run directly:

```bash
./run/run_job_seeker.sh --candidates sample_cpp_qt_engineer --target-countries germany --dry-run
```

`samples/candidates.sample.yaml` points each sample candidate at the matching sample resume. The scoring and market rules still live in `config/`; this directory only holds example input data and tracker templates.

## Real Candidate Data

Do not put real resumes or identifying candidate configuration in `samples/`.

For real local or server runs, place private material under:

```text
local_private/
  CV/
    real_candidate_resume.pdf
  config/
    candidates.local.yaml
```

Generate the local candidate overlay with:

```bash
python scripts/bootstrap_local_candidate_config.py --resume /path/to/resume.pdf
```
