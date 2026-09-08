#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$ROOT_DIR/firmware/idf"
PROFILE="${1:-production}"
shift || true

case "$PROFILE" in
  production)
    BUILD_DIR="$PROJECT_DIR/build"
    DEFAULTS="$PROJECT_DIR/sdkconfig.defaults"
    ;;
  hil)
    BUILD_DIR="$PROJECT_DIR/build-hil-16m"
    DEFAULTS="$PROJECT_DIR/sdkconfig.defaults;$PROJECT_DIR/sdkconfig.hil.defaults"
    ;;
  *)
    echo "usage: $0 [production|hil] [idf.py actions...]" >&2
    exit 2
    ;;
esac

: "${IDF_PATH:?Set IDF_PATH to the pinned ESP-IDF checkout}"
if [[ ! -f "$IDF_PATH/export.sh" ]]; then
  echo "IDF_PATH does not contain export.sh: $IDF_PATH" >&2
  exit 2
fi

source "$IDF_PATH/export.sh"
export SDKCONFIG="$BUILD_DIR/sdkconfig"
export SDKCONFIG_DEFAULTS="$DEFAULTS"

if [[ "$#" -eq 0 ]]; then
  set -- build
fi

idf.py -B "$BUILD_DIR" -C "$PROJECT_DIR" \
  -D "SDKCONFIG=$SDKCONFIG" \
  -D "SDKCONFIG_DEFAULTS=$SDKCONFIG_DEFAULTS" \
  "$@"
