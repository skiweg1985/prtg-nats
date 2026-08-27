#!/usr/bin/env bash
#
# What the updater does inside its container. Two jobs, and the split matters:
#
#   probe   read-only. Says where the checkout stands and what the branch has.
#           Safe to run on a timer, changes nothing, needs no write access.
#   apply   the update itself, one phase per invocation so the caller keeps
#           the job log truthful about where it stopped.
#
# The phases of `apply` are separate commands rather than one script because
# exactly one of them is the point of no return. Everything up to `build` can
# be undone by putting the checkout back; `recreate` replaces the container
# this platform runs in, and after it there is no process left to undo
# anything. Splitting them is what lets the caller offer a rollback for the
# common failure - a build that does not compile - and refuse to pretend it
# can offer one afterwards.

set -Eeuo pipefail

CHECKOUT="${PRTG_NATS_CHECKOUT:-}"
RUNTIME_DIR="${PRTG_NATS_RUNTIME_DIR:-/srv/prtg-nats/runtime}"
DEPLOY_KEY="${RUNTIME_DIR}/private/ssh/git-deploy"
KNOWN_HOSTS="${RUNTIME_DIR}/private/ssh/git_known_hosts"

die() {
  printf 'updater: %s\n' "$1" >&2
  exit "${2:-1}"
}

# The checkout is mounted at the same path it has on the host, which is not a
# detail: compose resolves a relative bind like ./web/Caddyfile against the
# project directory and hands the daemon an absolute path it reads as a host
# path. Mounted anywhere else, the recreated proxy would bind a directory that
# does not exist on the host and come up without its configuration.
require_checkout() {
  [[ -n "${CHECKOUT}" ]] || die 'PRTG_NATS_CHECKOUT is not set'
  [[ -d "${CHECKOUT}" ]] || die "checkout ${CHECKOUT} is not a directory"
  # A worktree keeps .git as a file pointing elsewhere, and that elsewhere is
  # not mounted here. Better to say so than to fail three commands later with
  # "not a git repository".
  [[ -e "${CHECKOUT}/.git" ]] || die "${CHECKOUT} is not a git checkout"
  [[ -d "${CHECKOUT}/.git" ]] ||
    die "${CHECKOUT}/.git is a file, so this is a git worktree; the updater needs the real repository"
  cd "${CHECKOUT}"
}

# Reaching the repository. The key is optional - a public repository over
# https needs none - but when it is there it is used exactly as strictly as
# the probe channel is: only this key, and only a host key we already know.
git_ssh_env() {
  [[ -f "${DEPLOY_KEY}" ]] || return 0
  local command="ssh -i ${DEPLOY_KEY} -o IdentitiesOnly=yes"
  if [[ -f "${KNOWN_HOSTS}" ]]; then
    command="${command} -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${KNOWN_HOSTS}"
  else
    # Refusing outright would leave an installation unable to update because
    # of a file it was never told to create. Saying so on every fetch is the
    # compromise: it works, and it is on the record that it is unpinned.
    printf 'updater: no pinned host key at %s; the connection is unauthenticated\n' \
      "${KNOWN_HOSTS}" >&2
    command="${command} -o StrictHostKeyChecking=accept-new"
  fi
  export GIT_SSH_COMMAND="${command}"
}

# Which phase the updater has reached, for the job's step list.
#
# An explicit marker rather than leaving the caller to recognise "Building the
# images..." in the output: that would make a progress display depend on the
# wording of a status line, and the first time somebody improved that wording
# the steps would silently stop advancing.
phase_marker() {
  printf '::phase %s\n' "$1"
}

json_string() {
  # Enough escaping for what git hands us: quotes and backslashes in a commit
  # subject, and every control character flattened to a space.
  #
  # Flattened rather than dropped, and all of them rather than most: a failing
  # git command answers in several lines, and a raw newline inside a JSON
  # string is not an escape problem, it is an unparseable document. The caller
  # would then see "the updater produced nonsense" instead of the reason the
  # repository could not be reached.
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\000-\037' ' '
}

# --- probe -----------------------------------------------------------------

# Everything the interface needs to say "you are here, the branch is there",
# as one JSON object on stdout. Diagnostics go to stderr so the caller can
# parse stdout without filtering it.
cmd_probe() {
  require_checkout
  git_ssh_env

  local branch head remote_head dirty ahead behind remote_error
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'HEAD')"
  head="$(git rev-parse HEAD)"
  dirty=false
  [[ -z "$(git status --porcelain)" ]] || dirty=true

  # ls-remote rather than fetch: it answers the only question a periodic check
  # asks, and it writes nothing into the checkout - so the timer cannot leave
  # a half-fetched repository behind for the update to trip over.
  #
  # A repository that cannot be reached is reported as exactly that. Folding
  # it into "nothing new" would be the worst possible lie here: an
  # installation whose deploy key expired would sit on an old version and
  # report itself up to date for as long as nobody looked.
  local target="${1:-${branch}}"
  remote_error=""
  if ! remote_head="$(git ls-remote origin "refs/heads/${target}" 2>&1 | cut -f1)"; then
    remote_error="${remote_head}"
    remote_head=""
  fi
  if [[ -n "${remote_error}" ]]; then
    printf 'updater: could not reach the repository: %s\n' "${remote_error}" >&2
  elif [[ -z "${remote_head}" ]]; then
    remote_error="the branch ${target} does not exist on origin"
    printf 'updater: %s\n' "${remote_error}" >&2
  fi

  ahead=0
  behind=0
  local commits='[]'
  # Counting needs the commit itself, and ls-remote only reported its name.
  # Without a fetch it is there only if an earlier one brought it in - which
  # is why "behind" can be 0 while the heads differ, and why the caller
  # compares the two hashes rather than trusting the count.
  if [[ -n "${remote_head}" ]] && git cat-file -e "${remote_head}^{commit}" 2>/dev/null; then
    behind="$(git rev-list --count "HEAD..${remote_head}" 2>/dev/null || printf '0')"
    ahead="$(git rev-list --count "${remote_head}..HEAD" 2>/dev/null || printf '0')"
    commits="$(list_commits "HEAD..${remote_head}")"
  fi

  printf '{"branch":"%s","head":"%s","dirty":%s,"remote_head":"%s",' \
    "$(json_string "${branch}")" "$(json_string "${head}")" "${dirty}" \
    "$(json_string "${remote_head}")"
  printf '"behind":%s,"ahead":%s,"reachable":%s,"error":"%s","commits":%s}\n' \
    "${behind}" "${ahead}" \
    "$([[ -z "${remote_error}" ]] && printf 'true' || printf 'false')" \
    "$(json_string "${remote_error}")" \
    "${commits}"
}

# The commits between here and there, newest first, for the interface to list.
# Capped: a checkout left alone for a year should not answer with a thousand
# entries nobody reads.
list_commits() {
  local range="$1"
  local first=1
  printf '['
  while IFS=$'\x1f' read -r sha subject when; do
    [[ -n "${sha}" ]] || continue
    [[ ${first} -eq 1 ]] || printf ','
    first=0
    printf '{"sha":"%s","subject":"%s","date":"%s"}' \
      "$(json_string "${sha}")" "$(json_string "${subject}")" \
      "$(json_string "${when}")"
  done < <(git log --max-count=50 --format=$'%H\x1f%s\x1f%cI' "${range}" 2>/dev/null)
  printf ']'
}

# --- apply -----------------------------------------------------------------

# Bring the remote state in without touching the working tree yet. Separate
# from the checkout so a network failure fails before anything has moved.
phase_fetch() {
  require_checkout || return 1
  git_ssh_env
  phase_marker fetch
  printf 'Fetching %s...\n' "${1:-origin}"
  git fetch --prune origin || return 1
  printf 'Fetched.\n'
}

# Move the checkout onto the target commit, on its branch.
#
# On its branch, and that is the point of doing this in two steps rather than
# one `checkout <commit>`. A checkout left on a detached HEAD reports its
# branch as "HEAD", and the next `git pull --ff-only` somebody types on the
# console fails for a reason that has nothing to do with what they are doing.
# The installation should look the same after an update from the interface as
# after one from the command line.
#
# --ff-only, never a merge commit: a checkout that has diverged from the
# branch holds work somebody did on this machine. Fast-forward or stop.
phase_checkout() {
  require_checkout || return 1
  local branch="$1"
  local target="$2"
  [[ -n "${branch}" ]] || die 'no branch given'
  [[ -n "${target}" ]] || die 'no target commit given'

  phase_marker checkout
  git cat-file -e "${target}^{commit}" 2>/dev/null ||
    die "commit ${target} is not in this repository"

  printf 'Moving %s from %s to %s...\n' "${branch}" \
    "$(git rev-parse --short HEAD)" "$(git rev-parse --short "${target}")"
  git checkout --quiet "${branch}" || return 1
  git merge --ff-only --quiet "${target}" || return 1
  printf 'Checkout is at %s.\n' "$(git rev-parse HEAD)"
}

# The long one, and the last one that can still be undone: nothing has been
# replaced while this runs. A build that fails leaves the running stack
# exactly as it was.
#
# The explicit `|| return 1` is not decoration. Bash suspends errexit inside a
# condition, and this function is called from one - without it a failing build
# would fall through to the line below, report success, and the rollback that
# exists for exactly this case would never run.
phase_build() {
  require_checkout || return 1
  phase_marker build
  printf 'Building the images...\n'
  GIT_COMMIT="$(git rev-parse HEAD)" \
    GIT_REF="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf '')" \
    docker compose --project-directory "${CHECKOUT}" build --pull || return 1
  printf 'Build finished.\n'
}

# The point of no return. This replaces prtg-nats-web-api, so the process that
# asked for the update stops existing partway through this command.
#
# No --force-recreate, deliberately, and unlike the shell path: compose then
# recreates only what actually changed. The proxy runs a pinned image with an
# unchanged configuration and stays up, which is what lets the browser show a
# "coming back" page instead of a connection error.
phase_recreate() {
  require_checkout || return 1
  phase_marker recreate
  printf 'Recreating the stack...\n'
  GIT_COMMIT="$(git rev-parse HEAD)" \
    GIT_REF="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf '')" \
    docker compose --project-directory "${CHECKOUT}" up -d --remove-orphans || return 1
  printf 'Stack recreated.\n'
}

# The whole sequence, for the caller that hands over and stops watching. The
# rollback is here rather than at the caller because after `recreate` there is
# no caller left to run one.
cmd_apply() {
  local branch="${1:-}"
  local target="${2:-}"
  local rollback_to="${3:-}"
  [[ -n "${branch}" ]] || die 'apply needs a branch'
  [[ -n "${target}" ]] || die 'apply needs a target commit'

  phase_fetch
  phase_checkout "${branch}" "${target}"

  if ! phase_build; then
    printf 'The build failed.\n' >&2
    if [[ -n "${rollback_to}" ]]; then
      printf 'Putting the checkout back to %s. Nothing was replaced.\n' \
        "${rollback_to}" >&2
      # reset rather than checkout: the branch itself was fast-forwarded a
      # moment ago, so putting only the working tree back would leave it
      # pointing at the version that does not build. Safe here for the same
      # reason the fast-forward was - the commits being left behind are on
      # the remote, and preflight has already refused a dirty checkout.
      git reset --hard --quiet "${rollback_to}" ||
        printf 'The checkout could not be put back.\n' >&2
    fi
    exit 2
  fi

  phase_recreate
  printf 'Update complete.\n'
}

# Build and replace, without touching the checkout.
#
# For the state where somebody pulled on the host and never rebuilt: the
# checkout is already where it should be, and fetching or moving it would be
# work with nothing to do. Which also means there is nothing to roll back if
# the build fails - the checkout was never moved, so the running stack is
# simply left alone.
cmd_rebuild() {
  if ! phase_build; then
    printf 'The build failed. Nothing was replaced.\n' >&2
    exit 2
  fi

  phase_recreate
  printf 'Rebuild complete.\n'
}

# --- dispatch --------------------------------------------------------------

case "${1:-}" in
probe)
  shift
  cmd_probe "$@"
  ;;
apply)
  shift
  cmd_apply "$@"
  ;;
rebuild)
  shift
  cmd_rebuild "$@"
  ;;
fetch)
  shift
  phase_fetch "$@"
  ;;
checkout)
  shift
  phase_checkout "$@"
  ;;
build)
  shift
  phase_build "$@"
  ;;
recreate)
  shift
  phase_recreate "$@"
  ;;
noop)
  # What compose runs when it builds this image. The build is the point.
  exit 0
  ;;
*)
  die "unknown command: ${1:-<none>}"
  ;;
esac
