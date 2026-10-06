# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the submitted-package security scanner."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

# The scripts append their reports to the runner's step summary and outputs
# whenever those are offered. A test run provokes failures on purpose and must
# not write them into the job that runs it, so these are dropped here, before
# any script is loaded or spawned; tests that need one set their own file.
for _runner_file in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT"):
    os.environ.pop(_runner_file, None)



SCRIPTS = Path(__file__).parents[1] / ".github" / "scripts"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
DATASET = "example/assets"


def load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {name} from {SCRIPTS}")
    module = importlib.util.module_from_spec(spec)
    # Registered before it is executed, so that the scanner's own
    # `from validate_submissions import ...` binds to this very module rather
    # than loading a second copy of it under the same name.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR_MODULE = load("validate_submissions")
SCANNER_MODULE = load("security_scan")


def package_url(asset: str, commit: str = COMMIT_A, dataset: str = DATASET) -> str:
    return (
        f"https://huggingface.co/datasets/{dataset}/resolve/{commit}/{asset}/"
        "com.nvidia.simready.packaging.json"
    )


# Bytes that genuinely are the format their name claims, so a test that is not
# about content does not have to say anything about it.
HEADS = {
    ".png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
    ".jpg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
    ".jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
    ".exr": b"\x76\x2f\x31\x01\x02\x00\x00\x00",
    ".glb": b"glTF\x02\x00\x00\x00\x4c\x00\x00\x00",
    ".usd": b"PXR-USDC\x00\x08\x00\x00",
    ".usdc": b"PXR-USDC\x00\x08\x00\x00",
    ".usda": b"#usda 1.0\n",
    ".wav": b"RIFF\x24\x08\x00\x00WAVEfmt ",
    ".m4a": b"\x00\x00\x00\x20ftypM4A \x00\x00\x02\x00",
    ".mp3": b"ID3\x04\x00\x00\x00\x00\x00\x23",
    ".json": b'{\n  "package_id": "simready.hf.example.assets.chair.1.0"\n}\n',
    ".toml": b'# a comment\nname = "chair"\n',
    ".mdl": b"mdl 1.0;\nimport ::df::*;\n",
    ".txt": b"Chair, 2026 edition.\nSee the dataset card for the licence.\n",
}


def implied_head(path: str) -> bytes:
    return HEADS[Path(path).suffix.lower()]


def asset_files(asset: str, *names: str) -> list[str]:
    """A believable asset folder: the package definition plus what is asked for."""
    return [f"{asset}/com.nvidia.simready.packaging.json"] + [f"{asset}/{name}" for name in names]


class SecurityScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._temporary_directory.name)

        self.added = [{"url": package_url("chair"), "path": "submissions/example/assets", "line": 1}]
        self.trees = {
            (DATASET, COMMIT_A, "chair"): asset_files(
                "chair", "simready_usd/chair.usd", "simready_usd/textures/albedo.png"
            )
        }
        self.roots = {(DATASET, COMMIT_A): {"README.md", ".gitattributes", "chair"}}
        self.heads: dict[str, bytes] = {}
        self.tree_error: Exception | None = None
        self.tree_queries: list[tuple[str, str, str]] = []
        self.head_queries: list[str] = []

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def scan(self) -> tuple[int, str]:
        index = self.directory / "added-packages.json"
        index.write_text(json.dumps(self.added), encoding="utf-8")
        arguments = [str(SCRIPTS / "security_scan.py"), str(index)]
        stdout, stderr = io.StringIO(), io.StringIO()

        def fake_asset_tree(dataset, commit, asset, budget):
            key = (dataset, commit, asset)
            self.tree_queries.append(key)
            budget.spend_tree()
            if self.tree_error:
                raise self.tree_error
            return self.trees.get(key)

        def fake_root_names(dataset, commit, budget):
            budget.spend_tree()
            if self.tree_error:
                raise self.tree_error
            return self.roots.get((dataset, commit), set())

        def fake_file_head(dataset, commit, path, budget):
            self.head_queries.append(path)
            budget.spend_head()
            return self.heads.get(path, implied_head(path))

        with (
            patch.object(sys, "argv", arguments),
            patch.object(SCANNER_MODULE, "asset_tree", side_effect=fake_asset_tree),
            patch.object(SCANNER_MODULE, "root_names", side_effect=fake_root_names),
            patch.object(SCANNER_MODULE, "file_head", side_effect=fake_file_head),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            return_code = SCANNER_MODULE.main()

        return return_code, stdout.getvalue() + stderr.getvalue()

    def assert_scan_fails(self, expected: str) -> str:
        return_code, output = self.scan()
        self.assertEqual(return_code, 1, output)
        self.assertIn(expected, output)
        return output

    def assert_scan_passes(self) -> str:
        return_code, output = self.scan()
        self.assertEqual(return_code, 0, output)
        return output

    # A package in order

    def test_allows_an_asset_whose_files_are_all_in_order(self) -> None:
        output = self.assert_scan_passes()
        self.assertIn("no problems found", output)

    # The reports the OEM's tools leave beside the asset

    def test_rejects_the_reports_a_plain_upload_carries(self) -> None:
        # submit on the bill-of-materials path uploads no HTML report. One from a
        # plain upload of the asset folder is an ordinary file of a type a package
        # may not hold.
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/.metadata/chair_validation.html")
        self.assert_scan_fails("SCAN.004")
        self.trees[(DATASET, COMMIT_A, "chair")][-1] = "chair/.metadata/chair_benchmark_results/index.html"
        self.assert_scan_fails("SCAN.004")

    # The file listing

    def test_reads_the_path_of_each_file_entry(self) -> None:
        # The tree API says more about a file than its path; only the path is
        # read, whatever else the entry carries.
        page = [
            {"type": "file", "path": "chair/simready_usd/chair.usd", "size": 1024,
             "oid": "f" * 40, "securityFileStatus": {"status": "safe"}},
            {"type": "file", "path": "chair/simready_usd/textures/albedo.png", "size": 2048},
        ]
        self.assertEqual(
            SCANNER_MODULE.parse_tree_page(page, DATASET),
            ["chair/simready_usd/chair.usd", "chair/simready_usd/textures/albedo.png"],
        )

    def test_drops_directory_entries(self) -> None:
        page = [{"type": "directory", "path": "chair/simready_usd"}]
        self.assertEqual(SCANNER_MODULE.parse_tree_page(page, DATASET), [])

    # File extensions

    def test_rejects_a_file_whose_extension_is_not_allowed(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/notes.md")
        self.assert_scan_fails("SCAN.004")

    def test_allows_a_plain_text_file(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/notes.txt")
        self.assert_scan_passes()

    def test_rejects_a_file_with_no_extension(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/LICENSE")
        self.assert_scan_fails("SCAN.004")

    def test_allows_an_extension_written_in_upper_case(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/simready_usd/textures/ROUGHNESS.PNG")
        self.assert_scan_passes()

    def test_allows_a_web_preview_mesh(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/web/sm_chair_01.glb")
        self.assert_scan_passes()

    def test_rejects_the_json_form_of_gltf_and_its_buffers(self) -> None:
        """Only the self-contained .glb is allowed: a .bin has no signature to check."""
        for name in ("chair/web/sm_chair_01.gltf", "chair/web/sm_chair_01.bin"):
            with self.subTest(name=name):
                self.trees[(DATASET, COMMIT_A, "chair")].append(name)
                self.assert_scan_fails("SCAN.004")

    def test_allows_the_package_definition_and_the_usd_thumbnail(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append(
            "chair/simready_usd/.thumbs/256x256/chair.usd.png"
        )
        self.assert_scan_passes()

    def test_rejects_an_lfsconfig_inside_an_asset_folder(self) -> None:
        """It is inert where it sits, but a package still may not carry one."""
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/.lfsconfig")
        self.assert_scan_fails("SCAN.004")

    # File content

    def test_rejects_a_zip_archive_named_as_a_texture(self) -> None:
        self.heads["chair/simready_usd/textures/albedo.png"] = b"PK\x03\x04\x14\x00\x00\x00"
        self.assert_scan_fails("SCAN.003")

    def test_rejects_a_gzip_archive_named_as_a_usd_file(self) -> None:
        self.heads["chair/simready_usd/chair.usd"] = b"\x1f\x8b\x08\x00\x00\x00\x00\x00"
        self.assert_scan_fails("is not .usd content")

    def test_rejects_a_zip_archive_named_as_a_web_preview_mesh(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/web/sm_chair_01.glb")
        self.heads["chair/web/sm_chair_01.glb"] = b"PK\x03\x04\x14\x00\x00\x00"
        self.assert_scan_fails("is not .glb content")

    def test_rejects_a_zip_archive_named_as_a_text_file(self) -> None:
        """A .txt has no signature, so it is held to being text, like .json and .toml."""
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/notes.txt")
        self.heads["chair/notes.txt"] = b"PK\x03\x04\x14\x00\x00\x00"
        self.assert_scan_fails("SCAN.003")

    def test_rejects_an_executable_named_as_a_texture(self) -> None:
        self.heads["chair/simready_usd/textures/albedo.png"] = b"\x7fELF\x02\x01\x01\x00"
        self.assert_scan_fails("SCAN.003")

    def test_names_the_file_whose_content_is_wrong(self) -> None:
        self.heads["chair/simready_usd/textures/albedo.png"] = b"PK\x03\x04\x14\x00\x00\x00"
        output = self.assert_scan_fails("chair/simready_usd/textures/albedo.png")
        self.assertIn("submissions/example/assets", output)

    def test_reads_no_file_content_for_a_file_already_rejected_by_its_extension(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/tool.exe")
        self.scan()
        self.assertNotIn("chair/tool.exe", self.head_queries)

    def test_allows_a_png_whose_bytes_are_a_png(self) -> None:
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.png", HEADS[".png"]))

    def test_allows_both_spellings_of_a_usd_file(self) -> None:
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.usd", b"PXR-USDC\x00\x08"))
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.usd", b"#usda 1.0\n"))

    def test_allows_an_mp3_that_starts_with_an_id3_tag(self) -> None:
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.mp3", b"ID3\x04\x00"))
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.mp3", b"\xff\xfb\x90\x00"))

    def test_allows_a_short_file_the_range_request_could_not_fill(self) -> None:
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.json", b"{}"))

    def test_allows_a_text_file_with_characters_beyond_ascii(self) -> None:
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.txt", "Stuhl, Größe 2 – Eiche\n".encode()))

    def test_rejects_a_text_file_in_utf_16(self) -> None:
        # The NUL in every other byte is what a UTF-16 note looks like from here,
        # and what distinguishes it from the UTF-8 the text formats require.
        self.assertEqual(
            SCANNER_MODULE.mismatch("a/b.txt", "Chair\n".encode("utf-16-le")),
            "contains a NUL byte",
        )

    def test_rejects_a_tar_archive_named_as_a_json_file(self) -> None:
        header = b"chair.usd" + b"\x00" * 90 + b"0000644\x00"
        self.assertEqual(
            SCANNER_MODULE.mismatch("a/b.json", header), "contains a NUL byte"
        )

    def test_rejects_a_gzip_archive_named_as_a_json_file(self) -> None:
        self.assertEqual(
            SCANNER_MODULE.mismatch("a/b.json", b"\x1f\x8b\x08\x00"), "is not UTF-8 text"
        )

    def test_rejects_a_zip_archive_named_as_a_json_file(self) -> None:
        """Zip's header is valid UTF-8, so only the control-character rule catches it."""
        self.assertEqual(
            SCANNER_MODULE.mismatch("a/b.json", b"PK\x03\x04\x14\x00"),
            "contains the control character U+0003",
        )

    def test_rejects_a_shell_script_named_as_a_json_file(self) -> None:
        self.assertEqual(
            SCANNER_MODULE.mismatch("a/b.json", b"#!/bin/sh\nrm -rf /\n"), "begins like a script"
        )

    def test_allows_a_json_file_whose_text_is_cut_mid_character(self) -> None:
        head = ("x" * 511 + "é").encode("utf-8")[:512]
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.json", head))

    def test_allows_tabs_and_newlines_in_a_toml_file(self) -> None:
        self.assertIsNone(SCANNER_MODULE.mismatch("a/b.toml", b'name\t= "chair"\r\n'))

    # The dataset's .lfsconfig

    def test_rejects_an_lfsconfig_at_the_root_of_the_dataset(self) -> None:
        self.roots[(DATASET, COMMIT_A)].add(".lfsconfig")
        self.assert_scan_fails("SCAN.006")

    def test_ignores_an_lfsconfig_outside_every_asset_folder(self) -> None:
        """Git LFS reads only the one at the root, so a nested copy is inert."""
        self.roots[(DATASET, COMMIT_A)].add("other_project")
        self.assert_scan_passes()

    def test_reports_one_lfsconfig_finding_for_a_whole_commit(self) -> None:
        self.added.append(
            {"url": package_url("table"), "path": "submissions/example/assets", "line": 2}
        )
        self.trees[(DATASET, COMMIT_A, "table")] = asset_files("table", "simready_usd/table.usd")
        self.roots[(DATASET, COMMIT_A)].add(".lfsconfig")
        output = self.assert_scan_fails("SCAN.006")
        # One annotation for the dataset, not one per package published from it.
        self.assertEqual(output.count("SCAN.006"), 1)

    # The shape of the work

    def test_reads_one_file_list_per_asset_folder(self) -> None:
        self.added.append(
            {"url": package_url("table"), "path": "submissions/example/assets", "line": 2}
        )
        self.added.append(
            {"url": package_url("chair"), "path": "submissions/other/assets", "line": 1}
        )
        self.trees[(DATASET, COMMIT_A, "table")] = asset_files("table", "simready_usd/table.usd")
        self.assert_scan_passes()
        self.assertEqual(len(self.tree_queries), 2)

    def test_scans_nothing_when_no_package_was_added(self) -> None:
        self.added = []
        self.assertIn("nothing to scan", self.assert_scan_passes())
        self.assertEqual(self.tree_queries, [])
        self.assertEqual(self.head_queries, [])

    def test_reports_an_asset_folder_that_has_no_files_at_that_commit(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")] = None
        self.assert_scan_fails("SCAN.005")

    def test_says_when_only_the_capitalisation_of_the_asset_folder_differs(self) -> None:
        self.added = [{"url": package_url("Chair"), "path": "submissions/example/assets", "line": 1}]
        # The folder name is echoed back so the submitter can see the case that
        # was actually submitted; the fix step comes from the criteria.
        output = self.assert_scan_fails("SCAN.005")
        self.assertIn("'Chair'", output)
        self.assertIn("capitalisation", output)

    def test_stops_when_one_pull_request_needs_too_many_requests(self) -> None:
        with patch.object(SCANNER_MODULE, "MAX_HEAD_REQUESTS", 1):
            self.assert_scan_fails("Submit one upload's worth of URLs per pull request")

    # Reporting

    def test_lists_a_bounded_number_of_example_paths_per_finding(self) -> None:
        self.trees[(DATASET, COMMIT_A, "chair")] += [
            f"chair/junk_{index:02d}.tmp" for index in range(20)
        ]
        output = self.assert_scan_fails("SCAN.004")
        self.assertIn("and 15 more", output)
        self.assertNotIn("junk_19.tmp", output)

    def test_anchors_each_finding_to_the_submission_line_that_added_the_url(self) -> None:
        self.added = [
            {"url": package_url("chair"), "path": "submissions/example/assets", "line": 7}
        ]
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/notes.md")
        output = self.assert_scan_fails("::error")
        self.assertIn("file=submissions/example/assets,line=7", output)

    def test_writes_the_findings_into_the_step_summary(self) -> None:
        summary = self.directory / "summary.md"
        self.trees[(DATASET, COMMIT_A, "chair")].append("chair/notes.md")
        with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}):
            self.scan()
        # The summary keeps the location for a reader with no file open, and the
        # rule id, which is the handle they will quote. The wording under it is
        # free to change without breaking this.
        written = summary.read_text(encoding="utf-8")
        self.assertIn("SCAN.004", written)
        self.assertIn("<code>submissions/example/assets</code> line 1", written)

    def test_reports_a_private_dataset_as_private(self) -> None:
        self.tree_error = VALIDATOR_MODULE.dataset_access_error(
            DATASET, HTTPError("u", 401, "Unauthorized", {}, None), "listing the files"
        )
        self.assert_scan_fails("SUB.018")

    def test_reports_a_gated_dataset_as_gated(self) -> None:
        self.tree_error = VALIDATOR_MODULE.dataset_access_error(
            DATASET, HTTPError("u", 403, "Forbidden", {}, None), "listing the files"
        )
        self.assert_scan_fails("SUB.018")

    def test_reports_a_rate_limited_scan_as_temporary(self) -> None:
        self.tree_error = VALIDATOR_MODULE.dataset_access_error(
            DATASET, HTTPError("u", 429, "Too Many Requests", {}, None), "listing the files"
        )
        self.assert_scan_fails("re-run the checks in a few minutes")


class ScannerAuthTest(unittest.TestCase):
    """The scanner reads Hugging Face exactly as the validator does.

    The validator runs one job earlier with the org's token and reads every
    package definition from a private NVIDIA dataset. If the scanner then asks
    anonymously it gets 401 and reports SUB.018 for a dataset the checks can in
    fact read. Both requests the scanner makes -- the file tree and the file head
    -- must therefore carry the validator's headers.
    """

    def _requests_made(self, token: str, call) -> list:
        seen = []

        class _Response(io.BytesIO):
            headers = {}

            def geturl(self):
                return "https://huggingface.co/x"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(request, timeout=30):
            seen.append(request)
            return _Response(b"[]")

        with (
            patch.object(VALIDATOR_MODULE, "HF_TOKEN", token),
            patch.object(SCANNER_MODULE, "open_hugging_face", side_effect=fake_open),
        ):
            call()
        return seen

    def test_the_tree_request_carries_the_token(self) -> None:
        seen = self._requests_made(
            "hf_example",
            lambda: SCANNER_MODULE.hugging_face_json("https://huggingface.co/api/x", DATASET, "tree"),
        )
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer hf_example")
        self.assertEqual(seen[0].get_header("User-agent"), VALIDATOR_MODULE.USER_AGENT)

    def test_the_file_head_request_carries_the_token_and_the_range(self) -> None:
        seen = self._requests_made(
            "hf_example",
            lambda: SCANNER_MODULE.file_head(DATASET, COMMIT_A, "chair/chair.usd",
                                             SCANNER_MODULE.RequestBudget()),
        )
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer hf_example")
        self.assertTrue(seen[0].get_header("Range", "").startswith("bytes=0-"))

    def test_no_token_means_anonymous_requests(self) -> None:
        seen = self._requests_made(
            "",
            lambda: SCANNER_MODULE.hugging_face_json("https://huggingface.co/api/x", DATASET, "tree"),
        )
        self.assertFalse(seen[0].has_header("Authorization"),
                         "an unset HF_TOKEN must not produce an empty Bearer header")


if __name__ == "__main__":
    unittest.main()
