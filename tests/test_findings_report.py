"""What a failing pull request is told.

A submission that fails used to get annotations and nothing else -- correct, and
placed where nobody looks first. The comment is what a submitter reads, so it
carries the same breakdown the dashboard shows: the rule id, linked to its entry
in the acceptance criteria, the gist beside it, and what to do underneath.

The wording comes from docs/acceptance-criteria.md rather than from here, so
correcting a word there changes what a submitter is told on the next run. These
hold that arrangement in place.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

# The scripts append their reports to the runner's step summary and outputs
# whenever those are offered. A test run provokes failures on purpose and must
# not write them into the job that runs it, so these are dropped here, before
# any script is loaded or spawned; tests that need one set their own file.
for _runner_file in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
    os.environ.pop(_runner_file, None)


ROOT = Path(__file__).resolve().parents[1]

# The links are built from the environment CI provides, so it is set before the
# module is loaded rather than patched afterwards.
os.environ.setdefault("GITHUB_REPOSITORY", "NVIDIA-Omniverse/simready-central")
_spec = importlib.util.spec_from_file_location(
    "validate_submissions_report", ROOT / ".github" / "scripts" / "validate_submissions.py")
VALIDATOR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(VALIDATOR)


class CriteriaTest(unittest.TestCase):
    """The wording lives in one file, and this is the one that reads it."""

    def test_the_criteria_are_read_from_the_checkout(self):
        # Not a copy kept here. One file, read where it sits, so a correction to
        # it is the whole edit.
        rules = VALIDATOR.criteria()
        self.assertGreater(len(rules), 20, "the acceptance criteria did not parse")
        self.assertIn("SUB.018", rules)
        self.assertEqual(rules["SUB.007"]["title"], "Duplicate URL")

    def test_the_worked_example_is_not_a_rule(self):
        # The criteria open by showing how to write an entry. It is fenced, and
        # treating it as a rule would put a criterion in front of a submitter
        # that no check can raise.
        self.assertNotIn("SUB.026", VALIDATOR.criteria())


class SubmitterScopeTest(unittest.TestCase):
    """A submitter may change submissions/ and nothing else.

    Not a review gate -- CI refuses it. The repository's own permissions decide
    who is who: submitters hold write, maintainers hold admin.
    author_association cannot tell them apart, because everyone is MEMBER,
    submitters included, being in the same organisation.
    """

    def setUp(self):
        self._saved = os.environ.get("PR_AUTHOR_PERMISSION")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("PR_AUTHOR_PERMISSION", None)
        else:
            os.environ["PR_AUTHOR_PERMISSION"] = self._saved

    def test_write_access_is_not_enough_to_change_the_checks(self):
        os.environ["PR_AUTHOR_PERMISSION"] = "write"
        self.assertFalse(VALIDATOR._author_maintains_the_repository())

    def test_admin_and_maintain_may_change_anything(self):
        for level in ("admin", "maintain", "ADMIN"):
            os.environ["PR_AUTHOR_PERMISSION"] = level
            self.assertTrue(VALIDATOR._author_maintains_the_repository(), level)

    def test_read_and_triage_are_not_maintainers(self):
        for level in ("read", "triage"):
            os.environ["PR_AUTHOR_PERMISSION"] = level
            self.assertFalse(VALIDATOR._author_maintains_the_repository(), level)

    def test_no_permission_means_unrestricted(self):
        # Off CI there is nobody to check. Failing closed here would reject every
        # pull request including the one that fixes it, and a submitter cannot
        # reach this anyway -- changing the workflow is the thing being refused.
        os.environ.pop("PR_AUTHOR_PERMISSION", None)
        self.assertTrue(VALIDATOR._author_maintains_the_repository())


class CriteriaMatchTheChecksTest(unittest.TestCase):
    """A step that does not work is worse than no step at all.

    SUB.025 told submitters to use a /blob/ URL. The check accepts /resolve/
    only, so following the rule exactly still failed it -- and the rule is
    supposed to be enough on its own. Found by opening a pull request that
    failed on purpose, which is the only way this class of bug shows up.
    """

    def _lines(self, rule_id):
        rule = VALIDATOR.criteria().get(rule_id)
        self.assertIsNotNone(rule, f"{rule_id} is not in the criteria")
        return " ".join(rule["description"])

    def test_the_url_shape_is_the_one_the_check_accepts(self):
        shown = self._lines("SUB.025")
        # The check's own description of the shape, so the two cannot drift.
        for part in ("/resolve/", "com.nvidia.simready.packaging.json"):
            self.assertIn(part, VALIDATOR.URL_SHAPE_DESCRIPTION)
            self.assertIn(part, shown,
                          f"SUB.025 does not mention {part}, which the check requires")
        self.assertNotIn("/blob/", shown,
                         "a blob URL is rejected, so telling anybody to use one is wrong")

    def test_the_commit_is_described_as_the_check_reads_it(self):
        # 40 hex characters. "a commit" alone let a short hash through the
        # instructions and into a rejection.
        self.assertIn("40", self._lines("SUB.025"))


class FindingsReportTest(unittest.TestCase):
    """The comment a failing submission gets."""

    def test_a_finding_is_worded_by_the_criteria(self):
        # The check raised this with its own sentence; the criteria say it
        # better, and say it in one place.
        finding = VALIDATOR.Finding("The same URL is listed twice.",
                                    "submissions/x/y", 7, "SUB.007 Duplicate URL")
        report = VALIDATOR.findings_report([finding])
        # One step is not a list, and two short ones are not two steps. The
        # check knows which line; the criteria know the fix; together they are
        # one sentence's worth and read as one.
        self.assertIn("The same URL is listed twice. Remove one.", report)
        self.assertNotIn("<li>", report, "a single step does not need a bullet")

    def test_the_id_links_to_its_own_entry(self):
        report = VALIDATOR.findings_report(
            [VALIDATOR.Finding("x.", None, None, "SUB.007 Duplicate URL")])
        # Lowercased with the dot dropped: GitHub's rule for a heading's anchor,
        # and the same one the dashboard computes. The branch is whatever the
        # module saw at import: main, or the base of the pull request CI runs in.
        branch = os.environ.get("GITHUB_BASE_REF") or "main"
        self.assertIn(f"/blob/{branch}/docs/acceptance-criteria.md#sub007", report)
        self.assertIn("<strong>SUB.007</strong></a>", report)
        self.assertNotIn("#SUB.007", report,
                         "an anchor GitHub does not build reaches nothing")

    def test_the_link_is_on_the_branch_the_pull_request_targets(self):
        # The checks read the criteria from the base branch, so that is the copy
        # a submitter is sent to. A pull request against qa-staging must not be
        # pointed at main's wording, even when the two happen to agree.
        with mock.patch.dict(os.environ, {"GITHUB_BASE_REF": "qa-staging"}):
            spec = importlib.util.spec_from_file_location(
                "validate_submissions_on_base", ROOT / ".github" / "scripts" / "validate_submissions.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertEqual(module.CRITERIA_DOC,
                         f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/blob/qa-staging/docs/acceptance-criteria.md")
        with mock.patch.dict(os.environ, {"GITHUB_BASE_REF": ""}):
            spec.loader.exec_module(module)
        self.assertIn("/blob/main/", module.CRITERIA_DOC, "a push event has no base; main is the answer")

    def test_a_title_with_a_token_keeps_the_filled_in_one(self):
        # [dataset] is a value only the check knows. Taking the criteria's title
        # would show the token itself in place of the dataset that is wrong.
        report = VALIDATOR.findings_report(
            [VALIDATOR.Finding("x.", None, None,
                               "SUB.018 whurd/pound-assets is not readable")])
        self.assertIn("whurd/pound-assets is not readable", report)
        self.assertNotIn("[dataset]", report)

    def test_a_finding_with_no_rule_says_so_once(self):
        # Its heading IS its message -- there is no title to separate from an
        # explanation -- so listing the sentences underneath would repeat it.
        report = VALIDATOR.findings_report(
            [VALIDATOR.Finding("Hugging Face is rate-limiting these checks.")])
        self.assertEqual(report.count("Hugging Face is rate-limiting these checks."), 1)
        self.assertIn("⚠", report,
                      "a finding with no id is not a rule that was broken")

    def test_a_url_template_survives_being_rendered(self):
        """Backticks are a literal, and the order of operations decides it.

        SUB.025 shows the shape a URL must have. Linkifying before honouring the
        code span turned it into
        `<a href="https://.../<commit>/<asset">...</a> folder>` -- broken at the
        space, and pointing somewhere nobody should go.
        """
        report = VALIDATOR.findings_report(
            [VALIDATOR.Finding("x.", None, None,
                               "SUB.025 Not a Hugging Face package URL")])
        self.assertIn("<code>https://huggingface.co/datasets/&lt;namespace&gt;", report)
        self.assertNotIn('<a href="https://huggingface.co/datasets/&lt;', report)

    def test_a_placeholder_is_shown_rather_than_swallowed(self):
        # A rule saying <namespace> is showing text. Unescaped, a browser reads
        # it as a tag it does not know and renders nothing at all.
        line = VALIDATOR._linkify("Name it submissions/<namespace>/<dataset>.")
        self.assertIn("&lt;namespace&gt;", line)
        self.assertNotIn("<namespace>", line)

    def test_a_written_link_still_links(self):
        line = VALIDATOR._linkify("Re-run the [packaging workflow](https://example.invalid/g).")
        self.assertIn('<a href="https://example.invalid/g">packaging workflow</a>', line)

    def test_the_report_carries_no_boilerplate(self):
        # The id is a link to its rule. A paragraph under every finding saying
        # so, on every failure, is noise -- and the one place a reader looks is
        # the finding, not the footer.
        report = VALIDATOR.findings_report(
            [VALIDATOR.Finding("x.", None, None, "SUB.007 Duplicate URL")])
        self.assertIn("acceptance-criteria.md#sub007", report,
                      "the id still has to reach its rule")
        self.assertNotIn("not a rule you broke", report)
        self.assertNotIn("Every id above", report)

    def test_an_underscore_survives_the_anchor(self):
        # GitHub keeps hyphens and underscores and drops the rest. Stripping the
        # underscore turned package_id into packageid, and SUB.021 linked to an
        # anchor that does not exist -- checked against the rendered document,
        # where 30 of 31 resolved and that one did not.
        self.assertEqual(
            VALIDATOR.heading_anchor("SUB.021 - Package definition has no package_id"),
            "sub021---package-definition-has-no-package_id")

    def test_a_token_in_a_heading_does_not_reach_the_anchor(self):
        # The anchor comes from the RENDERED heading, so a link inside one
        # contributes its words and not its markup. Leaving the markup in gave
        # "datasettokens".
        self.assertEqual(
            VALIDATOR.heading_anchor("SUB.018 - [dataset](#tokens) is not readable"),
            "sub018---dataset-is-not-readable")

    def test_a_relative_link_is_resolved_against_the_criteria(self):
        # A rule's line may link within the criteria -- "[dataset](#tokens)".
        # Rendered in a pull-request comment, a bare #fragment resolves against
        # the pull request, where that anchor does not exist.
        line = VALIDATOR._linkify("A [token](#tokens) is filled in.")
        self.assertIn('href="https://github.com/', line)
        self.assertIn("acceptance-criteria.md#tokens", line)
        self.assertNotIn('href="#tokens"', line)

    def test_unreadable_criteria_leave_the_finding_intact(self):
        # The finding still has to reach the submitter. Losing the file costs
        # the current wording, never the report.
        original = VALIDATOR.CRITERIA_PATH
        VALIDATOR.CRITERIA_PATH = "does/not/exist.md"
        VALIDATOR.criteria.cache_clear()
        try:
            report = VALIDATOR.findings_report(
                [VALIDATOR.Finding("The same URL is listed twice.", None, None,
                                   "SUB.007 Duplicate URL")])
            self.assertIn("SUB.007", report)
            self.assertIn("The same URL is listed twice.", report)
        finally:
            VALIDATOR.CRITERIA_PATH = original
            VALIDATOR.criteria.cache_clear()


if __name__ == "__main__":
    unittest.main()


class GroupedByRuleTest(unittest.TestCase):
    """A rule broken by four packages is one rule broken four times.

    Reported one finding at a time it became four headings carrying the same
    title and the same fix, with the files split between them -- the reader had
    to reassemble what the check already knew, and the page grew by a block per
    package while saying nothing new.
    """

    P = "submissions/kitJenNV/simready-oem-test"

    def rule(self, asset, line, files, note="It is the wrong type."):
        return VALIDATOR.Finding(
            "Remove them.", self.P, line,
            f"SCAN.004 File type a package may not contain ({len(files)} file(s))",
            files=tuple((f"{asset}/{f}", note) for f in files))

    def test_one_heading_however_many_packages_broke_it(self):
        report = VALIDATOR.findings_report([
            self.rule("box", 1, ["web/a.glb"]),
            self.rule("tin", 2, ["web/b.glb", "c.csv"]),
            self.rule("belt", 3, ["web/d.glb"]),
        ])
        self.assertEqual(report.count("SCAN.004"), 1, "a heading per package")
        # Every file still reaches the reader.
        for path in ("box/web/a.glb", "tin/web/b.glb", "tin/c.csv", "belt/web/d.glb"):
            self.assertIn(path, report)

    def test_the_lines_it_fired_on_are_named_together(self):
        report = VALIDATOR.findings_report([
            self.rule("box", 1, ["a.glb"]),
            self.rule("tin", 2, ["b.glb"]),
            self.rule("belt", 4, ["c.glb"]),
        ])
        self.assertIn("lines 1, 2 and 4", report)

    def test_a_note_they_all_share_is_said_once(self):
        report = VALIDATOR.findings_report([
            self.rule("box", 1, ["a.glb"]),
            self.rule("tin", 2, ["b.glb"]),
        ])
        self.assertEqual(report.count("It is the wrong type."), 1,
                         "the same sentence under every path is the rule talking")

    def test_notes_that_differ_stay_with_their_file(self):
        finding = VALIDATOR.Finding(
            "Replace them.", self.P, 1, "SCAN.003 File content does not match its name",
            files=(("a.png", "It is not .png content."),
                   ("b.usd", "It is not .usd content.")))
        report = VALIDATOR.findings_report([finding])
        self.assertIn("It is not .png content.", report)
        self.assertIn("It is not .usd content.", report)

    def test_findings_with_no_rule_are_not_merged(self):
        """They are not instances of anything, so they cannot share a heading."""
        a = VALIDATOR.Finding("Hugging Face timed out.", self.P, 1)
        b = VALIDATOR.Finding("The commit is gone.", self.P, 2)
        report = VALIDATOR.findings_report([a, b])
        self.assertIn("Hugging Face timed out.", report)
        self.assertIn("The commit is gone.", report)
