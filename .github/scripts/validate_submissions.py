#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate the definitive Hugging Face package lists changed by a PR."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import NamedTuple
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


USER_AGENT = "simready-central-submission-check"

# Hugging Face answers to these as well, so a submitter who used one is not
# wrong about where the package lives, only about how the catalog spells it.
HUGGING_FACE_ALIASES = frozenset({"hf.co", "www.hf.co", "www.huggingface.co"})
PACKAGING_FILE = "com.nvidia.simready.packaging.json"

# SimReady requires a submitted dataset's namespace and name to be free of dots,
# so that the third and fourth components of a package ID are exactly the
# namespace and the dataset. Hugging Face itself is more permissive than this.
# That is what makes package IDs unique across the whole catalog: two IDs that
# are equal must name the same dataset, hence the same submission file, where the
# uniqueness check can see them. Hugging Face dataset names do contain dots in
# practice (droid_1.0.1); such a dataset has to be renamed before submitting it.
#
# Asset folder names keep their dots, and versions need them. Neither is ever
# parsed out of an ID: the ID is matched against a prefix built from components
# that are already known.
NAMESPACE_PATTERN = r"[A-Za-z0-9_-]+"
NAME_PATTERN = r"[A-Za-z0-9._-]+"
COMMIT_PATTERN = r"[0-9a-f]{40}"

PACKAGE_URL = re.compile(
    r"https://huggingface\.co/datasets/"
    rf"(?P<namespace>{NAMESPACE_PATTERN})/(?P<dataset>{NAMESPACE_PATTERN})/resolve/"
    rf"(?P<commit>{COMMIT_PATTERN})/(?P<asset>{NAME_PATTERN})/" + re.escape(PACKAGING_FILE)
)

# Only used to say what is wrong with a URL that PACKAGE_URL rejected.
URL_SHAPE = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*)://(?P<host>[^/]*)(?P<path>/.*)?")

URL_SHAPE_DESCRIPTION = (
    "https://huggingface.co/datasets/<namespace>/<dataset>/resolve/"
    f"<40-character commit hash>/<asset folder>/{PACKAGING_FILE}"
)

# A package definition is a few hundred bytes of JSON. Anything remotely close
# to this ceiling is not one, and reading it would be a way to make the check
# spend the job's memory on a submitter's file.
MAX_DEFINITION_BYTES = 1 << 20


class PackageLine(NamedTuple):
    """One validated package URL, with the line it came from."""

    line_number: int
    url: str
    commit: str
    asset: str


class Finding(NamedTuple):
    """One thing wrong, and where it is.

    Keeping the location apart from the sentence is what lets GitHub put the
    sentence on the offending line of the offending file, rather than on the
    workflow that happened to run the check.
    """

    message: str
    path: str | None = None
    line: int | None = None

    def described(self) -> str:
        """The finding as one line, for a reader without the file open."""
        if self.path and self.line:
            return f"{self.path}:{self.line} {self.message}"
        if self.path:
            return f"{self.path} {self.message}"
        return self.message


class ValidationError(Exception):
    """A reason the submission was rejected.

    `findings` carries the individual findings when there is more than one, so
    they can be reported separately rather than as one run-on sentence. A check
    that has only one thing to say leaves it empty and the message stands alone.
    """

    def __init__(self, message: str, findings: list[Finding] | None = None) -> None:
        super().__init__(message)
        self.findings = findings or []


def rejection(message: str, path: str | None = None, line: int | None = None) -> ValidationError:
    """A rejection with one finding, anchored wherever there is something to point at."""
    finding = Finding(message, path, line)
    return ValidationError(finding.described(), [finding])


def escape_data(value: str) -> str:
    """Escape a workflow command's message, which is otherwise ended by a newline."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    """Escape a workflow command's property, where a comma or colon ends the field."""
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def report_failure(exc: Exception) -> None:
    """Say why, where the answer is visible without opening the job log.

    The log is the only place this used to appear, so a submitter saw a red
    cross and had to go digging through Actions to find out what was wrong with
    their submission. An ::error:: annotation puts each finding on the pull
    request itself, and the step summary leaves a readable copy on the run.

    One annotation per finding, not one for the lot: a submission with eighteen
    bad lines should list eighteen, not bury them in a single line. Each one
    carries the file and line it came from, so GitHub shows it against the URL
    that caused it instead of against the workflow file.
    """
    findings = list(getattr(exc, "findings", []) or []) or [Finding(str(exc))]
    print(f"Submission validation failed: {exc}", file=sys.stderr)

    for finding in findings:
        location = ""
        if finding.path:
            location = f" file={escape_property(finding.path)}"
            location += f",line={finding.line}" if finding.line else ""
        print(f"::error{location}::{escape_data(finding.message)}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["## Submission validation failed", ""]
    lines += [f"- {finding.described()}" for finding in findings]
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass  # A missing summary file is not a reason to fail differently.


def git(*args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=text,
    )
    if result.returncode:
        detail = result.stderr.strip() if text else result.stderr.decode(errors="replace").strip()
        raise ValidationError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def changed_submission_files(base: str, head: str) -> list[tuple[str, str]]:
    """Return the changed submissions/<namespace>/<dataset> files.

    A pull request that changes no submission file is not a submission, and the
    caller skips validation rather than failing: the repository also receives
    documentation, tooling, and workflow changes.

    Mixing submission files with unrelated files is still rejected. A submission
    pull request is reviewed and published as one self-contained catalog change,
    so it must not carry changes that the review pipeline does not inspect.
    """
    output = git("diff", "--name-status", "--no-renames", "-z", base, head, text=False)
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValidationError("Could not read the list of files changed by this pull request.")

    changed: list[tuple[str, str]] = []
    unrelated: list[str] = []
    for offset in range(0, len(fields), 2):
        status = fields[offset].decode("ascii", errors="replace")
        path = fields[offset + 1].decode("utf-8", errors="strict")
        parts = PurePosixPath(path).parts
        if len(parts) == 3 and parts[0] == "submissions":
            if status not in {"A", "M", "D"}:
                raise rejection(f"has an unsupported change type {status!r}.", path)
            changed.append((status, path))
        else:
            unrelated.append(path)

    if changed and unrelated:
        raise ValidationError(
            "A submission pull request must change only submissions/<namespace>/<dataset> "
            f"files. It also changes: {', '.join(sorted(unrelated))}. Move those changes "
            "into a separate pull request."
        )
    return changed


def submission_paths(revision: str) -> list[str]:
    """Every submissions/<namespace>/<dataset> file in one revision of the tree."""
    output = git("ls-tree", "-r", "--name-only", "-z", revision, "submissions/", text=False)
    paths = [field.decode("utf-8", errors="strict") for field in output.split(b"\0") if field]
    return [path for path in paths if len(PurePosixPath(path).parts) == 3]


def check_one_file_per_dataset(revision: str, changed: list[tuple[str, str]]) -> None:
    """Reject two submission files that name one dataset in different capitalisation.

    Git keeps `submissions/Alice/assets` and `submissions/alice/assets` apart, and
    Hugging Face does not: both name one dataset. Left alone they would be two
    catalog entries for it, and the package IDs each pins would collide while
    each file on its own looked perfectly consistent.

    Only a collision this pull request has a hand in is reported. A submission
    should not be rejected for the state of files it does not touch.
    """
    by_name: dict[str, list[str]] = {}
    for path in submission_paths(revision):
        by_name.setdefault(path.lower(), []).append(path)

    changed_paths = {path for _, path in changed}
    for paths in sorted(by_name.values()):
        if len(paths) < 2 or not changed_paths.intersection(paths):
            continue
        listed = " and ".join(sorted(paths))
        raise ValidationError(
            f"{listed} differ only in capitalisation, so they are two submissions for one Hugging "
            "Face dataset: Hugging Face finds a dataset whatever capitalisation you ask for. Keep "
            "one of them."
        )


def file_at(revision: str, path: str) -> str | None:
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:{path}"],
        check=False,
        capture_output=True,
    )
    if exists.returncode:
        return None
    content = git("show", f"{revision}:{path}", text=False)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise rejection("is not UTF-8 text.", path) from exc


def next_link(header: str | None) -> str | None:
    if not header:
        return None
    for item in header.split(","):
        match = re.match(r'\s*<([^>]+)>;\s*rel="?next"?', item)
        if match:
            return match.group(1)
    return None


def explain_url(url: str) -> str:
    """Say which part of a rejected URL is wrong, not merely that it is.

    A submitter who pasted the /blob/ link from their browser, or a branch name
    where a commit hash belongs, gets told exactly that. Falling back to "this
    is not a valid URL" leaves them comparing two long strings by eye.
    """
    if "?" in url or "#" in url:
        return (
            "carries a query string or fragment. A package-definition URL must be exactly "
            f"{URL_SHAPE_DESCRIPTION} with nothing appended."
        )

    shape = URL_SHAPE.fullmatch(url)
    if not shape:
        return f"is not a URL. Each line must be a package-definition URL: {URL_SHAPE_DESCRIPTION}."

    scheme = shape["scheme"].lower()
    if scheme != "https":
        return f"uses the {scheme!r} scheme. Only https is accepted."

    host = shape["host"].lower()
    if host in HUGGING_FACE_ALIASES:
        return (
            f"points at the host {host!r}. Hugging Face answers to that name too, so the link "
            f"works, but the catalog uses a single spelling. Replace {host} with huggingface.co."
        )
    if host != "huggingface.co":
        return (
            f"points at the host {host!r}. Packages are catalogued from huggingface.co only, so "
            "the URL must begin with https://huggingface.co/datasets/."
        )

    path = shape["path"] or ""
    segments = [segment for segment in path.split("/") if segment]
    if not segments or segments[0] != "datasets":
        return (
            "is not a Hugging Face dataset URL: its path does not begin with /datasets/. A model "
            "or Space URL cannot be catalogued."
        )
    segments = segments[1:]

    if len(segments) > 2 and segments[2] in {"blob", "tree"}:
        return (
            f"is a Hugging Face web-view link (/{segments[2]}/). Use the download link instead: "
            "replace that part of the URL with /resolve/."
        )
    if len(segments) < 6 or segments[2] != "resolve":
        return (
            f"does not have the shape {URL_SHAPE_DESCRIPTION}. Copy the URL of the "
            f"{PACKAGING_FILE} file from the asset folder at the commit you are submitting."
        )

    namespace, dataset, _, revision, asset = segments[0], segments[1], segments[2], segments[3], segments[4]
    filename = "/".join(segments[5:])

    if "." in namespace:
        return (
            f"has the namespace {namespace!r}, which contains a dot. SimReady requires a "
            "submitted dataset's namespace and name to be free of dots, so that the package ID "
            "simready.hf.<namespace>.<dataset>.<asset folder>.<version> is unambiguous."
        )
    if not re.fullmatch(NAMESPACE_PATTERN, namespace):
        return (
            f"has the namespace {namespace!r}. A Hugging Face namespace may contain only letters, "
            "digits, hyphens and underscores."
        )
    if "." in dataset:
        return (
            f"has the dataset name {dataset!r}, which contains a dot. SimReady requires a "
            "submitted dataset's namespace and name to be free of dots, so that the package ID "
            "simready.hf.<namespace>.<dataset>.<asset folder>.<version> is unambiguous. Rename "
            "the dataset on Hugging Face before submitting it."
        )
    if not re.fullmatch(NAMESPACE_PATTERN, dataset):
        return (
            f"has the dataset name {dataset!r}. A submitted dataset's name may contain only "
            "letters, digits, hyphens and underscores."
        )

    if not re.fullmatch(COMMIT_PATTERN, revision):
        if re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            return (
                f"writes the commit hash {revision!r} in upper case. Write it in lower case, "
                "exactly as Hugging Face shows it."
            )
        return (
            f"uses the revision {revision!r}, which is not a commit hash. Branch names, tags and "
            "shortened hashes can be moved to point at different content, so a catalogued package "
            "would stop being reproducible. Use the full 40-character commit hash."
        )

    if not re.fullmatch(NAME_PATTERN, asset):
        return (
            f"has the asset folder {asset!r}. An asset folder name may contain only letters, "
            "digits, dots, hyphens and underscores."
        )
    if filename != PACKAGING_FILE:
        return (
            f"ends with {filename!r}. A submission lists package definitions, so every URL must "
            f"end with {PACKAGING_FILE}."
        )
    return f"does not have the shape {URL_SHAPE_DESCRIPTION}."


def dataset_access_error(dataset: str, exc: HTTPError, what: str) -> ValidationError:
    """Turn a bare HTTP status into something a submitter can act on.

    Nothing here has to explain how the dataset was chosen. Hugging Face is not
    asked until the submission file has parsed and every URL in it has been shown
    to name the same dataset as the file, so by this point that much is settled.
    """
    if exc.code == 401:
        message = (
            f"The Hugging Face dataset {dataset} is private. Submitted datasets must be publicly "
            "readable: the catalog checks, and everyone who uses the catalog, download the "
            f"packages directly from Hugging Face. Make {dataset} public and re-run the checks."
        )
    elif exc.code == 403:
        message = (
            f"The Hugging Face dataset {dataset} is gated: it asks visitors to accept conditions "
            "before downloading. Submitted datasets must be readable without a gate. Remove the "
            f"access gate on {dataset} and re-run the checks."
        )
    elif exc.code == 404:
        message = (
            f"There is no Hugging Face dataset {dataset}. The submission file and every URL in "
            "it agree on that name, so either the dataset has not been created yet or the name "
            "is wrong in both."
        )
    else:
        message = (
            f"Hugging Face answered HTTP {exc.code} ({exc.reason}) while {what} for {dataset}. If "
            "this is not a temporary Hugging Face failure, check that the dataset is public."
        )
    return ValidationError(message)


def commit_history(dataset: str) -> list[str]:
    url = f"https://huggingface.co/api/datasets/{quote(dataset, safe='/')}/commits/main"
    commits: list[str] = []
    try:
        while url:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=30) as response:
                page = json.load(response)
                if not isinstance(page, list):
                    raise ValidationError(
                        f"Hugging Face returned commit data for {dataset} that this check does not "
                        "understand. Re-run the checks; if it keeps happening, open an issue."
                    )
                commits.extend(item["id"] for item in page if isinstance(item, dict) and "id" in item)
                url = next_link(response.headers.get("Link"))
    except HTTPError as exc:
        raise dataset_access_error(dataset, exc, "reading the commit history") from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"Could not reach Hugging Face while reading the commit history of {dataset}: {exc}. "
            "This is usually a temporary network failure; re-run the checks."
        ) from exc
    return commits


def declared_package_id(url: str, dataset: str) -> str:
    """Read the package_id out of one package definition on Hugging Face.

    The URL is pinned to a commit hash, so this reads exactly the bytes the
    submission points at and nothing that can later change underneath it.
    """
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            # Hugging Face redirects large files to a CDN. Following that is
            # fine; being redirected off https is not.
            if not response.geturl().lower().startswith("https://"):
                raise ValidationError(
                    f"Reading {url} was redirected away from https. Refusing to download the "
                    "package definition over an insecure connection."
                )
            payload = response.read(MAX_DEFINITION_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise ValidationError(
                f"There is no package definition at {url}. The dataset {dataset} exists, so check "
                "the asset folder name and the commit: names inside a dataset are stored in git "
                "and are case-sensitive, unlike the dataset's own name."
            ) from exc
        raise dataset_access_error(dataset, exc, f"downloading {url}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"Could not reach Hugging Face while downloading {url}: {exc}. This is usually a "
            "temporary network failure; re-run the checks."
        ) from exc

    if len(payload) > MAX_DEFINITION_BYTES:
        raise ValidationError(
            f"The package definition at {url} is larger than {MAX_DEFINITION_BYTES} bytes. A "
            f"{PACKAGING_FILE} is a small JSON file describing one package."
        )
    try:
        document = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValidationError(f"The package definition at {url} is not UTF-8 text.") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"The package definition at {url} is not valid JSON: {exc}.") from exc

    if not isinstance(document, dict):
        raise ValidationError(
            f"The package definition at {url} is not a JSON object, so it has no package_id."
        )
    package_id = document.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise ValidationError(
            f"The package definition at {url} has no package_id. Every {PACKAGING_FILE} must "
            "declare a package_id of the form "
            "simready.hf.<namespace>.<dataset>.<asset folder>.<version>."
        )
    return package_id


def parse_submission(path: str, content: str) -> list[PackageLine]:
    """Check every line of a submission file without touching the network."""
    _, namespace, dataset_name = PurePosixPath(path).parts
    for part, what in ((namespace, "namespace"), (dataset_name, "dataset name")):
        if "." in part:
            raise rejection(
                f"has the {what} {part!r}, which contains a dot. "
                "SimReady requires a submitted dataset's namespace and name to be free of dots, "
                "so that the package ID simready.hf.<namespace>.<dataset>.<asset folder>."
                "<version> is unambiguous. Rename the dataset on Hugging Face before submitting "
                "it.",
                path,
            )
        if not re.fullmatch(NAMESPACE_PATTERN, part):
            raise rejection(
                "is not named submissions/<namespace>/<dataset>. Name it after the Hugging Face "
                "dataset it catalogues, using only letters, digits, hyphens and underscores.",
                path,
            )
    expected_dataset = f"{namespace}/{dataset_name}"

    entries: list[PackageLine] = []
    seen_urls: dict[str, int] = {}

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        if not raw_line.strip():
            continue
        if raw_line != raw_line.strip():
            raise rejection("has leading or trailing whitespace.", path, line_number)
        match = PACKAGE_URL.fullmatch(raw_line)
        if not match:
            raise rejection(explain_url(raw_line), path, line_number)
        url_dataset = f"{match['namespace']}/{match['dataset']}"
        if url_dataset != expected_dataset:
            raise rejection(
                f"refers to {url_dataset}, not {expected_dataset}. The submission file name "
                "decides which dataset it catalogues, so every URL in it must be for "
                f"{expected_dataset}.",
                path,
                line_number,
            )
        if raw_line in seen_urls:
            raise rejection(
                f"duplicates an earlier URL, already listed on line {seen_urls[raw_line]}.",
                path,
                line_number,
            )
        seen_urls[raw_line] = line_number
        entries.append(PackageLine(line_number, raw_line, match["commit"], match["asset"]))

    commit_blocks: list[tuple[str, list[str]]] = []
    closed_commits: set[str] = set()
    for entry in entries:
        if not commit_blocks or commit_blocks[-1][0] != entry.commit:
            if entry.commit in closed_commits:
                raise rejection(
                    "repeats a non-contiguous commit block. Keep all URLs for commit "
                    f"{entry.commit} together, in one run of lines.",
                    path,
                    entry.line_number,
                )
            if commit_blocks:
                closed_commits.add(commit_blocks[-1][0])
            commit_blocks.append((entry.commit, []))
        commit_blocks[-1][1].append(entry.asset)

    for commit, assets in commit_blocks:
        if assets != sorted(assets):
            raise rejection(
                f"lists the assets in commit block {commit} out of order. List them in ascending "
                "order of asset folder name.",
                path,
            )
    return entries


def check_dataset_commits(
    path: str, entries: list[PackageLine], history_cache: dict[str, list[str]]
) -> None:
    """Check every listed commit is on the dataset's main branch, in order."""
    if not entries:
        return
    _, namespace, dataset_name = PurePosixPath(path).parts
    dataset = f"{namespace}/{dataset_name}"

    if dataset not in history_cache:
        history_cache[dataset] = commit_history(dataset)
    position = {commit: index for index, commit in enumerate(history_cache[dataset])}

    missing = [entry for entry in entries if entry.commit not in position]
    if missing:
        raise ValidationError(
            f"{path} lists {len(missing)} URL(s) whose commit is not on the main branch of "
            f"{dataset}.",
            [
                Finding(
                    f"uses the commit {entry.commit}, which is not on the main branch of "
                    f"{dataset}. Only commits on main can be catalogued. Check that the commit is "
                    "merged rather than left on a pull-request branch, and that the dataset's "
                    "history has not been rewritten since you copied the URL.",
                    path,
                    entry.line_number,
                )
                for entry in missing
            ],
        )

    block_positions = [
        position[commit] for commit, _ in _commit_blocks(entries)
    ]
    if block_positions != sorted(block_positions, reverse=True):
        raise rejection(
            "lists its commit blocks newest first. List the oldest commit's URLs first, so the "
            "file reads as the history of the submission.",
            path,
        )


def _commit_blocks(entries: list[PackageLine]) -> list[tuple[str, list[PackageLine]]]:
    blocks: list[tuple[str, list[PackageLine]]] = []
    for entry in entries:
        if not blocks or blocks[-1][0] != entry.commit:
            blocks.append((entry.commit, []))
        blocks[-1][1].append(entry)
    return blocks


def check_package_ids(path: str, entries: list[PackageLine]) -> None:
    """Check the package ID each URL declares, and that they are all distinct.

    The package ID is what has to be unique, not the asset folder name: two
    submitters may both call a folder `chair` and mean different chairs. The ID
    is built from the namespace, the dataset and the folder, and the submission
    file name pins the first two, so IDs cannot collide across submission files
    and uniqueness within this file is the whole check.

    The prefix is constructed rather than parsed. Asset folder names and versions
    contain dots, so splitting an ID on dots would not say where one component
    ends and the next begins.

    Package IDs are compared without regard to case, because the name of a
    Hugging Face dataset can be recapitalised by renaming it. Definitions
    published before such a rename keep the old capitalisation for ever, and both
    have to be able to sit in one file.
    """
    _, namespace, dataset_name = PurePosixPath(path).parts
    dataset = f"{namespace}/{dataset_name}"
    owners: dict[str, PackageLine] = {}

    for entry in entries:
        package_id = declared_package_id(entry.url, dataset)
        prefix = f"simready.hf.{namespace}.{dataset_name}.{entry.asset}."
        if not package_id.lower().startswith(prefix.lower()):
            raise rejection(
                f"points at a package definition whose package_id is {package_id!r}, but a "
                f"package published from the {entry.asset!r} folder of {dataset} must have a "
                f"package_id starting with {prefix!r}, followed by its version. Correct the "
                "package_id in the package definition on Hugging Face so that it matches where "
                "the package is published.",
                path,
                entry.line_number,
            )
        version = package_id[len(prefix):]
        if not version:
            raise rejection(
                f"points at a package definition whose package_id is {package_id!r}, which has no "
                f"version after {prefix!r}. A package_id must be "
                "simready.hf.<namespace>.<dataset>.<asset folder>.<version>.",
                path,
                entry.line_number,
            )
        if package_id.lower() in owners:
            first = owners[package_id.lower()]
            raise rejection(
                f"declares the package ID {package_id!r}, which line {first.line_number} already "
                "declares. A package ID identifies one package exactly, and is compared without "
                "regard to case, so two commits cannot publish it with different content. Give "
                f"the newer package a new version in place of {version!r}.",
                path,
                entry.line_number,
            )
        owners[package_id.lower()] = entry


def package_urls(content: str | None) -> list[str]:
    if content is None:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def set_step_output(name: str, value: str) -> None:
    """Expose a value to later workflow steps when running inside GitHub Actions."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        return
    with open(output_file, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def write_url_list(path: Path, urls: set[str]) -> None:
    ordered = sorted(urls)
    path.write_text("\n".join(ordered) + ("\n" if ordered else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_commit")
    parser.add_argument("proposed_commit")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()

    try:
        changed = changed_submission_files(args.base_commit, args.proposed_commit)
        if not changed:
            set_step_output("submission-files-changed", "false")
            # Still write the lists: the workflow uploads this directory
            # unconditionally and treats a missing one as an error.
            args.output_directory.mkdir(parents=True, exist_ok=True)
            write_url_list(args.output_directory / "added-packages.txt", set())
            write_url_list(args.output_directory / "removed-packages.txt", set())
            print("No submission files changed; this pull request is not a submission.")
            return 0
        set_step_output("submission-files-changed", "true")
        check_one_file_per_dataset(args.proposed_commit, changed)
        history_cache: dict[str, list[str]] = {}
        added_urls: set[str] = set()
        removed_urls: set[str] = set()
        url_count = 0

        # Every syntax check first, then the network: a submitter with a typo
        # should not wait for one download per URL to be told about it.
        parsed: list[tuple[str, list[PackageLine]]] = []
        for status, path in changed:
            if status == "D":
                print(f"Validated removed submission file: {path}")
                proposed_content = None
            else:
                proposed_content = file_at(args.proposed_commit, path)
                if proposed_content is None:
                    raise ValidationError(f"Could not read the proposed submission file {path}.")
                entries = parse_submission(path, proposed_content)
                parsed.append((path, entries))
                url_count += len(entries)
                print(f"Validated {path}: {len(entries)} package URL(s)")

            base_urls = set(package_urls(file_at(args.base_commit, path)))
            proposed_urls = set(package_urls(proposed_content))
            added_urls.update(proposed_urls - base_urls)
            removed_urls.update(base_urls - proposed_urls)

        for path, entries in parsed:
            check_dataset_commits(path, entries, history_cache)
        for path, entries in parsed:
            check_package_ids(path, entries)

        args.output_directory.mkdir(parents=True, exist_ok=True)
        write_url_list(args.output_directory / "added-packages.txt", added_urls)
        write_url_list(args.output_directory / "removed-packages.txt", removed_urls)
    except (ValidationError, UnicodeDecodeError) as exc:
        report_failure(exc)
        return 1

    print(f"Submission validation passed: {len(changed)} file(s), {url_count} proposed package URL(s).")
    print(f"Package changes: {len(added_urls)} added, {len(removed_urls)} removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
