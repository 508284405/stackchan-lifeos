#!/usr/bin/env sh
set -eu

compiler="${CXX:-c++}"
flags="-std=c++17 -Wall -Wextra -Wpedantic -Werror -Ifirmware/include"
tmp_root="${TMPDIR:-/tmp}/stackchan-lifeos-phase1"
mkdir -p "$tmp_root"

build_and_run() {
  name="$1"
  shift
  # Intentional word splitting for the fixed compiler flags above.
  # shellcheck disable=SC2086
  "$compiler" $flags "$@" -o "$tmp_root/$name"
  "$tmp_root/$name"
}

build_and_run legacy \
  firmware/src/firmware.cpp firmware/tests/test_firmware.cpp
build_and_run runtime \
  firmware/src/runtime/runtime.cpp firmware/tests/runtime/test_runtime.cpp
build_and_run behavior \
  firmware/src/behavior/behavior.cpp firmware/tests/behavior/test_behavior.cpp
build_and_run hal \
  firmware/src/hal/fake.cpp firmware/tests/hal/test_hal.cpp
build_and_run protocol \
  firmware/src/protocol/protocol.cpp firmware/tests/protocol/test_protocol.cpp
build_and_run phase1_controller \
  firmware/src/runtime/runtime.cpp firmware/src/behavior/behavior.cpp \
  firmware/src/hal/fake.cpp firmware/src/protocol/protocol.cpp \
  firmware/src/phase1/controller.cpp \
  firmware/tests/phase1/test_controller.cpp
