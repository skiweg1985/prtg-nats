# Completion for ./prtg-nats.
#
# Meant for bash, but runs just as well under zsh once
# "autoload -U +X bashcompinit && bashcompinit" has been loaded there. The
# setup is described in docs/guides/operations.md.
#
# User and probe names come from the runtime directory instead of a
# maintained list: a second truth about the created names would inevitably
# go stale. The directory belongs to root with 0700; without root
# privileges the name completion therefore stays empty.

_prtg_nats_commands='backup ca-info ca-path ca-show completion config help init
install-mpp iperf-server logs mpp-info probe renew-certificate restart
self sensor setup ssh-key start status stop update user verify'

# Old names: still accepted, but no longer suggested. They stand here so the
# drift check in tests/check-static.sh knows them and does not wrongly report
# a missing command.
# shellcheck disable=SC2034  # only read by the check

# The internal probe verbs (internal-*) are deliberately missing here: they
# are only called by the tool itself.
_prtg_nats_probe_commands='adopt apply configure enroll helper-update info
install-ca list show status unenroll'
_prtg_nats_user_commands='add delete list rotate show'
_prtg_nats_sensor_commands='deploy list prepare profile recover release remove reserve
show status'
_prtg_nats_iperf_server_commands='deploy forget install list revoke show'

# Finds the repository for the invocation being completed. For
# "prtg-nats" without a path the route goes over PATH and the symlink from
# "prtg-nats link" - guessing $PWD would be wrong as soon as you work from
# another directory.
_prtg_nats_project_dir() {
  local invoked="$1"
  local resolved=""

  case "${invoked}" in
    */*)
      resolved="${invoked}"
      ;;
    *)
      resolved="$(command -v -- "${invoked}" 2>/dev/null)"
      ;;
  esac
  [[ -n "${resolved}" ]] ||
    {
      printf '%s\n' "${PWD}"
      return 0
    }
  resolved="$(readlink -f -- "${resolved}" 2>/dev/null || printf '%s' "${resolved}")"
  (cd -- "${resolved%/*}" 2>/dev/null && pwd) || printf '%s\n' "${PWD}"
}

# Names of the *.env files in a runtime directory, without the extension.
_prtg_nats_names() {
  local directory="$1"
  local entry=""

  [[ -d "${directory}" ]] || return 0
  for entry in "${directory}"/*.env; do
    [[ -e "${entry}" ]] || continue
    entry="${entry##*/}"
    printf '%s\n' "${entry%.env}"
  done
}

_prtg_nats_users() {
  _prtg_nats_names "$1/runtime/credentials"
}

# Enrolled probes only: for "probe status" and relatives, a NATS user
# without inventory is no useful suggestion.
_prtg_nats_probes() {
  _prtg_nats_names "$1/runtime/probes"
}

# The configured endpoints. Like the user and probe names, from the
# runtime directory that belongs to root with 0700.
_prtg_nats_iperf_server_names() {
  _prtg_nats_names "$1/runtime/iperf"
}

# The sensors live in the repository and are therefore fully known even
# without root privileges.
_prtg_nats_sensors() {
  local entry=""

  [[ -d "$1/sensors" ]] || return 0
  for entry in "$1"/sensors/*/manifest.env; do
    [[ -e "${entry}" ]] || continue
    entry="${entry%/manifest.env}"
    printf '%s\n' "${entry##*/}"
  done
}

_prtg_nats() {
  local current="${COMP_WORDS[COMP_CWORD]}"
  local project_dir=""
  local candidates=""
  local subcommand=""

  project_dir="$(_prtg_nats_project_dir "${COMP_WORDS[0]}")"

  if [[ "${COMP_CWORD}" -eq 1 ]]; then
    candidates="${_prtg_nats_commands}"
  else
    subcommand="${COMP_WORDS[2]:-}"
    case "${COMP_WORDS[1]}" in
      probe)
        case "${COMP_CWORD}" in
          2)
            candidates="${_prtg_nats_probe_commands}"
            ;;
          3)
            case "${subcommand}" in
              list)
                candidates=""
                ;;
              status)
                candidates="--all $(_prtg_nats_probes "${project_dir}")"
                ;;
              enroll)
                # The user may be left out for an enrolled host, so the
                # option can already stand here.
                candidates="--reenroll $(_prtg_nats_users "${project_dir}")"
                ;;
              *)
                candidates="$(_prtg_nats_probes "${project_dir}")"
                ;;
            esac
            ;;
          *)
            case "${subcommand}" in
              enroll)
                candidates="--reenroll"
                ;;
              unenroll)
                candidates="--remove-access --remove-sensors --uninstall-mpp"
                ;;
              configure)
                candidates="--probe-name"
                ;;
              status)
                candidates="--format"
                ;;
            esac
            # The value of --format is a fixed choice.
            [[ "${COMP_WORDS[COMP_CWORD - 1]}" != "--format" ]] ||
              candidates="text json"
            ;;
        esac
        ;;
      sensor)
        case "${COMP_CWORD}" in
          2)
            candidates="${_prtg_nats_sensor_commands}"
            ;;
          3)
            case "${subcommand}" in
              list)
                candidates=""
                ;;
              status)
                candidates="--all $(_prtg_nats_probes "${project_dir}")"
                ;;
              prepare)
                candidates="--all $(_prtg_nats_probes "${project_dir}")"
                ;;
              *)
                candidates="$(_prtg_nats_sensors "${project_dir}")"
                ;;
            esac
            ;;
          4)
            case "${subcommand}" in
              deploy)
                candidates="--all $(_prtg_nats_probes "${project_dir}")"
                ;;
              profile|recover|remove|reserve|release)
                candidates="$(_prtg_nats_probes "${project_dir}")"
                ;;
            esac
            ;;
          *)
            case "${subcommand}" in
              deploy)
                candidates="--dry-run"
                ;;
              profile)
                candidates="--from-file --remove"
                ;;
              recover)
                candidates="--transaction"
                ;;
            esac
            ;;
        esac
        ;;
      iperf-server)
        case "${COMP_CWORD}" in
          2)
            candidates="${_prtg_nats_iperf_server_commands}"
            ;;
          3)
            case "${subcommand}" in
              list)
                candidates=""
                ;;
              install)
                # The SSH target is typed freely; only the options can be
                # suggested, and those come afterwards.
                candidates=""
                ;;
              *)
                candidates="$(_prtg_nats_iperf_server_names "${project_dir}")"
                ;;
            esac
            ;;
          4)
            case "${subcommand}" in
              deploy|revoke)
                candidates="--all $(_prtg_nats_probes "${project_dir}")"
                ;;
              forget)
                candidates="--yes"
                ;;
              install)
                candidates="--name --user --port --rotate --dry-run"
                ;;
            esac
            ;;
          *)
            case "${subcommand}" in
              deploy)
                candidates="--dry-run"
                ;;
              install)
                candidates="--name --user --port --rotate --dry-run"
                ;;
            esac
            ;;
        esac
        ;;
      user)
        case "${COMP_CWORD}" in
          2)
            candidates="${_prtg_nats_user_commands}"
            ;;
          3)
            case "${subcommand}" in
              add|list)
                candidates=""
                ;;
              *)
                candidates="$(_prtg_nats_users "${project_dir}")"
                ;;
            esac
            ;;
          *)
            case "${subcommand}" in
              delete)
                candidates="--yes"
                ;;
              rotate)
                candidates="--server-only"
                ;;
            esac
            ;;
        esac
        ;;
      install-mpp)
        candidates='--nats-user --probe-name --wizard --no-enroll --no-config
          --dry-run --keep-on-failure --rollback-on-failure'
        [[ "${COMP_WORDS[COMP_CWORD - 1]}" != "--nats-user" ]] ||
          candidates="$(_prtg_nats_users "${project_dir}")"
        ;;
      mpp-info)
        [[ "${COMP_CWORD}" -ne 2 ]] ||
          candidates="$(_prtg_nats_users "${project_dir}")"
        ;;
      ssh-key)
        [[ "${COMP_CWORD}" -ne 2 ]] || candidates="info show"
        ;;
      self)
        case "${COMP_CWORD}" in
          2)
            candidates="install uninstall"
            ;;
          *)
            candidates="--link-only --completion-only --relink bash zsh"
            ;;
        esac
        ;;
      completion)
        [[ "${COMP_CWORD}" -ne 2 ]] || candidates="bash zsh"
        ;;
      config)
        [[ "${COMP_CWORD}" -ne 2 ]] || candidates="--edit"
        ;;
      verify)
        [[ "${COMP_CWORD}" -ne 2 ]] || candidates="--offline"
        ;;
    esac
  fi

  # shellcheck disable=SC2207  # compgen deliberately yields word-split suggestions
  COMPREPLY=($(compgen -W "${candidates}" -- "${current}"))
}

complete -F _prtg_nats prtg-nats ./prtg-nats
