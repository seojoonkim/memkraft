#!/usr/bin/env bash
# Fast-forward main to a PR head whose required checks are green.
# Usage: scripts/ff_merge_pr.sh <pr-number> [remote]
set -euo pipefail
pr="${1:?usage: ff_merge_pr.sh <pr-number> [remote]}"
remote="${2:-origin}"
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
head="$(gh pr view "$pr" --repo "$repo" --json headRefOid --jq .headRefOid)"
state="$(gh pr view "$pr" --repo "$repo" --json state --jq .state)"
[ "$state" = OPEN ] || { echo "PR #$pr is $state" >&2; exit 1; }
git fetch -q "$remote" main "pull/$pr/head"
git merge-base --is-ancestor "$remote/main" "$head" || {
  echo "PR #$pr head $head is not on top of $remote/main; rebase it first" >&2; exit 1; }
gh pr checks "$pr" --repo "$repo" --required >/dev/null || {
  echo "required checks on $head are not all green" >&2; exit 1; }
git push "$remote" "$head:refs/heads/main"
gh pr view "$pr" --repo "$repo" --json state,mergeCommit --jq '"PR #\(.number // "'"$pr"'") \(.state) \(.mergeCommit.oid // "")"'
