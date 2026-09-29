#!/usr/bin/env bash
# Read-only worktree prune audit. Classifies every git worktree by size, merge
# state, uncommitted work, remote/PR state, a live Grok CLI session inside it,
# and the most recent chat that operated in it. Emits a table sorted by size
# with a suggested bucket. Never deletes anything; deletion stays a
# human-gated step in the playbook.
#
# Usage: worktree-audit.sh [repo-path]   (defaults to the current repo)
set -u

repo="${1:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -z "$repo" ] && { echo "not in a git repo; pass a repo path" >&2; exit 1; }
cd "$repo" || exit 1

# Main worktree is the first entry; everything else is a candidate.
main_wt=$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')

# origin/main drives the merge check. Best-effort; stale is fine for a first pass.
git fetch origin main --quiet 2>/dev/null || echo "warn: could not fetch origin/main; merged column may be stale" >&2

# PR state by branch, fetched once. Empty if gh is unavailable.
prs=$(mktemp)
gh pr list --author "@me" --state all --limit 1000 \
	--json number,state,headRefName,headRefOid 2>/dev/null > "$prs" || echo "[]" > "$prs"

# Grok CLI sessions: ~/.grok/sessions/<cwd-key>/<session-id>/, where <cwd-key>
# is the session's working directory URL-encoded. A key over 255 bytes is a
# slug plus hash instead, with the original path in the group's .cwd file.
grok_home="${GROK_HOME:-$HOME/.grok}"
sessions="$grok_home/sessions"
cwd_key() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
transcripts="$sessions/$(cwd_key "$main_wt")"

# Session groups whose working directory is $1 or inside it.
session_groups() {
	local key; key=$(cwd_key "$1")
	for g in "$sessions/$key" "$sessions/$key"%2F*; do [ -d "$g" ] && echo "$g"; done
	for c in "$sessions"/*/.cwd; do
		[ -f "$c" ] || continue
		case "$(cat "$c")" in "$1" | "$1"/*) dirname "$c" ;; esac
	done
}

# Live Grok CLI sessions whose working directory is $1 or inside it.
live_sessions() {
	[ -f "$grok_home/active_sessions.json" ] || return 0
	jq -r --arg wt "$1" '.[] | select(.cwd == $wt or (.cwd | startswith($wt + "/"))) | .pid' \
		"$grok_home/active_sessions.json" 2>/dev/null | while read -r pid; do
		kill -0 "$pid" 2>/dev/null && echo "$pid"
	done
}
now=$(date +%s)

# GNU and BSD stat/date spell these differently; Grok CLI runs on both.
mtime() { stat -c '%Y' "$1" 2>/dev/null || stat -f '%m' "$1" 2>/dev/null; }
ymd() { date -d "@$1" '+%Y-%m-%d' 2>/dev/null || date -r "$1" '+%Y-%m-%d' 2>/dev/null; }

printf "SIZE\tAGE\tMERGED\tDIRTY\tREMOTE\tPR\tLAST_CHAT\tBUCKET\tWORKTREE\n"

git worktree list --porcelain | awk '/^worktree /{print $2}' | while read -r wt; do
	[ "$wt" = "$main_wt" ] && continue

	size=$(du -sh "$wt" 2>/dev/null | awk '{print $1}')
	head=$(git -C "$wt" rev-parse HEAD 2>/dev/null)
	head_ts=$(git -C "$wt" log -1 --format='%ct' HEAD 2>/dev/null || echo 0)
	age=$([ "$head_ts" -gt 0 ] 2>/dev/null && echo "$(( (now - head_ts) / 86400 ))d" || echo "?")

	# Squash-merged branches are not ancestors of main, so PR state is the
	# real signal; merge-base only catches fast-forward/rebase merges.
	git merge-base --is-ancestor "$head" origin/main 2>/dev/null && merged=YES || merged=no

	# Removing the worktree deletes tracked edits, untracked files, and ignored
	# files alike, even an ignored build directory someone wrote a file into,
	# so every kind present is listed.
	porcelain=$(git -C "$wt" status --porcelain --ignored=matching 2>/dev/null)
	dirty=""
	for kind in 'wip:^[^?!]' 'untracked:^??' 'ignored:^!!'; do
		n=$(printf '%s\n' "$porcelain" | grep -c "${kind#*:}")
		[ "$n" -gt 0 ] && dirty="${dirty:+$dirty,}${kind%%:*}:$n"
	done
	[ -z "$dirty" ] && dirty=clean

	branch=$(git -C "$wt" symbolic-ref --quiet --short HEAD 2>/dev/null || echo "")
	if [ -z "$branch" ]; then remote=detached
	elif git -C "$wt" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
		[ "$(git -C "$wt" rev-parse "origin/$branch" 2>/dev/null)" = "$head" ] \
			&& remote=pushed \
			|| remote="ahead$(git -C "$wt" rev-list --count "origin/$branch..HEAD" 2>/dev/null)"
	else remote=no-remote; fi

	pr_line=$([ -n "$branch" ] && jq -r --arg b "$branch" \
		'.[] | select(.headRefName==$b) | "#\(.number)/\(.state) \(.headRefOid // "")"' "$prs" 2>/dev/null | head -1)
	pr=${pr_line%% *}
	pr_head=${pr_line#* }
	[ -z "$pr" ] && pr="-"
	pr_contains=no
	if [ -n "$pr_head" ] && [ "$pr_head" != "$pr" ]; then
		git -C "$wt" merge-base --is-ancestor "$head" "$pr_head" 2>/dev/null && pr_contains=yes
	fi

	# Most recent chat whose transcript operated in this worktree. Match path
	# followed by "/" or a quote so glint-482 does not match glint-482-r37.
	last="-"; last_ts=0
	f=$({ [ -d "$transcripts" ] && grep -rlF -e "${wt}/" -e "${wt}\"" "$transcripts" 2>/dev/null
		session_groups "$wt" | while read -r g; do find "$g" -type f 2>/dev/null; done; } \
		| while read -r t; do printf '%s %s\n' "$(mtime "$t")" "$t"; done | sort -rn | head -1)
	if [ -n "$f" ]; then last_ts=$(echo "$f" | awk '{print $1}')
		last=$(ymd "$last_ts"); fi
	recent=$([ "$last_ts" -gt 0 ] 2>/dev/null && [ $(( (now - last_ts) / 86400 )) -le 4 ] && echo yes || echo no)
	[ -n "$(live_sessions "$wt")" ] && { live=yes; last=live; } || live=no

	# A detached HEAD's commits live on no branch, so removing the worktree
	# strands them unless some ref still contains that commit.
	unreachable=no
	[ -z "$branch" ] && [ -z "$(git -C "$wt" for-each-ref --contains "$head" --count=1 2>/dev/null)" ] && unreachable=yes

	if [ "$live" = yes ]; then bucket=hold-live-session
	else case "$dirty" in wip:*) bucket=hold-wip ;; untracked:*) bucket=hold-untracked ;; ignored:*) bucket=hold-ignored ;; *)
		if [ "$unreachable" = yes ]; then bucket=hold-unreachable
		else case "$pr" in *OPEN*) bucket=hold-open-pr ;; *)
			if [ "$recent" = yes ]; then bucket=verify-recent-chat
			elif [ "$merged" = YES ] || { [ "${pr##*/}" = MERGED ] && [ "$pr_contains" = yes ]; }; then bucket=safe
			else bucket=review; fi ;;
		esac; fi ;;
	esac; fi

	printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
		"$size" "$age" "$merged" "$dirty" "$remote" "$pr" "$last" "$bucket" "$wt"
done | sort -t$'\t' -k1,1 -rh

rm -f "$prs"
