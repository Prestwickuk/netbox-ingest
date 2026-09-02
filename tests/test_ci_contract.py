from pathlib import Path
import json
import re
import unittest

import yaml


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


class DeploymentHandoffTests(unittest.TestCase):
    """The publish workflow's handoff to loft-sh/loft-prod.

    Production ran on a stale digest until somebody hand-edited a loft-prod PR.
    These checks pin the three behaviours that make the automated handoff safe
    to leave unattended: a successful publish hands over the digest it actually
    pushed, a failed publish hands over nothing, and a redelivery of the same
    digest cannot become a second PR.
    """

    def setUp(self) -> None:
        self.workflow = yaml.safe_load((WORKFLOWS / "publish.yaml").read_text())
        self.deploy = self.workflow["jobs"]["deploy"]

    def test_successful_publish_hands_over_the_digest_it_pushed(self) -> None:
        # The digest has to come from the build step's own output. Re-resolving
        # the tag later would race a subsequent push and could deploy an image
        # this run never built.
        self.assertEqual(
            self.workflow["jobs"]["publish"]["outputs"]["digest"],
            "${{ steps.build.outputs.digest }}",
        )

        dispatch = self._dispatch_step()
        self.assertEqual(dispatch["with"]["target-repo"], "loft-sh/loft-prod")
        self.assertEqual(dispatch["with"]["event-type"], "update-harold-version")

        payload = json.loads(dispatch["with"]["payload"])
        self.assertEqual(payload["digest"], "${{ needs.publish.outputs.digest }}")

    def test_failed_publish_creates_no_deployment_pr(self) -> None:
        # `needs` is the guard. GitHub skips a needing job when its dependency
        # fails or is cancelled, so there is no path from a failed publish to a
        # dispatch as long as nothing overrides that with always().
        self.assertEqual(self.deploy["needs"], "publish")
        self.assertNotIn("always()", self.deploy.get("if", ""))

        # workflow_dispatch can target any branch, so the handoff is limited to
        # refs that represent a real release.
        self.assertIn("refs/heads/main", self.deploy["if"])
        self.assertIn("refs/tags/v", self.deploy["if"])

    def test_duplicate_delivery_cannot_open_a_second_pr(self) -> None:
        # Idempotency is the receiver's job: loft-prod rolls one fixed branch
        # forward, and an unchanged digest yields no diff and therefore no PR.
        # What has to hold here is that this side sends the digest alone and
        # never anything per-run that would make a redelivery look distinct.
        payload = json.loads(self._dispatch_step()["with"]["payload"])
        self.assertEqual(
            set(payload),
            {"digest", "sha", "ref"},
            "a per-run field in the payload would defeat receiver-side deduplication",
        )
        self.assertNotIn("github.run_id", str(payload))
        self.assertNotIn("github.run_number", str(payload))

    def test_dispatch_credential_can_reach_another_repository(self) -> None:
        # secrets.GITHUB_TOKEN is scoped to this repository and cannot dispatch
        # into loft-prod. Passing it would fail only at runtime, on a release.
        env = self._dispatch_step()["env"]
        self.assertEqual(env["GH_TOKEN"], "${{ secrets.GH_ACCESS_TOKEN }}")

    def test_handoff_reuses_the_shared_dispatch_action(self) -> None:
        self.assertEqual(
            self._dispatch_step()["uses"].split("#")[0].strip(),
            "loft-sh/github-actions/.github/actions/"
            "repository-dispatch@repository-dispatch/v1",
        )

    def test_handoff_needs_no_write_permission_in_this_repository(self) -> None:
        # The PR is opened in loft-prod, so this job reads and nothing more.
        self.assertEqual(self.deploy["permissions"], {"contents": "read"})

    def _dispatch_step(self) -> dict:
        return next(
            step
            for step in self.deploy["steps"]
            if "repository-dispatch" in step.get("uses", "")
        )


if __name__ == "__main__":
    unittest.main()
