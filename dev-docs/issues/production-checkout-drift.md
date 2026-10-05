# Production source differs from this checkout

Status: open — reconcile before the next full deployment.

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
