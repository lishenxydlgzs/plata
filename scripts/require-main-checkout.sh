#!/usr/bin/env bash
# Sourced by production deployment entry points before any remote mutation.
require_main_checkout() {
    local checkout_root common_dir primary_root branch
    checkout_root="$(git -C "$WORKSPACE_ROOT" rev-parse --show-toplevel)"
    common_dir="$(git -C "$WORKSPACE_ROOT" rev-parse --path-format=absolute --git-common-dir)"
    primary_root="$(cd "$(dirname "$common_dir")" && pwd -P)"
    checkout_root="$(cd "$checkout_root" && pwd -P)"
    branch="$(git -C "$WORKSPACE_ROOT" branch --show-current)"
    if [[ "$checkout_root" != "$primary_root" || "$branch" != main ]]; then
        echo "Deploy only from the primary checkout on main: $primary_root" >&2
        echo "Commit worktree changes and merge them into main first." >&2
        return 1
    fi
}
