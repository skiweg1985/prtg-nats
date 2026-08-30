#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(
    CDPATH=''
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"
# shellcheck source=tools/iperf3/versions.env
source "${SCRIPT_DIR}/versions.env"

for variable in \
    IPERF_VERSION IPERF_SOURCE_URL IPERF_SOURCE_SHA256 \
    OPENSSL_VERSION OPENSSL_SOURCE_URL OPENSSL_SOURCE_SHA256 \
    IPERF_SOURCE_DATE_EPOCH; do
    if [[ -z "${!variable:-}" ]]; then
        echo "Required build input is unset: ${variable}" >&2
        exit 1
    fi
done

if [[ "$(uname -m)" != x86_64 ]]; then
    echo "The artifact builder must run in its pinned linux/amd64 container" >&2
    exit 1
fi
if ! command -v dpkg >/dev/null 2>&1 || \
        [[ "$(dpkg --print-architecture)" != amd64 ]]; then
    echo "The artifact builder requires the Debian amd64 toolchain" >&2
    exit 1
fi

builder_glibc="$(ldd --version 2>&1 | sed -n '1{s/.* //;p;q;}')"
if [[ "${builder_glibc}" != 2.31 ]]; then
    echo "The builder must use the Debian 11 GLIBC_2.31 baseline" >&2
    exit 1
fi

OUTPUT_DIR="${1:-${SCRIPT_DIR}/artifacts}"
if [[ -e "${OUTPUT_DIR}" && ! -d "${OUTPUT_DIR}" ]]; then
    echo "Artifact output exists and is not a directory: ${OUTPUT_DIR}" >&2
    exit 1
fi
if [[ -d "${OUTPUT_DIR}" ]] &&
        [[ -n "$(find "${OUTPUT_DIR}" -mindepth 1 -print -quit)" ]]; then
    echo "Artifact output must be empty: ${OUTPUT_DIR}" >&2
    exit 1
fi

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/iperf3-build.XXXXXXXX")"
cleanup() {
    rm -rf -- "${BUILD_ROOT}"
}
trap cleanup EXIT

DOWNLOAD_DIR="${BUILD_ROOT}/downloads"
STAGING_DIR="${BUILD_ROOT}/artifacts"
mkdir -p "${DOWNLOAD_DIR}" "${STAGING_DIR}"

fetch_verified() {
    local url="$1"
    local sha256="$2"
    local destination="$3"

    curl --fail --silent --show-error --location \
        --proto '=https' --tlsv1.2 --retry 5 --retry-all-errors \
        "${url}" --output "${destination}.part"
    printf '%s  %s\n' "${sha256}" "${destination}.part" | sha256sum -c -
    mv "${destination}.part" "${destination}"
}

IPERF_ARCHIVE="${DOWNLOAD_DIR}/iperf-${IPERF_VERSION}.tar.gz"
OPENSSL_ARCHIVE="${DOWNLOAD_DIR}/openssl-${OPENSSL_VERSION}.tar.gz"
fetch_verified "${IPERF_SOURCE_URL}" "${IPERF_SOURCE_SHA256}" \
    "${IPERF_ARCHIVE}"
fetch_verified "${OPENSSL_SOURCE_URL}" "${OPENSSL_SOURCE_SHA256}" \
    "${OPENSSL_ARCHIVE}"

export SOURCE_DATE_EPOCH="${IPERF_SOURCE_DATE_EPOCH}"
JOBS="${IPERF_BUILD_JOBS:-$(nproc)}"
if [[ ! "${JOBS}" =~ ^[1-9][0-9]*$ ]]; then
    echo "IPERF_BUILD_JOBS must be a positive integer" >&2
    exit 1
fi

COMMON_CFLAGS=(
    -O2
    -fPIC
    -fstack-protector-strong
    -D_FORTIFY_SOURCE=2
    -ffunction-sections
    -fdata-sections
    "-ffile-prefix-map=${BUILD_ROOT}=/usr/src/iperf3-build"
)
COMMON_LDFLAGS=(
    -pie
    "-Wl,--as-needed"
    "-Wl,--gc-sections"
    "-Wl,-z,relro"
    "-Wl,-z,now"
)

IPERF_LICENSE_SOURCE=""
OPENSSL_LICENSE_SOURCE=""

build_target() {
    local platform="$1"
    local host_triplet="$2"
    local cross_prefix="$3"
    local openssl_target="$4"
    local cc="${cross_prefix}gcc"
    local ar="${cross_prefix}ar"
    local ranlib="${cross_prefix}ranlib"
    local strip="${cross_prefix}strip"
    local target_root="${BUILD_ROOT}/${platform}"
    local openssl_source="${target_root}/openssl-source"
    local openssl_dest="${target_root}/openssl-dest"
    local openssl_prefix="${openssl_dest}/usr/local/openssl"
    local iperf_source="${target_root}/iperf-source"
    local iperf_build="${target_root}/iperf-build"
    local libatomic

    for tool in "${cc}" "${ar}" "${ranlib}" "${strip}"; do
        if ! command -v "${tool}" >/dev/null 2>&1; then
            echo "Required cross-build tool is missing: ${tool}" >&2
            exit 1
        fi
    done

    mkdir -p "${openssl_source}" "${openssl_dest}" \
        "${iperf_source}" "${iperf_build}"
    tar -xzf "${OPENSSL_ARCHIVE}" -C "${openssl_source}" \
        --strip-components=1
    tar -xzf "${IPERF_ARCHIVE}" -C "${iperf_source}" \
        --strip-components=1

    (
        cd "${openssl_source}"
        env CROSS_COMPILE="${cross_prefix}" \
            perl ./Configure "${openssl_target}" \
                no-shared no-module no-tests no-apps no-docs \
                --prefix=/usr/local/openssl \
                --openssldir=/etc/ssl \
                --libdir=lib \
                "${COMMON_CFLAGS[@]}"
        make -s -j "${JOBS}" build_libs
        make -s install_dev DESTDIR="${openssl_dest}"
    )

    if find "${openssl_prefix}/lib" -maxdepth 1 -name '*.so*' -print -quit |
            grep -q .; then
        echo "${platform}: OpenSSL unexpectedly produced shared libraries" >&2
        exit 1
    fi

    libatomic="$(${cc} -print-file-name=libatomic.a)"
    if [[ "${libatomic}" != /* || ! -f "${libatomic}" ]]; then
        echo "${platform}: static libatomic archive was not found" >&2
        exit 1
    fi

    (
        cd "${iperf_build}"
        CC="${cc}" \
        AR="${ar}" \
        RANLIB="${ranlib}" \
        STRIP="${strip}" \
        CFLAGS="${COMMON_CFLAGS[*]}" \
        CPPFLAGS="-I${openssl_prefix}/include" \
        LDFLAGS="-L${openssl_prefix}/lib ${COMMON_LDFLAGS[*]}" \
        LIBS="${libatomic} -Wl,-Bdynamic -ldl -pthread" \
            "${iperf_source}/configure" \
                --build="$("${iperf_source}/config/config.guess")" \
                --host="${host_triplet}" \
                --disable-shared \
                --enable-static \
                --disable-profiling \
                --without-sctp \
                --with-openssl="${openssl_prefix}"
        if ! grep -q '^#define HAVE_SSL 1$' src/iperf_config.h; then
            echo "${platform}: iPerf3 authentication support is disabled" >&2
            exit 1
        fi
        make -s -C src -j "${JOBS}" iperf3
    )

    mkdir -p "${STAGING_DIR}/${platform}"
    "${strip}" --strip-unneeded "${iperf_build}/src/iperf3"
    install -m 0755 "${iperf_build}/src/iperf3" \
        "${STAGING_DIR}/${platform}/iperf3"

    if grep -aFq "${BUILD_ROOT}" "${STAGING_DIR}/${platform}/iperf3"; then
        echo "${platform}: the binary contains its temporary build path" >&2
        exit 1
    fi

    if [[ -z "${IPERF_LICENSE_SOURCE}" ]]; then
        IPERF_LICENSE_SOURCE="${iperf_source}/LICENSE"
        OPENSSL_LICENSE_SOURCE="${openssl_source}/LICENSE.txt"
    fi
}

build_target \
    linux-amd64-glibc x86_64-linux-gnu x86_64-linux-gnu- linux-x86_64
build_target \
    linux-arm64-glibc aarch64-linux-gnu aarch64-linux-gnu- linux-aarch64
build_target \
    linux-armhf-glibc arm-linux-gnueabihf arm-linux-gnueabihf- linux-armv4

mkdir -p \
    "${STAGING_DIR}/licenses/iperf3" \
    "${STAGING_DIR}/licenses/openssl" \
    "${STAGING_DIR}/licenses/gcc-runtime"
install -m 0644 "${IPERF_LICENSE_SOURCE}" \
    "${STAGING_DIR}/licenses/iperf3/LICENSE"
install -m 0644 "${OPENSSL_LICENSE_SOURCE}" \
    "${STAGING_DIR}/licenses/openssl/LICENSE.txt"
install -m 0644 /usr/share/doc/gcc-10-base/copyright \
    "${STAGING_DIR}/licenses/gcc-runtime/COPYRIGHT"
install -m 0644 "${SCRIPT_DIR}/THIRD_PARTY_NOTICES.md" \
    "${STAGING_DIR}/THIRD_PARTY_NOTICES.md"

python3 - "${STAGING_DIR}" "${IPERF_VERSION}" <<'PY'
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
artifacts = {}
for platform in platforms:
    relative_path = f"{platform}/iperf3"
    payload = (root / relative_path).read_bytes()
    artifacts[platform] = {
        "path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }

document = {
    "format": 1,
    "tools": {
        "iperf3": {
            "version": version,
            "artifacts": artifacts,
        }
    },
}
(root / "manifest.json").write_text(
    json.dumps(document, indent=2) + "\n",
    encoding="utf-8",
)
PY

bash "${SCRIPT_DIR}/check-artifacts.sh" "${STAGING_DIR}"
mkdir -p "${OUTPUT_DIR}"
cp -a "${STAGING_DIR}/." "${OUTPUT_DIR}/"
echo "Managed iPerf3 artifacts were written to ${OUTPUT_DIR}."
