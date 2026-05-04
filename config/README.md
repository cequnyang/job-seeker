# Config Layout

This repository keeps public workflow rules in Git and private candidate data under `local_private/`.

Tracked public config:

- `migration_profiles.yaml`: campaign defaults, tracker template paths, market names, policy links, and country-level risk metadata.
- `scoring_config.yaml`: shared scoring defaults for job matching, filters, dynamic query generation, and language preferences.
- `*_java_backend.yaml`: market-specific scoring templates for backend/platform candidates.
- `*_cpp_industrial.yaml`: market-specific scoring templates for C++/Qt/industrial-software candidates.

Sample data:

- `samples/candidates.sample.yaml`: de-identified sample candidates used by demos, tests, and documentation. Real candidates should not be added here.

Local-only generated config:

- `local_private/config/candidates.local.yaml`

Generate local candidate config with:

```bash
python scripts/bootstrap_local_candidate_config.py --resume /path/to/resume.pdf
```

Only candidate-specific overlays should be generated locally by default. The rule and market files above stay tracked because they are part of the reproducible workflow: tests, dry runs, Docker runs, and public examples need the same defaults after a fresh clone. If these files were generated only on each user's machine, two runs of the same command could silently use different scoring weights, market metadata, or tracker contracts.

The generated candidate entries are intentionally redacted. They keep the resume path and rule inputs needed by the workflow, but they do not preserve the source person's real name as the config display name.
