#!/usr/bin/env bash
#
# Builds the Tessera RPM. Needs only rpm-build and desktop-file-utils.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VERSION="$(sed -n 's/^Version:[[:space:]]*//p' packaging/tessera.spec)"
TOPDIR="${HOME}/rpmbuild"

command -v rpmbuild >/dev/null || {
    echo "rpmbuild is missing. Install it with: sudo dnf install rpm-build desktop-file-utils" >&2
    exit 1
}

mkdir -p "${TOPDIR}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

stage="$(mktemp -d)/tessera-${VERSION}"
mkdir -p "${stage}"
cp -a tessera packaging docs "${stage}/"
cp README.md LICENSE pyproject.toml "${stage}/"

# The LDAC decoder ships as source: it has to be compiled against whichever
# PipeWire the machine is running, so there is nothing to prebuild here.
mkdir -p "${stage}/native/ldac-decoder" "${stage}/scripts"
cp native/ldac-decoder/ldacBT_dec.c native/ldac-decoder/README.md "${stage}/native/ldac-decoder/"
cp scripts/build-ldac-decoder.sh "${stage}/scripts/"
if [[ -f third_party/libldacdec/libldacdec.c ]]; then
    mkdir -p "${stage}/third_party/libldacdec"
    cp third_party/libldacdec/*.c third_party/libldacdec/*.h \
       third_party/libldacdec/LICENSE "${stage}/third_party/libldacdec/"
else
    echo "note: third_party/libldacdec is empty, so the RPM will not carry the" >&2
    echo "      LDAC decoder. git submodule update --init to include it." >&2
fi
# Never ship build artefacts from the working tree.
find "${stage}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

tar -czf "${TOPDIR}/SOURCES/tessera-${VERSION}.tar.gz" \
    -C "$(dirname "${stage}")" "tessera-${VERSION}"
cp packaging/tessera.spec "${TOPDIR}/SPECS/"

rpmbuild -ba "${TOPDIR}/SPECS/tessera.spec"

printf '\nBuilt:\n'
find "${TOPDIR}/RPMS" "${TOPDIR}/SRPMS" -name "tessera-${VERSION}*" -newermt '-5 minutes' -printf '  %p\n'
printf '\nInstall with:\n  sudo dnf install %s/RPMS/noarch/tessera-%s-*.noarch.rpm\n' "${TOPDIR}" "${VERSION}"
