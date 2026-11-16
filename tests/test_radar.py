import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("radar", Path(__file__).parents[1] / "scripts/radar.py")
radar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radar)


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "README.md").write_text("# Demo\nPython local app\nAPI_KEY=private-example", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self):
        profile = radar.scan({"projects": [{"id": "app", "path": str(self.root)}]})
        text = "---\nname: example\n---\nTesting local apps"
        sha = "a" * 40
        candidate = {"id": "org/repo:skills/example/SKILL.md", "name": "example", "repo": "org/repo",
                     "path": "skills/example/SKILL.md", "commit": sha, "text": text,
                     "source_url": f"https://github.com/org/repo/blob/{sha}/skills/example/SKILL.md",
                     "sha256": hashlib.sha256(text.encode()).hexdigest(), "verified": True, "checked_at": "2026-11-16T00:00:00Z"}
        decision = {"project_id": "app", "candidate_id": candidate["id"], "decision": "recommend", "compatibility": "yes",
                    "already_covered": False, "project_evidence": ["README.md"], "skill_evidence": "Testing local apps",
                    "reason": "Exercises local app behavior", "constraints": "Content review only", "overlap": "none",
                    "scores": {k: 3 for k in radar.WEIGHTS}, "score_reasons": {k: "Fixture explanation" for k in radar.WEIGHTS}}
        return profile, [candidate], {"decisions": [decision]}

    def test_scan_never_reads_data_env_or_source_body(self):
        (self.root / "data").mkdir()
        (self.root / "data/README.md").write_text("customer-private")
        (self.root / ".env").write_text("SECRET=private")
        (self.root / "main.py").write_text("print('do-not-execute')")
        with patch.object(radar, "github", side_effect=AssertionError("Scanner must stay offline")):
            p = radar.scan_project({"id": "app", "path": str(self.root)})
        value = json.dumps(p)
        self.assertNotIn("customer-private", value)
        self.assertNotIn("do-not-execute", value)
        self.assertNotIn("private-example", value)
        self.assertIn("main.py", p["source_paths"])

    def test_document_escape_and_data_read_rejected(self):
        for document in ("../README.md", "data/customer.md", ".env"):
            with self.subTest(document=document), self.assertRaises(ValueError):
                radar.scan_project({"id": "app", "path": str(self.root), "documents": [document]})

    def test_explicit_symlink_not_followed(self):
        outside = self.root.parent / (self.root.name + "-outside.md")
        outside.write_text("external-secret")
        try:
            try:
                (self.root / "PRODUCT.md").symlink_to(outside)
            except OSError:
                self.skipTest("OS does not permit symlink creation")
            p = radar.scan_project({"id": "app", "path": str(self.root)})
            self.assertNotIn("external-secret", json.dumps(p))
        finally:
            outside.unlink()

    def test_manifest_only_dependencies_and_script_names(self):
        (self.root / "package.json").write_text(json.dumps({"scripts": {"dev": "SECRET=do-not-copy node app"}, "dependencies": {"react": "19.2.0", "private": "https://user:pass@example.test/repo.git"}}))
        p = radar.scan_project({"id": "app", "path": str(self.root)})
        text = json.dumps(p)
        self.assertIn("19.2.0", text)
        self.assertNotIn("do-not-copy", text)
        self.assertNotIn("user:pass", text)

    def test_multiline_skill_description(self):
        header = "\nname: demo\ndescription: |\n  Reads docs.\n  Uses Python.\nmetadata:\n  version: 1\n"
        self.assertEqual(radar.metadata_value(header, "description"), "Reads docs. Uses Python.")

    def test_duplicate_projects_rejected(self):
        with self.assertRaises(ValueError):
            radar.scan({"projects": [{"id": "a", "path": str(self.root)}] * 2})

    def test_scan_truncation_is_visible(self):
        (self.root / "README.md").write_text("x" * 40000)
        p = radar.scan_project({"id": "app", "path": str(self.root)})
        self.assertTrue(p["evidence"][0]["truncated"])
        self.assertEqual(len(p["evidence"][0]["text"]), 32000)

    def test_bad_recommendations_rejected(self):
        for field, value in (("compatibility", "no"), ("compatibility", "unknown"), ("already_covered", True), ("skill_evidence", "invented quote"), ("project_evidence", ["nonexistent.py"])):
            profile, candidates, assessment = self.fixture()
            assessment["decisions"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                radar.validate_assessment(profile, candidates, assessment)

    def test_unverified_archived_or_tampered_candidate_rejected(self):
        for field, value in (("verified", False), ("repo_archived", True), ("text", "tampered"), ("commit", "main"), ("source_url", "https://github.com/org/repo/blob/main/skills/example/SKILL.md")):
            profile, candidates, assessment = self.fixture()
            candidates[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                radar.validate_assessment(profile, candidates, assessment)

    def test_report_escapes_remote_text_and_uses_generic_title(self):
        profile, candidates, assessment = self.fixture()
        assessment["title"] = "Demo radar"
        assessment["summary"] = '<script>alert("x")</script>'
        assessment["decisions"][0]["reason"] = '<img src=x onerror=alert(1)>'
        out = self.root / "report"
        radar.report(profile, candidates, assessment, out)
        content = (out / "report.html").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertNotIn("<img ", content)
        self.assertIn("Demo radar", content)
        self.assertNotIn("Private project name", content)

    def test_search_records_partial_failure_and_deduplicates(self):
        repo = {"full_name": "org/repo", "stargazers_count": 100}
        with patch.object(radar, "github", side_effect=[{"items": [repo, repo], "total_count": 1, "incomplete_results": True}, RuntimeError("GitHub HTTP 403")]):
            result = radar.search({"queries": ["python skill", "react skill"]})
        self.assertEqual(len(result["repositories"]), 1)
        self.assertEqual(len(result["failures"]), 1)
        self.assertTrue(result["queries"][0]["incomplete_results"])

    def test_inspect_pins_commit_before_fetching(self):
        sha = "b" * 40
        text = "---\nname: demo\n---\nRead data"
        responses = [{"default_branch": "main", "stargazers_count": 20, "pushed_at": "2026-11-16", "archived": False},
                     {"sha": sha}, {"encoding": "base64", "size": len(text), "content": base64.b64encode(text.encode()).decode()}]
        with patch.object(radar, "github", side_effect=responses) as call:
            candidate = radar.inspect_skill("org/repo", "skills/demo/SKILL.md")
        self.assertIn("?ref=" + sha, call.call_args_list[-1].args[0])
        self.assertEqual(candidate["commit"], sha)
        self.assertEqual(candidate["sha256"], hashlib.sha256(text.encode()).hexdigest())

    def test_scores_require_finite_bounded_numbers(self):
        for value in (-1, 6, float("nan"), True):
            scores = {k: 3 for k in radar.WEIGHTS}
            scores["fit"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                radar.score(scores)


if __name__ == "__main__":
    unittest.main()
