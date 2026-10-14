#!/usr/bin/env bash
# Builds trigger_agent.zip for a python3.12 Lambda. temporalio ships a
# compiled core, so pin the target platform - building on a Mac would
# otherwise bundle a macOS wheel that won't load on Lambda.
#
#   ./build.sh            # x86_64 (default)
#   ARCH=arm64 ./build.sh # for an arm64 function

set -euo pipefail

cd "$(dirname "$0")"
case "${ARCH:-x86_64}" in
  x86_64) PLATFORM=manylinux2014_x86_64 ;;
  arm64) PLATFORM=manylinux2014_aarch64 ;;
  *) echo "ARCH must be x86_64 or arm64" >&2; exit 1 ;;
esac

rm -rf build trigger_agent.zip
pip install -r requirements.txt -t build \
  --platform "$PLATFORM" --python-version 3.12 --implementation cp --only-binary=:all:
cp handler.py build/
(cd build && zip -qr ../trigger_agent.zip .)
rm -rf build
echo "Built $(pwd)/trigger_agent.zip"
