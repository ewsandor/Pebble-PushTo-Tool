import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
MANUAL_WORKFLOW = (ROOT / ".github/workflows/manual-publish.yml").read_text(encoding="utf-8")


def job(name):
    match = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)", WORKFLOW)
    if not match:
        raise AssertionError(f"Missing job: {name}")
    return match.group(1)


def shell_step(name, step):
    match = re.search(
        rf"(?ms)^      - name: {re.escape(step)}\n.*?^        run: \|\n"
        rf"(.*?)(?=^      - name:|^  [\w-]+:|\Z)", job(name)
    )
    if not match:
        raise AssertionError(f"Missing shell step: {step}")
    return textwrap.dedent(match.group(1))


def manual_shell_step(step):
    match = re.search(
        rf"(?ms)^      - name: {re.escape(step)}\n.*?^        run: \|\n"
        rf"(.*?)(?=^      - name:|^  [\w-]+:|\Z)", MANUAL_WORKFLOW
    )
    if not match:
        raise AssertionError(f"Missing manual workflow shell step: {step}")
    return textwrap.dedent(match.group(1))


class ReleaseWorkflowTests(unittest.TestCase):
    def test_manual_ticket_done_records_successful_deployment(self):
        self.assertIn("types: [closed]", MANUAL_WORKFLOW)
        validate = re.search(r"(?m)^  validate-ticket:\n(.*?)(?=^  deployment:)", MANUAL_WORKFLOW, re.S).group(1)
        self.assertIn("github.event.issue.state_reason == 'completed'", validate)
        self.assertIn("github.event.issue.closed_by.login == 'ewsandor'", validate)
        self.assertIn("Publish\\ PushTo\\ Tool\\ v", MANUAL_WORKFLOW)
        self.assertIn('index("ewsandor") != null', MANUAL_WORKFLOW)
        self.assertIn("Pebble Appstore", MANUAL_WORKFLOW)
        self.assertIn("needs: validate-ticket", MANUAL_WORKFLOW)
        self.assertIn("url: ${{ github.event.issue.html_url }}", MANUAL_WORKFLOW)
        self.assertIn("deployments: write", MANUAL_WORKFLOW)
        self.assertNotIn("APPSTORE_API_BASE", MANUAL_WORKFLOW)

    def test_not_planned_close_does_not_create_deployment(self):
        condition = re.search(r"(?m)^    if: (.+)$", MANUAL_WORKFLOW).group(1)
        for reason, expected in (("completed", True), ("not_planned", False)):
            expression = condition.replace(
                "github.event.issue.state_reason", repr(reason)
            ).replace("github.event.issue.closed_by.login", repr("ewsandor")).replace("&&", "and")
            with self.subTest(reason=reason):
                self.assertEqual(eval(expression, {"__builtins__": {}}), expected)

    def test_manual_ticket_validation_requires_exact_assigned_release_ticket(self):
        script = manual_shell_step("Validate manual publishing ticket")
        for title, assignees, succeeds in (
                ("Publish PushTo Tool v0.4.0", '["ewsandor"]', True),
                ("Publish PushTo Tool v0.4.0", '[]', False),
                ("Other Publish PushTo Tool v0.4.0", '["ewsandor"]', False)):
            with self.subTest(title=title, assignees=assignees), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                process = subprocess.run(
                    ["bash", "-c", script],
                    env={**os.environ, "TITLE": title, "ASSIGNEES": assignees,
                         "GITHUB_OUTPUT": str(output)},
                    capture_output=True, text=True,
                )
                self.assertEqual(process.returncode == 0, True, process.stderr)
                if succeeds:
                    self.assertEqual(output.read_text().strip(), "version=0.4.0")
                else:
                    self.assertFalse(output.exists())

    def test_release_version_matches_package_metadata(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lockfile = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lockfile["version"], package["version"])
        self.assertEqual(lockfile["packages"][""]["version"], package["version"])

    def test_appstore_oidc_and_identity_are_configured_consistently(self):
        publish = job("publish")
        self.assertIn("id-token: write", publish)
        self.assertIn("APPSTORE_API_BASE: https://appstore-api.repebble.com", WORKFLOW)
        self.assertIn("isPublished=true", publish)
        self.assertIn("continue-on-error: true", publish)
        self.assertIn("steps.publish.outcome", publish)

    def test_store_listing_identity_matches_package(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        pebble = package["pebble"]
        self.assertEqual(pebble["displayName"], "PushTo Tool")
        self.assertRegex(pebble["uuid"], r"(?i)^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")

    def test_versioned_pbw_artifact_uses_current_name_end_to_end(self):
        self.assertIn(
            'cp build/*.pbw "dist/Pebble-PushTo-Tool-${{ steps.meta.outputs.version }}.pbw"',
            job("build"))
        self.assertIn(
            '--pbw "dist/Pebble-PushTo-Tool-${{ needs.build.outputs.version }}.pbw"',
            job("store-screenshots"))
        self.assertIn(
            '-F "pbwFile=@dist/Pebble-PushTo-Tool-$VERSION.pbw"',
            job("publish"))
        self.assertNotIn("astroslop", WORKFLOW)

    def test_release_precedes_tests_and_store_work_is_gated(self):
        release = job("release")
        self.assertIn("needs: [build]", release)
        self.assertIn("github.event_name == 'push' && github.ref_type == 'tag'", release)
        self.assertIn("name: pbw-release", release)
        self.assertIn("files: dist/*.pbw", release)
        self.assertIn("generate_release_notes: true", release)
        self.assertIn("needs: [build, test, release]", job("comprehensive"))
        self.assertIn("needs: [build, test, release, comprehensive]", job("comprehensive-gate"))
        self.assertIn("needs: [build, release, comprehensive-gate]", job("store-screenshots"))
        self.assertIn("needs: [build, release, comprehensive-gate, store-screenshots]", job("publish"))
        for name in ("comprehensive", "store-screenshots", "publish"):
            self.assertNotRegex(job(name), r"(?m)^    if: .*always\(\)")

    def test_manual_issue_is_guarded_and_artifacts_are_linked(self):
        issue = job("manual-publishing-issue")
        for condition in ("needs.build.result == 'success'",
                          "needs.comprehensive-gate.result == 'success'",
                          "needs.store-screenshots.result == 'success'",
                          "needs.publish.outputs.outcome == 'failure'"):
            self.assertIn(condition, issue)
        self.assertIn("issues: write", issue)
        self.assertIn("actions: read", issue)
        self.assertIn("contents: read", issue)
        self.assertIn("pbw-release", issue)
        self.assertIn("comprehensive-report", issue)
        self.assertIn("comprehensive-release-", issue)
        self.assertIn("store-screenshots-", issue)
        self.assertNotIn("gh release upload", WORKFLOW)

    def test_issue_condition_for_publish_and_gate_outcomes(self):
        condition = re.search(r"(?m)^    if: (.+)$", job("manual-publishing-issue")).group(1)
        cases = (
            ("success", "success", "success", "failure", False, True),
            ("success", "success", "success", "success", False, False),
            ("success", "success", "success", "skipped", False, False),
            ("success", "success", "success", "", False, False),
            ("failure", "success", "success", "failure", False, False),
            ("success", "failure", "success", "failure", False, False),
            ("success", "skipped", "success", "failure", False, False),
            ("success", "success", "failure", "failure", False, False),
            ("success", "success", "success", "failure", True, False),
        )
        for build, gate, screenshots, publish, cancelled, expected in cases:
            with self.subTest(build=build, gate=gate, screenshots=screenshots,
                              publish=publish, cancelled=cancelled):
                expression = condition.replace("always()", "True").replace(
                    "!cancelled()", str(not cancelled)).replace("&&", " and ")
                for key, value in (("needs.build.result", build),
                                   ("needs.comprehensive-gate.result", gate),
                                   ("needs.store-screenshots.result", screenshots),
                                   ("needs.publish.outputs.outcome", publish)):
                    expression = expression.replace(key, repr(value))
                self.assertEqual(eval(expression, {"__builtins__": {}}), expected)

    def test_publisher_success_failures_and_existing_version(self):
        script = shell_step("publish", "Publish to Pebble App Store")
        for scenario, expected, stage in (
                ("ok", 0, "upload"), ("already", 0, "listing"),
                ("auth", 1, "authentication"), ("listing", 1, "listing"),
                ("upload", 1, "upload")):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy(ROOT / "package.json", root / "package.json")
                (root / "dist").mkdir()
                (root / "dist" / "Pebble-PushTo-Tool-0.3.2.pbw").write_bytes(b"pbw")
                curl = root / "curl"
                curl.write_text(textwrap.dedent("""\
                    #!/bin/bash
                    echo "$*" >> "$CALLS"
                    case "$*" in
                      *developer/me*)
                        [ "$SCENARIO" != listing ] || exit 22
                        versions='[]'
                        [ "$SCENARIO" != already ] || versions='["0.3.2"]'
                        printf '{"apps":[{"app_uuid":"0f463d47-47e7-46f1-a7d6-94590ffc001d","app_id":"app-1","versions":%s}]}' "$versions"
                        ;;
                      *dashboard/apps*)
                        [ "$SCENARIO" != upload ] || exit 22
                        echo '{}'
                        ;;
                      *)
                        [ "$SCENARIO" != auth ] || exit 22
                        echo '{"value":"example-oidc-token"}'
                        ;;
                    esac
                """))
                curl.chmod(0o755)
                output = root / "output"
                env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                       "GITHUB_OUTPUT": str(output), "VERSION": "0.3.2",
                       "APP_ID": "app-1", "APPSTORE_API_BASE": "https://store.example",
                       "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example?x=1",
                       "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "example-request-token",
                       "SCENARIO": scenario, "CALLS": str(root / "calls")}
                process = subprocess.run(["bash", "-c", script], cwd=root, env=env,
                                         capture_output=True, text=True)
                self.assertEqual(process.returncode == 0, expected == 0, process.stderr)
                self.assertEqual(output.read_text().splitlines()[-1], f"stage={stage}")
                calls = (root / "calls").read_text()
                self.assertEqual("dashboard/apps" in calls, scenario in ("ok", "upload"))
                if scenario != "auth":
                    self.assertIn("Authorization: Bearer " + "example-oidc-token", calls)

    def test_failure_ticket_creation_and_rerun_deduplication(self):
        script = shell_step("manual-publishing-issue", "Create or update manual publishing issue")
        for existing, release_failure in ((False, False), (True, False), (False, True)):
            with self.subTest(existing=existing, release_failure=release_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                release_url = "https://github.com/ewsandor/Pebble-PushTo-Tool/releases/tag/v0.3.2"
                release_notes = "## Changes\n\n* Fix Node warnings (100%).\n* Keep `$(touch unexpected)` as text.\n"
                (root / "release-fixture.json").write_text(json.dumps({
                    "url": release_url, "body": release_notes,
                }))
                gh = root / "gh"
                gh.write_text(textwrap.dedent("""\
                    #!/bin/bash
                    echo "$*" >> "$CALLS"
                    case "$1 $2" in
                      "release view")
                        [ "$RELEASE_FAILURE" != yes ] || { echo "Release lookup failed" >&2; exit 1; }
                        cat release-fixture.json ;;
                      "api --paginate") printf 'pbw-release\\t123\\ncomprehensive-report\\t456\\nstore-screenshots-emery\\t789\\n' ;;
                      "issue list")
                        if [ "$EXISTING" = yes ]; then
                          echo '[{"number":42,"title":"Publish PushTo Tool v0.3.2","state":"CLOSED"}]'
                        else
                          echo '[]'
                        fi ;;
                    esac
                """))
                gh.chmod(0o755)
                env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                       "GH_REPO": "ewsandor/Pebble-PushTo-Tool", "VERSION": "0.3.2",
                       "FAILURE_STAGE": "upload", "RUN_ID": "1234",
                       "RUN_URL": "https://github.com/ewsandor/Pebble-PushTo-Tool/actions/runs/1234",
                       "CALLS": str(root / "calls"), "EXISTING": "yes" if existing else "no",
                       "RELEASE_FAILURE": "yes" if release_failure else "no"}
                process = subprocess.run(["bash", "-c", script], cwd=root, env=env,
                                         capture_output=True, text=True)
                calls = (root / "calls").read_text()
                self.assertIn(
                    "release view v0.3.2 --repo ewsandor/Pebble-PushTo-Tool --json url,body",
                    calls)
                if release_failure:
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn("Release lookup failed", process.stderr)
                    self.assertNotIn("issue ", calls)
                    continue
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual("issue create" in calls, not existing)
                self.assertEqual("issue comment 42" in calls, existing)
                self.assertEqual("issue reopen 42" in calls, existing)
                self.assertIn("ewsandor", calls)
                body = (root / "issue.md").read_text()
                for text in ("upload", "pbw-release", "comprehensive-report",
                             "store-screenshots-emery", "/artifacts/123"):
                    self.assertIn(text, body)
                self.assertIn(f"[GitHub release v0.3.2]({release_url})", body)
                self.assertIn("\n".join(f"> {line}" for line in release_notes.splitlines()), body)
                self.assertFalse((root / "unexpected").exists())

    def test_comprehensive_reports_are_uploaded_after_pass_or_failure(self):
        matrix = job("comprehensive")
        self.assertRegex(matrix, r"(?m)^      fail-fast: false$")
        self.assertRegex(matrix, r"(?m)^      - name: Ensure a final comprehensive report exists\n"
                         r"        if: always\(\)$")
        self.assertRegex(matrix, r"(?m)^      - name: Upload comprehensive reports and failure evidence\n"
                         r"        if: always\(\)$")
        self.assertIn("name: comprehensive-release-${{ matrix.platform }}", matrix)
        gate = job("comprehensive-gate")
        self.assertIn("if: always() && !cancelled()", gate)
        self.assertRegex(gate, r"(?m)^      - name: Upload final comprehensive gate report\n"
                         r"        if: always\(\)$")
        self.assertIn("name: comprehensive-report", gate)

    def test_gate_requires_each_prerequisite_and_writes_report(self):
        gate = job("comprehensive-gate")
        for name, dependency in (("BUILD", "build"), ("HOST", "test"),
                                 ("RELEASE", "release"), ("EMULATOR", "comprehensive")):
            self.assertIn(f"{name}: ${{{{ needs.{dependency}.result }}}}", gate)
        script = re.search(r"(?ms)^          python3 - <<'PY'\n(.*?)^          PY$", gate)
        self.assertIsNotNone(script)
        script = textwrap.dedent(script.group(1))
        names = ("BUILD", "HOST", "RELEASE", "EMULATOR")
        for failed in (None, *names):
            for result in (("success",) if failed is None else ("failure", "skipped", "cancelled")):
                checks = {name: result if name == failed else "success" for name in names}
                with self.subTest(failed=failed, result=result), tempfile.TemporaryDirectory() as directory:
                    process = subprocess.run([sys.executable, "-c", script], cwd=directory,
                                             env={**os.environ, **checks}, capture_output=True, text=True)
                    report = json.loads((Path(directory) / "comprehensive-report.json").read_text())
                    self.assertEqual(process.returncode == 0, failed is None)
                    self.assertEqual(report["success"], failed is None)
                    self.assertEqual(report["checks"], {key.lower(): value for key, value in checks.items()})


if __name__ == "__main__":
    unittest.main()
