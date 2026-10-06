# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the example library scripts under docs/examples promise.

index_to_library.py turns a pulled workspace into library.json, one record per
asset; library_page.py turns that into a page. The guide walks readers through
both, so the shapes they document are held here: the five fields a consumer
library retains, how the validator's stringified lists are read, that a package
missing a source is recorded rather than dropped, and that whatever text a
record carries cannot break out of the page's script element.

Neither script needs simready-manage or USD to be importable for these: the
readers that do are exercised through stand-ins, the way the workspace would
present them.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "examples"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIBRARY = _load("index_to_library")
PAGE = _load("library_page")

NAMES = {
    "FET_000_PHYSX": {"0.1.0": {"display_name": "Core (PhysX)", "runtime": "PhysX"}},
    "FET_004_PHYSX": {
        "0.3.0": {"display_name": "Multi-Body (PhysX) old", "runtime": "PhysX"},
        "0.4.0": {"display_name": "Simulate Multi-Body Physics (PhysX)", "runtime": "PhysX"},
    },
}

URL = {
    "source_url": "https://huggingface.co/datasets/acme/props/resolve/0123456789abcdef0123456789abcdef01234567/crate_a01/com.nvidia.simready.packaging.json",
    "repo_id": "acme/props",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "owner": "acme",
}
PACKAGING = {"package_id": "simready.hf.acme.props.crate_a01.2026.09.12_00", "license": "cc-by-4.0", "format_version": "1.0"}
HEADER = {"profile_id": "Robotics-Prop", "profile_version": "3.1.0", "simready_validate_version": "x", "tier_versions": {"tier_core": "y"}}
USD = {
    "author": "ACME", "asset_license": "CC-BY 4.0", "category": "prop", "asset_type": "prop", "mass": 1.5,
    "Variants": {"Physics": {"PhysX": {}, "Newton": {}}},
}


def _verify(**overrides) -> dict:
    base = {
        "asset_name": "crate_a01", "asset_path": "/ws/crate_a01", "bom_ok": None, "bom_issues": [],
        "validation_ok": True, "validation_issues": [],
        "validation_summary": {"FET_000_PHYSX": {"passed": True, "version": "0.1.0", "dependencies": "[]"}},
        "benchmark_ok": True, "benchmark_issues": [],
        "benchmark_summary": [{
            "test_name": "culling_xz", "engine": "isaac_sim", "engine_version": "6.1.0.0", "status": "pass",
            "passed": True, "duration": 9.7, "message": "", "metrics": {"culling_xz_max_diff": {"value": 0}}, "kit_logs": [],
        }],
        "passed": True,
    }
    base.update(overrides)
    return base


FAILING = _verify(
    validation_ok=False,
    validation_summary={
        "FET_000_PHYSX": {"passed": False, "version": "0.1.0", "optional requirements": "['com.nvidia.simready.RV.001']"},
        "FET_004_PHYSX": {"passed": False, "version": "0.9.0", "failing requirements": "['RB.MB.001', 'RB.MB.002']"},
    },
    benchmark_ok=False,
    benchmark_summary=[
        {"test_name": "grasp_and_lift", "engine": "isaac_sim", "status": "fail", "passed": False, "message": "All 1 grasp identifier(s) failed.</script><script>alert(1)</script>"},
        {"test_name": "ground_drop", "engine": "isaac_sim_newton", "status": "skipped", "passed": False, "message": ""},
    ],
    passed=False,
)
ABSENT = _verify(validation_ok=None, validation_summary={}, benchmark_ok=None, benchmark_summary=[], passed=False)


def _record(verify=None, **overrides) -> dict:
    kwargs = dict(
        url=URL, verify=verify or _verify(), header=HEADER, packaging=PACKAGING, usd_metadata=USD, names=NAMES,
        asset_dir_rel="crate_a01", root_usd_rel="crate_a01/crate_a01.usd",
        thumbnail_rel="crate_a01/.thumbs/256x256/crate_a01.usd.png",
    )
    kwargs.update(overrides)
    return LIBRARY.build_record("crate_a01", **kwargs)


class ReadingTest(unittest.TestCase):
    """The small readers, on the shapes the tools actually write."""

    def test_submission_lines_skip_blanks_and_comments(self):
        self.assertEqual(LIBRARY.submission_lines("# batch\n\n https://a \nhttps://b\n"), ["https://a", "https://b"])

    def test_stringified_lists_are_read_as_lists(self):
        # The validator writes list fields as Python literals, not JSON.
        self.assertEqual(LIBRARY.as_list("['RB.MB.001', 'RB.MB.002']"), ["RB.MB.001", "RB.MB.002"])
        self.assertEqual(LIBRARY.as_list(["a", "b"]), ["a", "b"])
        self.assertEqual(LIBRARY.as_list(""), [])
        self.assertEqual(LIBRARY.as_list(None), [])
        self.assertEqual(LIBRARY.as_list("not a literal"), ["not a literal"])

    def test_feature_names_come_from_the_tier(self):
        view = LIBRARY.feature_view("FET_000_PHYSX", {"passed": True, "version": "0.1.0"}, NAMES)
        self.assertEqual((view["display_name"], view["runtime"]), ("Core (PhysX)", "PhysX"))
        # A version the tier does not ship falls back to its newest name; an id it
        # does not know keeps its id, so a tier that is not installed still reads.
        self.assertEqual(LIBRARY.feature_view("FET_004_PHYSX", {"version": "0.9.0"}, NAMES)["display_name"],
                         "Simulate Multi-Body Physics (PhysX)")
        self.assertEqual(LIBRARY.feature_view("FET_099_ROS", {}, NAMES)["display_name"], "FET_099_ROS")

    def test_benchmark_view_keeps_results_not_metrics(self):
        rows = LIBRARY.benchmark_view(_verify()["benchmark_summary"])
        self.assertEqual(rows[0]["test_name"], "culling_xz")
        self.assertNotIn("metrics", rows[0])

    def test_version_is_what_follows_the_package_id_prefix(self):
        self.assertEqual(LIBRARY.package_version(PACKAGING["package_id"], "acme/props", "crate_a01", "fb"), "2026.09.12_00")
        self.assertEqual(LIBRARY.package_version("something.else", "acme/props", "crate_a01", "acme/props@01234567"),
                         "acme/props@01234567")
        self.assertIsNone(LIBRARY.package_version(None, "acme/props", "crate_a01", None))

    def test_usd_values_become_json(self):
        class Vec:
            def __iter__(self):
                return iter((1.0, 2.0, 3.0))

        class Opaque:
            def __str__(self):
                return "opaque"

        self.assertEqual(LIBRARY.jsonable({"extents": Vec(), "n": 1, "o": Opaque(), "l": (1, "a")}),
                         {"extents": [1.0, 2.0, 3.0], "n": 1, "o": "opaque", "l": [1, "a"]})

    def test_sidecar_header_and_packaging_are_read_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "crate_a01"
            (asset / ".metadata").mkdir(parents=True)
            # The validator keys its record by the USD path it validated.
            (asset / ".metadata" / "crate_a01_validation.json").write_text(json.dumps(
                {"/somewhere/crate_a01.usd": {"features_summary": {"FET_000_PHYSX": {"passed": True}}, **HEADER}}))
            (asset / "com.nvidia.simready.packaging.json").write_text(json.dumps({**PACKAGING, "metadata": []}))
            self.assertEqual(LIBRARY.read_sidecar_header(asset, "crate_a01"), HEADER)
            self.assertEqual(LIBRARY.read_packaging(asset), PACKAGING)
            self.assertEqual(LIBRARY.read_sidecar_header(asset, "other"), {})
            self.assertEqual(LIBRARY.read_packaging(asset / "nowhere"), {})

    def test_root_usd_and_thumbnail_follow_the_folder_convention(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "crate_a01"
            (asset / ".thumbs" / "256x256").mkdir(parents=True)
            (asset / "crate_a01.usda").write_text("#usda 1.0")
            (asset / "payload.usda").write_text("#usda 1.0")
            (asset / ".thumbs" / "256x256" / "crate_a01.usda.png").write_bytes(b"png")
            (asset / ".thumbs" / "256x256" / "crate_a01_dark.png").write_bytes(b"png")
            root = LIBRARY.find_root_usd(asset, "crate_a01")
            self.assertEqual(root.name, "crate_a01.usda")
            self.assertEqual(LIBRARY.find_thumbnail(root).name, "crate_a01.usda.png")
            # Two USDs and neither named after the folder: no guess is made.
            (asset / "crate_a01.usda").unlink()
            (asset / "other.usda").write_text("#usda 1.0")
            self.assertIsNone(LIBRARY.find_root_usd(asset, "crate_a01"))

    def test_root_usd_is_found_in_the_submitted_asset_folder(self):
        # submit publishes the asset folder with its intermediate folder below it,
        # so the root USD and the thumbnail sit one level down.
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "crate_a01"
            inner = asset / "simready_usd"
            (inner / ".thumbs" / "256x256").mkdir(parents=True)
            (asset / ".metadata").mkdir()
            (asset / "com.nvidia.simready.packaging.json").write_text("{}")
            (inner / "crate_a01.usda").write_text("#usda 1.0")
            (inner / "payload.usda").write_text("#usda 1.0")
            (inner / ".thumbs" / "256x256" / "crate_a01.usda.png").write_bytes(b"png")
            root = LIBRARY.find_root_usd(asset, "crate_a01")
            self.assertEqual(root, inner / "crate_a01.usda")
            self.assertEqual(LIBRARY.find_thumbnail(root).name, "crate_a01.usda.png")
            # One USD in the folder, not named after the asset: still the root.
            (inner / "crate_a01.usda").unlink()
            (inner / "payload.usda").rename(inner / "other.usda")
            self.assertEqual(LIBRARY.find_root_usd(asset, "crate_a01"), inner / "other.usda")
            # Two of them and neither named after the folder: no guess is made.
            (inner / "second.usda").write_text("#usda 1.0")
            self.assertIsNone(LIBRARY.find_root_usd(asset, "crate_a01"))


class RecordTest(unittest.TestCase):
    """What a library record holds."""

    RETAINED = ("source_url", "owner", "version", "license", "validation")

    def test_a_record_retains_the_consumer_fields(self):
        record = _record()
        for field in self.RETAINED:
            self.assertIn(field, record)
        self.assertEqual(record["source_url"], URL["source_url"])
        self.assertEqual(record["owner"], "acme")
        self.assertEqual(record["version"], "2026.09.12_00")
        self.assertEqual(record["license"], "cc-by-4.0")
        self.assertEqual(record["usd_license"], "CC-BY 4.0")
        self.assertEqual(record["validation"]["profile"], "Robotics-Prop")
        self.assertEqual(record["validation"]["features"][0]["display_name"], "Core (PhysX)")
        self.assertEqual(record["physics_variants"], ["Newton", "PhysX"])
        self.assertEqual(record["missing"], [])
        self.assertEqual(record["files"]["validation_sidecar"], "crate_a01/.metadata/crate_a01_validation.json")

    def test_failures_are_listed_with_their_requirements(self):
        record = _record(FAILING)
        self.assertEqual(record["validation"]["failed_features"], ["FET_000_PHYSX", "FET_004_PHYSX"])
        by_id = {f["id"]: f for f in record["validation"]["features"]}
        self.assertEqual(by_id["FET_004_PHYSX"]["failing_requirements"], ["RB.MB.001", "RB.MB.002"])
        self.assertEqual(by_id["FET_000_PHYSX"]["optional_requirements"], ["com.nvidia.simready.RV.001"])
        self.assertEqual([t["status"] for t in record["benchmark"]["results"]], ["fail", "skipped"])

    def test_missing_sources_are_named_not_fatal(self):
        # An asset that carries less is a finding about the asset; the record is
        # still written and says what was not there.
        record = _record(ABSENT, url={}, packaging={}, usd_metadata={}, header={},
                         root_usd_rel=None, thumbnail_rel=None)
        self.assertEqual(record["missing"], [
            "submission_url", "packaging", "validation_sidecar", "benchmark_sidecar",
            "root_usd", "usd_metadata", "thumbnail"])
        for field in self.RETAINED:
            self.assertIn(field, record)
        self.assertIsNone(record["license"])
        self.assertIsNone(record["version"])
        self.assertEqual(record["validation"]["features"], [])
        self.assertIsNone(record["files"]["validation_sidecar"])


@dataclasses.dataclass
class _FakeVerifyResult:
    asset_name: str
    asset_path: str
    bom_ok: object = None
    bom_issues: list = dataclasses.field(default_factory=list)
    validation_ok: object = True
    validation_summary: dict = dataclasses.field(default_factory=dict)
    validation_issues: list = dataclasses.field(default_factory=list)
    benchmark_ok: object = True
    benchmark_summary: list = dataclasses.field(default_factory=list)
    benchmark_issues: list = dataclasses.field(default_factory=list)
    passed: bool = True


class EndToEndTest(unittest.TestCase):
    """main() over a workspace, with the tools' readers stood in for."""

    def test_main_writes_the_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            asset = ws / "crate_a01"
            (asset / ".thumbs" / "256x256").mkdir(parents=True)
            (asset / ".metadata").mkdir()
            (asset / "crate_a01.usd").write_bytes(b"PXR-USDC")
            (asset / ".thumbs" / "256x256" / "crate_a01.usd.png").write_bytes(b"png")
            (asset / "com.nvidia.simready.packaging.json").write_text(json.dumps(PACKAGING))
            (asset / ".metadata" / "crate_a01_validation.json").write_text(json.dumps(
                {"/x/crate_a01.usd": {"features_summary": FAILING["validation_summary"], **HEADER}}))
            (ws / "simready_review_report.html").write_text("<html>")
            submission = Path(tmp) / "acme-props"
            submission.write_text(URL["source_url"] + "\n" + URL["source_url"].replace("crate_a01", "gone_b01") + "\n")
            features = Path(tmp) / "features"
            features.mkdir()
            (features / "FET_000_PHYSX-0.1.0.json").write_text(json.dumps(
                {"id": "FET_000_PHYSX", "version": "0.1.0", "display_name": "Core (PhysX)", "runtime": "PhysX"}))

            # Stand-ins for what the workspace tools would return.
            manage = types.ModuleType("simready.manage")
            manage.WorkspaceConfig = lambda root, content_root: (root, content_root)
            manage.WorkspaceManager = lambda config: types.SimpleNamespace(verify=lambda root: [
                _FakeVerifyResult("crate_a01", str(asset), **{k: FAILING[k] for k in (
                    "validation_ok", "validation_summary", "benchmark_ok", "benchmark_summary", "passed")})])
            hf_backend = types.ModuleType("simready.manage.backends.hf_backend")
            hf_backend.parse_fixed_url = lambda url: re.search(
                r"datasets/([^/]+/[^/]+)/resolve/([0-9a-f]+)/([^/]+)/", url).groups()
            pxr = types.ModuleType("pxr")
            pxr.Sdf = types.SimpleNamespace(Layer=types.SimpleNamespace(
                FindOrOpen=lambda path: types.SimpleNamespace(customLayerData={"SimReady_Metadata": USD})))
            tier = types.SimpleNamespace(load=lambda: types.SimpleNamespace(features_path=features), dist=None)
            modules = {
                "simready": types.ModuleType("simready"), "simready.manage": manage,
                "simready.manage.backends": types.ModuleType("simready.manage.backends"),
                "simready.manage.backends.hf_backend": hf_backend, "pxr": pxr,
            }
            with mock.patch.dict(sys.modules, modules), \
                 mock.patch("importlib.metadata.entry_points", lambda group: [tier]), \
                 mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                code = LIBRARY.main(["--workspace", str(ws), "--submission-file", str(submission)])
            self.assertEqual(code, 0)

            library = json.loads((ws / "library.json").read_text())
            self.assertEqual([a["asset_name"] for a in library["assets"]], ["crate_a01"])
            self.assertEqual(library["not_pulled"], ["gone_b01"])
            self.assertEqual(library["datasets"], ["acme/props"])
            record = library["assets"][0]
            self.assertEqual(record["thumbnail"], "crate_a01/.thumbs/256x256/crate_a01.usd.png")
            self.assertEqual(record["validation"]["features"][0]["display_name"], "Core (PhysX)")
            self.assertEqual(record["usd_metadata"]["category"], "prop")
            self.assertEqual(record["missing"], [])
            self.assertEqual(library["review_report"], "simready_review_report.html")

            # The page renders the same file, next to it.
            with mock.patch("sys.stdout"):
                self.assertEqual(PAGE.main(["--library", str(ws / "library.json")]), 0)
            self.assertTrue((ws / "library.html").is_file())


class PageTest(unittest.TestCase):
    """The page carries the library verbatim and nothing in it can escape the script element."""

    def _library(self):
        return {"generated": "now", "submission_file": "/s/acme-props", "workspace": "/ws", "datasets": ["acme/props"],
                "review_report": "simready_review_report.html",
                "tool_versions": {"simready-manage": "1"}, "not_pulled": ["gone_b01"], "not_listed": [],
                "assets": [_record(), _record(FAILING)]}

    def test_the_library_round_trips_through_the_page(self):
        library = self._library()
        page = PAGE.render_html(library, "Title <with> & marks")
        self.assertEqual(page.count("</script>"), 1, "a record's text closed the script element")
        self.assertIn("\\u003c/script", page)
        self.assertIn("<title>Title &lt;with&gt; &amp; marks</title>", page)
        embedded = re.search(r"const LIBRARY = (.*?);\nconst FACET_DEFS", page, re.S).group(1)
        self.assertEqual(json.loads(embedded), library)
        self.assertIn("not pulled: gone_b01", page)
        # Facet groups start collapsed; only a group with a selection opens.
        self.assertNotIn("<details open>", page)

    def test_every_facet_names_a_field_the_record_has(self):
        # The facets are JavaScript over a record; this keeps their field names
        # tied to what build_record writes.
        record = _record()
        for facet in PAGE.FACETS:
            for name in re.findall(r"\br\.([a-z_]+)", facet["values"]):
                self.assertIn(name, record, f"facet {facet['key']} reads r.{name}")


if __name__ == "__main__":
    unittest.main()
