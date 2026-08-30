# Third-party notices

The managed binaries are built from the source releases pinned in
`versions.env`. Their complete license texts are shipped beside each artifact.

## iPerf3

iPerf3 is distributed under a three-clause BSD license. The binary contains
the statically linked `libiperf` from the same iPerf3 release. See
`licenses/iperf3/LICENSE` in the artifact set.

## OpenSSL

The binaries are linked against static OpenSSL `libssl` and `libcrypto`
archives and have no dynamic dependency on them. OpenSSL 3 is distributed
under the Apache License 2.0. See `licenses/openssl/LICENSE.txt` in the
artifact set.

## GCC runtime components

The toolchain may include small parts of its runtime libraries in an output
binary. They are covered by the GNU General Public License with the GCC Runtime
Library Exception. The Debian toolchain's complete copyright and license
notice is included as `licenses/gcc-runtime/COPYRIGHT`.

## GNU C Library

glibc is not bundled. Each binary uses the target system's dynamic loader and
glibc, and is built against the Debian 11 baseline (glibc 2.31).
