from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class PipelineContractTests(unittest.TestCase):
    def test_ci_runs_tests_and_builds_the_production_platform(self) -> None:
        workflow = (WORKFLOWS / "ci.yaml").read_text()

        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("platforms: linux/amd64", workflow)
        self.assertIn("push: false", workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_publish_requires_tests_and_pushes_a_verifiable_image(self) -> None:
        workflow = (WORKFLOWS / "publish.yaml").read_text()

        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("IMAGE_NAME: loft-sh/harold", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("platforms: linux/amd64", workflow)
        self.assertIn("push: true", workflow)
        self.assertIn("sbom: true", workflow)
        self.assertIn("provenance: mode=max", workflow)

    def test_third_party_actions_are_pinned_to_full_commit_shas(self) -> None:
        uses_pattern = re.compile(r"uses:\s*([^@\s]+)@([^\s#]+)")

        for path in WORKFLOWS.glob("*.y*ml"):
            for action, ref in uses_pattern.findall(path.read_text()):
                if action.startswith("loft-sh/github-actions/"):
                    continue
                self.assertRegex(
                    ref,
                    r"^[0-9a-f]{40}$",
                    f"{path.name}: {action}@{ref} is not SHA-pinned",
                )

    def test_obsolete_conda_workflow_is_removed(self) -> None:
        self.assertFalse((WORKFLOWS / "python-package-conda.yml").exists())


if __name__ == "__main__":
    unittest.main()
