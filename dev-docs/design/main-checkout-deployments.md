# Deploy from the primary main checkout

Commit development worktrees and integrate into the primary checkout's `main`
branch before deploying. The deployment and source-sync entry points share a
preflight guard: resolve the common Git directory to find the primary checkout,
compare canonical filesystem paths, and require the current branch to be `main`.
Reject linked worktrees and detached HEADs before building or contacting production.
Keep the existing backup, database snapshot, restart, health check and rollback
workflow. Unrelated local changes must not be reset or committed as part of a merge.
