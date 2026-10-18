# Production source differs from this checkout

Status: resolved — verified 2026-10-17 before the ontology media deployment.

During the 2026-10-04 branding update, the production workspace exposed newer CC
playlist catalog controls that are absent from this branch's frontend source.
The full deployment was rolled back using release `20261004T230157Z`, retaining
current databases. The restored production frontend was copied to a temporary
build directory; only the icon, brand wording, favicon and metadata were changed.
Its rebuilt assets and those three source files were uploaded without another
backend restart or full source sync. Browser verification confirmed both the new
icon and the existing CC song catalog; no console errors were observed.

The branch's branding implementation is commit `99d17bc`. The live frontend also
contains the newer production features. Before using `scripts/deploy.sh` again,
locate and integrate the source branch for those features, or compare production
source against this checkout and reconcile intentionally. A full rsync from the
current branch would otherwise remove them. The private pre-branding release
archive remains on the robot for recovery.

## Resolution

Compared SHA-256 hashes for all 104 tracked application files under `packages/`
and `scripts/` in the primary checkout at `f5b22ec` against production on
2026-10-17: no differences. The newer features are now integrated into `main`;
the historical warning no longer blocks a normal deployment. `AGENTS.md` now
requires committing and merging worktree changes into the primary checkout's
`main` before deployment. Both deploy and source-sync entry points enforce that
checkout and branch rule before remote operations.
