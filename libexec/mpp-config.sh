#!/usr/bin/env bash

# Shared generation of the MPP runtime configuration.
#
# This library deliberately has no dependency on common.sh, because it is used
# in two roles:
#
#   * on the main NATS server, to render the configuration centrally and then
#     roll it out over the restricted management channel;
#   * on the probe itself, when install-mpp.sh runs directly there.
#
# It is only ever sourced and sets no state while being sourced.

MPP_CONFIG_PLACEHOLDERS=(
  PROBE_ID
  ACCESS_KEY
  PROBE_NAME
  NATS_HOST
  NATS_PORT
  NATS_USER
  NATS_PASSWORD
  SERVER_CA
  CLIENT_NAME
)

mpp_config_template_path() {
  local library_dir=""
  local candidate=""

  library_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  for candidate in \
    "${MPP_CONFIG_TEMPLATE:-}" \
    "${library_dir}/../config/mpprobe-config.yaml.template" \
    "${library_dir}/mpprobe-config.yaml.template"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  printf 'MPP configuration template not found.\n' >&2
  return 1
}

mpp_generate_uuid() {
  local uuid=""

  if [[ -r /proc/sys/kernel/random/uuid ]]; then
    uuid="$(</proc/sys/kernel/random/uuid)"
  elif command -v uuidgen >/dev/null 2>&1; then
    uuid="$(uuidgen | tr '[:upper:]' '[:lower:]')"
  elif command -v openssl >/dev/null 2>&1; then
    uuid="$(
      openssl rand -hex 16 |
        sed -E 's/^(.{8})(.{4})(.{3})(.{1})(.{3})(.{12})$/\1-\2-4\3-a\5-\6/'
    )"
  else
    printf 'No source for a random UUID is available.\n' >&2
    return 1
  fi
  mpp_validate_probe_id "${uuid}" || {
    printf 'Generated an unusable UUID.\n' >&2
    return 1
  }
  printf '%s\n' "${uuid}"
}

# A short host label that is safe for YAML and for PRTG. For a hostname the
# short form before the first dot is what is wanted. An IP address must not be
# treated that way: 172.23.106.18 would become "172" and from that the name
# "multi-platform-probe@172", which distinguishes nothing in PRTG. Addresses
# therefore keep every octet.
mpp_host_label() {
  local host="$1"

  if [[ "${host}" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    host="${host//./-}"
  else
    host="${host%%.*}"
  fi
  host="$(printf '%s' "${host}" | tr '[:upper:]' '[:lower:]')"
  host="${host//[^a-z0-9-]/-}"
  host="${host#-}"
  host="${host%-}"
  printf '%s\n' "${host:-probe}"
}

mpp_default_probe_name() {
  printf 'multi-platform-probe@%s\n' "$(mpp_host_label "$1")"
}

# The readable part of an access key. In PRTG every access key sits below the
# next in one field; without a speaking part there is no way to tell which line
# belongs to which probe. From a probe name such as
# "multi-platform-probe@site-north" the distinguishing part is what remains.
mpp_access_key_label() {
  local label=""

  label="$(mpp_host_label "${1##*@}")"
  printf '%s\n' "${label:0:32}"
}

# The speaking part deliberately comes first: when skimming the key list in
# PRTG the start of the line is visible, the end of it often is not. The random
# part is kept in full - it carries the security, the readable part only the
# association.
mpp_default_access_key() {
  local uuid=""

  uuid="$(mpp_generate_uuid)" || return 1
  printf '%s-%s\n' "$(mpp_access_key_label "$1")" "${uuid}"
}

mpp_validate_probe_id() {
  [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
}

mpp_validate_access_key() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$ ]]
}

# The leading character is restricted so the value can stay an unquoted YAML
# plain scalar.
mpp_validate_probe_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$ ]]
}

mpp_validate_nats_host() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]]
}

mpp_validate_nats_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] && (($1 >= 1 && $1 <= 65535))
}

mpp_validate_nats_user() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

mpp_validate_nats_password() {
  [[ "$1" =~ ^[A-Za-z0-9._~+=/-]{8,256}$ ]]
}

mpp_validate_server_ca() {
  [[ "$1" =~ ^/[A-Za-z0-9._/-]{1,255}$ ]]
}

mpp_validate_client_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]
}

# Checks the nine core values from the caller's MPP_* variables.
mpp_validate_values() {
  mpp_validate_probe_id "${MPP_PROBE_ID:-}" ||
    {
      printf 'Invalid probe id (expected a UUID).\n' >&2
      return 1
    }
  mpp_validate_access_key "${MPP_ACCESS_KEY:-}" ||
    {
      printf 'Invalid probe access key.\n' >&2
      return 1
    }
  mpp_validate_probe_name "${MPP_PROBE_NAME:-}" ||
    {
      printf 'Invalid probe name.\n' >&2
      return 1
    }
  mpp_validate_nats_host "${MPP_NATS_HOST:-}" ||
    {
      printf 'Invalid NATS host.\n' >&2
      return 1
    }
  mpp_validate_nats_port "${MPP_NATS_PORT:-}" ||
    {
      printf 'Invalid NATS port.\n' >&2
      return 1
    }
  mpp_validate_nats_user "${MPP_NATS_USER:-}" ||
    {
      printf 'Invalid NATS user.\n' >&2
      return 1
    }
  mpp_validate_nats_password "${MPP_NATS_PASSWORD:-}" ||
    {
      printf 'Invalid NATS password for an unquoted YAML value.\n' >&2
      return 1
    }
  mpp_validate_server_ca "${MPP_SERVER_CA:-}" ||
    {
      printf 'Invalid CA path.\n' >&2
      return 1
    }
  mpp_validate_client_name "${MPP_CLIENT_NAME:-}" ||
    {
      printf 'Invalid NATS client name.\n' >&2
      return 1
    }
}

# Renders the template to stdout. The values are passed through the
# environment so the password never appears in a process list.
mpp_render_config() {
  local template=""

  mpp_validate_values || return 1
  template="$(mpp_config_template_path)" || return 1

  env \
    MPP_CFG_PROBE_ID="${MPP_PROBE_ID}" \
    MPP_CFG_ACCESS_KEY="${MPP_ACCESS_KEY}" \
    MPP_CFG_PROBE_NAME="${MPP_PROBE_NAME}" \
    MPP_CFG_NATS_HOST="${MPP_NATS_HOST}" \
    MPP_CFG_NATS_PORT="${MPP_NATS_PORT}" \
    MPP_CFG_NATS_USER="${MPP_NATS_USER}" \
    MPP_CFG_NATS_PASSWORD="${MPP_NATS_PASSWORD}" \
    MPP_CFG_SERVER_CA="${MPP_SERVER_CA}" \
    MPP_CFG_CLIENT_NAME="${MPP_CLIENT_NAME}" \
    awk -v placeholders="${MPP_CONFIG_PLACEHOLDERS[*]}" '
      function replace_all(text, needle, value,   result, position) {
        result = ""
        while ((position = index(text, needle)) > 0) {
          result = result substr(text, 1, position - 1) value
          text = substr(text, position + length(needle))
        }
        return result text
      }
      BEGIN {
        split(placeholders, names, " ")
        for (position in names) {
          known[names[position]] = 1
        }
      }
      {
        line = $0
        for (name in known) {
          line = replace_all(line, "@@" name "@@", ENVIRON["MPP_CFG_" name])
        }
        if (index(line, "@@") > 0) {
          exit 42
        }
        print line
      }
    ' "${template}" ||
    {
      printf 'The configuration template contains unknown placeholders.\n' >&2
      return 1
    }
}

# Reads a top-level scalar (id, access_key, name) from an MPP configuration.
mpp_read_config_field() {
  local config_path="$1"
  local field="$2"

  [[ -f "${config_path}" ]] || return 1
  awk -v field="${field}" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }
    $0 ~ "^" field "[[:space:]]*:" {
      value = $0
      sub("^" field "[[:space:]]*:[[:space:]]*", "", value)
      value = trim(value)
      if ((substr(value, 1, 1) == "\"" &&
           substr(value, length(value), 1) == "\"") ||
          (substr(value, 1, 1) == "\047" &&
           substr(value, length(value), 1) == "\047")) {
        value = substr(value, 2, length(value) - 2)
      }
      if (value == "" || length(value) > 4096 || value ~ /[[:cntrl:]]/) {
        exit 42
      }
      found = value
      matches++
    }
    END {
      if (matches != 1) {
        exit 42
      }
      print found
    }
  ' "${config_path}"
}

# Reads nats.authentication.user without printing the adjacent password.
mpp_read_config_nats_user() {
  local config_path="$1"

  [[ -f "${config_path}" ]] || return 1
  awk '
    function indent_count(value, copy) {
      copy = value
      sub(/[^[:space:]].*$/, "", copy)
      return length(copy)
    }
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }
    /^[[:space:]]*authentication:[[:space:]]*$/ {
      in_auth = 1
      auth_indent = indent_count($0)
      next
    }
    in_auth && $0 !~ /^[[:space:]]*$/ && indent_count($0) <= auth_indent {
      in_auth = 0
    }
    in_auth && /^[[:space:]]*user:[[:space:]]*/ {
      value = $0
      sub(/^[[:space:]]*user:[[:space:]]*/, "", value)
      value = trim(value)
      gsub(/^["\047]|["\047]$/, "", value)
      found = value
      matches++
    }
    END {
      if (matches != 1 || found == "") {
        exit 42
      }
      print found
    }
  ' "${config_path}"
}

# Allows tab, newline, printable ASCII and UTF-8 continuation bytes, and
# rejects every other control character.
mpp_is_free_of_control_characters() {
  local remaining=""

  remaining="$(
    LC_ALL=C tr -d '\11\12\15\40-\176\200-\377' < "$1" | wc -c | tr -d '[:space:]'
  )"
  [[ "${remaining}" == "0" ]]
}

# A coarse structural check of a fully rendered configuration. It is no
# replacement for a YAML parser, but it does stop incomplete or tampered
# content from being handed to prtg.mpprobe.service.
mpp_validate_rendered_config() {
  local config_path="$1"
  local required_pattern=""

  [[ -s "${config_path}" ]] ||
    {
      printf 'Rendered MPP configuration is empty.\n' >&2
      return 1
    }
  [[ "$(wc -c < "${config_path}")" -le 65536 ]] ||
    {
      printf 'Rendered MPP configuration is unexpectedly large.\n' >&2
      return 1
    }
  if ! mpp_is_free_of_control_characters "${config_path}"; then
    printf 'Rendered MPP configuration contains control characters.\n' >&2
    return 1
  fi
  for required_pattern in \
    '^id:[[:space:]]' \
    '^access_key:[[:space:]]' \
    '^name:[[:space:]]' \
    '^[[:space:]]+url:[[:space:]]+tls://' \
    '^[[:space:]]+user:[[:space:]]' \
    '^[[:space:]]+password:[[:space:]]' \
    '^[[:space:]]+server_ca:[[:space:]]+/'; do
    [[ "$(grep -c -E "${required_pattern}" "${config_path}")" == "1" ]] ||
      {
        printf 'Rendered MPP configuration lacks exactly one %s entry.\n' \
          "${required_pattern}" >&2
        return 1
      }
  done
}
