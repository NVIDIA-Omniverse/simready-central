#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate the definitive Hugging Face package lists changed by a PR."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, NamedTuple
from pathlib import Path, PurePosixPath
from functools import lru_cache
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
    title: str = ""
    # The files the finding is about, each paired with what is wrong with THAT
    # one. A rule broken by twenty files is one rule and twenty files: the rule
    # says how to fix it once, and each file says which it is and why it is
    # here. Collapsed into "for example a, b, c" they were a list of names with
    # nothing said about any of them.
    files: tuple[tuple[str, str], ...] = ()
    # How many the check found beyond the ones listed.
    more: int = 0

    # Any AREA.NNN, not a fixed list: SUB.018, SCAN.004, and whatever areas come
    # later -- SIM.0042, WARN.0001 -- without this needing to hear about them.
    # A dotted area (VM.MDL.001) is one id, and three digits keeps it from
    # matching a version number in passing.
    RULE_ID = re.compile(r"^([A-Z][A-Z0-9]*(?:\.[A-Z][A-Z0-9]*)*\.\d{3,}) ")

    # Areas whose findings are advisory. Severity belongs to the area rather
    # than to each rule, so a new rule in a known area needs no decision here.
    ADVISORY_AREAS = frozenset({"WARN"})

    def rule(self) -> str | None:
        """The rule id this finding cites, if it cites one."""
        match = self.RULE_ID.match(self.title)
        return match.group(1) if match else None

    def area(self) -> str | None:
        """The family the rule belongs to: SUB, SCAN, VM.MDL, WARN."""
        rule = self.rule()
        return rule.rsplit(".", 1)[0] if rule else None

    def level(self) -> str:
        """error or warning, in GitHub's vocabulary for annotations.

        Red is a rule the submitter broke and must fix. Orange is everything
        else: an advisory area, or no id at all -- a network blip, a rate limit,
        malformed data from Hugging Face -- so nobody goes hunting for a mistake
        they did not make. The guide has said as much in prose since the ids were
        introduced; this is the same statement, made where it can be acted on.
        """
        area = self.area()
        if area is None:
            return "warning"
        return "warning" if area in self.ADVISORY_AREAS else "error"

    def heading(self) -> str:
        """The short line a reviewer scans before reading anything else."""
        return self.title or self.message

    def detail(self) -> list[str]:
        """The supporting lines, one sentence each.

        Split on punctuation followed by whitespace rather than punctuation
        alone: these messages name files, and "albedo.png" would otherwise break
        into a line ending "albedo." and another beginning "png".
        """
        return [part.strip()
                for part in re.split(r"(?<=[.!?])\s+", self.message)
                if part.strip()]

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

    def __init__(self, message: str, findings: list[Finding] | None = None,
                 title: str = "") -> None:
        super().__init__(message)
        self.findings = findings or []
        self.title = title


def rejection(message: str, path: str | None = None, line: int | None = None,
              title: str = "") -> ValidationError:
    """A rejection with one finding, anchored wherever there is something to point at.

    The title is read first: GitHub shows it in bold with the message beneath it
    in grey. It carries the gist in a few words, prefixed with the rule id in the
    established form, and the message only adds to it -- repeating the title
    there wastes the one line a reviewer actually reads.
    """
    finding = Finding(message, path, line, title)
    return ValidationError(finding.described(), [finding])


def escape_data(value: str) -> str:
    """Escape a workflow command's message, which is otherwise ended by a newline."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    """Escape a workflow command's property, where a comma or colon ends the field."""
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


# The rules a finding cites. Absolute, because a relative link does not resolve
# in a pull-request comment the way it does in a rendered file -- and the comment
# is where a submitter reads this. On the branch the pull request targets, since
# that is the copy of the criteria these checks were read from: GITHUB_BASE_REF
# is set on pull-request events, and main is the answer everywhere else. Falls
# back to the path alone off CI, where there is no server to build a URL against.
_SERVER = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
_REPO = os.environ.get("GITHUB_REPOSITORY", "")
_BRANCH = os.environ.get("GITHUB_BASE_REF") or "main"
CRITERIA_PATH = "docs/acceptance-criteria.md"
CRITERIA_DOC = f"{_SERVER}/{_REPO}/blob/{_BRANCH}/{CRITERIA_PATH}" if _REPO else CRITERIA_PATH


# A rule is one heading: the id, a dash, the title. Everything that reads this
# file finds a rule by that pattern, and builds the link from the heading itself
# -- so the anchor is whatever GitHub currently makes of it, rather than one
# recorded somewhere and left to rot when the title improves.
_RULE_ID = re.compile(
    r"^### ([A-Z][A-Z0-9]*(?:\.[A-Z][A-Z0-9]*)*\.\d{3,})\s+-\s+(.+)$")
_RULE_BULLET = re.compile(r"^[-*]\s+(.+)$")
_CODE_SPAN = re.compile(r"`([^`]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
_BARE_URL = re.compile(r"(?<![/\w])(https://\S+?)(?=[.,)\]]?(?:\s|$))")
# A token in a heading points at what it means. The brackets stay when the link
# comes off: they are what marks it a value the checks fill in, and stripping
# them left a title reading "dataset is not readable".
_TOKEN_LINK = re.compile(r"\[([^\]]+)\]\(#tokens\)")
# A [token] is a value only the checks know. The criteria cannot fill one in, so
# a title carrying one keeps the finding's own, which already has it.
_RULE_TOKEN = re.compile(r"\[[^\]]+\]")


def heading_anchor(heading: str) -> str:
    """The fragment GitHub builds from a heading.

    Lowercased, everything but letters, digits, spaces and hyphens dropped, then
    spaces to hyphens. Its rule, not a choice: a heading reading
    `SUB.026 - [dataset] has no dataset card` is reached at
    `#sub026---dataset-has-no-dataset-card`.

    Computed rather than recorded. The heading is read on every run, so a link
    built from it follows a reworded title instead of pointing where the title
    used to be. A link somebody pasted elsewhere does still rot -- that is the
    cost of the title being in the anchor at all.
    """
    # A heading may carry a link -- the tokens point at what they mean -- and
    # GitHub builds the anchor from the rendered TEXT. Leaving the markup in
    # gave "#sub002---dot-in-the-nametokens", which reaches nothing.
    text = _MD_LINK.sub(r"\1", heading)
    lowered = re.sub(r"[^a-z0-9 _-]", "", text.lower())
    return re.sub(r"\s", "-", lowered.strip())


@lru_cache(maxsize=1)
def criteria() -> dict[str, dict[str, Any]]:
    """The rules as the acceptance criteria currently word them.

    Read from the checkout, so correcting a word there changes what a submitter
    is told on the next run -- the same arrangement the dashboard has, and the
    reason the wording lives in one file rather than in three.

    An unreadable file is not fatal: the finding keeps the wording the check
    raised it with, which is correct, only less current.
    """
    try:
        text = Path(CRITERIA_PATH).read_text(encoding="utf-8")
    except OSError:
        return {}
    rules: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    fenced = False
    for raw in text.split("\n"):
        line = raw.strip()
        # The document opens with a worked example of how to write an entry. It
        # is fenced, and an example is not a rule.
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        head = _RULE_ID.match(line)
        if head:
            current = {"title": _TOKEN_LINK.sub(r"[\1]", head.group(2)).strip(),
                       "anchor": heading_anchor(line[len("### "):]),
                       "description": []}
            rules[head.group(1)] = current
            continue
        if current is None:
            continue
        bullet = _RULE_BULLET.match(line)
        if bullet:
            current["description"].append(bullet.group(1).strip())
            continue
        if not line:
            continue
        # Prose sits in a NOTE callout, so it is indented under the rule and
        # marked as the aside it is. The markers are presentation and do not
        # belong in what an agent is handed.
        if line.startswith(">"):
            body = line.lstrip(">").strip()
            if body and not body.startswith("[!"):
                current.setdefault("prose", [])
                current["prose"].append(body)
            continue
        # A section's caption, which belongs to the section and not to a rule.
        if line.startswith("<sub"):
            continue
        current = None          # anything else ends the rule
    return rules


def _file_lines(finding: Finding) -> list[str]:
    """One line per offending file: which file, and what is wrong with it.

    An annotation renders no markup, so there is no heading to hang the
    explanation under -- the two share a line instead. Everything the comment
    shows is still here, which is the point: a submitter reading the annotation
    on the line that failed should not have to open anything else.
    """
    lines = [f"{path}: {note}" if note else path for path, note in finding.files]
    if finding.more:
        lines.append(f"and {finding.more} more")
    return lines


# Files listed under one rule before the rest become a count. The per-package
# cap is lower, and gathering four packages under one heading would otherwise
# stack four capped lists into a page of paths.
FILES_SHOWN = 10


def _esc(text: str) -> str:
    """A path, as HTML. It is a name, not markup -- and some of them contain <."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plain(text: str) -> str:
    """One line of a rule, for somewhere that renders nothing.

    An annotation is plain text: backticks and link markup arrive as
    punctuation, so a fix step reading "Re-run the [packaging workflow](https://
    ...)" turns into noise exactly where somebody is trying to read it.
    """
    text = _MD_LINK.sub(r"\1 (\2)", text)
    return _CODE_SPAN.sub(r"\1", text)


def _linkify(text: str) -> str:
    """One line of a rule, as HTML.

    The lines are markdown and reach a pull-request comment, a run summary and
    the dashboard. Three things have to happen in this order, and getting the
    order wrong is what produced
    `<a href="https://.../<commit>/<asset">...</a> folder>`:

    1. Escape. A rule that says `<namespace>` is showing a placeholder, not
       opening a tag, and an unescaped one simply disappears.
    2. Code spans. Whatever is inside backticks is a literal -- a URL template
       there is an example of a shape, not somewhere to send anybody.
    3. Links, outside code only. A written [label](url) first, then bare URLs.

    Bare URLs only, never a bare <namespace>/<dataset>: that pattern also
    matches a file path, and chair/notes.txt looks exactly like a dataset name.
    """
    escaped = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    parts, last = [], 0
    for span in _CODE_SPAN.finditer(escaped):
        parts.append(("text", escaped[last:span.start()]))
        parts.append(("code", span.group(1)))
        last = span.end()
    parts.append(("text", escaped[last:]))

    out = []
    for kind, chunk in parts:
        if kind == "code":
            out.append(f"<code>{chunk}</code>")
            continue
        # One pass. Running the bare-URL pattern over the output of the
        # written-link one linkified the href it had just produced.
        cursor = 0
        for link in _MD_LINK.finditer(chunk):
            out.append(_BARE_URL.sub(r'<a href="\1">\1</a>', chunk[cursor:link.start()]))
            # A bare #fragment is relative to the page it is rendered on.
            # Inside the criteria that is the criteria; in a pull-request
            # comment it is the pull request, where the anchor does not exist
            # and the link goes nowhere. Resolve it against the document it
            # was written in.
            href = link.group(2)
            if href.startswith("#"):
                href = f"{CRITERIA_DOC}{href}"
            out.append(f'<a href="{href}">{link.group(1)}</a>')
            cursor = link.end()
        out.append(_BARE_URL.sub(r'<a href="\1">\1</a>', chunk[cursor:]))
    return "".join(out)


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
    findings = (list(getattr(exc, "findings", []) or [])
                or [Finding(str(exc), title=getattr(exc, "title", ""))])
    print(f"Submission validation failed: {exc}", file=sys.stderr)

    for finding in findings:
        # Properties are built as a list and joined, because the separator rules
        # are unforgiving: a space before the first, commas between the rest, and
        # nothing at all when there are none. Appending them as strings produced
        # "::error,title=..." for a finding with no path -- a leading comma, which
        # GitHub does not parse. It printed the line literally instead, so the
        # annotation never existed and the dashboard reported no findings on a
        # submission that had one.
        properties = []
        if finding.path:
            properties.append(f"file={escape_property(finding.path)}")
            if finding.line:
                properties.append(f"line={finding.line}")
        # GitHub shows title in bold and the message beneath it in grey. The
        # title carries the gist so a reviewer can scan a column of findings; the
        # message only adds to it, and never repeats it.
        if finding.title:
            properties.append(f"title={escape_property(finding.heading())}")
        prefix = " " + ",".join(properties) if properties else ""
        # One supporting sentence per line, joined with a real newline. The
        # workflow command carries it as %0A and GitHub decodes it back, so the
        # annotation arrives already broken into lines.
        #
        # Splitting HERE is the point. Every consumer downstream -- the pull
        # request, the check run, the dashboard -- would otherwise have to guess
        # where the lines go by hunting for full stops, and a message naming a
        # file breaks that guess. Doing it once, where the sentences were
        # written, means nobody downstream has to.
        # The criteria's own lines, so the annotation on the offending line says
        # exactly what the comment and the dashboard say. It used to carry the
        # sentence the check raised it with -- "Already listed on line 1." --
        # which names the problem and not the fix.
        rule = criteria().get(finding.rule() or "", {})
        # The criteria's lines, exactly. Appending the check's own sentence to
        # them produced a second line saying the same thing in other words, and
        # made what a submitter sees depend on which check raised it rather than
        # on what the rule says. Where the file and line matter they are already
        # properties of the annotation.
        #
        # A finding with no rule to quote keeps its own text -- there is nothing
        # else to show, and it is how the checks report what is not a rule.
        detail = [_plain(line) for line in rule.get("description") or []] or finding.detail()
        body = chr(10).join(detail + _file_lines(finding)) or finding.message
        print(f"::{finding.level()}{prefix}::{escape_data(body)}")

    report = findings_report(findings)
    # The run's own summary, and a copy the workflow posts on the pull request.
    # Same text in both: a submitter should not have to open Actions to be told
    # what to fix, and a reviewer should not read something different there.
    _write(os.environ.get("GITHUB_STEP_SUMMARY"), report, append=True)
    _write(os.environ.get("SUBMISSION_REPORT_PATH"), report, append=False)
    # One entry per RULE, not per finding: eighteen duplicate URLs are one rule
    # broken eighteen times, and eighteen rows in the Checks tab would bury the
    # other thing that went wrong.
    _write(os.environ.get("SUBMISSION_FINDINGS_JSON"),
           json.dumps(_findings_json(findings), indent=2) + "\n", append=False)


def findings_by_rule(findings: list[Finding]) -> list[dict[str, Any]]:
    """The failure grouped by rule, for a check run each.

    Titles and lines come from the criteria, so a row in the Checks tab says
    what the comment says. Where a rule broke more than once, every place it
    broke is listed under the one row.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for finding in findings:
        rule_id = finding.rule() or ""
        entry = grouped.get(rule_id)
        if entry is None:
            rule = criteria().get(rule_id, {})
            anchor = rule.get("anchor") or (heading_anchor(rule_id) if rule_id else "")
            entry = {
                "id": rule_id,
                "title": _doc_title(finding, rule),
                "level": finding.level(),
                "lines": [_plain(line) for line in
                          (rule.get("description") or finding.detail())],
                "url": f"{CRITERIA_DOC}#{anchor}" if rule_id else "",
                "where": [],
                # How many files the check found beyond the ones it listed. The
                # rule counted them, so the rule reports the omission; the files
                # themselves are their own collection.
                "files_omitted": 0,
            }
            grouped[rule_id] = entry
        if finding.path:
            where = finding.path + (f":{finding.line}" if finding.line else "")
            if where not in entry["where"]:
                entry["where"].append(where)
        entry["files_omitted"] += finding.more
    return list(grouped.values())


def findings_by_file(findings: list[Finding]) -> list[dict[str, Any]]:
    """Each offending file, with the checks it failed and why it failed each.

    Its own entry, not a line under a rule. A file that is both the wrong type
    and unscanned is one file with two checks against it -- read once, saying
    both -- rather than a name appearing in two lists. The rule is referenced by
    id and stated in full over in the rules, so neither collection is nested
    inside the other and neither has to be read to understand the other.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for finding in findings:
        rule_id = finding.rule() or ""
        rule = criteria().get(rule_id, {})
        anchor = rule.get("anchor") or (heading_anchor(rule_id) if rule_id else "")
        for path, note in finding.files:
            entry = grouped.setdefault(path, {"path": path, "checks": []})
            check = {
                "id": rule_id,
                "title": _doc_title(finding, rule),
                "level": finding.level(),
                # What is wrong with THIS file, which only the check knows. The
                # criteria say how to fix the rule; they cannot name a file.
                "note": _plain(note),
                "url": f"{CRITERIA_DOC}#{anchor}" if rule_id else "",
            }
            if check not in entry["checks"]:
                entry["checks"].append(check)
    return list(grouped.values())


def _findings_json(findings: list[Finding]) -> dict[str, Any]:
    """The failure as two independent collections: the rules, and the files.

    A rule is not a property of a file and a file is not a property of a rule.
    Nesting either inside the other made one of them a footnote.
    """
    return {"rules": findings_by_rule(findings), "files": findings_by_file(findings)}


def _doc_title(finding: Finding, rule: dict[str, Any]) -> str:
    """The criteria's title for the rule, or the finding's where they have none.

    A title carrying a [token] is a template only the check can fill in, so the
    already-filled-in one wins there.
    """
    doc_title = rule.get("title") or ""
    if doc_title and _RULE_TOKEN.search(doc_title):
        doc_title = ""
    rule_id = finding.rule() or ""
    heading = finding.heading()
    return doc_title or (heading[len(rule_id):].strip() if rule_id else heading)


def _write(path: str | None, text: str, append: bool) -> None:
    if not path:
        return
    try:
        with open(path, "a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass  # Neither file is a reason to fail differently from the findings.


def findings_report(findings: list[Finding]) -> str:
    """The findings as GitHub-flavoured HTML: heading, then supporting lines.

    The same shape the dashboard uses, because it is the same information and a
    submitter should not have to learn it twice: the rule id in bold, linked to
    its own entry in the acceptance criteria, the gist beside it, and what to do
    underneath.
    """
    lines = ["## Submission validation failed", ""]
    for group in _by_rule(findings):
        finding = group.lead
        where = f" <code>{finding.path}</code>" if finding.path else ""
        where += f" {_lines_phrase(group.at)}" if group.at else ""
        mark = "✗" if finding.level() == "error" else "⚠"
        rule = criteria().get(finding.rule() or "", {})
        lines.append(f"{mark} {_rule_link(finding, rule)}{where}")
        # The criteria's own lines when the rule has any, so the pull request
        # says exactly what the dashboard says. Otherwise the finding's, which
        # for an untitled one IS its heading -- listing it again underneath
        # would print the same words twice.
        # The criteria decide what is shown and how many lines it takes. One
        # line is not a list; a single bullet is a bullet for its own sake.
        detail = rule.get("description") or (finding.detail() if finding.title else [])
        if len(detail) == 1:
            lines.append(f"<blockquote>{_linkify(detail[0])}</blockquote>")
        elif detail:
            lines.append("<ul>")
            lines += [f"<li>{_linkify(part)}</li>" for part in detail]
            lines.append("</ul>")
        # Then the files, if the check named any. A file is not a fix step and
        # must not read like one: the path is set in code and the explanation
        # sits under it in small text, so the rule above and the files below are
        # told apart at a glance rather than run together as one list.
        lines += _file_block(group.files, group.more)
        lines.append("")
    # No footer. It repeated what the linked id already says, in every comment,
    # on every failure -- and the orange mark carries the no-id case on its own.
    return "\n".join(lines).rstrip() + "\n"


class _RuleGroup(NamedTuple):
    """One rule, and everything it caught -- however many packages that took."""
    lead: Finding
    at: list[int]                       # the submission lines it fired on
    files: list[tuple[str, str]]
    more: int


def _by_rule(findings: list[Finding]) -> list[_RuleGroup]:
    """The findings gathered by rule, in the order the rules first appear.

    A rule broken by four packages is one rule broken four times. Reported one
    finding at a time it became four headings carrying the same title and the
    same fix, with the files split between them -- so the reader had to
    reassemble what the check already knew, and the page grew by a block per
    package while saying nothing new.

    Findings with no rule id stay separate: they are not instances of anything.
    """
    groups: list[_RuleGroup] = []
    index: dict[str, int] = {}
    for finding in findings:
        rule_id = finding.rule()
        at = index.get(rule_id) if rule_id else None
        if at is None:
            groups.append(_RuleGroup(finding, [], [], 0))
            if rule_id:
                index[rule_id] = len(groups) - 1
            at = len(groups) - 1
        group = groups[at]
        if finding.line and finding.line not in group.at:
            group.at.append(finding.line)
        for entry in finding.files:
            if entry not in group.files:
                group.files.append(entry)
        groups[at] = group._replace(more=group.more + finding.more)
    return groups


def _lines_phrase(at: list[int]) -> str:
    """"line 2", or "lines 1, 2 and 4"."""
    nums = [str(n) for n in sorted(at)]
    if len(nums) == 1:
        return f"line {nums[0]}"
    return "lines " + ", ".join(nums[:-1]) + f" and {nums[-1]}"


def _file_block(files: list[tuple[str, str]], more: int = 0) -> list[str]:
    """The offending files, each its own heading with its own explanation.

    A definition list, not another bullet list: the fix steps directly above are
    bullets, and a second run of bullets underneath them read as more of the
    same. A file is a different kind of thing from a step -- it is the subject,
    and the line under it is what is wrong with that subject -- so it gets the
    markup that says so, and the two can be told apart at a glance.
    """
    if not files:
        return []
    shown = files[:FILES_SHOWN]
    more += len(files) - len(shown)
    # One note repeated under every path is the RULE talking, not the files: it
    # said the same sentence fifteen times and told a reader nothing they could
    # not get from the heading. Said once above the list when they all share it,
    # and per file only where the files actually differ -- which is the case it
    # was built for, a mismatched extension naming the type it really is.
    notes = {note for _, note in shown if note}
    shared = notes.pop() if len(notes) == 1 and len(shown) > 1 else None
    out = [f"<blockquote>{_linkify(shared)}</blockquote>"] if shared else []
    out.append("<dl>")
    for path, note in shown:
        out.append(f"<dt><code>{_esc(path)}</code></dt>")
        if note and not shared:
            out.append(f"<dd><sub>{_linkify(note)}</sub></dd>")
    out.append("</dl>")
    if more:
        out.append(f"<sub>and {more} more</sub>")
    return out


def _rule_link(finding: Finding, rule: dict[str, Any]) -> str:
    """The heading, with its rule id linked to the criteria entry.

    GitHub builds a heading's anchor by lowercasing it and dropping the dots, so
    SUB.018 is reached at #sub018. Its rule, not a choice, and the same one the
    dashboard computes.
    """
    rule_id = finding.rule()
    if not rule_id:
        return f"<strong>{_linkify(finding.heading())}</strong>"
    # The criteria's title, unless it carries a token only the check can fill in.
    doc_title = rule.get("title") or ""
    if doc_title and _RULE_TOKEN.search(doc_title):
        doc_title = ""
    rest = doc_title or finding.heading()[len(rule_id):].strip()
    anchor = rule.get("anchor") or heading_anchor(rule_id)
    link = f'<a href="{CRITERIA_DOC}#{anchor}"><strong>{rule_id}</strong></a>'
    # Escaped for the same reason the lines are: a title naming [dataset] or a
    # path is showing text, not markup.
    safe = _linkify(rest)
    return f"{link} <strong>{safe}</strong>" if rest else link


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


# What git records about a file besides its content, for the finding's text.
# A tree entry has one of these modes and no other; the permission bits a
# filesystem keeps are not stored. An ordinary file is 100644.
MODE_NAMES = {
    "100755": "an executable file",
    "120000": "a symbolic link",
    "160000": "a submodule",
}


def changed_submission_files(base: str, head: str) -> list[tuple[str, str]]:
    """Return the changed submissions/<namespace>/<dataset> files.

    A pull request that changes no submission file is not a submission, and the
    caller skips validation rather than failing: the repository also receives
    documentation, tooling, and workflow changes.

    Mixing submission files with unrelated files is still rejected. A submission
    pull request is reviewed and published as one self-contained catalog change,
    so it must not carry changes that the review pipeline does not inspect.

    A submission file that still exists at head has to be an ordinary file: its
    bytes are the catalog entry. A symbolic link points at some other file, a
    submodule has no content here, and an executable bit rides into the catalog
    for nothing.
    """
    # --raw gives ":<old mode> <new mode> <old sha> <new sha> <status>" for each
    # file, then its path. --no-renames keeps one path per entry: a moved file
    # is a removal and an addition, each judged on its own.
    output = git("diff", "--raw", "--no-renames", "-z", base, head, text=False)
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValidationError(
            "git named no files for the range under review.",
            title="Could not read the pull request's file list",
        )

    changed: list[tuple[str, str]] = []
    unrelated: list[str] = []
    for offset in range(0, len(fields), 2):
        header = fields[offset].decode("ascii", errors="replace")
        path = fields[offset + 1].decode("utf-8", errors="strict")
        parts = PurePosixPath(path).parts
        if not (len(parts) == 3 and parts[0] == "submissions"):
            unrelated.append(path)
            continue

        old_mode, new_mode = header.lstrip(":").split(" ", 2)[:2]
        if new_mode == "000000":
            changed.append(("D", path))
            continue
        if new_mode != "100644":
            raise rejection(
                f"Git stores it as {MODE_NAMES.get(new_mode, f'mode {new_mode}')}.",
                path,
                title="SUB.004 Submission file is not an ordinary file",
            )
        changed.append(("A" if old_mode == "000000" else "M", path))

    # A submitter may change submissions/ and nothing else. Whoever maintains the
    # repository may change anything -- the documentation and these checks have
    # to be editable, and they are the people who edit them.
    #
    # The repository's own permission levels decide it: submitters hold write,
    # maintainers hold admin. author_association cannot, because everyone is
    # MEMBER -- submitters included, being in the same organisation.
    if unrelated and not _author_maintains_the_repository():
        raise ValidationError(
            f"This pull request changes {', '.join(sorted(unrelated))}. A submission may "
            "change files under submissions/ and nothing else.",
            title="SUB.019 Pull request changes files outside submissions/",
        )
    if changed and unrelated:
        raise ValidationError(
            f"It also changes {', '.join(sorted(unrelated))}. Move those into a separate "
            "pull request.",
            title="SUB.019 Pull request changes files outside submissions/",
        )
    return changed


# What the repository grants somebody who may change more than a submission.
MAINTAINER_PERMISSIONS = frozenset({"admin", "maintain"})


def _author_maintains_the_repository() -> bool:
    """Whether whoever opened this pull request may change more than submissions/.

    Unknown means yes. Run outside CI there is no author and no permission to
    read, and failing closed there would reject every pull request including the
    ones that fix it. On CI the workflow always supplies this, so unknown is not
    a hole a submitter can open -- they cannot change the workflow either.
    """
    return os.environ.get("PR_AUTHOR_PERMISSION", "").strip().lower() in (
        MAINTAINER_PERMISSIONS | {""})


def submission_paths(revision: str) -> list[str]:
    """Every submissions/<namespace>/<dataset> file in one revision of the tree."""
    output = git("ls-tree", "-r", "--name-only", "-z", revision, "submissions/", text=False)
    paths = [field.decode("utf-8", errors="strict") for field in output.split(b"\0") if field]
    return [path for path in paths if len(PurePosixPath(path).parts) == 3]


# A commit is signed off when a Signed-off-by trailer names the person who made
# it: the Developer Certificate of Origin that CONTRIBUTING.md asks of every
# contributor. simready-manage submit writes one from the submitter's git
# identity; a hand-made pull request, a removal for instance, may lack it, and
# nothing else on GitHub checks. The email has to be the author's or the
# committer's: a trailer naming somebody else certifies nothing.
SIGN_OFF = re.compile(r"^Signed-off-by:\s*(?P<name>.*?)\s*<(?P<email>[^>]+)>\s*$", re.M)


def check_commits_signed_off(base: str, head: str) -> None:
    """Every commit under review is signed off by its author or committer (SUB.027).

    Merge commits are left out, as the DCO check on GitHub leaves them out: a
    merge of the base branch into the submission carries nobody's change.
    """
    record, field = "\x1e", "\x1f"
    output = git(
        "log", "--no-merges", "--reverse",
        f"--format=%H{field}%ae{field}%ce{field}%s{field}%B{record}",
        f"{base}..{head}",
    )
    unsigned: list[tuple[str, str]] = []
    for entry in output.split(record):
        if not entry.strip():
            continue
        sha, author, committer, subject, body = entry.lstrip("\n").split(field, 4)
        signers = {match.group("email").strip().lower() for match in SIGN_OFF.finditer(body)}
        if signers & {author.strip().lower(), committer.strip().lower()}:
            continue
        why = (f"signed off by {', '.join(sorted(signers))}, but authored by {author}"
               if signers else "no Signed-off-by trailer")
        unsigned.append((f"{sha[:7]} {subject}", why))
    if unsigned:
        finding = Finding(
            f"{len(unsigned)} commit(s) in this pull request are not signed off by their author.",
            title="SUB.027 Commit is not signed off",
            files=tuple(unsigned),
        )
        raise ValidationError(finding.described(), [finding])


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
        raise rejection(
            f"{listed} name one Hugging Face dataset: it answers to any capitalisation. "
            "Keep one of them.",
            title="SUB.017 Two submission files for one dataset",
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
        raise rejection(
            "Submission files are plain text. Re-save it as UTF-8.",
            path,
            title="SUB.003 Submission file is not UTF-8",
        ) from exc


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


# Hugging Face counts requests in fixed five-minute windows, so the longest it
# can ever ask us to wait is one window. Waiting that out once is cheaper than
# asking a submitter to come back and press the button again.
MAX_RATE_LIMIT_WAIT = 300


def rate_limit_wait(exc: HTTPError) -> int | None:
    """How long Hugging Face says to wait, when it has just turned us away.

    Hugging Face implements the RateLimit header draft, so a 429 states the
    seconds left in the current window rather than leaving us to guess. None
    when this is not a rate limit, or when it says something we cannot use.
    """
    if exc.code != 429:
        return None
    header = exc.headers.get("RateLimit", "") if exc.headers else ""
    match = re.search(r"\bt=(\d+)", header)
    if not match:
        return None
    seconds = int(match.group(1))
    return seconds if 0 < seconds <= MAX_RATE_LIMIT_WAIT else None


def open_hugging_face(request: Request, timeout: int = 30):
    """Open a request, waiting once if Hugging Face asks us to slow down.

    Anonymous requests are counted per IP address, and these checks run on
    whichever runner GitHub hands out, so a submission can be refused because of
    traffic that has nothing to do with it. One wait of exactly the length
    Hugging Face named turns that failed run into a slow one.
    """
    try:
        return urlopen(request, timeout=timeout)
    except HTTPError as exc:
        seconds = rate_limit_wait(exc)
        if seconds is None:
            raise
        print(
            f"Hugging Face is rate-limiting these checks. Waiting {seconds}s for its window to "
            "reset, then trying once more."
        )
        time.sleep(seconds)
        return urlopen(request, timeout=timeout)


# Read Hugging Face as someone, when CI is given a token.
#
# Without one every request is anonymous, and a private dataset is unreadable no
# matter who owns it -- nvidia/simready-assets answers 401 to an anonymous caller
# exactly as a personal dataset does.
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()


def hf_headers() -> dict[str, str]:
    """Headers for a Hugging Face request, authenticated when a token is set."""
    headers = {"User-Agent": USER_AGENT}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"
    return headers


# A submission has to be READABLE BY THIS CI. That is the whole rule, and it is
# not the same as "not marked private on Hugging Face".
#
# What disqualifies a dataset:
#
#   * private under a PERSONAL namespace (ronanDB/..., whurd/...). Only the
#     account owner can grant these checks access, and no institutional
#     relationship substitutes for that -- Hugging Face is a public service and
#     knows nothing about who works where.
#   * private under an ORG namespace this token is not a member of.
#   * gated: readable only after a visitor accepts conditions.
#
# What does NOT disqualify it: being private to the world while readable by this
# CI's token. The catalog can fetch it, and so can every consumer, through the
# same route.
#
# Hugging Face reports these differently depending on who asks, which is the
# whole reason unreadable_reason() exists.


def unreadable_reason(dataset: str, code: int) -> str | None:
    """Why a dataset could not be read, and what to do about it.

    A finished sentence or two, not a fragment: the dataset name lives in the
    finding title, so there is no subject here to attach a bare verb to.

    Hugging Face answers 404 -- not 403 -- when an AUTHENTICATED caller lacks
    access, hiding existence rather than admitting the refusal. Taken at face
    value that produces "there is no dataset X", which is false and sends the
    submitter to check for a typo that is not there.

    So a 404 is disambiguated by asking again anonymously. An anonymous 401 means
    the dataset exists and is private; an anonymous 404 means it really is not
    there.
    """
    if code == 403:
        return ("It is gated: visitors must accept conditions before downloading. "
                "Remove the access gate.")
    if code == 401:
        return ("It is private, and these checks have not been granted access. "
                "Make it public.")
    if code != 404:
        return None
    if not HF_TOKEN:
        # Anonymous, so 404 means what it says. The ambiguity is created by the
        # token: Hugging Face hides a private dataset from a caller it does not
        # recognise as having access, and only then does 404 mean "not yours".
        return None
    # 404 with a token in hand is ambiguous; ask again as nobody to resolve it.
    probe = Request(f"https://huggingface.co/api/datasets/{quote(dataset, safe='/')}",
                    headers={"User-Agent": USER_AGENT})
    try:
        with open_hugging_face(probe):
            return None          # readable anonymously, so the 404 was about something else
    except HTTPError as anon:
        if anon.code in (401, 403):
            # "It exists" is the point: a 404 from an authenticated caller looks
            # like a missing dataset, and the whole reason for the anonymous
            # probe is to say otherwise.
            # Plainly. "These checks were not invited" was a joke about access
            # that read as a step somebody had missed -- there is nowhere to
            # invite a check to, and a submitter hunting for the button that
            # does it will not find one. Anonymous says the same thing and is
            # the actual reason: no account, so nothing can be shared with it.
            return ("It exists, but it is private, and these checks are "
                    "anonymous. Make it public.")
    except (URLError, TimeoutError):
        return None
    return None


def dataset_access_error(dataset: str, exc: HTTPError, what: str) -> ValidationError:
    """Turn a bare HTTP status into something a submitter can act on.

    Nothing here has to explain how the dataset was chosen. Hugging Face is not
    asked until the submission file has parsed and every URL in it has been shown
    to name the same dataset as the file, so by this point that much is settled.
    """
    # Ask what actually went wrong before falling back to status-code guesses:
    # with a token in hand a 404 usually means "private and not shared with you",
    # not "does not exist".
    diagnosed = unreadable_reason(dataset, exc.code)
    if diagnosed:
        return ValidationError(
            diagnosed,
            title=f"SUB.018 {dataset} is not readable",
        )
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
    elif exc.code == 429:
        message = (
            f"Hugging Face is rate-limiting the anonymous requests these checks make (HTTP 429) "
            f"while {what} for {dataset}. Nothing is necessarily wrong with the submission; "
            "re-run the checks in a few minutes."
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
            request = Request(url, headers=hf_headers())
            with open_hugging_face(request) as response:
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
            f"{exc}. Usually a temporary network failure -- re-run the checks.",
            title=f"Could not reach Hugging Face while reading {dataset}",
        ) from exc
    return commits


def declared_package_id(url: str, dataset: str) -> str:
    """The package_id from one package definition, read through the cache."""
    document = declared_definition(url, dataset)
    package_id = document.get("package_id")
    if not isinstance(package_id, str) or not package_id:
        raise ValidationError(
            f"Every {PACKAGING_FILE} declares one, of the form "
            "simready.hf.<namespace>.<dataset>.<asset folder>.<version>.",
            title="SUB.021 Package definition has no package_id",
        )
    return package_id


_DEFINITION_CACHE: dict[str, dict] = {}


def declared_definition(url: str, dataset: str) -> dict:
    """The whole package definition, downloaded at most once per URL.

    Cached because more than one check wants the document and the URL is pinned
    to a commit, so the bytes cannot change between reads within a run. Without
    the cache every added check costs another download per package.
    """
    if url in _DEFINITION_CACHE:
        return _DEFINITION_CACHE[url]
    request = Request(url, headers=hf_headers())
    try:
        with open_hugging_face(request) as response:
            if not response.geturl().lower().startswith("https://"):
                raise ValidationError(
                    f"Reading {url} was redirected away from https. Refusing to download the "
                    "package definition over an insecure connection."
                )
            payload = response.read(MAX_DEFINITION_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise ValidationError(
                f"Nothing at {url}. Check the asset folder name and the commit.",
                title="SUB.024 No package definition at the URL",
            ) from exc
        raise dataset_access_error(dataset, exc, f"downloading {url}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"{exc}. Usually a temporary network failure -- re-run the checks.",
            title="Could not reach Hugging Face",
        ) from exc
    if len(payload) > MAX_DEFINITION_BYTES:
        raise ValidationError(
            f"The package definition at {url} is larger than {MAX_DEFINITION_BYTES >> 20} MB. A "
            "package definition is a few hundred bytes; re-run the packaging workflow rather "
            "than editing it by hand, upload the result and submit the URL of the new commit.",
            title="SUB.023 Package definition is not valid JSON",
        )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"{exc}. Read from {url}.",
            title="SUB.023 Package definition is not valid JSON",
        ) from exc
    if not isinstance(document, dict):
        raise ValidationError(
            f"Read from {url}.",
            title="SUB.022 Package definition is not a JSON object",
        )
    _DEFINITION_CACHE[url] = document
    return document


# The bill of materials simready-manage submit writes beside
# the package definition: every file of the package with its SHA-256 hash.
BOM_FILE = ".metadata/com.nvidia.simready.packaging.bom.json"
# About 300 bytes per file, so this is a bill of some 200,000 files: beyond any
# asset, and still a size the runner reads without trouble. One byte more than
# the cap is requested so that an oversize bill is recognised rather than parsed
# truncated and reported as broken JSON.
MAX_BOM_BYTES = 64 << 20
# A file Hugging Face keeps outside LFS carries no SHA-256 in its listing and is
# read whole to compute one. Hugging Face stores anything above 10 MB in LFS, so
# such a file is small; the cap only guards against a listing that says otherwise.
MAX_HASHED_BYTES = 64 << 20


def bill_of_materials(url: str, dataset: str) -> dict | None:
    """The bill of materials beside the package definition at `url`.

    None when the package has none. Anything else that goes wrong is raised:
    an unreadable dataset as SUB.018 through dataset_access_error, a bill that
    is not JSON as SUB.028, since a bill nobody can read is no bill.
    """
    asset_url = url[: -len(PACKAGING_FILE)]
    bom_url = asset_url + BOM_FILE
    request = Request(bom_url, headers=hf_headers())
    try:
        with open_hugging_face(request) as response:
            payload = response.read(MAX_BOM_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise dataset_access_error(dataset, exc, f"downloading {bom_url}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"{exc}. Usually a temporary network failure -- re-run the checks.",
            title="Could not reach Hugging Face",
        ) from exc
    if len(payload) > MAX_BOM_BYTES:
        raise ValidationError(
            f"The bill of materials at {bom_url} is larger than {MAX_BOM_BYTES >> 20} MB, more "
            "than this check reads, so the package cannot be verified. Run simready-manage submit "
            "again; if the package really holds that many files, open an issue.",
            title="SUB.028 Bill of materials is too large to read",
        )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"{exc}. Read from {bom_url}. Run simready-manage submit again and submit the URL "
            "of the new commit.",
            title="SUB.028 Bill of materials is not valid JSON",
        ) from exc
    return document if isinstance(document, dict) else {}


def package_files(dataset: str, commit: str, asset: str) -> dict[str, tuple[int | None, str | None]]:
    """Every file under the asset folder at that commit.

    Keyed by the path relative to the asset folder, each with its size and the
    SHA-256 Hugging Face reports for a file it stores in LFS. A file stored in
    git carries only a blob id there, so its hash is None and has to be computed
    from the content. An asset folder that is not there at that commit is an
    empty listing: every file the bill names is then missing.
    """
    url = (f"https://huggingface.co/api/datasets/{quote(dataset, safe='/')}/tree/{commit}/"
           f"{quote(asset)}?recursive=true")
    prefix = asset + "/"
    files: dict[str, tuple[int | None, str | None]] = {}
    try:
        while url:
            request = Request(url, headers=hf_headers())
            with open_hugging_face(request) as response:
                page = json.load(response)
                if not isinstance(page, list):
                    raise ValidationError(
                        f"Hugging Face returned a listing of {asset} in {dataset} that this check "
                        "does not understand. Re-run the checks; if it keeps happening, open an issue."
                    )
                for item in page:
                    if not isinstance(item, dict) or item.get("type") != "file":
                        continue
                    path = item.get("path", "")
                    if not isinstance(path, str) or not path.startswith(prefix):
                        continue
                    size = item.get("size")
                    lfs = item.get("lfs")
                    sha256 = lfs.get("oid") if isinstance(lfs, dict) else None
                    files[path[len(prefix):]] = (
                        size if isinstance(size, int) else None,
                        sha256 if isinstance(sha256, str) else None,
                    )
                url = next_link(response.headers.get("Link"))
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise dataset_access_error(dataset, exc, f"listing {asset} at {commit[:12]}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"{exc}. Usually a temporary network failure -- re-run the checks.",
            title="Could not reach Hugging Face",
        ) from exc
    return files


def file_sha256(url: str, dataset: str) -> str:
    """The SHA-256 of a file Hugging Face stores outside LFS, read whole."""
    request = Request(url, headers=hf_headers())
    digest = hashlib.sha256()
    total = 0
    try:
        with open_hugging_face(request, timeout=120) as response:
            while chunk := response.read(1 << 20):
                total += len(chunk)
                if total > MAX_HASHED_BYTES:
                    raise ValidationError(
                        f"{url} is larger than {MAX_HASHED_BYTES >> 20} MB, which no file Hugging "
                        "Face keeps outside LFS should be.",
                        title="Could not hash a package file",
                    )
                digest.update(chunk)
    except HTTPError as exc:
        raise dataset_access_error(dataset, exc, f"downloading {url}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValidationError(
            f"{exc}. Usually a temporary network failure -- re-run the checks.",
            title="Could not reach Hugging Face",
        ) from exc
    return digest.hexdigest()


def check_bills_of_materials(path: str, entries: list[PackageLine]) -> None:
    """Every added package carries a bill of materials, and every file it names
    is in the dataset at that commit with that SHA-256 (SUB.028).

    simready-manage verify compares a pulled package with its bill of materials;
    this does the same against Hugging Face before review, so what the reviewer
    pulls is the package that was built. Files the bill does not name are not
    judged here: the scan rules say what a package may hold. The LFS record
    Hugging Face keeps for a large file already carries its SHA-256; a small file
    stored in git is downloaded and hashed, after its size was seen to agree.
    """
    _, namespace, dataset_name = PurePosixPath(path).parts
    dataset = f"{namespace}/{dataset_name}"
    findings: list[Finding] = []
    for entry in entries:
        bill = bill_of_materials(entry.url, dataset)
        if bill is None:
            findings.append(Finding(
                f"No {BOM_FILE} beside the package definition. Package with "
                "`simready-manage submit`, as the guide describes.",
                path, entry.line_number,
                f"SUB.028 {entry.asset} has no bill of materials",
            ))
            continue
        items = bill.get("items")
        if not isinstance(items, list):
            findings.append(Finding(
                "The bill of materials has no `items` list. Run simready-manage submit again "
                "and submit the URL of the new commit.",
                path, entry.line_number,
                f"SUB.028 {entry.asset} has an unreadable bill of materials",
            ))
            continue
        files = package_files(dataset, entry.commit, entry.asset)
        wrong: list[tuple[str, str]] = []
        for item in items:
            rel = item.get("relative_path") if isinstance(item, dict) else None
            if not isinstance(rel, str) or not rel:
                wrong.append(("(entry without relative_path)", "names no file"))
                continue
            declared = item.get("hash") if isinstance(item.get("hash"), dict) else {}
            expected = declared.get("sha256") or item.get("sha256")
            if rel not in files:
                wrong.append((rel, "in the bill of materials, not in the dataset"))
                continue
            if not isinstance(expected, str) or not expected:
                wrong.append((rel, "the bill of materials records no SHA-256 for it"))
                continue
            size, actual = files[rel]
            if actual is None:
                declared_size = item.get("size")
                if isinstance(declared_size, int) and isinstance(size, int) and declared_size != size:
                    wrong.append((rel, f"{size} bytes in the dataset, {declared_size} in the bill of materials"))
                    continue
                actual = file_sha256(
                    f"https://huggingface.co/datasets/{dataset}/resolve/{entry.commit}/"
                    f"{quote(entry.asset)}/{quote(rel, safe='/')}",
                    dataset,
                )
            if actual.lower() != expected.lower():
                wrong.append((rel, f"SHA-256 differs: {actual[:12]} in the dataset, "
                                   f"{expected[:12]} in the bill of materials"))
        if wrong:
            findings.append(Finding(
                f"{len(wrong)} of the {len(items)} file(s) in the bill of materials are not in "
                "the dataset as the bill describes them. The upload is not the package that was "
                "built: run simready-manage submit again and submit the URL of the new commit.",
                path, entry.line_number,
                f"SUB.028 {entry.asset} does not match its bill of materials",
                files=tuple(wrong[:FILES_SHOWN]),
                more=len(wrong) - len(wrong[:FILES_SHOWN]),
            ))
    if findings:
        raise ValidationError(
            f"{len(findings)} package(s) in {path} fail their bill of materials.", findings
        )


def dataset_license(dataset: str) -> str | None:
    """The license Hugging Face reports for a dataset, or None if it says nothing.

    None is not a failure. A dataset card without a license is a different
    problem from a package that disagrees with its dataset, and only the second
    is this check's business.
    """
    url = f"https://huggingface.co/api/datasets/{quote(dataset, safe='/')}"
    request = Request(url, headers=hf_headers())
    try:
        with open_hugging_face(request) as response:
            card = (json.load(response) or {}).get("cardData") or {}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None
    value = card.get("license")
    return value if isinstance(value, str) and value else None


def dataset_is_private(dataset: str) -> bool:
    """Whether Hugging Face marks the dataset private, as far as these checks can tell.

    Only meaningful with a token: an anonymous caller cannot read a private
    dataset at all, so anything it got this far with is public. Any failure to
    ask reads as "not private" -- this feeds a reminder, never a verdict.
    """
    if not HF_TOKEN:
        return False
    url = f"https://huggingface.co/api/datasets/{quote(dataset, safe='/')}"
    request = Request(url, headers=hf_headers())
    try:
        with open_hugging_face(request) as response:
            info = json.load(response) or {}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return False
    return info.get("private") is True


def remind_private_dataset(dataset: str) -> None:
    """Say, without failing, that a private dataset passed only here.

    An instance of these checks that holds a token of the dataset's organisation
    reads a private dataset as if it were public, which is right for testing a
    submission and wrong for the catalog: the public instance holds no such
    token and rejects the same dataset with SUB.018. A notice -- not a finding,
    not a rule -- so the submitter is told before they are surprised elsewhere.
    """
    if dataset_is_private(dataset):
        print(
            f"::notice title=Private dataset::{dataset} is private. It passed because this "
            "repository's checks can read it. The public SimReady Central cannot, so make the "
            "dataset public before submitting it there."
        )


def parse_submission(path: str, content: str) -> list[PackageLine]:
    """Check every line of a submission file without touching the network."""
    _, namespace, dataset_name = PurePosixPath(path).parts
    for part, what in ((namespace, "namespace"), (dataset_name, "dataset name")):
        if "." in part:
            raise rejection(
                f"{part!r} contains a dot, and a package ID is built from these parts, so a dot "
                "makes them ambiguous. Rename the dataset on Hugging Face before submitting it.",
                path,
                title=f"SUB.002 Dot in the {what}",
            )
        if not re.fullmatch(NAMESPACE_PATTERN, part):
            raise rejection(
                "Name it after the Hugging Face dataset it catalogues, using only letters, "
                "digits, hyphens and underscores.",
                path,
                title="SUB.001 Submission file is misnamed",
            )
    expected_dataset = f"{namespace}/{dataset_name}"

    entries: list[PackageLine] = []
    seen_urls: dict[str, int] = {}

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        if not raw_line.strip():
            continue
        if raw_line != raw_line.strip():
            raise rejection(
                "Remove the spaces or tabs so the URL is the whole line.",
                path, line_number,
                title="SUB.005 Whitespace around the URL",
            )
        match = PACKAGE_URL.fullmatch(raw_line)
        if not match:
            # explain_url says which way the URL is wrong -- wrong host, wrong
            # shape, missing commit -- so the title stays general and the
            # message carries the specific.
            raise rejection(
                explain_url(raw_line), path, line_number,
                title="SUB.025 Not a Hugging Face package URL",
            )
        url_dataset = f"{match['namespace']}/{match['dataset']}"
        if url_dataset != expected_dataset:
            raise rejection(
                "The submission file name decides which dataset it catalogues, so every URL in "
                "it has to be for that one.",
                path,
                line_number,
                title=f"SUB.006 URL is for {url_dataset}, not {expected_dataset}",
            )
        if raw_line in seen_urls:
            raise rejection(
                f"Already listed on line {seen_urls[raw_line]}.",
                path,
                line_number,
                title="SUB.007 Duplicate URL",
            )
        seen_urls[raw_line] = line_number
        entries.append(PackageLine(line_number, raw_line, match["commit"], match["asset"]))

    commit_blocks: list[tuple[str, list[str]]] = []
    closed_commits: set[str] = set()
    for entry in entries:
        if not commit_blocks or commit_blocks[-1][0] != entry.commit:
            if entry.commit in closed_commits:
                raise rejection(
                    f"Keep every URL for commit {entry.commit} together, in one run of lines.",
                    path,
                    entry.line_number,
                    title="SUB.008 Commit block is split up",
                )
            if commit_blocks:
                closed_commits.add(commit_blocks[-1][0])
            commit_blocks.append((entry.commit, []))
        commit_blocks[-1][1].append(entry.asset)

    for commit, assets in commit_blocks:
        if assets != sorted(assets):
            raise rejection(
                "List them in ascending order of asset folder name.",
                path,
                title=f"SUB.009 Assets out of order in {commit}",
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
            f"{len(missing)} URL(s) in {path} point at commits that are not there.",
            [
                Finding(
                    "Only commits on main can be catalogued. Check it is merged rather than "
                    "left on a pull-request branch, and that the dataset's history has not "
                    "been rewritten since you copied the URL.",
                    path,
                    entry.line_number,
                    f"SUB.020 {entry.commit[:12]} is not on {dataset}'s main branch",
                )
                for entry in missing
            ],
        )

    block_positions = [
        position[commit] for commit, _ in _commit_blocks(entries)
    ]
    if block_positions != sorted(block_positions, reverse=True):
        raise rejection(
            "List the oldest commit's URLs first, so the file reads as the history of the "
            "submission.",
            path,
            title="SUB.010 Commit blocks are not oldest first",
        )


def _commit_blocks(entries: list[PackageLine]) -> list[tuple[str, list[PackageLine]]]:
    blocks: list[tuple[str, list[PackageLine]]] = []
    for entry in entries:
        if not blocks or blocks[-1][0] != entry.commit:
            blocks.append((entry.commit, []))
        blocks[-1][1].append(entry)
    return blocks


# Package definitions this repository knows how to read. A definition written to
# a newer format may carry rules these checks do not know about, and passing it
# silently would claim a verification that did not happen.
KNOWN_FORMAT_VERSIONS = {"1.0"}


def check_package_licenses(path: str, entries: list[PackageLine],
                           license_cache: dict[str, str | None]) -> None:
    """Every package declares a license, and it is the dataset's license.

    The submission guide requires the two to match, and nothing checked it. They
    drift easily because three spellings of the same license are in circulation:
    the sample assets ship CC-BY-4.0 (SPDX), simready-package's help shows SPDX
    casing, and the guide and Hugging Face dataset cards use cc-by-4.0. Comparing
    case-insensitively is therefore deliberate -- the guide never says whether
    the match is case-sensitive, and failing a submission over capitalisation
    would be a rule nobody was told about.
    """
    _, namespace, dataset_name = PurePosixPath(path).parts
    dataset = f"{namespace}/{dataset_name}"
    if dataset not in license_cache:
        license_cache[dataset] = dataset_license(dataset)
    declared_for_dataset = license_cache[dataset]

    for entry in entries:
        document = declared_definition(entry.url, dataset)

        version = document.get("format_version")
        if version is not None and str(version) not in KNOWN_FORMAT_VERSIONS:
            raise rejection(
                f"These checks read {', '.join(sorted(KNOWN_FORMAT_VERSIONS))}. Passing an "
                "unknown format would claim a verification that did not happen.",
                path, entry.line_number,
                title=f"SUB.013 Unsupported package format {version!r}",
            )

        package_license = document.get("license")
        if not isinstance(package_license, str) or not package_license.strip():
            raise rejection(
                "Every package states the licence its content is published under.",
                path, entry.line_number,
                title="SUB.011 Package declares no licence",
            )
        if (declared_for_dataset
                and package_license.strip().lower() != declared_for_dataset.strip().lower()):
            raise rejection(
                f"The package says {package_license!r}, the dataset says "
                f"{declared_for_dataset!r}. Fix whichever is wrong, or the catalog states terms "
                "the content is not offered under.",
                path, entry.line_number,
                title=f"SUB.012 Licence does not match {dataset}",
            )


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
                f"It says {package_id!r}, but a package published from {entry.asset!r} in "
                f"{dataset} has to start {prefix!r}. Correct it on Hugging Face.",
                path,
                entry.line_number,
                title="SUB.014 Package ID does not match where it is published",
            )
        version = package_id[len(prefix):]
        if not version:
            raise rejection(
                f"{package_id!r} stops at {prefix!r}. The form is "
                "simready.hf.<namespace>.<dataset>.<asset folder>.<version>.",
                path,
                entry.line_number,
                title="SUB.015 Package ID has no version",
            )
        if package_id.lower() in owners:
            first = owners[package_id.lower()]
            raise rejection(
                f"Line {first.line_number} declares {package_id!r} too, and IDs are compared "
                f"ignoring case. Give the newer package a version other than {version!r}.",
                path,
                entry.line_number,
                title="SUB.016 Duplicate package ID",
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


def write_package_index(path: Path, added: list[tuple[str, PackageLine]]) -> None:
    """Write each added URL with the submission file and line that added it.

    The list of added URLs is a sorted set, with nothing to say where any of them
    came from. A later check that finds something wrong with one of these packages
    would then have nothing to point at, and would have to name the URL in its own
    sentence rather than put the finding on the line that caused it. This carries
    that anchor across the boundary between one job and the next.
    """
    records = [
        {"url": entry.url, "path": file_path, "line": entry.line_number}
        for file_path, entry in sorted(added, key=lambda item: (item[0], item[1].line_number))
    ]
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


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
            write_package_index(args.output_directory / "added-packages.json", [])
            print("No submission files changed; this pull request is not a submission.")
            return 0
        set_step_output("submission-files-changed", "true")
        check_commits_signed_off(args.base_commit, args.proposed_commit)
        check_one_file_per_dataset(args.proposed_commit, changed)
        history_cache: dict[str, list[str]] = {}
        added_urls: set[str] = set()
        removed_urls: set[str] = set()
        url_count = 0

        # Every syntax check first, then the network: a submitter with a typo
        # should not wait for one download per URL to be told about it.
        parsed: list[tuple[str, list[PackageLine]]] = []
        added_records: list[tuple[str, PackageLine]] = []
        for status, path in changed:
            entries: list[PackageLine] = []
            if status == "D":
                print(f"Validated removed submission file: {path}")
                proposed_content = None
            else:
                proposed_content = file_at(args.proposed_commit, path)
                if proposed_content is None:
                    raise ValidationError(
            f"Nothing readable at {path} in the proposed commit.",
            title="Could not read the submission file",
        )
                entries = parse_submission(path, proposed_content)
                parsed.append((path, entries))
                url_count += len(entries)
                print(f"Validated {path}: {len(entries)} package URL(s)")

            base_urls = set(package_urls(file_at(args.base_commit, path)))
            proposed_urls = set(package_urls(proposed_content))
            added_here = proposed_urls - base_urls
            added_urls.update(added_here)
            removed_urls.update(base_urls - proposed_urls)
            added_records += [(path, entry) for entry in entries if entry.url in added_here]

        license_cache: dict[str, str | None] = {}
        for path, entries in parsed:
            check_dataset_commits(path, entries, history_cache)
            check_package_ids(path, entries)
            check_package_licenses(path, entries, license_cache)
            _, namespace, dataset_name = PurePosixPath(path).parts
            remind_private_dataset(f"{namespace}/{dataset_name}")

        # The bill of materials last, and for the added packages only: it is the
        # one check that may download files, and a package already catalogued
        # was checked when it was added.
        added_by_path: dict[str, list[PackageLine]] = {}
        for path, entry in added_records:
            added_by_path.setdefault(path, []).append(entry)
        for path, added in added_by_path.items():
            check_bills_of_materials(path, added)

        args.output_directory.mkdir(parents=True, exist_ok=True)
        write_url_list(args.output_directory / "added-packages.txt", added_urls)
        write_url_list(args.output_directory / "removed-packages.txt", removed_urls)
        write_package_index(args.output_directory / "added-packages.json", added_records)
    except (ValidationError, UnicodeDecodeError) as exc:
        report_failure(exc)
        return 1

    print(f"Submission validation passed: {len(changed)} file(s), {url_count} proposed package URL(s).")
    print(f"Package changes: {len(added_urls)} added, {len(removed_urls)} removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
