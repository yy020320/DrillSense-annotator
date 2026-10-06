# DrillSense Annotator release checklist

## Required before the first public release

- [ ] Replace the placeholder repository URL and author metadata in `CITATION.cff`.
- [ ] Decide whether the paper, source code and any demo data can legally be released.
- [ ] Remove private recordings, annotation exports, model weights and project-specific paths from the repository.
- [ ] Add one synthetic or de-identified WAV/CSV example and document its provenance.
- [ ] Rebuild and smoke-test the Windows executable on a clean machine.
- [ ] Check PySide6, Qt, libsndfile and PyInstaller notices for binary redistribution.
- [ ] Tag the source and binary as the same version, for example `v0.1.0`.
- [ ] Create a GitHub/GitLab release and archive the exact source snapshot with Zenodo for a DOI.
- [ ] Add the repository URL and DOI to the paper's Data/Code Availability statement.

## Recommended after release

- [ ] Add automated compile and import checks in CI.
- [ ] Add a short screen recording or screenshots showing the annotation workflow.
- [ ] Add issue templates for bug reports and data-format questions.
- [ ] Keep raw field recordings in a controlled data repository, not in the code repository.
