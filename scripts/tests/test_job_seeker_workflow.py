import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = ROOT / "scripts"
RUN_PATH = SCRIPTS_ROOT / "run.py"
PIPELINE_PATH = SCRIPTS_ROOT / "pipeline" / "linkedin_jobs.py"
COCKPIT_PATH = SCRIPTS_ROOT / "migration_cockpit.py"
LAUNCHER_PATH = SCRIPTS_ROOT / "job_seeker_launcher.py"
CANDIDATE_REPORT_PATH = SCRIPTS_ROOT / "candidate_market_report.py"
BOOTSTRAP_PATH = SCRIPTS_ROOT / "bootstrap_local_candidate_config.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class JobSeekerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_module(RUN_PATH, "job_runner")
        cls.pipeline = load_module(PIPELINE_PATH, "linkedin_jobs")
        cls.cockpit = load_module(COCKPIT_PATH, "migration_cockpit")
        cls.launcher = load_module(LAUNCHER_PATH, "job_seeker_launcher")
        cls.candidate_report = load_module(CANDIDATE_REPORT_PATH, "candidate_market_report")
        cls.bootstrap = load_module(BOOTSTRAP_PATH, "bootstrap_local_candidate_config")

    def test_runner_defaults_to_all_without_legacy_dashboard_command(self):
        args = self.runner.build_parser().parse_args([])
        self.assertEqual(args.command, "all")
        self.assertEqual(args.resume.suffix.lower(), ".md")
        self.assertNotIn("dashboard", self.runner.build_parser()._subparsers._group_actions[0].choices)

    def test_load_profiles_merges_public_sample_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            original_local_config = self.cockpit.DEFAULT_LOCAL_CANDIDATES_CONFIG
            self.cockpit.DEFAULT_LOCAL_CANDIDATES_CONFIG = Path(td) / "missing.local.yaml"
            try:
                config = self.cockpit.load_profiles()
            finally:
                self.cockpit.DEFAULT_LOCAL_CANDIDATES_CONFIG = original_local_config
        self.assertIn("sample_backend_engineer", config["candidates"])
        self.assertIn("germany", config["markets"])
        self.assertEqual(self.cockpit.default_candidate_keys(config), ["sample_backend_engineer", "sample_cpp_qt_engineer"])

    def test_pipeline_analysis_outputs_only_summary_and_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td)
            artifacts = self.pipeline.write_analysis_outputs(
                output_dir,
                {
                    "generated_at": "2026-05-04T12:00:00Z",
                    "jobs_analyzed": 0,
                    "resume": {"path": str(ROOT / "samples" / "sample_cpp_qt_engineer.md"), "strengths": [], "years_experience": 8, "english_level": "B2"},
                    "rule_analysis": {"ranked_jobs": []},
                    "resume_tailoring_cluster": {},
                    "top_matches": [],
                    "resume_tailoring": {},
                },
            )
            self.assertEqual(set(artifacts.keys()), {"summary", "markdown"})
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertTrue((output_dir / "analysis.md").exists())

    def test_campaign_summary_update_drops_cockpit_html_pointer(self):
        latest = {"jobs_json": "jobs.json", "cockpit_html": "obsolete.html"}
        updated = self.cockpit.update_latest_with_campaign_summary(latest, Path("campaign_summary.json"))
        self.assertEqual(updated["campaign_summary_json"], "campaign_summary.json")
        self.assertNotIn("cockpit_html", updated)

    def test_launcher_only_reads_campaign_summary_artifact(self):
        parsed = self.launcher.parse_artifacts(
            "CAMPAIGN_SUMMARY: /tmp/campaign_summary.json\nDASHBOARD: /tmp/dashboard.html\nCOCKPIT: /tmp/cockpit.html\n"
        )
        self.assertEqual(parsed, {"campaign_summary": "/tmp/campaign_summary.json"})

    def test_candidate_report_renders_country_tabs_and_policy_sources(self):
        candidate = self.launcher.Candidate(
            resume_path=ROOT / "samples" / "sample_cpp_qt_engineer.md",
            name="Sample C++ Qt Engineer",
            email="sample.cppqt@example.com",
            slug="sample_cpp_qt_engineer",
        )
        market_results = [
            {
                "market_key": "germany",
                "market_name": "Germany",
                "country": "Germany",
                "immigration_path": "EU Blue Card",
                "job_search_mode": "Local",
                "risk_notes": ["German language can be a filter."],
                "policy_sources": [{"label": "Make it in Germany", "url": "https://example.com", "last_reviewed": "2026-05-01"}],
                "jobs": [{"title": "C++ Engineer", "company": "Acme", "location": "Berlin", "success_score": 82, "score": 78, "job_url": "https://example.com/job"}],
            },
            {
                "market_key": "canada",
                "market_name": "Canada",
                "country": "Canada",
                "immigration_path": "Express Entry",
                "job_search_mode": "Hybrid",
                "risk_notes": [],
                "policy_sources": [],
                "jobs": [],
            },
        ]
        html = self.candidate_report.render_candidate_market_report(candidate, market_results, 60, 7, "20260504_120000")
        self.assertIn("Germany", html)
        self.assertIn("Canada", html)
        self.assertIn("Make it in Germany", html)
        self.assertIn("Open job source", html)

    def test_bootstrap_local_candidate_config_writes_redacted_entry(self):
        with tempfile.TemporaryDirectory() as td:
            output_path = Path(td) / "candidates.local.yaml"
            copy_dir = Path(td) / "CV"
            rc = self.bootstrap.main(
                [
                    "--resume",
                    str(ROOT / "samples" / "sample_cpp_qt_engineer.md"),
                    "--output",
                    str(output_path),
                    "--resume-copy-dir",
                    str(copy_dir),
                ]
            )
            self.assertEqual(rc, 0)
            payload = self.cockpit.load_yaml_object(output_path)
            candidates = payload.get("candidates", {})
            self.assertEqual(len(candidates), 1)
            key, entry = next(iter(candidates.items()))
            self.assertTrue(key.startswith("local_cpp_qt_engineer_"))
            self.assertTrue(str(entry["name"]).startswith("Local Candidate"))
            self.assertEqual(entry["aliases"], [key])
            self.assertNotIn("Sample C++ Qt Engineer", entry["name"])

    def test_bootstrap_import_does_not_require_playwright(self):
        script = f"""
import importlib.abc
import sys

class BlockPlaywright(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise ModuleNotFoundError("No module named 'playwright'")
        return None

sys.meta_path.insert(0, BlockPlaywright())
sys.path.insert(0, {str(SCRIPTS_ROOT)!r})
import bootstrap_local_candidate_config
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
