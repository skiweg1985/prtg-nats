#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(
    CDPATH=''
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"
# shellcheck source=tools/iperf3/versions.env
source "${SCRIPT_DIR}/versions.env"

ARTIFACT_DIR="${1:?usage: check-artifacts.sh ARTIFACT_DIR}"
READELF_BIN="${READELF:-readelf}"

if [[ ! -d "${ARTIFACT_DIR}" ]]; then
    echo "Artifact directory does not exist: ${ARTIFACT_DIR}" >&2
    exit 1
fi
if ! command -v "${READELF_BIN}" >/dev/null 2>&1; then
    echo "readelf is required to validate the managed binaries" >&2
    exit 1
fi

python3 - "${ARTIFACT_DIR}" "${IPERF_VERSION}" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
platforms = (
    "linux-amd64-glibc",
    "linux-arm64-glibc",
    "linux-armhf-glibc",
)

try:
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid artifact manifest: {exc}") from exc

if set(document) != {"format", "tools"} or document["format"] != 1:
    raise SystemExit("manifest must contain only format=1 and tools")
if set(document["tools"]) != {"iperf3"}:
    raise SystemExit("manifest must describe exactly the iperf3 tool")

tool = document["tools"]["iperf3"]
if set(tool) != {"version", "artifacts"} or tool["version"] != version:
    raise SystemExit(f"manifest must describe iperf3 {version}")
if set(tool["artifacts"]) != set(platforms):
    raise SystemExit("manifest does not contain the three required platforms")

for platform in platforms:
    record = tool["artifacts"][platform]
    expected_path = f"{platform}/iperf3"
    if set(record) != {"path", "sha256", "size"}:
        raise SystemExit(f"unexpected manifest fields for {platform}")
    if record["path"] != expected_path:
        raise SystemExit(f"unexpected artifact path for {platform}")
    if not isinstance(record["size"], int) or record["size"] <= 0:
        raise SystemExit(f"invalid artifact size for {platform}")
    if (
        not isinstance(record["sha256"], str)
        or len(record["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in record["sha256"])
    ):
        raise SystemExit(f"invalid artifact checksum for {platform}")

    binary = root / record["path"]
    if not binary.is_file():
        raise SystemExit(f"artifact is missing: {binary}")
    payload = binary.read_bytes()
    if len(payload) != record["size"]:
        raise SystemExit(f"artifact size mismatch for {platform}")
    if hashlib.sha256(payload).hexdigest() != record["sha256"]:
        raise SystemExit(f"artifact checksum mismatch for {platform}")

required_files = (
    "THIRD_PARTY_NOTICES.md",
    "licenses/iperf3/LICENSE",
    "licenses/openssl/LICENSE.txt",
    "licenses/gcc-runtime/COPYRIGHT",
)
for relative_path in required_files:
    if not (root / relative_path).is_file():
        raise SystemExit(f"required notice is missing: {relative_path}")
PY

platforms=(
    linux-amd64-glibc
    linux-arm64-glibc
    linux-armhf-glibc
)

for platform in "${platforms[@]}"; do
    binary="${ARTIFACT_DIR}/${platform}/iperf3"
    case "${platform}" in
        linux-amd64-glibc)
            expected_machine="Advanced Micro Devices X86-64"
            expected_interpreter="/lib64/ld-linux-x86-64.so.2"
            ;;
        linux-arm64-glibc)
            expected_machine="AArch64"
            expected_interpreter="/lib/ld-linux-aarch64.so.1"
            ;;
        linux-armhf-glibc)
            expected_machine="ARM"
            expected_interpreter="/lib/ld-linux-armhf.so.3"
            ;;
    esac

    machine="$(${READELF_BIN} -h "${binary}" |
        awk -F: '/^[[:space:]]*Machine:/ {
            sub(/^[[:space:]]+/, "", $2); print $2
        }')"
    if [[ "${machine}" != "${expected_machine}" ]]; then
        echo "${platform}: expected ${expected_machine}, got ${machine}" >&2
        exit 1
    fi

    if [[ "${platform}" == "linux-armhf-glibc" ]]; then
        arm_attributes="$(${READELF_BIN} -A "${binary}")"
        if ! grep -Eq 'Tag_CPU_arch:[[:space:]]+v7$' <<<"${arm_attributes}"; then
            echo "${platform}: the managed armhf baseline must be ARMv7" >&2
            exit 1
        fi
        if ! grep -Fq 'Tag_ABI_VFP_args: VFP registers' \
                <<<"${arm_attributes}"; then
            echo "${platform}: the managed armhf binary is not hard-float" >&2
            exit 1
        fi
    fi

    interpreter="$(${READELF_BIN} -l "${binary}" |
        sed -n 's/.*Requesting program interpreter: \(.*\)]/\1/p')"
    if [[ "${interpreter}" != "${expected_interpreter}" ]]; then
        echo "${platform}: unexpected interpreter ${interpreter}" >&2
        exit 1
    fi

    needed="$(${READELF_BIN} -d "${binary}" |
        sed -n 's/.*Shared library: \[\([^]]*\)\].*/\1/p')"
    if ! grep -Fxq 'libc.so.6' <<<"${needed}"; then
        echo "${platform}: the binary is not dynamically linked to glibc" >&2
        exit 1
    fi
    for forbidden_library in libiperf.so libcrypto.so libssl.so libatomic.so; do
        if grep -Fq "${forbidden_library}" <<<"${needed}"; then
            echo "${platform}: ${forbidden_library} must not be dynamic" >&2
            exit 1
        fi
    done

    glibc_versions="$(${READELF_BIN} --version-info "${binary}" |
        grep -oE 'GLIBC_[0-9]+(\.[0-9]+)*' | sort -Vu || true)"
    highest_glibc="$(tail -n 1 <<<"${glibc_versions}")"
    python3 - "${platform}" "${highest_glibc}" <<'PY'
import sys

platform, symbol = sys.argv[1:]
if not symbol:
    raise SystemExit(f"{platform}: no glibc symbol versions found")
version = tuple(int(part) for part in symbol.removeprefix("GLIBC_").split("."))
if version > (2, 31):
    raise SystemExit(f"{platform}: requires {symbol}, newer than GLIBC_2.31")
PY
done

echo "Managed iPerf3 artifacts are valid."
