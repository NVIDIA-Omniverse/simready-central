#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check the content of the packages a pull request adds to the catalog.

The package list check that runs first reads the submission file and the package
definition each URL names, and nothing else. This reads what those URLs actually
publish: what kinds of file the asset folder holds, whether each file is what its
name says, and whether the dataset carries an `.lfsconfig` that would send a
download somewhere the catalog cannot vouch for.

Only public Hugging Face metadata and the first few hundred bytes of each file are
read. Nothing submitted is downloaded whole, parsed, or executed. What the files
hold beyond their first bytes is the review's business: the reviewer pulls every
package and audits its content before deciding the pull request.
"""

from __future__ import annotations

import argparse
import codecs
import json
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from validate_submissions import (
    Finding,
    PACKAGE_URL,
    ValidationError,
    dataset_access_error,
    hf_headers,
    next_link,
    open_hugging_face,
    report_failure,
)

HUGGING_FACE = "https://huggingface.co/"

# What a SimReady package may contain. Compared without regard to case.
ALLOWED_EXTENSIONS = frozenset({
    ".usd", ".usda", ".usdc", ".png", ".jpeg", ".jpg", ".exr", ".glb",
    ".m4a", ".mp3", ".wav", ".mdl", ".toml", ".json", ".txt",
})

# Only a .lfsconfig at the root of a repository does anything: Git LFS looks for
# it in the working tree, the index and HEAD, never in a subdirectory. One inside
# a submitted asset folder is caught anyway, as a file with no allowed extension.
LFSCONFIG = ".lfsconfig"


TREE_PAGE_SIZE = 100      # the largest page the tree API will serve
MAX_ASSET_PAGES = 50      # 5,000 files in one asset folder
MAX_TREE_REQUESTS = 300   # per run, inside Hugging Face's 500-per-300s api budget
HEAD_BYTES = 512          # enough to reach the "ustar" a tar header carries at 257
MAX_HEAD_REQUESTS = 2000  # per run, inside the separate 3,000-per-300s resolvers budget
EXAMPLES_PER_FINDING = 5

# What a file has to start with to be the format its name claims. A .usd is
# either of the two spellings of USD, crate or ASCII, so it accepts both. A .glb
# is glTF in its self-contained binary form, and the only form allowed: the JSON
# form (.gltf) points at .bin buffers beside it, and a .bin has no signature to
# check against its name.
SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".exr": (b"\x76\x2f\x31\x01",),
    ".usdc": (b"PXR-USDC",),
    ".usda": (b"#usda",),
    ".usd": (b"PXR-USDC", b"#usda"),
    ".glb": (b"glTF",),
}

# .json, .toml, .mdl and .txt carry no signature, so they are checked the other
# way round: the file has to actually be text. That means decoding as UTF-8, and
# holding no control character but tab, newline and carriage return. This is not
# a guess about what binaries look like, it is what these formats permit. TOML
# disallows "the control characters other than tab (U+0000 to U+0008, U+000A to
# U+001F, U+007F)" in strings and in comments alike, and JSON requires U+0000 to
# U+001F to be escaped. A .txt has no specification to cite, so it is held to
# the same line: a note in UTF-16 is rejected, but so is a binary that only
# carries the name.
TEXT_CONTROLS = frozenset({0x09, 0x0A, 0x0D})
TEXT_EXTENSIONS = frozenset({".json", ".toml", ".mdl", ".txt"})

# Between them those two rules already reject every archive and executable worth
# naming: a zip on the U+0003 in its header, a rar on U+001A, an ELF on U+007F, a
# tar on the NULs padding its header, and gzip, xz, 7z, Mach-O and PE on not being
# UTF-8 at all. Only these two begin with printable ASCII and so need saying.
BINARY_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"BZh", "a bzip2 archive"),
    (b"#!", "a script"),
)


# One asset folder at one commit: what a file list is read for.
AssetKey = tuple[str, str, str]


class AddedPackage(NamedTuple):
    """One package URL this pull request adds, and where it was added."""

    url: str
    path: str | None
    line: int | None
    dataset: str
    commit: str
    asset: str

    def asset_key(self) -> AssetKey:
        """The folder this URL names, at the commit it pins."""
        return (self.dataset, self.commit, self.asset)


class RequestBudget:
    """How many requests this run may still make to Hugging Face.

    Two counters, because Hugging Face meters the two paths separately and far
    more generously for downloads than for its API. A submission large enough to
    exhaust either is one that should have been split up, and saying so is more
    use to a submitter than letting the run collect a rate-limit failure.
    """

    def __init__(self) -> None:
        self.trees = 0
        self.heads = 0

    def spend_tree(self) -> None:
        self.trees += 1
        if self.trees > MAX_TREE_REQUESTS:
            raise ValidationError(
                f"These checks read one file list per asset folder and may ask Hugging Face for "
                f"at most {MAX_TREE_REQUESTS} of them in one run. This pull request needs more. "
                "Submit one upload's worth of URLs per pull request."
            )

    def spend_head(self) -> None:
        self.heads += 1
        if self.heads > MAX_HEAD_REQUESTS:
            raise ValidationError(
                f"These checks read the first {HEAD_BYTES} bytes of every submitted file and may "
                f"do so at most {MAX_HEAD_REQUESTS} times in one run. This pull request adds more "
                "files than that. Submit one upload's worth of URLs per pull request."
            )


def read_added_packages(path: Path) -> list[AddedPackage]:
    """Read the added URLs, each with the submission line that added it."""
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"Could not read the list of added packages at {path}: {exc}.")
    except json.JSONDecodeError as exc:
        raise ValidationError(f"The list of added packages at {path} is not valid JSON: {exc}.")
    if not isinstance(records, list):
        raise ValidationError(f"The list of added packages at {path} is not a JSON list.")

    packages = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("url"), str):
            raise ValidationError(f"The list of added packages at {path} has an entry with no URL.")
        url = record["url"]
        match = PACKAGE_URL.fullmatch(url)
        if not match:
            # The package list check has already passed at this point, so this
            # cannot be a submitter's typo; it means the two jobs disagree.
            raise ValidationError(f"The added package URL {url} is not one these checks recognise.")
        line = record.get("line")
        packages.append(
            AddedPackage(
                url=url,
                path=record.get("path") if isinstance(record.get("path"), str) else None,
                line=line if isinstance(line, int) else None,
                dataset=f"{match['namespace']}/{match['dataset']}",
                commit=match["commit"],
                asset=match["asset"],
            )
        )
    return packages


def hugging_face_json(url: str, dataset: str, what: str) -> tuple[object, str | None]:
    """Read one page of Hugging Face metadata, and where the next one is."""
    # As the org when CI holds a token, anonymously otherwise -- the same rule
    # the validator applies, so a dataset it could read is one this can scan.
    request = Request(url, headers=hf_headers())
    try:
        with open_hugging_face(request) as response:
            return json.load(response), next_link(response.headers.get("Link"))
    except HTTPError as exc:
        raise dataset_access_error(dataset, exc, what) from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"Could not reach Hugging Face while {what} for {dataset}: {exc}. This is usually a "
            "temporary network failure; re-run the checks."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Hugging Face returned something other than JSON while {what} for {dataset}: {exc}. "
            "Re-run the checks; if it keeps happening, open an issue."
        ) from exc


def parse_tree_page(payload: object, dataset: str) -> list[str]:
    """Turn one page of a Hugging Face file listing into file paths.

    Directories are dropped: they have no extension to check, and every file in
    them is listed in its own right.
    """
    if not isinstance(payload, list):
        raise ValidationError(
            f"Hugging Face returned a file list for {dataset} that this check does not understand. "
            "Re-run the checks; if it keeps happening, open an issue."
        )
    files = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        path = entry.get("path")
        if isinstance(path, str):
            files.append(path)
    return files


def tree_pages(url: str, dataset: str, what: str, budget: RequestBudget, limit: int) -> list[str]:
    """Follow a paginated file listing to its end, or to the page limit."""
    files: list[str] = []
    pages = 0
    while url:
        budget.spend_tree()
        payload, following = hugging_face_json(url, dataset, what)
        files += parse_tree_page(payload, dataset)
        pages += 1
        if pages >= limit:
            if following:
                raise ValidationError(
                    f"Hugging Face lists more than {limit * TREE_PAGE_SIZE} files while {what} for "
                    f"{dataset}. These checks read the whole listing, so something this large "
                    "cannot be checked automatically. Ask NVIDIA to review this submission by hand."
                )
            break
        # Being sent off Hugging Face mid-listing is not pagination.
        url = following if following and following.startswith(HUGGING_FACE) else ""
    return files


def asset_tree(dataset: str, commit: str, asset: str, budget: RequestBudget) -> list[str] | None:
    """Every file in one asset folder, or None when the folder is not there."""
    url = (
        f"https://huggingface.co/api/datasets/{quote(dataset, safe='/')}/tree/{commit}/"
        f"{quote(asset, safe='/')}?recursive=true&limit={TREE_PAGE_SIZE}"
    )
    what = f"listing the files of {asset} at commit {commit}"
    try:
        return tree_pages(url, dataset, what, budget, MAX_ASSET_PAGES)
    except ValidationError as exc:
        if isinstance(exc.__cause__, HTTPError) and exc.__cause__.code == 404:
            return None
        raise


def root_names(dataset: str, commit: str, budget: RequestBudget) -> set[str]:
    """The names at the root of a dataset, which is where an .lfsconfig counts."""
    url = (
        f"https://huggingface.co/api/datasets/{quote(dataset, safe='/')}/tree/{commit}"
        f"?limit={TREE_PAGE_SIZE}"
    )
    what = f"listing the root of the dataset at commit {commit}"
    return set(tree_pages(url, dataset, what, budget, MAX_ASSET_PAGES))


def file_head(dataset: str, commit: str, path: str, budget: RequestBudget) -> bytes:
    """The first bytes of one file, read at the commit the submission pinned.

    A range request, so a texture costs the same as a text file no matter how
    large it is. A file shorter than the window is not an error; it just answers
    with everything it has.
    """
    budget.spend_head()
    url = (
        f"https://huggingface.co/datasets/{quote(dataset, safe='/')}/resolve/{commit}/"
        f"{quote(path, safe='/')}"
    )
    request = Request(url, headers={**hf_headers(), "Range": f"bytes=0-{HEAD_BYTES - 1}"})
    try:
        with open_hugging_face(request) as response:
            # Hugging Face redirects file reads to a CDN. Following that is fine;
            # being redirected off https is not.
            if not response.geturl().lower().startswith("https://"):
                raise ValidationError(
                    f"Reading {url} was redirected away from https. Refusing to read submitted "
                    "content over an insecure connection."
                )
            return response.read(HEAD_BYTES)
    except HTTPError as exc:
        raise dataset_access_error(dataset, exc, f"reading {path} at commit {commit}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"Could not reach Hugging Face while reading {path} at commit {commit} of {dataset}: "
            f"{exc}. This is usually a temporary network failure; re-run the checks."
        ) from exc


def text_problem(head: bytes) -> str | None:
    """What stops these bytes from being text, if anything.

    Decoded a piece at a time on purpose: the window can cut a multi-byte
    character in half, and a whole-string decode would call a perfectly good file
    malformed for ending where it was asked to end.
    """
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head, False)
    except UnicodeDecodeError:
        return "is not UTF-8 text"
    for character in text:
        code = ord(character)
        if code == 0:
            return "contains a NUL byte"
        if (code < 0x20 and code not in TEXT_CONTROLS) or code == 0x7F:
            return f"contains the control character U+{code:04X}"
    for signature, description in BINARY_SIGNATURES:
        if head.startswith(signature):
            return f"begins like {description}"
    return None


def mismatch(path: str, head: bytes) -> str | None:
    """What the file really looks like, when that is not what its name claims.

    An archive renamed to .png is still an archive, and this is what catches that
    on the pull request. It reads the head only: a file built to carry a valid
    header and hide something after it gets through this check.
    """
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in SIGNATURES:
        if not any(head.startswith(signature) for signature in SIGNATURES[suffix]):
            return f"is not {suffix} content"
    elif suffix == ".wav":
        if not (head.startswith(b"RIFF") and head[8:12] == b"WAVE"):
            return "is not .wav content"
    elif suffix == ".m4a":
        if head[4:8] != b"ftyp":
            return "is not .m4a content"
    elif suffix == ".mp3":
        sync = len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0
        if not (head.startswith(b"ID3") or sync):
            return "is not .mp3 content"
    elif suffix in TEXT_EXTENSIONS:
        return text_problem(head)
    return None


def summarised(package: AddedPackage, paths: list[str], sentence: str, advice: str,
               title: str = "") -> Finding:
    """One finding for one kind of problem, however many files show it.

    A package whose every texture is wrong is one mistake, not three hundred, and
    three hundred annotations would bury the other packages in the same list.

    The title says what is wrong and how many; the rule says how to fix it, once;
    and each file is listed under it with what is wrong with that one. Keeping
    the count in the title is what lets a reviewer weigh two findings without
    reading either.

    They used to be collapsed into "For example a, b, c, and 15 more" -- a row of
    names with nothing said about any of them, and no way to tell a file apart
    from the rule it broke. A path may arrive on its own or paired with its own
    explanation; on its own it takes the shared one, which is what they have in
    common.
    """
    entries = sorted((p, "") if isinstance(p, str) else tuple(p) for p in paths)
    shown = entries[:EXAMPLES_PER_FINDING]
    return Finding(
        advice,
        package.path,
        package.line,
        f"{title} ({len(entries)} file(s))" if title else "",
        files=tuple((path, note or sentence) for path, note in shown),
        more=len(entries) - len(shown),
    )


def check_asset_present(package: AddedPackage, files: list[str] | None) -> list[Finding]:
    """Say so when there is nothing at the path the URL names."""
    if files:
        return []
    return [
        Finding(
            f"Nothing at that path in commit {package.commit} of {package.dataset}. Names "
            "inside a dataset are stored in git and are case-sensitive, unlike the dataset's "
            "own name.",
            package.path,
            package.line,
            f"SCAN.005 {package.asset!r} holds no files",
        )
    ]


def split_by_extension(files: list[str]) -> tuple[list[str], list[str]]:
    """The files a package may hold, and the ones it may not."""
    allowed, rejected = [], []
    for path in files:
        if PurePosixPath(path).suffix.lower() in ALLOWED_EXTENSIONS:
            allowed.append(path)
        else:
            rejected.append(path)
    return allowed, rejected


def check_no_lfsconfig(package: AddedPackage, names: set[str]) -> list[Finding]:
    """One finding per dataset commit: this is a fact about the repository."""
    if LFSCONFIG not in names:
        return []
    return [
        Finding(
            "It tells Git LFS to fetch this dataset's content from a server other than "
            "Hugging Face, which the catalog cannot vouch for. Remove it and submit the URL "
            "of the new commit.",
            package.path,
            package.line,
            f"SCAN.006 {package.dataset} has a {LFSCONFIG} at its root",
        )
    ]


def check_file_types(package: AddedPackage, files: list[str], budget: RequestBudget) -> list[Finding]:
    """Read enough of each file to see whether it is what its name claims."""
    wrong: list[tuple[str, str]] = []
    for path in files:
        head = file_head(package.dataset, package.commit, path, budget)
        problem = mismatch(path, head)
        if problem:
            wrong.append((path, f"It {problem}."))
    if not wrong:
        return []
    return [summarised(
        package, wrong, "Its content is not what its name says.",
        "Renaming an archive or a program to look like a texture does not make it one. Remove "
        "them, upload the asset again, and submit the URL of the new commit.",
        title="SCAN.003 File content does not match its name",
    )]


def check_package(package: AddedPackage, files: list[str] | None, budget: RequestBudget) -> list[Finding]:
    """Every content check for one added package."""
    findings = check_asset_present(package, files)
    if findings:
        return findings

    allowed, rejected = split_by_extension(files)
    if rejected:
        findings.append(summarised(
            package, rejected, "A SimReady package may not contain this type of file.",
            "A package may hold only "
            + ", ".join(sorted(ALLOWED_EXTENSIONS))
            + " files. Remove the others, upload the asset again, and submit the URL of "
            "the new commit.",
            title="SCAN.004 File type a package may not contain",
        ))

    # Only the files that survived the checks above are worth reading: one that
    # is being rejected anyway does not need a request spent on it.
    findings += check_file_types(package, allowed, budget)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("added_packages", type=Path)
    args = parser.parse_args()

    try:
        packages = read_added_packages(args.added_packages)
        if not packages:
            print("No package URLs were added; there is nothing to scan.")
            return 0

        budget = RequestBudget()
        findings: list[Finding] = []
        trees: dict[AssetKey, list[str] | None] = {}
        seen_commits: set[tuple[str, str]] = set()

        for package in packages:
            commit_key = (package.dataset, package.commit)
            if commit_key not in seen_commits:
                seen_commits.add(commit_key)
                findings += check_no_lfsconfig(
                    package, root_names(package.dataset, package.commit, budget)
                )

            asset_key = package.asset_key()
            if asset_key not in trees:
                trees[asset_key] = asset_tree(*asset_key, budget)
            findings += check_package(package, trees[asset_key], budget)

        if findings:
            raise ValidationError(
                f"{len(findings)} problem(s) with the content of the added package(s).", findings
            )
    except ValidationError as exc:
        report_failure(exc)
        return 1

    print(
        f"Checked {len(packages)} added package(s) across {len(seen_commits)} dataset commit(s): "
        f"no problems found. {budget.trees} file list(s) and {budget.heads} file head(s) read."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
