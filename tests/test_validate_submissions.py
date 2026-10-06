# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the submission validator CLI."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

# The scripts append their reports to the runner's step summary and outputs
# whenever those are offered. A test run provokes failures on purpose and must
# not write them into the job that runs it, so these are dropped here, before
# any script is loaded or spawned; tests that need one set their own file.
for _runner_file in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
    os.environ.pop(_runner_file, None)



VALIDATOR = Path(__file__).parents[1] / ".github" / "scripts" / "validate_submissions.py"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
# The one file the default bill of materials names, held by the default dataset
# listing with the same size and hash.
BILL_ITEM = {"relative_path": "simready_usd/asset.usd", "size": 3, "hash": {"sha256": "0" * 64}}

VALIDATOR_SPEC = importlib.util.spec_from_file_location("validate_submissions", VALIDATOR)
if VALIDATOR_SPEC is None or VALIDATOR_SPEC.loader is None:
    raise RuntimeError(f"Could not load validator module from {VALIDATOR}")
VALIDATOR_MODULE = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(VALIDATOR_MODULE)


def package_url(namespace: str, dataset: str, asset: str, commit: str = COMMIT_A) -> str:
    return (
        f"https://huggingface.co/datasets/{namespace}/{dataset}/resolve/"
        f"{commit}/{asset}/com.nvidia.simready.packaging.json"
    )


def implied_package_id(url: str) -> str:
    """The package ID a well-formed package definition at `url` would declare."""
    match = VALIDATOR_MODULE.PACKAGE_URL.fullmatch(url)
    assert match is not None, url
    return (
        f"simready.hf.{match['namespace']}.{match['dataset']}.{match['asset']}"
        f".2026.03.03_{match['commit'][:2]}"
    )


def rate_limited(seconds: str | None) -> HTTPError:
    """A 429 as Hugging Face sends one, with the seconds left in its window."""
    headers = Message()
    if seconds is not None:
        headers["RateLimit"] = f'"api";r=0;t={seconds}'
    return HTTPError("https://huggingface.co/api", 429, "Too Many Requests", headers, None)


class RateLimitTest(unittest.TestCase):
    """Hugging Face says how long to wait, so waiting beats failing the run."""

    def test_reads_the_seconds_left_in_the_window(self) -> None:
        self.assertEqual(VALIDATOR_MODULE.rate_limit_wait(rate_limited("42")), 42)

    def test_ignores_a_status_that_is_not_a_rate_limit(self) -> None:
        headers = Message()
        headers["RateLimit"] = '"api";r=0;t=42'
        not_limited = HTTPError("https://huggingface.co/api", 503, "Oops", headers, None)
        self.assertIsNone(VALIDATOR_MODULE.rate_limit_wait(not_limited))

    def test_ignores_a_rate_limit_that_does_not_say_how_long(self) -> None:
        self.assertIsNone(VALIDATOR_MODULE.rate_limit_wait(rate_limited(None)))

    def test_ignores_a_wait_longer_than_one_window(self) -> None:
        """Nothing legitimate asks for longer, so a wait that long is not obeyed."""
        beyond = VALIDATOR_MODULE.MAX_RATE_LIMIT_WAIT + 1
        self.assertIsNone(VALIDATOR_MODULE.rate_limit_wait(rate_limited(str(beyond))))

    def test_waits_once_and_then_succeeds(self) -> None:
        answers = [rate_limited("7"), "the response"]

        def fake_urlopen(request, timeout=30):
            answer = answers.pop(0)
            if isinstance(answer, HTTPError):
                raise answer
            return answer

        slept: list[float] = []
        with (
            patch.object(VALIDATOR_MODULE, "urlopen", side_effect=fake_urlopen),
            patch.object(VALIDATOR_MODULE.time, "sleep", slept.append),
            redirect_stdout(io.StringIO()),
        ):
            result = VALIDATOR_MODULE.open_hugging_face(
                VALIDATOR_MODULE.Request("https://huggingface.co/api")
            )

        self.assertEqual(result, "the response")
        self.assertEqual(slept, [7])

    def test_gives_up_when_the_second_attempt_is_refused_too(self) -> None:
        with (
            patch.object(VALIDATOR_MODULE, "urlopen", side_effect=rate_limited("7")),
            patch.object(VALIDATOR_MODULE.time, "sleep", lambda _: None),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(HTTPError):
                VALIDATOR_MODULE.open_hugging_face(
                    VALIDATOR_MODULE.Request("https://huggingface.co/api")
                )

    def test_does_not_wait_for_an_ordinary_failure(self) -> None:
        refusal = HTTPError("https://huggingface.co/api", 404, "Not Found", Message(), None)
        slept: list[float] = []
        with (
            patch.object(VALIDATOR_MODULE, "urlopen", side_effect=refusal),
            patch.object(VALIDATOR_MODULE.time, "sleep", slept.append),
        ):
            with self.assertRaises(HTTPError):
                VALIDATOR_MODULE.open_hugging_face(
                    VALIDATOR_MODULE.Request("https://huggingface.co/api")
                )
        self.assertEqual(slept, [])

class AnnotationSyntaxTest(unittest.TestCase):
    """The workflow command has to parse, or the finding does not exist.

    GitHub is unforgiving here and silent about it: a malformed ::error line is
    printed literally and no annotation is created. Nothing fails, nothing warns,
    and every consumer downstream -- the pull request, the check run, the
    dashboard reading annotations -- simply sees no findings on a submission that
    has one.

    That is exactly what happened: a finding with no path emitted
    "::error,title=..." with a leading comma, and PR #18 showed "No findings
    reported" while failing.
    """

    def emit(self, finding):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            VALIDATOR_MODULE.report_failure(
                VALIDATOR_MODULE.ValidationError("summary", [finding]))
        return buffer.getvalue().splitlines()[0]

    def test_properties_are_separated_from_the_command_by_a_space(self):
        line = self.emit(VALIDATOR_MODULE.Finding("detail", None, None, "SUB.018 Something"))
        self.assertTrue(line.startswith("::error title="),
                        "properties must follow a space, not a comma: " + line)

    def test_a_located_finding_keeps_file_line_and_title(self):
        line = self.emit(
            VALIDATOR_MODULE.Finding("detail", "submissions/a/b", 3, "SUB.007 Duplicate URL"))
        self.assertTrue(line.startswith("::error file=submissions/a/b,line=3,title="), line)

    def test_a_finding_with_nothing_to_say_about_itself_has_no_properties(self):
        line = self.emit(VALIDATOR_MODULE.Finding("detail"))
        # No title, so no rule -- a warning, and the point here is only that
        # nothing separates the command from an empty property list.
        self.assertTrue(line.startswith("::warning::"), line)

    def test_supporting_sentences_are_separate_lines(self):
        """One sentence per line, encoded as %0A, so nobody downstream guesses.

        GitHub decodes %0A back to a newline in the annotation. Splitting here --
        where the sentences were written -- means the pull request, the check run
        and the dashboard all receive the lines rather than each hunting for full
        stops in a message that may name a file.
        """
        line = self.emit(VALIDATOR_MODULE.Finding(
            "First thing. Second thing. Third thing.", None, None, "SUB.999 Heading"))
        body = line.split("::", 2)[2]
        # One per line, encoded, so no consumer downstream has to hunt for the
        # full stops itself.
        self.assertIn("First thing.%0ASecond thing.%0AThird thing.", body)

    def test_a_file_name_does_not_become_a_line_break(self):
        # albedo.png must not split: the dot is not followed by whitespace.
        line = self.emit(VALIDATOR_MODULE.Finding(
            "For example chair/albedo.png. Remove it.", None, None, "SUB.999 Heading"))
        body = line.split("::", 2)[2]
        # The dot in a filename is not a line break. The sentence after it is,
        # because that dot IS followed by whitespace.
        self.assertIn("For example chair/albedo.png.", body)
        self.assertNotIn("albedo.%0Apng", body)

    def test_each_offending_file_is_named_with_what_is_wrong_with_it(self):
        """A file is its own entry, not a name in a row of names.

        The scan used to collapse them into "For example a, b, c, and 15 more",
        which named files without saying anything about any of them. Each one
        carries its own explanation now, and the path stays whole on its line.
        """
        line = self.emit(VALIDATOR_MODULE.Finding(
            "Remove them.", None, None, "SUB.999 Heading",
            files=(("chair/albedo.png", "It is not .png content."),
                   ("chair/notes.txt", "It is not .txt content.")),
            more=3))
        body = line.split("::", 2)[2]
        self.assertIn("chair/albedo.png: It is not .png content.", body)
        self.assertIn("chair/notes.txt: It is not .txt content.", body)
        self.assertIn("and 3 more", body)
        # One file per line, so the reader is not left splitting them apart.
        self.assertIn("%0Achair/notes.txt", body)

    def test_no_annotation_ever_starts_its_properties_with_a_separator(self):
        # The general form of the bug, not just the one case that bit.
        for finding in (
            VALIDATOR_MODULE.Finding("d", None, None, "SUB.001 T"),
            VALIDATOR_MODULE.Finding("d", "p", None, "SUB.001 T"),
            VALIDATOR_MODULE.Finding("d", "p", 2, "SUB.001 T"),
            VALIDATOR_MODULE.Finding("d", "p", 2),
            VALIDATOR_MODULE.Finding("d"),
        ):
            line = self.emit(finding)
            self.assertNotIn("::error,", line, line)
            self.assertNotIn("::error ,", line, line)
            self.assertNotIn(",,", line, line)


# The id shape, in the two places it has to be recognised. Deliberately not a
# list of known areas: SUB and SCAN are the ones that exist today, and WARN.001
# or SIM.001 must be found by the same guard without it being edited first.
#
# In code an id counts only at the head of a string literal -- where a finding is
# RAISED, as opposed to anywhere one is mentioned in a comment. The parentheses
# used to mark that, before the id became a link and lost them.
RULE_ID_IN_CODE = r"[\"']([A-Z][A-Z0-9]*(?:\.[A-Z][A-Z0-9]*)*\.\d{3,})\s"
# In the criteria a rule is one heading: the id, a dash, the title. Prose
# elsewhere in the repository mentions ids owned by other validators
# validators (NP.005, VM.MDL.001); those are references, not rules documented here.
RULE_HEADING = r"^### ([A-Z][A-Z0-9]*(?:\.[A-Z][A-Z0-9]*)*\.\d{3,})\s+-\s+.+$"


class RuleIndexTest(unittest.TestCase):
    """Every id a submitter can be shown is one they can look up.

    The ids are the stable handle -- the wording under them is meant to improve
    freely -- so an id reaching a pull request with no entry in the guide leaves
    the submitter holding a reference to nothing. This is the only thing keeping
    the two in step, and it is cheaper than remembering.
    """

    ROOT = Path(__file__).parents[1]
    SCRIPTS = (ROOT / ".github" / "scripts" / "validate_submissions.py",
               ROOT / ".github" / "scripts" / "security_scan.py")
    CRITERIA = ROOT / "docs" / "acceptance-criteria.md"

    def ids_in_code(self):
        found = set()
        for script in self.SCRIPTS:
            # At the head of a string literal: where an id is RAISED, rather
            # than anywhere it is mentioned in a comment. The parentheses used
            # to do this job, before the id became a link and lost them.
            found |= set(re.findall(RULE_ID_IN_CODE,
                                    script.read_text(encoding="utf-8")))
        return set(found)

    def ids_in_guide(self):
        # Fenced blocks are skipped. The document opens with a worked example of
        # how to write an entry, and an example is not a rule -- counting it
        # would report a criterion nothing raises, which is this guard's own
        # failure mode turned against it.
        text = self.CRITERIA.read_text(encoding="utf-8")
        lines, fenced = [], False
        for line in text.split("\n"):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced:
                lines.append(line)
        return set(re.findall(RULE_HEADING, "\n".join(lines), re.M))

    def test_every_id_raised_is_documented(self):
        undocumented = self.ids_in_code() - self.ids_in_guide()
        self.assertFalse(
            undocumented,
            "these ids can reach a pull request but are not in the acceptance "
            "criteria, so a submitter cannot look them up: "
            + ", ".join(sorted(undocumented)),
        )

    # The one area no check raises: what NVIDIA's reviewer decides by hand and
    # cites in the comment closing the pull request. Documented so a submitter
    # can look those up too, and exempt from the guard in that one direction.
    MANUAL_AREA = "REVIEW."

    def test_the_guide_documents_no_id_that_cannot_happen(self):
        # The other direction matters too: an entry for a check that was removed
        # sends someone looking for a rule that no longer exists.
        phantom = {rule_id for rule_id in self.ids_in_guide() - self.ids_in_code()
                   if not rule_id.startswith(self.MANUAL_AREA)}
        self.assertFalse(
            phantom,
            "the acceptance criteria document ids nothing raises: " + ", ".join(sorted(phantom)),
        )

    def test_the_manual_rules_are_documented_and_never_raised(self):
        # The exemption is for the reviewer's rules alone. A check that starts
        # raising one of them has taken over a manual decision, and the entry
        # then belongs in a checked section like any other.
        manual = {rule_id for rule_id in self.ids_in_guide() if rule_id.startswith(self.MANUAL_AREA)}
        self.assertTrue(manual, "the review section of the criteria has no rules")
        self.assertFalse(manual & self.ids_in_code(),
                         "a check raises an id of the manual review area")


class FindingLevelTest(unittest.TestCase):
    """Red means you broke a rule; orange means the checks could not run.

    The guide has said this in prose since the ids were introduced -- "a failure
    with no id is not a rule you broke" -- and the id is what decides it, so
    there is no second list to keep in step. A submitter who sees orange should
    re-run rather than go hunting for what they did wrong.
    """

    def test_a_finding_that_names_a_rule_is_an_error(self):
        finding = VALIDATOR_MODULE.Finding("d", None, None, "SUB.018 Not readable")
        self.assertEqual(finding.rule(), "SUB.018")
        self.assertEqual(finding.level(), "error")

    def test_a_finding_that_names_no_rule_is_a_warning(self):
        # Rate limits and network blips arrive this way: nothing is wrong with
        # the submission, so it must not be coloured as though something is.
        finding = VALIDATOR_MODULE.Finding("Hugging Face is rate-limiting these checks.")
        self.assertIsNone(finding.rule())
        self.assertEqual(finding.level(), "warning")

    def test_an_id_in_prose_does_not_make_a_rule(self):
        # Only an id at the head of the title counts. Otherwise a message that
        # merely mentions a rule would be recoloured by the mention.
        finding = VALIDATOR_MODULE.Finding("d", None, None, "Something about SUB.018 happened")
        self.assertIsNone(finding.rule())

    def test_every_documented_rule_would_be_shown_in_red(self):
        # The guide's colour key promises this: everything with an id blocks.
        for rule_id in sorted(RuleIndexTest().ids_in_guide()):
            finding = VALIDATOR_MODULE.Finding("d", None, None, f"{rule_id} heading")
            self.assertEqual(finding.level(), "error", rule_id)


class UnreadableDatasetTest(unittest.TestCase):
    """A submission must be readable BY THIS CI, and the message has to say why.

    "Private" is not one thing. A dataset under a personal account is private to
    everyone the owner has not invited, and this CI is never the owner -- being
    an NVIDIA employee grants nothing, because Hugging Face is a public service
    that knows nothing about NVIDIA's internal boundaries.

    The trap: Hugging Face answers 404, not 403, when an AUTHENTICATED caller
    lacks access. It hides existence rather than admitting the refusal. Taken at
    face value that says "there is no dataset X", sending the submitter to hunt a
    typo that is not there -- which is exactly what happened to Central PRs #18
    and #19 the moment CI was given a token.
    """

    def test_gated_is_named_as_gated(self) -> None:
        reason = VALIDATOR_MODULE.unreadable_reason("example/assets", 403)
        self.assertIn("gated", reason)

    def test_anonymous_401_is_named_as_private(self) -> None:
        reason = VALIDATOR_MODULE.unreadable_reason("example/assets", 401)
        self.assertIn("private", reason)
        # The fix an outside submitter can actually carry out. An org-owned
        # dataset also passes, but nobody outside has one, so the finding does
        # not offer it -- that lives in an internal note in the criteria.
        self.assertIn("public", reason,
                      "the message must say what to do, not only what is wrong")

    def test_anonymous_404_is_taken_at_face_value(self) -> None:
        # With no token the ambiguity does not exist: 404 means absent, and
        # probing again would be a wasted request on every genuine typo.
        with patch.object(VALIDATOR_MODULE, "HF_TOKEN", ""):
            self.assertIsNone(VALIDATOR_MODULE.unreadable_reason("example/assets", 404))

    def test_authenticated_404_that_is_private_says_so(self) -> None:
        # The anonymous probe answers 401, so the dataset exists and is simply
        # not ours to read. Reporting "there is no dataset" here would be false.
        with (
            patch.object(VALIDATOR_MODULE, "HF_TOKEN", "hf_example"),
            patch.object(VALIDATOR_MODULE, "urlopen",
                         side_effect=HTTPError("url", 401, "Unauthorized", {}, None)),
        ):
            reason = VALIDATOR_MODULE.unreadable_reason("ronanDB/simready-pound", 404)
        self.assertIsNotNone(reason, "a private dataset was reported as non-existent")
        # The claim is that it EXISTS -- that is what the anonymous probe buys,
        # and what stops a submitter hunting a typo in a correct name.
        self.assertIn("exists", reason)
        self.assertIn("private", reason)
        self.assertNotIn("has not been created", reason or "")
        # Why it cannot be read, in terms of something the submitter can change.
        # It used to say the checks "were not invited" -- a joke about access
        # that reads as a step somebody missed, when there is nowhere to invite
        # a check to and no button that does it.
        self.assertIn("anonymous", reason)
        self.assertNotIn("invited", reason)

    def test_authenticated_404_that_is_really_absent_stays_absent(self) -> None:
        with (
            patch.object(VALIDATOR_MODULE, "HF_TOKEN", "hf_example"),
            patch.object(VALIDATOR_MODULE, "urlopen",
                         side_effect=HTTPError("url", 404, "Not Found", {}, None)),
        ):
            self.assertIsNone(
                VALIDATOR_MODULE.unreadable_reason("example/typo", 404),
                "a genuine typo must still read as a typo",
            )


class _Response:
    """What urlopen hands back, reduced to what these functions read from it."""

    headers: dict = {}

    def __init__(self, body: bytes, url: str = "https://huggingface.co/datasets/example/assets/x") -> None:
        self.body, self.url, self.offset = body, url, 0

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        end = len(self.body) if size is None or size < 0 else self.offset + size
        chunk = self.body[self.offset:end]
        self.offset = end
        return chunk

    def geturl(self) -> str:
        return self.url


class OversizeDocumentTest(unittest.TestCase):
    """A document over its cap is named as too large, never parsed truncated.

    Reading one byte more than the cap is how an oversize file is told apart
    from one that fits. Without the length check that follows, a valid bill of
    200,000 files would be cut off mid-way and reported as broken JSON, which
    sends the submitter to fix a file that is fine.
    """

    def test_a_bill_of_materials_over_the_cap_is_named_as_too_large(self) -> None:
        body = b"x" * (VALIDATOR_MODULE.MAX_BOM_BYTES + 1)
        with patch.object(VALIDATOR_MODULE, "urlopen", return_value=_Response(body)):
            with self.assertRaises(VALIDATOR_MODULE.ValidationError) as caught:
                VALIDATOR_MODULE.bill_of_materials(
                    package_url("example", "assets", "big"), "example/assets")
        self.assertIn("SUB.028", caught.exception.title)
        self.assertIn("too large", caught.exception.title)
        self.assertIn("64 MB", str(caught.exception))
        self.assertNotIn("JSON", caught.exception.title)

    def test_a_bill_of_materials_at_the_cap_is_parsed(self) -> None:
        body = b'{"items": []' + b" " * (VALIDATOR_MODULE.MAX_BOM_BYTES - 13) + b"}"
        self.assertEqual(len(body), VALIDATOR_MODULE.MAX_BOM_BYTES)
        with patch.object(VALIDATOR_MODULE, "urlopen", return_value=_Response(body)):
            bill = VALIDATOR_MODULE.bill_of_materials(
                package_url("example", "assets", "wide"), "example/assets")
        self.assertEqual(bill, {"items": []})

    def test_a_package_definition_over_the_cap_is_named_as_too_large(self) -> None:
        body = b"x" * (VALIDATOR_MODULE.MAX_DEFINITION_BYTES + 1)
        url = package_url("example", "assets", "oversize", "c" * 40)
        with patch.object(VALIDATOR_MODULE, "urlopen", return_value=_Response(body, url)):
            with self.assertRaises(VALIDATOR_MODULE.ValidationError) as caught:
                VALIDATOR_MODULE.declared_definition(url, "example/assets")
        self.assertIn("SUB.023", caught.exception.title)
        self.assertIn("larger than 1 MB", str(caught.exception))

    def test_a_file_over_the_cap_is_not_hashed(self) -> None:
        body = b"x" * (VALIDATOR_MODULE.MAX_HASHED_BYTES + 1)
        with patch.object(VALIDATOR_MODULE, "urlopen", return_value=_Response(body)):
            with self.assertRaises(VALIDATOR_MODULE.ValidationError) as caught:
                VALIDATOR_MODULE.file_sha256("https://huggingface.co/datasets/example/assets/resolve/x/a/b.usd",
                                             "example/assets")
        self.assertIn("larger than 64 MB", str(caught.exception))


class HuggingFaceAuthTest(unittest.TestCase):
    """CI reads Hugging Face as someone when a token is set, anonymously when not.

    Anonymous is why a private dataset is unreadable regardless of who owns it:
    nvidia/simready-assets answers 401 to an anonymous caller exactly as a
    personal dataset does. A token makes the org's own datasets verifiable rather
    than waived; personal namespaces still will not open, because access there is
    the individual's to grant.
    """

    def _module(self, token: str | None):
        if token is None:
            os.environ.pop("HF_TOKEN", None)
        else:
            os.environ["HF_TOKEN"] = token
        try:
            module = importlib.util.module_from_spec(VALIDATOR_SPEC)
            VALIDATOR_SPEC.loader.exec_module(module)
            return module
        finally:
            os.environ.pop("HF_TOKEN", None)

    def test_no_token_means_anonymous_requests(self) -> None:
        headers = self._module(None).hf_headers()
        self.assertNotIn("Authorization", headers,
                         "an unset HF_TOKEN must not produce an empty Bearer header")
        self.assertIn("User-Agent", headers)

    def test_a_token_is_sent_as_a_bearer(self) -> None:
        headers = self._module("hf_example").hf_headers()
        self.assertEqual(headers.get("Authorization"), "Bearer hf_example")

    def test_whitespace_is_not_a_token(self) -> None:
        # A secret that exists but is blank is the shape a misconfigured repo
        # takes, and "Bearer " would fail every request in a way that reads like
        # the dataset being private rather than the token being empty.
        self.assertNotIn("Authorization", self._module("   ").hf_headers())


def _filesystem_is_case_sensitive() -> bool:
    """Whether two paths differing only in case can both exist here.

    On Windows and macOS they cannot: writing submissions/Alice/Assets and
    submissions/alice/assets produces ONE file, so a test that needs two never
    reaches the check it is about and fails against whatever runs first. That
    failure looks like a bug in the validator and is not one, and it has been
    mistaken for one -- hence skipping loudly rather than leaving it red.
    """
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "casecheck"
        probe.write_text("", encoding="utf-8")
        return not (Path(directory) / "CASECHECK").exists()


CASE_SENSITIVE = _filesystem_is_case_sensitive()


class ValidatorIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary_directory.name)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Submission Validator Tests")
        self.git("config", "user.email", "validator-tests@example.com")
        self.write("README.md", "fixture repository\n")
        self.base = self.commit("Create fixture repository")
        self.commit_history = [COMMIT_B, COMMIT_A]
        self.commit_history_error: Exception | None = None
        self.commit_history_queries: list[str] = []
        # By default every package definition declares the package ID that its
        # own URL implies, so a test only has to say something here when the
        # package ID is the thing under test.
        self.package_ids: dict[str, str] = {}
        self.package_id_error: Exception | None = None
        self.package_definitions: dict[str, dict] = {}
        self.dataset_licence: str | None = "cc-by-4.0"
        self.package_id_queries: list[str] = []
        # By default every package carries a bill of materials naming one file
        # that the dataset holds with the same hash, so the bill check is
        # invisible to tests about something else. None stands for no bill.
        self.bill_items: dict[str, list | None] = {}
        self.package_files: dict[tuple[str, str, str], dict[str, tuple[int | None, str | None]]] = {}
        self.file_hashes: dict[str, str] = {}
        self.hash_queries: list[str] = []

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def write(self, relative_path: str, content: str | bytes) -> None:
        path = self.repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def remove(self, relative_path: str) -> None:
        (self.repo / relative_path).unlink()

    def commit(self, message: str, *options: str) -> str:
        """A signed-off commit, as submit makes them; `options` go to git commit."""
        self.git("add", "--all")
        self.git("commit", "--quiet", "-s", "-m", message, *options)
        return self.git("rev-parse", "HEAD")

    def commit_unsigned(self, message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def validate(
        self, base: str | None = None, head: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        output_directory = self.repo / "validation-output"
        arguments = [
            str(VALIDATOR),
            base or self.base,
            head or self.git("rev-parse", "HEAD"),
            "--output-directory",
            str(output_directory),
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()

        def fake_commit_history(dataset: str) -> list[str]:
            self.commit_history_queries.append(dataset)
            if self.commit_history_error:
                raise self.commit_history_error
            return list(self.commit_history)

        def fake_declared_definition(url: str, dataset: str) -> dict:
            """The package definition a test asks for, or a conformant default.

            Defaults to agreeing with the dataset licence so the licence check is
            invisible to tests that are about something else.
            """
            if url in self.package_definitions:
                return self.package_definitions[url]
            return {"format_version": "1.0",
                    "package_id": implied_package_id(url),
                    "license": self.dataset_licence,
                    "metadata": []}

        def fake_dataset_license(dataset: str) -> str | None:
            return self.dataset_licence

        def fake_bill_of_materials(url: str, dataset: str) -> dict | None:
            if url in self.bill_items:
                items = self.bill_items[url]
                return None if items is None else {"format_version": "1.0", "items": items}
            return {"format_version": "1.0", "items": [dict(BILL_ITEM)]}

        def fake_package_files(dataset: str, commit: str, asset: str) -> dict:
            key = (dataset, commit, asset)
            if key in self.package_files:
                return dict(self.package_files[key])
            return {BILL_ITEM["relative_path"]: (BILL_ITEM["size"], BILL_ITEM["hash"]["sha256"])}

        def fake_file_sha256(url: str, dataset: str) -> str:
            self.hash_queries.append(url)
            return self.file_hashes.get(url, "f" * 64)

        def fake_declared_package_id(url: str, dataset: str) -> str:
            self.package_id_queries.append(url)
            if url in self.package_ids:
                return self.package_ids[url]
            if self.package_id_error:
                raise self.package_id_error
            return implied_package_id(url)

        with (
            chdir(self.repo),
            patch.object(sys, "argv", arguments),
            patch.object(VALIDATOR_MODULE, "commit_history", side_effect=fake_commit_history),
            patch.object(
                VALIDATOR_MODULE, "declared_package_id", side_effect=fake_declared_package_id
            ),
            patch.object(
                VALIDATOR_MODULE, "declared_definition", side_effect=fake_declared_definition
            ),
            patch.object(
                VALIDATOR_MODULE, "dataset_license", side_effect=fake_dataset_license
            ),
            patch.object(VALIDATOR_MODULE, "bill_of_materials", side_effect=fake_bill_of_materials),
            patch.object(VALIDATOR_MODULE, "package_files", side_effect=fake_package_files),
            patch.object(VALIDATOR_MODULE, "file_sha256", side_effect=fake_file_sha256),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            return_code = VALIDATOR_MODULE.main()

        return subprocess.CompletedProcess(
            arguments,
            return_code,
            stdout.getvalue(),
            stderr.getvalue(),
        )

    def assert_validation_fails(self, expected_message: str) -> subprocess.CompletedProcess[str]:
        result = self.validate()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected_message, result.stdout + result.stderr)
        self.assertFalse((self.repo / "validation-output").exists())
        return result

    def add_base_submission(self, path: str, content: str) -> None:
        self.write(path, content)
        self.base = self.commit("Add base submission")

    def test_adds_valid_submission_and_writes_sorted_added_package_list(self) -> None:
        first = package_url("example", "assets", "zebra")
        second = package_url("example", "assets", "alpha", COMMIT_B)
        self.write("submissions/example/assets", f"{first}\n\n{second}\n")
        self.commit("Add submission")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Validated submissions/example/assets: 2 package URL(s)", result.stdout)
        self.assertIn("Package changes: 2 added, 0 removed.", result.stdout)
        self.assertEqual(self.commit_history_queries, ["example/assets"])
        self.assertEqual(
            (self.repo / "validation-output" / "added-packages.txt").read_text(encoding="utf-8"),
            "".join(f"{url}\n" for url in sorted([first, second])),
        )
        self.assertEqual(
            (self.repo / "validation-output" / "removed-packages.txt").read_text(encoding="utf-8"),
            "",
        )

    def test_modification_reports_added_and_removed_urls(self) -> None:
        old = package_url("example", "assets", "old")
        retained = package_url("example", "assets", "retained")
        new = package_url("example", "assets", "new", COMMIT_B)
        self.add_base_submission("submissions/example/assets", f"{old}\n{retained}\n")
        self.write("submissions/example/assets", f"{retained}\n{new}\n")
        self.commit("Update submission")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.repo / "validation-output" / "added-packages.txt").read_text(encoding="utf-8"),
            f"{new}\n",
        )
        self.assertEqual(
            (self.repo / "validation-output" / "removed-packages.txt").read_text(encoding="utf-8"),
            f"{old}\n",
        )

    def test_records_the_file_and_line_of_each_added_package_url(self) -> None:
        """The next job needs to know which line added a URL, to report against it."""
        kept = package_url("example", "assets", "kept")
        first = package_url("example", "assets", "new_a", COMMIT_B)
        second = package_url("example", "assets", "new_b", COMMIT_B)
        self.add_base_submission("submissions/example/assets", f"{kept}\n")
        self.write("submissions/example/assets", f"{kept}\n{first}\n{second}\n")
        self.commit("Add two packages")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        index = json.loads(
            (self.repo / "validation-output" / "added-packages.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            index,
            [
                {"url": first, "path": "submissions/example/assets", "line": 2},
                {"url": second, "path": "submissions/example/assets", "line": 3},
            ],
        )

    def test_writes_an_empty_added_package_index_when_no_submission_changed(self) -> None:
        self.write("README.md", "unrelated change\n")
        self.commit("Touch nothing under submissions")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(
                (self.repo / "validation-output" / "added-packages.json").read_text(
                    encoding="utf-8"
                )
            ),
            [],
        )

    def test_deletes_submission_and_reports_all_removed_urls(self) -> None:
        url = package_url("example", "assets", "retired")
        self.add_base_submission("submissions/example/assets", f"{url}\n")
        self.remove("submissions/example/assets")
        self.commit("Delete submission")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Validated removed submission file: submissions/example/assets", result.stdout
        )
        self.assertEqual(
            (self.repo / "validation-output" / "removed-packages.txt").read_text(encoding="utf-8"),
            f"{url}\n",
        )

    def test_no_changes_passes_and_writes_empty_package_lists(self) -> None:
        result = self.validate(self.base, self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No submission files changed", result.stdout)
        self.assertEqual(
            (self.repo / "validation-output" / "added-packages.txt").read_text(encoding="utf-8"),
            "",
        )
        self.assertEqual(
            (self.repo / "validation-output" / "removed-packages.txt").read_text(encoding="utf-8"),
            "",
        )

    def test_non_submission_change_passes(self) -> None:
        self.write("README.md", "changed\n")
        self.commit("Change documentation")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("this pull request is not a submission", result.stdout)

    def test_rejects_submission_mixed_with_unrelated_change(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'asset')}\n",
        )
        self.write("README.md", "changed\n")
        self.commit("Mix submission and documentation")
        self.assert_validation_fails("SUB.019")

    # SUB.004: a submission file that exists at head is an ordinary file

    def set_executable(self, relative_path: str, executable: bool) -> None:
        """Set or clear the executable bit the way git will record it.

        Where the filesystem keeps mode bits git reads them from disk; where it
        does not (core.fileMode false) the index entry is the only place the
        bit lives, so it is set there as well.
        """
        path = self.repo / relative_path
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR if executable else mode & ~stat.S_IXUSR)
        self.git("add", relative_path)
        self.git("update-index", "--chmod=+x" if executable else "--chmod=-x", relative_path)

    def test_rejects_an_executable_bit_set_on_an_existing_file(self) -> None:
        # The content is untouched: git lists the file as modified all the same.
        self.add_base_submission(
            "submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n"
        )
        self.set_executable("submissions/example/assets", True)
        self.commit("Make the submission file executable")
        result = self.assert_validation_fails("SUB.004")
        self.assertIn("an executable file", result.stdout + result.stderr)

    def test_rejects_a_new_file_committed_as_executable(self) -> None:
        self.write("submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n")
        self.set_executable("submissions/example/assets", True)
        self.commit("Add an executable submission file")
        self.assert_validation_fails("SUB.004")

    def test_rejects_a_symbolic_link_in_place_of_a_file(self) -> None:
        self.add_base_submission(
            "submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n"
        )
        self.remove("submissions/example/assets")
        os.symlink("../../README.md", self.repo / "submissions" / "example" / "assets")
        self.commit("Replace the submission file with a link")
        result = self.assert_validation_fails("SUB.004")
        self.assertIn("a symbolic link", result.stdout + result.stderr)

    def test_rejects_a_submodule_entry_at_a_submission_path(self) -> None:
        self.add_base_submission(
            "submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n"
        )
        # A gitlink entry needs no repository behind it to be committed.
        self.git(
            "update-index", "--add", "--cacheinfo", f"160000,{self.base},submissions/example/assets"
        )
        self.git("commit", "--quiet", "-s", "-m", "Turn the submission file into a submodule")
        result = self.assert_validation_fails("SUB.004")
        self.assertIn("a submodule", result.stdout + result.stderr)

    def test_clearing_an_executable_bit_passes(self) -> None:
        # The fix the rule asks for has to get through, or nobody can follow it.
        self.write("submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n")
        self.set_executable("submissions/example/assets", True)
        self.base = self.commit("Add an executable submission file")
        self.set_executable("submissions/example/assets", False)
        self.commit("Make the submission file an ordinary file")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Package changes: 0 added, 0 removed.", result.stdout)

    def test_a_renamed_file_is_a_removal_and_an_addition(self) -> None:
        # Git would report a rename; the validator sees the old file go and the
        # new one arrive, and judges the new one by its content. Here that
        # content still names the old dataset, so the addition fails on that.
        self.add_base_submission(
            "submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n"
        )
        self.git("mv", "submissions/example/assets", "submissions/example/renamed")
        self.commit("Rename the submission file")
        result = self.assert_validation_fails("SUB.006")
        self.assertNotIn("SUB.004", result.stdout + result.stderr)

    # SUB.027: every commit of a submission is signed off

    def test_rejects_a_submission_commit_without_a_sign_off(self) -> None:
        self.write("submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n")
        self.commit_unsigned("Add submission by hand")
        result = self.assert_validation_fails("SUB.027")
        self.assertIn("Add submission by hand", result.stdout + result.stderr)
        self.assertIn("no Signed-off-by trailer", result.stdout + result.stderr)

    def test_rejects_a_sign_off_by_somebody_else(self) -> None:
        self.write("submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n")
        self.commit_unsigned("Add submission\n\nSigned-off-by: Somebody Else <else@example.com>")
        result = self.assert_validation_fails("SUB.027")
        self.assertIn("signed off by else@example.com", result.stdout + result.stderr)

    def test_accepts_a_sign_off_by_the_committer(self) -> None:
        # Somebody else's change, committed and signed off here: the committer
        # certifies it, which is what the trailer is for.
        self.write("submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n")
        self.commit("Add submission", "--author=Original Author <author@example.com>")
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_merge_commits_need_no_sign_off(self) -> None:
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        self.write("submissions/example/assets", f"{package_url('example', 'assets', 'asset')}\n")
        self.commit("Add one submission")
        self.git("checkout", "--quiet", "-b", "side", self.base)
        self.write("submissions/example/props", f"{package_url('example', 'props', 'prop')}\n")
        self.commit("Add another submission")
        self.git("checkout", "--quiet", branch)
        self.git("merge", "--quiet", "--no-ff", "--no-edit", "side")
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_pull_request_that_is_not_a_submission_needs_no_sign_off(self) -> None:
        self.write("README.md", "changed\n")
        self.commit_unsigned("Change documentation")
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("this pull request is not a submission", result.stdout)

    # SUB.028: the bill of materials exists and describes the dataset

    def _submit_one(self, asset: str = "asset") -> str:
        url = package_url("example", "assets", asset)
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add submission")
        return url

    def test_rejects_a_package_without_a_bill_of_materials(self) -> None:
        url = self._submit_one()
        self.bill_items[url] = None
        result = self.assert_validation_fails("SUB.028")
        self.assertIn("has no bill of materials", result.stdout + result.stderr)
        self.assertIn("::error file=submissions/example/assets,line=1,title=", result.stdout)

    def test_rejects_a_listed_file_missing_from_the_dataset(self) -> None:
        url = self._submit_one()
        self.bill_items[url] = [dict(BILL_ITEM), {"relative_path": "simready_usd/gone.png",
                                                  "size": 1, "hash": {"sha256": "1" * 64}}]
        result = self.assert_validation_fails("SUB.028")
        self.assertIn("simready_usd/gone.png", result.stdout + result.stderr)
        self.assertIn("not in the dataset", result.stdout + result.stderr)

    def test_rejects_a_listed_file_whose_hash_differs(self) -> None:
        self._submit_one()
        self.package_files[("example/assets", COMMIT_A, "asset")] = {
            BILL_ITEM["relative_path"]: (BILL_ITEM["size"], "9" * 64)}
        result = self.assert_validation_fails("SUB.028")
        self.assertIn("SHA-256 differs", result.stdout + result.stderr)

    def test_hashes_a_file_hugging_face_keeps_outside_lfs(self) -> None:
        # No SHA-256 in the listing: the file is downloaded and hashed.
        self._submit_one()
        self.package_files[("example/assets", COMMIT_A, "asset")] = {
            BILL_ITEM["relative_path"]: (BILL_ITEM["size"], None)}
        resolve = (f"https://huggingface.co/datasets/example/assets/resolve/{COMMIT_A}/asset/"
                   f"{BILL_ITEM['relative_path']}")
        self.file_hashes[resolve] = BILL_ITEM["hash"]["sha256"]
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.hash_queries, [resolve])

    def test_rejects_a_downloaded_file_whose_hash_differs(self) -> None:
        self._submit_one()
        self.package_files[("example/assets", COMMIT_A, "asset")] = {
            BILL_ITEM["relative_path"]: (BILL_ITEM["size"], None)}
        resolve = (f"https://huggingface.co/datasets/example/assets/resolve/{COMMIT_A}/asset/"
                   f"{BILL_ITEM['relative_path']}")
        self.file_hashes[resolve] = "9" * 64
        self.assert_validation_fails("SHA-256 differs")
        self.assertEqual(self.hash_queries, [resolve])

    def test_a_size_mismatch_is_reported_without_downloading(self) -> None:
        self._submit_one()
        self.package_files[("example/assets", COMMIT_A, "asset")] = {
            BILL_ITEM["relative_path"]: (99, None)}
        result = self.assert_validation_fails("SUB.028")
        self.assertIn("99 bytes in the dataset", result.stdout + result.stderr)
        self.assertEqual(self.hash_queries, [])

    def test_files_the_bill_does_not_name_are_not_judged_here(self) -> None:
        self._submit_one()
        self.package_files[("example/assets", COMMIT_A, "asset")] = {
            BILL_ITEM["relative_path"]: (BILL_ITEM["size"], BILL_ITEM["hash"]["sha256"]),
            "simready_usd/extra.txt": (5, None)}
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_bill_is_checked_for_added_packages_only(self) -> None:
        # The older package sits at the older commit, so the file reads as history.
        old = package_url("example", "assets", "old", COMMIT_A)
        self.bill_items[old] = None
        self.add_base_submission("submissions/example/assets", f"{old}\n")
        new = package_url("example", "assets", "asset", COMMIT_B)
        self.write("submissions/example/assets", f"{old}\n\n{new}\n")
        self.commit("Add another package")
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nested_submission_path_is_treated_as_non_submission(self) -> None:
        self.write("submissions/example/assets/nested", "content\n")
        self.commit("Add nested file")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("this pull request is not a submission", result.stdout)

    def test_rejects_non_utf8_submission(self) -> None:
        self.write("submissions/example/assets", b"\xff\xfe")
        self.commit("Add binary submission")
        self.assert_validation_fails("SUB.003")

    def test_rejects_non_package_url(self) -> None:
        self.write("submissions/example/assets", "https://example.com/package\n")
        self.commit("Add malformed submission")
        self.assert_validation_fails("points at the host 'example.com'")

    def test_rejects_url_for_different_dataset(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('another', 'dataset', 'asset')}\n",
        )
        self.commit("Add mismatched submission")
        self.assert_validation_fails("SUB.006")
        # Hugging Face is not asked about a dataset the file and its URLs disagree
        # about, so an access failure can never be the first thing reported.
        self.assertEqual(self.commit_history_queries, [])
        self.assertEqual(self.package_id_queries, [])

    def test_rejects_leading_or_trailing_whitespace(self) -> None:
        self.write(
            "submissions/example/assets",
            f" {package_url('example', 'assets', 'asset')}\n",
        )
        self.commit("Add whitespace")
        self.assert_validation_fails("SUB.005")

    def test_rejects_duplicate_url(self) -> None:
        url = package_url("example", "assets", "asset")
        self.write("submissions/example/assets", f"{url}\n{url}\n")
        self.commit("Duplicate URL")
        self.assert_validation_fails("SUB.007")

    def test_rejects_unsorted_assets_within_commit(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'zebra')}\n"
            f"{package_url('example', 'assets', 'alpha')}\n",
        )
        self.commit("Add unsorted assets")
        self.assert_validation_fails(
            "SUB.009")

    def test_rejects_non_contiguous_commit_block(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'alpha', COMMIT_A)}\n"
            f"{package_url('example', 'assets', 'beta', COMMIT_B)}\n"
            f"{package_url('example', 'assets', 'gamma', COMMIT_A)}\n",
        )
        self.commit("Repeat commit block")
        self.assert_validation_fails("SUB.008")

    def test_rejects_commit_missing_from_public_dataset_history(self) -> None:
        self.commit_history = [COMMIT_A]
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'asset', COMMIT_B)}\n",
        )
        self.commit("Add unknown dataset commit")
        self.assert_validation_fails(
            "SUB.020")

    def test_rejects_dataset_without_public_commit_history(self) -> None:
        self.commit_history_error = VALIDATOR_MODULE.ValidationError(
            "Could not read the public Hugging Face commit history for example/assets"
        )
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'asset')}\n",
        )
        self.commit("Add private dataset")
        self.assert_validation_fails("Could not read the public Hugging Face commit history")

    def test_rejects_commit_blocks_not_ordered_oldest_first(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'newer', COMMIT_B)}\n"
            f"{package_url('example', 'assets', 'older', COMMIT_A)}\n",
        )
        self.commit("Add newest commit first")
        self.assert_validation_fails("SUB.010")

    def test_allows_a_new_version_of_asset_in_its_existing_submission(self) -> None:
        old = package_url("example", "assets", "asset", COMMIT_A)
        new = package_url("example", "assets", "asset", COMMIT_B)
        self.add_base_submission("submissions/example/assets", f"{old}\n")
        self.write("submissions/example/assets", f"{old}\n{new}\n")
        self.commit("Add asset version")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_allows_the_same_asset_folder_name_in_two_submissions(self) -> None:
        """Two submitters may both publish a folder called `chair`.

        The folder name is not what has to be unique; the package ID is, and it
        carries the namespace and dataset that make these two different.
        """
        self.add_base_submission(
            "submissions/alice/simready-assets",
            f"{package_url('alice', 'simready-assets', 'chair')}\n",
        )
        self.write(
            "submissions/bob/my-assets",
            f"{package_url('bob', 'my-assets', 'chair')}\n",
        )
        self.commit("Bob submits his own chair")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_allows_the_same_asset_folder_name_added_by_one_pull_request(self) -> None:
        self.write(
            "submissions/alice/simready-assets",
            f"{package_url('alice', 'simready-assets', 'chair')}\n",
        )
        self.write(
            "submissions/bob/my-assets",
            f"{package_url('bob', 'my-assets', 'chair')}\n",
        )
        self.commit("Two submitters, one folder name")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_repeated_package_id_in_one_submission(self) -> None:
        """The same version republished at a second commit is two packages with one ID."""
        first = package_url("example", "assets", "asset", COMMIT_A)
        second = package_url("example", "assets", "asset", COMMIT_B)
        self.package_ids = {
            first: "simready.hf.example.assets.asset.2026.03.03_00",
            second: "simready.hf.example.assets.asset.2026.03.03_00",
        }
        self.write("submissions/example/assets", f"{first}\n{second}\n")
        self.commit("Republish a version")
        self.assert_validation_fails(
            "SUB.016")

    def test_rejects_package_id_for_a_different_asset_folder(self) -> None:
        url = package_url("example", "assets", "chair")
        self.package_ids = {url: "simready.hf.example.assets.stool.2026.03.03_00"}
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add mislabelled package")
        self.assert_validation_fails(
            "SUB.014")

    def test_rejects_package_id_for_a_different_dataset(self) -> None:
        url = package_url("example", "assets", "chair")
        self.package_ids = {url: "simready.hf.other.assets.chair.2026.03.03_00"}
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add package from elsewhere")
        self.assert_validation_fails(
            "SUB.014")

    def test_rejects_package_id_without_a_version(self) -> None:
        url = package_url("example", "assets", "chair")
        self.package_ids = {url: "simready.hf.example.assets.chair."}
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add unversioned package")
        self.assert_validation_fails("SUB.015")

    def test_rejects_dotted_dataset_name(self) -> None:
        """A dot would make the namespace and dataset in a package ID ambiguous."""
        self.write(
            "submissions/example/droid_1.0.1",
            f"{package_url('example', 'droid_1.0.1', 'sm_box_a01')}\n",
        )
        self.commit("Add dotted dataset")
        self.assert_validation_fails(
            "SUB.002")

    def test_rejects_empty_submission_under_a_dotted_dataset_name(self) -> None:
        self.write("submissions/example/droid_1.0.1", "")
        self.commit("Add empty dotted dataset")
        self.assert_validation_fails(
            "SUB.002")

    def test_accepts_dots_in_the_asset_folder_and_version(self) -> None:
        url = package_url("example", "assets", "sm_box.a01")
        self.package_ids = {url: "simready.hf.example.assets.sm_box.a01.2026.03.03_00"}
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add dotted asset folder")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reports_unreadable_package_definition(self) -> None:
        self.package_id_error = VALIDATOR_MODULE.ValidationError(
            "The package definition at https://example.invalid is not valid JSON: line 1."
        )
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'chair')}\n",
        )
        self.commit("Add broken package definition")
        self.assert_validation_fails("is not valid JSON")

    def test_reads_no_package_definitions_until_the_file_parses(self) -> None:
        self.write("submissions/example/assets", "https://example.com/package\n")
        self.commit("Add malformed submission")
        self.assert_validation_fails("points at the host 'example.com'")
        self.assertEqual(self.package_id_queries, [])
        self.assertEqual(self.commit_history_queries, [])

    def test_rejects_non_https_url(self) -> None:
        url = package_url("example", "assets", "chair").replace("https://", "http://", 1)
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add insecure URL")
        self.assert_validation_fails("uses the 'http' scheme. Only https is accepted")

    def test_rejects_url_on_another_host(self) -> None:
        url = package_url("example", "assets", "chair").replace(
            "huggingface.co", "huggingface.co.evil.example", 1
        )
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add look-alike host")
        self.assert_validation_fails("points at the host 'huggingface.co.evil.example'")

    def test_rejects_the_hf_co_short_form_and_says_why(self) -> None:
        """hf.co works; the catalog still wants one spelling."""
        url = package_url("example", "assets", "chair").replace(
            "huggingface.co", "hf.co", 1
        )
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add short-form host")
        self.assert_validation_fails(
            "points at the host 'hf.co'. Hugging Face answers to that name too, so the link "
            "works, but the catalog uses a single spelling. Replace hf.co with huggingface.co."
        )

    def test_rejects_web_view_link(self) -> None:
        url = package_url("example", "assets", "chair").replace("/resolve/", "/blob/", 1)
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add blob link")
        self.assert_validation_fails("is a Hugging Face web-view link (/blob/)")

    def test_rejects_branch_name_in_place_of_commit(self) -> None:
        url = package_url("example", "assets", "chair").replace(COMMIT_A, "main", 1)
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add branch URL")
        self.assert_validation_fails("uses the revision 'main', which is not a commit hash")

    def test_rejects_uppercase_commit_hash(self) -> None:
        url = package_url("example", "assets", "chair").replace(COMMIT_A, COMMIT_A.upper(), 1)
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add upper-case hash")
        self.assert_validation_fails("in upper case. Write it in lower case")

    def test_rejects_url_with_query_string(self) -> None:
        url = f"{package_url('example', 'assets', 'chair')}?download=true"
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add URL with query")
        self.assert_validation_fails("carries a query string or fragment")

    def test_rejects_url_for_another_file(self) -> None:
        url = package_url("example", "assets", "chair").replace(
            "com.nvidia.simready.packaging.json", "README.md", 1
        )
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add wrong file")
        self.assert_validation_fails("ends with 'README.md'")

    def test_rejects_namespace_containing_a_dot(self) -> None:
        url = package_url("exam.ple", "assets", "chair")
        self.write("submissions/exam.ple/assets", f"{url}\n")
        self.commit("Add dotted namespace")
        self.assert_validation_fails(
            "SUB.002")

    def test_rejects_empty_submission_under_a_dotted_namespace(self) -> None:
        """The file name alone decides a package ID prefix, so check it directly."""
        self.write("submissions/exam.ple/assets", "")
        self.commit("Add empty dotted namespace")
        result = self.assert_validation_fails("SUB.002")
        self.assertIn("::error file=submissions/exam.ple/assets,title=", result.stdout)

    def test_names_the_line_whose_commit_is_not_on_main(self) -> None:
        self.commit_history = [COMMIT_A]
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'alpha', COMMIT_A)}\n"
            f"{package_url('example', 'assets', 'beta', COMMIT_B)}\n",
        )
        self.commit("Add unmerged commit")
        result = self.assert_validation_fails(
            "SUB.020")
        # One annotation per bad line, anchored to it, not one for the file.
        self.assertIn(
            "::error file=submissions/example/assets,line=2,title=", result.stdout
        )
        self.assertNotIn(COMMIT_A, result.stdout.split("::error ", 1)[1])

    def test_reports_a_private_dataset_as_private(self) -> None:
        self.commit_history_error = VALIDATOR_MODULE.dataset_access_error(
            "example/assets", HTTPError("url", 401, "Unauthorized", {}, None), "reading"
        )
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'chair')}\n",
        )
        self.commit("Add private dataset")
        self.assert_validation_fails(
            "SUB.018")

    def test_reports_a_gated_dataset_as_gated(self) -> None:
        self.commit_history_error = VALIDATOR_MODULE.dataset_access_error(
            "example/assets", HTTPError("url", 403, "Forbidden", {}, None), "reading"
        )
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'chair')}\n",
        )
        self.commit("Add gated dataset")
        self.assert_validation_fails("SUB.018")

    def test_reports_a_missing_dataset(self) -> None:
        self.commit_history_error = VALIDATOR_MODULE.dataset_access_error(
            "example/assets", HTTPError("url", 404, "Not Found", {}, None), "reading"
        )
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'chair')}\n",
        )
        self.commit("Add missing dataset")
        self.assert_validation_fails(
            "There is no Hugging Face dataset example/assets. The submission file and every URL "
            "in it agree on that name"
        )

    @unittest.skipUnless(CASE_SENSITIVE,
                         "needs a case-sensitive filesystem to create both files")
    def test_rejects_two_submission_files_differing_only_in_case(self) -> None:
        """Git keeps these apart; Hugging Face does not."""
        self.add_base_submission(
            "submissions/alice/assets",
            f"{package_url('alice', 'assets', 'chair')}\n",
        )
        self.write(
            "submissions/Alice/Assets",
            f"{package_url('Alice', 'Assets', 'stool')}\n",
        )
        self.commit("Add a second file for one dataset")
        self.assert_validation_fails(
            "SUB.017")

    @unittest.skipUnless(CASE_SENSITIVE,
                         "needs a case-sensitive filesystem to create both files")
    def test_rejects_two_such_files_added_by_one_pull_request(self) -> None:
        self.write(
            "submissions/alice/assets",
            f"{package_url('alice', 'assets', 'chair')}\n",
        )
        self.write(
            "submissions/Alice/Assets",
            f"{package_url('Alice', 'Assets', 'stool')}\n",
        )
        self.commit("Add both spellings at once")
        self.assert_validation_fails("SUB.017")

    def test_allows_removing_one_of_two_such_files(self) -> None:
        self.write("submissions/alice/assets", "")
        self.add_base_submission(
            "submissions/Alice/Assets",
            f"{package_url('Alice', 'Assets', 'stool')}\n",
        )
        self.remove("submissions/alice/assets")
        self.commit("Keep one spelling")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_a_package_id_recapitalised_by_renaming_the_dataset(self) -> None:
        """A rename recapitalises new definitions; the old ones keep the old case."""
        old = package_url("alice", "assets", "chair", COMMIT_A)
        new = package_url("alice", "assets", "chair", COMMIT_B)
        self.package_ids = {
            old: "simready.hf.alice.assets.chair.2026.03.03_00",
            new: "simready.hf.Alice.Assets.chair.2026.04.04_00",
        }
        self.write("submissions/alice/assets", f"{old}\n{new}\n")
        self.commit("Add a version published after a rename")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_package_ids_differing_only_in_case(self) -> None:
        first = package_url("example", "assets", "chair", COMMIT_A)
        second = package_url("example", "assets", "chair", COMMIT_B)
        self.package_ids = {
            first: "simready.hf.example.assets.chair.2026.03.03_00",
            second: "simready.hf.Example.Assets.Chair.2026.03.03_00",
        }
        self.write("submissions/example/assets", f"{first}\n{second}\n")
        self.commit("Republish a version in different case")
        self.assert_validation_fails("SUB.016")

    def test_reports_a_missing_package_definition_as_a_missing_folder(self) -> None:
        url = package_url("example", "assets", "Chair")
        self.package_id_error = VALIDATOR_MODULE.ValidationError(
            f"There is no package definition at {url}. The dataset example/assets exists, so "
            "check the asset folder name and the commit: names inside a dataset are stored in "
            "git and are case-sensitive, unlike the dataset's own name."
        )
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add mis-capitalised asset folder")
        self.assert_validation_fails("There is no package definition at")

    def test_anchors_each_finding_to_the_line_that_caused_it(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'chair')}\n"
            "https://example.com/package\n",
        )
        self.commit("Add a bad second line")
        result = self.validate()

        self.assertEqual(result.returncode, 1)
        self.assertIn("::error file=submissions/example/assets,line=2,title=", result.stdout)
        # The location is the annotation's, so the sentence does not repeat it.
        self.assertNotIn("submissions/example/assets:2 points", result.stdout)

    def test_writes_the_location_into_the_step_summary(self) -> None:
        summary = self.repo / "step-summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            self.write(
                "submissions/example/assets",
                f" {package_url('example', 'assets', 'chair')}\n",
            )
            self.commit("Add indented URL")
            self.validate()

        # A reader of the summary has no file open, so it keeps the location --
        # and the rule id, which is the handle they will quote when asking about
        # it. The wording underneath is free to change without breaking this.
        written = summary.read_text(encoding="utf-8")
        self.assertIn("SUB.005", written)
        self.assertIn("<code>submissions/example/assets</code> line 1", written)

    def test_escapes_a_message_that_would_end_the_workflow_command(self) -> None:
        self.assertEqual(
            VALIDATOR_MODULE.escape_data("100% done\nnext"), "100%25 done%0Anext"
        )
        self.assertEqual(
            VALIDATOR_MODULE.escape_property("a:b,c"), "a%3Ab%2Cc"
        )

    # The package licence and the dataset licence have to agree. The guide
    # requires the match and nothing enforced it, so the catalog could state
    # terms the content is not offered under. Three spellings of the same
    # licence are in circulation -- CC-BY-4.0 from the sample assets, SPDX
    # casing from simready-package's help, cc-by-4.0 from the guide and the
    # Hugging Face card -- so the comparison ignores case rather than failing
    # a submission over capitalisation nobody was told about.

    def _submit(self, definition: dict) -> None:
        url = package_url("example", "assets", "chair")
        self.package_definitions[url] = dict(definition, package_id=implied_package_id(url))
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add submission")

    def test_a_package_licence_that_contradicts_the_dataset_is_rejected(self) -> None:
        self.dataset_licence = "cc-by-4.0"
        self._submit({"format_version": "1.0", "license": "apache-2.0"})
        self.assert_validation_fails("apache-2.0")

    def test_capitalisation_alone_is_not_a_disagreement(self) -> None:
        self.dataset_licence = "cc-by-4.0"
        self._submit({"format_version": "1.0", "license": "CC-BY-4.0"})
        self.assertEqual(self.validate().returncode, 0)

    def test_a_package_with_no_licence_is_rejected(self) -> None:
        self._submit({"format_version": "1.0"})
        self.assert_validation_fails("SUB.011")

    def test_a_dataset_card_without_a_licence_does_not_fail_the_package(self) -> None:
        # A card with no licence is a different problem from a package that
        # disagrees with its dataset, and only the second is this check's.
        self.dataset_licence = None
        self._submit({"format_version": "1.0", "license": "cc-by-4.0"})
        self.assertEqual(self.validate().returncode, 0)

    def test_an_unknown_format_version_is_refused_rather_than_assumed(self) -> None:
        self._submit({"format_version": "9.9", "license": "cc-by-4.0"})
        self.assert_validation_fails("SUB.013")


if __name__ == "__main__":
    unittest.main()


class PrivateDatasetNoticeTest(unittest.TestCase):
    """A private dataset that passed is announced, not failed.

    With the organisation's token these checks read a private dataset as if it
    were public. That is fine for a test submission and wrong for the catalog,
    whose public checks hold no token. The notice is the only trace of that
    difference, so it must appear exactly when the dataset is private and the
    checks could read it -- and never fail anything.
    """

    def _run(self, token: str, info):
        out = io.StringIO()

        class _Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with (
            patch.object(VALIDATOR_MODULE, "HF_TOKEN", token),
            patch.object(VALIDATOR_MODULE, "urlopen",
                         return_value=_Response(json.dumps(info).encode())),
            redirect_stdout(out),
        ):
            VALIDATOR_MODULE.remind_private_dataset("nvidia/simready-central-test-assets")
        return out.getvalue()

    def test_a_private_dataset_read_with_a_token_gets_a_notice(self) -> None:
        printed = self._run("hf_example", {"private": True, "gated": False})
        self.assertTrue(printed.startswith("::notice "), printed)
        self.assertIn("nvidia/simready-central-test-assets is private", printed)
        self.assertIn("public SimReady Central cannot", printed)

    def test_a_public_dataset_gets_no_notice(self) -> None:
        self.assertEqual(self._run("hf_example", {"private": False}), "")

    def test_no_token_means_no_notice(self) -> None:
        # Anonymous checks cannot have read a private dataset; there is nothing to
        # say, and no request to make.
        with patch.object(VALIDATOR_MODULE, "urlopen",
                          side_effect=AssertionError("must not ask Hugging Face")):
            out = io.StringIO()
            with patch.object(VALIDATOR_MODULE, "HF_TOKEN", ""), redirect_stdout(out):
                VALIDATOR_MODULE.remind_private_dataset("nvidia/simready-central-test-assets")
        self.assertEqual(out.getvalue(), "")

    def test_a_failed_lookup_stays_silent(self) -> None:
        out = io.StringIO()
        with (
            patch.object(VALIDATOR_MODULE, "HF_TOKEN", "hf_example"),
            patch.object(VALIDATOR_MODULE, "urlopen",
                         side_effect=HTTPError("url", 500, "boom", {}, None)),
            redirect_stdout(out),
        ):
            VALIDATOR_MODULE.remind_private_dataset("nvidia/simready-central-test-assets")
        self.assertEqual(out.getvalue(), "")
