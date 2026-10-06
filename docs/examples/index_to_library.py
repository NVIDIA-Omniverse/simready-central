#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a JSON library from the packages pulled for one submission file.

Reads a workspace that ``simready-manage pull --no-convert`` filled from one
``submissions/<namespace>/<dataset>`` file and writes ``library.json``: one record
per asset, holding what the package says about itself. Each reader below takes
one source, and is named for it:

    read_submission_urls   the submission file                 source URL, owner, commit
    read_sidecars          manifest.csv and .metadata/         validation and benchmark results
    read_sidecar_header    .metadata/<asset>_validation.json   profile and tool versions
    read_packaging         com.nvidia.simready.packaging.json  package id and license
    read_usd_metadata      the root USD's SimReady_Metadata    category, mass, extents, variants
    find_thumbnail         .thumbs/256x256/<root usd>.png
    load_feature_names     the installed tiers' features/      display names for FET ids

Nothing here runs a validator or edits a package. A source an asset does not
carry is listed under ``missing`` in its record, and the record is written
anyway: that is a finding about the asset, and the page shows it as one.

Usage:
    python index_to_library.py --workspace <dir> --submission-file <file> [--output <file>]
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("index_to_library")

PACKAGING_FILE = "com.nvidia.simready.packaging.json"
METADATA_DIR = ".metadata"
USD_SUFFIXES = (".usd", ".usda", ".usdc")
# What the validation sidecar records besides the per-feature results.
SIDECAR_HEADER_KEYS = ("profile_id", "profile_version", "simready_validate_version", "tier_versions")
# The distributions whose versions the library records.
TOOL_DISTRIBUTIONS = ("simready-manage", "simready-validate", "usd-core")
# The entry-point group every installed SimReady Foundation tier registers under.
TIER_ENTRY_POINT_GROUP = "simready.tier"


# ---------------------------------------------------------------------------
# One reader per source
# ---------------------------------------------------------------------------


def submission_lines(text: str) -> list[str]:
    """The URLs in a submission file: blank lines and ``#`` comments dropped, as ``pull`` reads it."""
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def read_submission_urls(path: Path) -> dict[str, dict]:
    """``{asset name: {source_url, repo_id, commit, owner}}`` from a submission file.

    ``parse_fixed_url`` is the parser ``simready-manage pull`` applies to the same
    lines. The owner is the Hugging Face namespace the dataset lives in.
    """
    from simready.manage.backends.hf_backend import parse_fixed_url

    urls: dict[str, dict] = {}
    for url in submission_lines(path.read_text(encoding="utf-8")):
        repo_id, commit, asset = parse_fixed_url(url)
        urls[asset] = {
            "source_url": url,
            "repo_id": repo_id,
            "commit": commit,
            "owner": repo_id.split("/", 1)[0],
        }
    return urls


def read_sidecars(workspace: Path) -> dict[str, dict]:
    """``{asset name: VerifyResult as a dict}`` for every asset ``pull`` registered.

    ``WorkspaceManager.verify`` walks ``manifest.csv``, reads each package's
    ``.metadata/<asset>_validation.json`` and ``.metadata/<asset>_benchmark.json``,
    and returns the per-feature and per-test results. It runs no tool. It does
    write ``simready_review_report.html`` into the workspace, the same report
    ``simready-manage verify`` produces.
    """
    from simready.manage import WorkspaceConfig, WorkspaceManager

    root = str(workspace)
    manager = WorkspaceManager(WorkspaceConfig(root=root, content_root=root))
    return {result.asset_name: dataclasses.asdict(result) for result in manager.verify(root)}


def read_sidecar_header(asset_dir: Path, asset: str) -> dict:
    """Profile and tool versions from the validation sidecar.

    ``verify`` returns the feature results but not these, so they are read from
    the same file. The validator keys the record by the USD path it validated.
    """
    path = asset_dir / METADATA_DIR / f"{asset}_validation.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    for entry in raw if isinstance(raw, list) else [raw]:
        records = [entry] if "features_summary" in entry else list(entry.values())
        for record in records:
            if isinstance(record, dict) and "features_summary" in record:
                return {key: record.get(key) for key in SIDECAR_HEADER_KEYS}
    return {}


def read_packaging(asset_dir: Path) -> dict:
    """Package id, license and format version from the package definition."""
    path = asset_dir / PACKAGING_FILE
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: data.get(key) for key in ("package_id", "license", "format_version")}


def find_root_usd(asset_dir: Path, asset: str) -> Path | None:
    """The package's root USD: the one named after the asset folder.

    The same convention ``simready-manage`` applies when it registers a pulled
    package. ``submit`` publishes the asset folder with its intermediate folder
    (``simready_usd/`` in SimReady Foundation content) below it, so the root USD
    is looked for at the top level of the package and then one folder down,
    skipping the ``.metadata`` and ``.thumbs`` folders. At either level a folder
    holding exactly one USD counts as well.
    """
    folders = [asset_dir] + sorted(
        path for path in asset_dir.iterdir() if path.is_dir() and not path.name.startswith(".")
    )
    for folder in folders:
        for suffix in USD_SUFFIXES:
            candidate = folder / f"{asset}{suffix}"
            if candidate.is_file():
                return candidate
    for folder in folders:
        candidates = [path for path in folder.iterdir() if path.is_file() and path.suffix in USD_SUFFIXES]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return None
    return None


def read_usd_metadata(root_usd: Path) -> dict:
    """``SimReady_Metadata`` from the root layer's ``customLayerData``.

    This is where SimReady Foundation places an asset's own description
    (category, type, mass, extents, license, physics variants), and the read
    ``simready-manage`` uses for its workspace cache. Only the root layer is
    opened; payloads stay closed.
    """
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(root_usd.as_posix())
    if layer is None:
        return {}
    return jsonable(layer.customLayerData.get("SimReady_Metadata", {}))


def find_thumbnail(root_usd: Path) -> Path | None:
    """The thumbnail SimReady Foundation places next to a root USD."""
    candidate = root_usd.parent / ".thumbs" / "256x256" / f"{root_usd.name}.png"
    return candidate if candidate.is_file() else None


def load_feature_names() -> dict[str, dict[str, dict]]:
    """``{feature id: {version: {display_name, runtime}}}`` from every installed tier.

    A tier registers itself under the ``simready.tier`` entry-point group and
    ships one JSON file per feature version in its ``features`` folder.
    """
    from importlib.metadata import entry_points

    names: dict[str, dict[str, dict]] = {}
    for entry_point in entry_points(group=TIER_ENTRY_POINT_GROUP):
        tier = entry_point.load()
        for path in sorted(Path(tier.features_path).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            names.setdefault(data["id"], {})[data["version"]] = {
                "display_name": data.get("display_name"),
                "runtime": data.get("runtime"),
            }
    return names


def tool_versions() -> dict[str, str]:
    """Versions of the tools whose output the library was built from."""
    from importlib.metadata import PackageNotFoundError, entry_points, version

    names = list(TOOL_DISTRIBUTIONS)
    names += [ep.dist.name for ep in entry_points(group=TIER_ENTRY_POINT_GROUP) if ep.dist]
    versions = {}
    for name in names:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            pass
    return versions


# ---------------------------------------------------------------------------
# Shaping what was read
# ---------------------------------------------------------------------------


def jsonable(value):
    """USD values as plain JSON: vectors and arrays become lists, the rest text."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return [jsonable(item) for item in value]
    except TypeError:
        return str(value)


def as_list(value) -> list[str]:
    """List-valued sidecar fields, which the validator writes as Python literals such as ``"['RV.001']"``."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    try:
        parsed = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return [str(value)]
    return [str(item) for item in parsed] if isinstance(parsed, (list, tuple)) else [str(parsed)]


def _version_key(text: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in str(text).split("."))


def feature_view(feature_id: str, data: dict, names: dict) -> dict:
    """One feature's result with the tier's display name for it."""
    versions = names.get(feature_id, {})
    version = data.get("version")
    info = versions.get(version) or (versions[max(versions, key=_version_key)] if versions else {})
    return {
        "id": feature_id,
        "display_name": info.get("display_name") or feature_id,
        "runtime": info.get("runtime"),
        "version": version,
        "passed": data.get("passed"),
        "failing_requirements": as_list(data.get("failing requirements")),
        "optional_requirements": as_list(data.get("optional requirements")),
    }


def benchmark_view(results: list) -> list[dict]:
    """The per-test benchmark results without their raw metrics."""
    keys = ("test_name", "engine", "engine_version", "status", "passed", "duration", "message")
    return [{key: result.get(key) for key in keys} for result in results]


def package_version(package_id: str | None, repo_id: str, asset: str, fallback: str | None) -> str | None:
    """The version in a package id, which is ``simready.hf.<namespace>.<dataset>.<asset>.<version>``."""
    prefix = f"simready.hf.{repo_id.replace('/', '.')}.{asset}."
    if package_id and package_id.startswith(prefix):
        return package_id[len(prefix):]
    return fallback


def physics_variants(usd_metadata: dict) -> list[str]:
    variants = usd_metadata.get("Variants", {})
    physics = variants.get("Physics", {}) if isinstance(variants, dict) else {}
    return sorted(physics) if isinstance(physics, dict) else []


def build_record(
    asset: str,
    *,
    url: dict,
    verify: dict,
    header: dict,
    packaging: dict,
    usd_metadata: dict,
    names: dict,
    asset_dir_rel: str | None,
    root_usd_rel: str | None,
    thumbnail_rel: str | None,
) -> dict:
    """One library record. Absent sources are named under ``missing``, never raised."""
    repo_id = url.get("repo_id", "")
    commit = url.get("commit", "")
    features = [
        feature_view(feature_id, data, names)
        for feature_id, data in sorted(verify.get("validation_summary", {}).items())
    ]
    present = {
        "submission_url": bool(url),
        "packaging": bool(packaging),
        "validation_sidecar": verify.get("validation_ok") is not None,
        "benchmark_sidecar": verify.get("benchmark_ok") is not None,
        "root_usd": root_usd_rel is not None,
        "usd_metadata": bool(usd_metadata),
        "thumbnail": thumbnail_rel is not None,
    }
    return {
        "asset_name": asset,
        # The fields a consumer library must retain.
        "source_url": url.get("source_url"),
        "owner": url.get("owner"),
        "version": package_version(
            packaging.get("package_id"), repo_id, asset, f"{repo_id}@{commit[:8]}" if commit else None
        ),
        "license": packaging.get("license"),
        "validation": {
            "profile": header.get("profile_id"),
            "profile_version": header.get("profile_version"),
            "simready_validate_version": header.get("simready_validate_version"),
            "tier_versions": header.get("tier_versions"),
            "passed": verify.get("validation_ok"),
            "features": features,
            "failed_features": [f["id"] for f in features if f["passed"] is False],
        },
        # The rest of what the package carries.
        "usd_author": usd_metadata.get("author"),
        "usd_license": usd_metadata.get("asset_license"),
        "category": usd_metadata.get("category"),
        "asset_type": usd_metadata.get("asset_type"),
        "physics_variants": physics_variants(usd_metadata),
        "benchmark": {
            "passed": verify.get("benchmark_ok"),
            "results": benchmark_view(verify.get("benchmark_summary", [])),
        },
        "package": {
            "package_id": packaging.get("package_id"),
            "format_version": packaging.get("format_version"),
            "repo_id": repo_id or None,
            "commit": commit or None,
        },
        "usd_metadata": usd_metadata,
        "thumbnail": thumbnail_rel,
        "files": {
            "directory": asset_dir_rel,
            "root_usd": root_usd_rel,
            "packaging": f"{asset_dir_rel}/{PACKAGING_FILE}" if asset_dir_rel else None,
            "validation_sidecar": (
                f"{asset_dir_rel}/{METADATA_DIR}/{asset}_validation.json" if present["validation_sidecar"] else None
            ),
            "benchmark_sidecar": (
                f"{asset_dir_rel}/{METADATA_DIR}/{asset}_benchmark.json" if present["benchmark_sidecar"] else None
            ),
        },
        "missing": [name for name, found in present.items() if not found],
    }


# ---------------------------------------------------------------------------
# Putting the library together
# ---------------------------------------------------------------------------


def collect(workspace: Path, submission_file: Path) -> dict:
    """Every record for the workspace, plus what was read to build them."""
    urls = read_submission_urls(submission_file)
    sidecars = read_sidecars(workspace)
    names = load_feature_names()

    def relative(path: Path | None) -> str | None:
        return path.relative_to(workspace).as_posix() if path else None

    records = []
    for asset, verify in sorted(sidecars.items()):
        asset_dir = Path(verify["asset_path"])
        root_usd = find_root_usd(asset_dir, asset)
        records.append(
            build_record(
                asset,
                url=urls.get(asset, {}),
                verify=verify,
                header=read_sidecar_header(asset_dir, asset),
                packaging=read_packaging(asset_dir),
                usd_metadata=read_usd_metadata(root_usd) if root_usd else {},
                names=names,
                asset_dir_rel=relative(asset_dir),
                root_usd_rel=relative(root_usd),
                thumbnail_rel=relative(find_thumbnail(root_usd)) if root_usd else None,
            )
        )
    # verify() wrote the workspace-wide review report while reading the sidecars.
    review_report = workspace / "simready_review_report.html"

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "submission_file": str(submission_file),
        "workspace": str(workspace),
        "datasets": sorted({url["repo_id"] for url in urls.values()}),
        "tool_versions": tool_versions(),
        "review_report": relative(review_report) if review_report.is_file() else None,
        # Listed but not in the workspace, and the reverse: pull reports a failed
        # URL only in its log, so the library says which ones it is missing.
        "not_pulled": sorted(set(urls) - set(sidecars)),
        "not_listed": sorted(set(sidecars) - set(urls)),
        "assets": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workspace", required=True, type=Path, help="the directory given to simready-manage pull")
    parser.add_argument(
        "--submission-file", required=True, type=Path, help="the submissions/<namespace>/<dataset> file that was pulled"
    )
    parser.add_argument("--output", type=Path, help="where to write the library (default: <workspace>/library.json)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show what simready-manage logs while reading")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    workspace = args.workspace.resolve()
    library = collect(workspace, args.submission_file.resolve())
    if not library["assets"]:
        print("No assets found in the workspace manifest. Run simready-manage pull first.", file=sys.stderr)
        return 1

    output = args.output or workspace / "library.json"
    output.write_text(json.dumps(library, indent=2) + "\n", encoding="utf-8")
    print(f"{len(library['assets'])} asset(s) written to {output}")
    for record in library["assets"]:
        if record["missing"]:
            print(f"  {record['asset_name']}: missing {', '.join(record['missing'])}")
    for asset in library["not_pulled"]:
        print(f"  {asset}: listed in the submission file but not in the workspace", file=sys.stderr)
    for asset in library["not_listed"]:
        print(f"  {asset}: in the workspace but not in the submission file", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
