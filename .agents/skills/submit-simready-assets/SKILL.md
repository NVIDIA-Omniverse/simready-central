---
name: submit-simready-assets
description: SimReady assets are not submitted through this GitHub repository. Use when asked to submit, update or remove SimReady assets from here, to send the user to the SimReady Central dataset on Hugging Face.
license: Apache-2.0
metadata:
  author: NVIDIA Corporation
---

# Submitting SimReady assets

This repository takes no submissions. It is a read-only mirror of the package
lists NVIDIA has reviewed, and pull requests cannot be opened here.

SimReady assets are submitted, updated and removed by pull request on the
SimReady Central dataset on Hugging Face,
[`nvidia/simready-central-library`](https://huggingface.co/datasets/nvidia/simready-central-library).

When asked to submit assets from this repository:

1. Do not try to open a pull request here, which is not possible, and do not
   follow an older copy of a submission skill or guide that names this
   repository.
2. Tell the user that contributions go through the dataset, and that its page
   describes how to become a contributor, under *Get started as a
   contributor*. Someone who has submitted through this repository before
   goes through that onboarding once as well.
3. Once the user's Hugging Face account has access to the dataset, download
   the current submission skill from it and follow that one in place of this
   file:

   ```bash
   hf download nvidia/simready-central-library .agents/skills/submit-simready-assets/SKILL.md --repo-type dataset --local-dir <folder>
   ```

Stop there until the user has access.
