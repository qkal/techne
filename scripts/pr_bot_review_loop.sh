#!/usr/bin/env bash
# Poll open PRs for bot review comments, print actionable findings, optionally merge.
set -euo pipefail

REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
POLL_SECONDS="${POLL_SECONDS:-30}"
MAX_ROUNDS="${MAX_ROUNDS:-6}"
MERGE="${MERGE:-0}"
BASE_BRANCH="${BASE_BRANCH:-master}"

is_bot_login() {
  local login="$1"
  [[ "$login" =~ [Bb]ot$ ]] \
    || [[ "$login" == "coderabbitai" ]] \
    || [[ "$login" == "cursor[bot]" ]] \
    || [[ "$login" == "github-actions[bot]" ]]
}

has_actionable_body() {
  local body="$1"
  if [[ "$body" =~ "rate limited" ]] || [[ "$body" =~ "Review limit reached" ]]; then
    return 1
  fi
  if [[ "$body" =~ "review in progress" ]] || [[ "$body" =~ "Currently processing" ]]; then
    return 1
  fi
  if [[ "$body" =~ "(suggestion)" ]] \
    || [[ "$body" =~ "Potential issue" ]] \
    || [[ "$body" =~ "Actionable" ]] \
    || [[ "$body" =~ "Consider" ]] \
    || [[ "$body" =~ "nitpick" ]] \
    || [[ "$body" == *"\`\`\`suggestion"* ]]; then
    return 0
  fi
  return 1
}

collect_actionable_comments() {
  local pr="$1"
  local found=0

  while IFS= read -r line; do
    local login path body
    login=$(jq -r '.user.login' <<<"$line")
    path=$(jq -r '.path // ""' <<<"$line")
    body=$(jq -r '.body' <<<"$line")
    if is_bot_login "$login" && has_actionable_body "$body"; then
      found=1
      echo "PR #$pr actionable comment from $login on ${path:-issue}"
      echo "$body" | head -c 1200
      echo
      echo "---"
    fi
  done < <(
    gh api "repos/$REPO/pulls/$pr/comments" --paginate 2>/dev/null \
      | jq -c '.[]' || true
    gh api "repos/$REPO/issues/$pr/comments" --paginate 2>/dev/null \
      | jq -c '.[]' || true
    gh api "repos/$REPO/pulls/$pr/reviews" --paginate 2>/dev/null \
      | jq -c '.[] | select(.body != null and .body != "")' || true
  )

  return "$found"
}

pr_ci_green() {
  local pr="$1"
  gh pr checks "$pr" --required 2>/dev/null | rg -q "fail|pending" && return 1 || return 0
}

merge_ready_prs() {
  mapfile -t prs < <(gh pr list --state open --base "$BASE_BRANCH" --json number -q '.[].number' | sort -n)
  for pr in "${prs[@]}"; do
    if ! pr_ci_green "$pr"; then
      echo "PR #$pr: CI not green, skip merge"
      continue
    fi
    if collect_actionable_comments "$pr"; then
      echo "PR #$pr: actionable bot comments remain, skip merge"
      continue
    fi
    echo "Merging PR #$pr into $BASE_BRANCH"
    gh pr merge "$pr" --merge --delete-branch || {
      echo "Merge failed for PR #$pr" >&2
      return 1
    }
  done
}

main() {
  echo "Repo: $REPO | rounds=$MAX_ROUNDS | interval=${POLL_SECONDS}s | merge=$MERGE"
  local round actionable_total=0
  for ((round = 1; round <= MAX_ROUNDS; round++)); do
    echo
    echo "=== Round $round/$MAX_ROUNDS ==="
    mapfile -t prs < <(gh pr list --state open --base "$BASE_BRANCH" --json number -q '.[].number' | sort -n)
    if ((${#prs[@]} == 0)); then
      echo "No open PRs targeting $BASE_BRANCH"
      break
    fi
    local round_actionable=0
    for pr in "${prs[@]}"; do
      echo "Checking PR #$pr ..."
      if collect_actionable_comments "$pr"; then
        round_actionable=1
        actionable_total=1
      else
        echo "PR #$pr: no actionable bot comments"
      fi
      gh pr checks "$pr" 2>/dev/null | rg "Test|fail|pending" || true
    done
    if ((round_actionable == 0)) && ((MERGE == 1)); then
      merge_ready_prs
      break
    fi
    if ((round < MAX_ROUNDS)); then
      sleep "$POLL_SECONDS"
    fi
  done

  if ((actionable_total == 0)); then
    echo
    echo "No actionable bot review comments detected."
  fi
}

main "$@"
