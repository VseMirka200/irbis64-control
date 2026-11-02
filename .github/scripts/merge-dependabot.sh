#!/usr/bin/env bash
# Merge only unambiguous patch/minor updates authored by Dependabot.
# Runs on the trusted default branch; never checks out pull-request code.
set -euo pipefail

repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
base="${BASE_BRANCH:-main}"
method="${MERGE_METHOD:-squash}"

# Fail closed on grouped, prerelease, non-semver and major-version updates.
is_safe_version_update() {
  local title="$1"
  local pattern='^Bump .+ from v?([0-9]+([.][0-9]+){0,2}) to v?([0-9]+([.][0-9]+){0,2})( in .+)?$'
  [[ "$title" =~ $pattern ]] || return 1

  local old_version="${BASH_REMATCH[1]}"
  local new_version="${BASH_REMATCH[3]}"
  local old_major old_minor old_patch new_major new_minor new_patch
  read -r old_major old_minor old_patch <<< "${old_version//./ }"
  read -r new_major new_minor new_patch <<< "${new_version//./ }"
  old_minor="${old_minor:-0}"
  old_patch="${old_patch:-0}"
  new_minor="${new_minor:-0}"
  new_patch="${new_patch:-0}"

  (( 10#$new_major == 10#$old_major &&
     (10#$new_minor > 10#$old_minor ||
      (10#$new_minor == 10#$old_minor && 10#$new_patch > 10#$old_patch)) ))
}

# Read-only lookup of open PRs; only trust the actual GitHub author and branch.
prs="$(gh api "repos/$repo/pulls?state=open&per_page=100" --paginate \
  --jq '.[] | select(.user.login == "dependabot[bot]" and .head.repo.full_name == .base.repo.full_name and (.head.ref | startswith("dependabot/"))) | [.number, .title, .head.sha] | @tsv')"

if [[ -z "$prs" ]]; then
  echo "No eligible Dependabot pull requests found."
  exit 0
fi

while IFS=$'\t' read -r number title head_sha; do
  if ! is_safe_version_update "$title"; then
    echo "Skip PR #$number: not a simple patch/minor version bump."
    continue
  fi

  # Verify the PR still matches the inspected author, title, branch and SHA.
  details="$(gh api "repos/$repo/pulls/$number")" || continue
  if ! jq -e --arg repo "$repo" --arg base "$base" --arg title "$title" --arg sha "$head_sha" '
    .user.login == "dependabot[bot]" and
    .head.repo.full_name == $repo and
    (.head.ref | startswith("dependabot/")) and
    .base.ref == $base and
    .title == $title and
    .head.sha == $sha and
    .draft == false
  ' <<< "$details" >/dev/null; then
    echo "Skip PR #$number: PR metadata changed or is not trusted."
    continue
  fi

  checks="$(gh pr view "$number" --repo "$repo" \
    --json statusCheckRollup,mergeStateStatus)" || continue

  # Require at least one passing check and no pending, failed, or cancelled
  # checks. A missing check suite does NOT count as a passing build.
  if ! jq -e '
    (.statusCheckRollup // []) as $checks |
    (($checks | length) > 0) and
    ($checks | any(.[]; (.__typename == "CheckRun" and .conclusion == "SUCCESS") or (.__typename == "StatusContext" and .state == "SUCCESS"))) and
    ($checks | all(.[];
      if .__typename == "CheckRun" then
        .status == "COMPLETED" and (.conclusion == "SUCCESS" or .conclusion == "NEUTRAL" or .conclusion == "SKIPPED")
      elif .__typename == "StatusContext" then
        .state == "SUCCESS"
      else
        false
      end
    )) and
    (.mergeStateStatus == "CLEAN")
  ' <<< "$checks" >/dev/null; then
    echo "Skip PR #$number: checks pending/failed/missing or merge is blocked."
    continue
  fi

  echo "All checks passed; merging Dependabot patch/minor PR #$number."
  # Lease prevents merging if the PR head changed after our checks.
  if ! gh pr merge "$number" --repo "$repo" \
    "--$method" --delete-branch --match-head-commit "$head_sha"; then
    echo "Unable to merge PR #$number (repository policy may block the merge)."
  fi
done <<< "$prs"
