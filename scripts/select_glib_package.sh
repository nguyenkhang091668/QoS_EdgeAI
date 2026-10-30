#!/bin/bash
set -e

OS_RELEASE_FILE="${1:-/etc/os-release}"

if [ ! -r "$OS_RELEASE_FILE" ]; then
    echo "Không đọc được thông tin hệ điều hành: $OS_RELEASE_FILE" >&2
    exit 1
fi

# shellcheck disable=SC1090
. "$OS_RELEASE_FILE"

if [ "${ID:-}" != "debian" ]; then
    echo "Chỉ hỗ trợ Raspberry Pi OS/Debian, nhận được ID=${ID:-unknown}" >&2
    exit 1
fi

case "${VERSION_ID:-}" in
    12)
        echo "libglib2.0-0"
        ;;
    13)
        echo "libglib2.0-0t64"
        ;;
    *)
        echo "Chưa hỗ trợ Debian VERSION_ID=${VERSION_ID:-unknown}" >&2
        exit 1
        ;;
esac
