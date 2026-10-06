## Submission

**OEM organization:** <!-- the company or team publishing these assets -->

**Primary contact:** <!-- name and email we should reply to about this submission -->

**Hugging Face dataset:** <!-- namespace/dataset, e.g. acme/simready-assets -->

**Validation profile used:** <!-- the --profile value you passed, e.g. Robotics-Prop; the version is in each asset's validation sidecar -->

**Assets added or updated:** <!-- how many assets this submission adds or gives a new version to -->

**Total packages listed:** <!-- lines in submissions/<namespace>/<dataset> after this change, including versions submitted earlier -->

## Checklist

- [ ] The Hugging Face dataset is public and ungated, and its dataset card
      declares the same license as the packages.
- [ ] Every asset was submitted with `simready-manage submit`, using the profile
      named above.
- [ ] Packaging succeeded for every asset in the list. Any asset that failed a
      step was left out of this submission rather than included as-is.
- [ ] This pull request changes only `submissions/<namespace>/<dataset>`, and
      nothing else.
- [ ] Each line in that file links to one of your packages in the dataset above,
      pinned to a commit hash so it always resolves to the same files.
- [ ] Any lines removed in this pull request were removed deliberately.
      **Only remove a line if you are sure that is what you want.** The file is
      the complete list of what the catalog serves, so removing a line withdraws
      that asset version and breaks anything already using it. Submitting a new
      version does **not** require removing the old one: add the new line and leave
      the existing ones alone.
- [ ] Each submitted asset folder holds only USD, texture, audio, MDL, TOML
      and JSON files.

## Notes for reviewers

<!-- What is in this asset set, in a sentence. Anything you already know is
     wrong or unusual — validation features that failed, why they are acceptable,
     and whether you used --skip-validate, --ignore-validation-results,
     --skip-benchmark or --ignore-benchmark-results. -->
