---
name: submit-simready-assets
description: Validate, package, and upload local SimReady assets, then open a SimReady Central PR. Use for OEM submissions. Do not use for NVIDIA review or merging.
license: Apache-2.0
metadata:
  author: NVIDIA Corporation
---

# Submit SimReady Assets

## Purpose

Turn a short request into a complete, auditable OEM asset submission while
using the current OEM guide as the source of truth.

## Prerequisites

Require access to the requested asset folder and the accounts, tools, and
destinations specified by the current OEM guide. Discover what is already
available before asking the user for anything missing.

## Examples

```text
Help me submit the SimReady assets in /projects/factory-assets.
```

```text
Package and upload the assets in ./delivery, then open the SimReady Central PR.
```

## Use the authoritative guide

1. Locate the SimReady Central checkout independently of the current working
   directory. Start with the repository containing this `SKILL.md`; otherwise,
   look for a checkout containing `docs/oem-asset-submission-guide.md` in the
   current directory or one of its parents. If no checkout exists, retrieve the
   guide from the same repository and revision as this skill using authenticated
   GitHub access. If that fails, ask the user to authenticate or provide the
   guide; never treat an unrelated Git repository as SimReady Central.
2. Read `docs/oem-asset-submission-guide.md` completely before acting and follow
   its current workflow exactly. The guide is the single source of truth for
   prerequisites, commands, validation rules, package identity, uploads, and
   the review request. Do not rely on remembered versions of those details.
3. Before inspecting the user's assets or running workflow commands, give the
   user a very brief, plain-language summary derived from the current guide.
   Describe only the major local and remote stages, disclose that the local
   processing modifies the source assets, and state that open questions and
   required choices will be clarified with the user before proceeding. Do not
   include commands or implementation details in this overview.

## Resolve the request

1. Resolve the asset source directory from the request. If it was not provided,
   ask for it rather than assuming a default folder.
2. Inspect the source, existing configuration, authenticated CLI state, and
   remote destinations without modifying them. Derive safely discoverable facts
   before asking questions.
3. Ask one concise set of questions for the required guide inputs that remain
   unknown. Do not invent values or create a remote destination the user did not
   authorize.
4. In that question set, request a one-sentence description of what the
   submitted asset set contains unless the user already provided one. Do not
   infer its meaning from package, folder, or dataset names.
5. Require a publicly accessible Hugging Face dataset for every submission.
   Create a new dataset with the guide's `--public` option, and verify that an
   existing dataset can be read without credentials before using it.
6. When submitting a new asset version, preserve all existing catalog URLs by
   default. Do not ask whether older versions should be removed; discuss
   replacement or removal only when the user explicitly requests it.

## Execute with explicit boundaries

1. Treat the asset source as read-only until the user explicitly confirms
   source modifications. Before the first source write, identify the affected
   asset folders and explain what the applicable guide commands will modify.
2. Follow the guide in order. Do not silently skip failed steps, weaken its
   acceptance rules, or substitute different commands.
3. Never modify unrelated files or asset folders. Preserve detailed command
   output for diagnosis without exposing credentials.
4. Before remote writes, show the resolved destinations and affected assets.
   Obtain confirmation when a destination was inferred or existing remote data
   will be replaced.
5. Before opening the review request, draft a short factual title and
   description from the actual submission results, the user's asset-set
   description, and the current guide. Show both to the user and ask whether to
   use them or make edits. Never open a review request with an empty description.
6. Report the submission outcome concisely, including successful and excluded
   assets, relevant validation or packaging failures, immutable upload links,
   and the review-request link and status. Do not merge the review request.

## Handle validation failures

1. When profile validation fails, preserve its structured report and the
   validator's evidence. Locate the exact SimReady Foundation checkout and
   revision used by the failed validation, normally from its `--project-config`
   path. Do not substitute or update the checkout while diagnosing the result.
2. Read that checkout's `AGENTS.md`. Resolve the reported profile and version
   through its machine-readable TOML under `nv_core/sr_specs/docs/profiles/`,
   then resolve the exact feature versions and requirement IDs through their
   feature JSON manifests. Use these sources to distinguish required features
   from optional ones. Apply only the exceptions explicitly permitted by the
   current OEM guide.
3. Give the user one short, human-readable summary covering **all** remaining
   failed required features. Group affected assets by feature and requirement.
   For each group, use the matching feature and requirement documentation to
   state what capability failed, what the requirement means, which assets are
   affected, and the validator's specific reason. Determine internally whether
   SimReady Foundation provides a matching conformance skill. Tell the user only
   whether a Foundation repair workflow is available; do not expose its name,
   path, internal routing, or implementation details.
4. After the complete summary, ask one concise question: whether to exclude the
   affected assets and continue with those that passed, or hand the failures to
   the available Foundation repair workflow for a closer look. Do not start the
   repair workflow merely because it exists.
5. If the user chooses exclusion, omit a new asset from packaging, upload, and
   the pull request. For a failed update, skip the new version but preserve every
   previously submitted immutable URL by default; exclusion must not
   unintentionally remove an existing catalog version. Report all excluded
   assets and stop if none remain eligible for submission.
6. If the user requests a closer look, locate the matching conformance skill in
   the Foundation checkout's canonical `skills/` tree, using `.agents/skills/`
   only as a compatibility fallback. Read its `SKILL.md` completely and hand off
   the entire investigation and repair process to it. Do not duplicate, abridge,
   or add feature-specific repair instructions in this submission skill. Follow
   Foundation's feature-gate order when handing off multiple failures.
7. Continue to enforce the submission boundary during the handoff: investigation
   remains read-only until the user explicitly approves asset repair. If no
   matching Foundation skill exists, say briefly that no automated repair
   workflow is available and offer either exclusion or a closer manual review of
   the Foundation documentation.

## Limitations

- Stop after opening or updating the OEM submission PR; do not perform NVIDIA's
  internal review, approve the PR, merge it, or publish packages to the central
  repository.
- Do not modify source assets without the user's explicit confirmation.

## Troubleshooting

- If the guide cannot be read, ask the user to authenticate or provide it.
- If validation, packaging, upload, or PR creation fails, stop at that stage,
  preserve the diagnostic output, and continue only after the cause is fixed or
  the affected assets are explicitly excluded where the guide permits it.
