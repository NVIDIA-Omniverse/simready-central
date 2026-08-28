# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the submission validator CLI."""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


VALIDATOR = Path(__file__).parents[1] / ".github" / "scripts" / "validate_submissions.py"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40

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
        self.package_id_queries: list[str] = []

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

    def commit(self, message: str) -> str:
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
        self.assert_validation_fails("A submission pull request must change only")

    def test_nested_submission_path_is_treated_as_non_submission(self) -> None:
        self.write("submissions/example/assets/nested", "content\n")
        self.commit("Add nested file")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("this pull request is not a submission", result.stdout)

    def test_rejects_non_utf8_submission(self) -> None:
        self.write("submissions/example/assets", b"\xff\xfe")
        self.commit("Add binary submission")
        self.assert_validation_fails("is not UTF-8 text")

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
        self.assert_validation_fails("refers to another/dataset, not example/assets")
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
        self.assert_validation_fails("has leading or trailing whitespace")

    def test_rejects_duplicate_url(self) -> None:
        url = package_url("example", "assets", "asset")
        self.write("submissions/example/assets", f"{url}\n{url}\n")
        self.commit("Duplicate URL")
        self.assert_validation_fails("duplicates an earlier URL")

    def test_rejects_unsorted_assets_within_commit(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'zebra')}\n"
            f"{package_url('example', 'assets', 'alpha')}\n",
        )
        self.commit("Add unsorted assets")
        self.assert_validation_fails(
            f"lists the assets in commit block {COMMIT_A} out of order"
        )

    def test_rejects_non_contiguous_commit_block(self) -> None:
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'alpha', COMMIT_A)}\n"
            f"{package_url('example', 'assets', 'beta', COMMIT_B)}\n"
            f"{package_url('example', 'assets', 'gamma', COMMIT_A)}\n",
        )
        self.commit("Repeat commit block")
        self.assert_validation_fails("repeats a non-contiguous commit block")

    def test_rejects_commit_missing_from_public_dataset_history(self) -> None:
        self.commit_history = [COMMIT_A]
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'asset', COMMIT_B)}\n",
        )
        self.commit("Add unknown dataset commit")
        self.assert_validation_fails(
            f"uses the commit {COMMIT_B}, which is not on the main branch of example/assets"
        )

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
        self.assert_validation_fails("lists its commit blocks newest first")

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
            "submissions/example/assets:2 declares the package ID "
            "'simready.hf.example.assets.asset.2026.03.03_00', which line 1 already declares"
        )

    def test_rejects_package_id_for_a_different_asset_folder(self) -> None:
        url = package_url("example", "assets", "chair")
        self.package_ids = {url: "simready.hf.example.assets.stool.2026.03.03_00"}
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add mislabelled package")
        self.assert_validation_fails(
            "must have a package_id starting with 'simready.hf.example.assets.chair.'"
        )

    def test_rejects_package_id_for_a_different_dataset(self) -> None:
        url = package_url("example", "assets", "chair")
        self.package_ids = {url: "simready.hf.other.assets.chair.2026.03.03_00"}
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add package from elsewhere")
        self.assert_validation_fails(
            "must have a package_id starting with 'simready.hf.example.assets.chair.'"
        )

    def test_rejects_package_id_without_a_version(self) -> None:
        url = package_url("example", "assets", "chair")
        self.package_ids = {url: "simready.hf.example.assets.chair."}
        self.write("submissions/example/assets", f"{url}\n")
        self.commit("Add unversioned package")
        self.assert_validation_fails("which has no version after")

    def test_rejects_dotted_dataset_name(self) -> None:
        """A dot would make the namespace and dataset in a package ID ambiguous."""
        self.write(
            "submissions/example/droid_1.0.1",
            f"{package_url('example', 'droid_1.0.1', 'sm_box_a01')}\n",
        )
        self.commit("Add dotted dataset")
        self.assert_validation_fails(
            "has the dataset name 'droid_1.0.1', which contains a dot"
        )

    def test_rejects_empty_submission_under_a_dotted_dataset_name(self) -> None:
        self.write("submissions/example/droid_1.0.1", "")
        self.commit("Add empty dotted dataset")
        self.assert_validation_fails(
            "submissions/example/droid_1.0.1 has the dataset name 'droid_1.0.1', which contains "
            "a dot"
        )

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
            "has the namespace 'exam.ple', which contains a dot"
        )

    def test_rejects_empty_submission_under_a_dotted_namespace(self) -> None:
        """The file name alone decides a package ID prefix, so check it directly."""
        self.write("submissions/exam.ple/assets", "")
        self.commit("Add empty dotted namespace")
        result = self.assert_validation_fails("has the namespace 'exam.ple'")
        self.assertIn("::error file=submissions/exam.ple/assets::", result.stdout)

    def test_names_the_line_whose_commit_is_not_on_main(self) -> None:
        self.commit_history = [COMMIT_A]
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'alpha', COMMIT_A)}\n"
            f"{package_url('example', 'assets', 'beta', COMMIT_B)}\n",
        )
        self.commit("Add unmerged commit")
        result = self.assert_validation_fails(
            f"uses the commit {COMMIT_B}, which is not on the main branch of example/assets"
        )
        # One annotation per bad line, anchored to it, not one for the file.
        self.assertIn(
            "::error file=submissions/example/assets,line=2::", result.stdout
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
            "The Hugging Face dataset example/assets is private. Submitted datasets must be "
            "publicly readable"
        )

    def test_reports_a_gated_dataset_as_gated(self) -> None:
        self.commit_history_error = VALIDATOR_MODULE.dataset_access_error(
            "example/assets", HTTPError("url", 403, "Forbidden", {}, None), "reading"
        )
        self.write(
            "submissions/example/assets",
            f"{package_url('example', 'assets', 'chair')}\n",
        )
        self.commit("Add gated dataset")
        self.assert_validation_fails("is gated: it asks visitors to accept conditions")

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
            "submissions/Alice/Assets and submissions/alice/assets differ only in capitalisation"
        )

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
        self.assert_validation_fails("differ only in capitalisation")

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
        self.assert_validation_fails("which line 1 already declares")

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
        self.assertIn("::error file=submissions/example/assets,line=2::", result.stdout)
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

        # A reader of the summary has no file open, so it keeps the location.
        self.assertIn(
            "- submissions/example/assets:1 has leading or trailing whitespace.",
            summary.read_text(encoding="utf-8"),
        )

    def test_escapes_a_message_that_would_end_the_workflow_command(self) -> None:
        self.assertEqual(
            VALIDATOR_MODULE.escape_data("100% done\nnext"), "100%25 done%0Anext"
        )
        self.assertEqual(
            VALIDATOR_MODULE.escape_property("a:b,c"), "a%3Ab%2Cc"
        )

if __name__ == "__main__":
    unittest.main()
