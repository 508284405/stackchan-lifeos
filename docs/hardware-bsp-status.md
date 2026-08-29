# StackChan hardware BSP status

Status: CoreS3/StackChan software identity confirmed; real LifeOS HAL integration remains blocked.

## Evidence checked

- The repository contains an ESP-IDF shell at `firmware/idf`, but the current
  host has no `IDF_PATH`, `idf.py`, or `cmake` command. The shell README names
  ESP-IDF 5.5.4 as its required baseline and explicitly says the target HAL,
  feedback calibration, and local self-test are not implemented.
- Reusable local code exists at
  `/Users/wangyu/Documents/Codex/2026-08-27/nin/work/stackchan-usb-control/arduino-user/libraries/M5StackChan`.
  It is an Arduino library (includes `Arduino.h`, `M5Unified.hpp`, and
  `M5GFX.h`), not an ESP-IDF component. The installed archive is
  `M5StackChan-1.0.1.zip`; the local M5Unified archives are 0.2.20 and 0.2.21.
- The local StackChan API exposes useful target primitives: `Motion` has
  `moveYaw`, `movePitch`, `getCurrentYawAngle`, `getCurrentPitchAngle`, and
  `getTorqueEnabled`; `M5StackChan_Class` exposes `Display()`,
  `TouchSensor`, `setServoPowerEnabled`, and battery readings. It does not
  provide a CMake/ESP-IDF component boundary or a verified mapping to the
  LifeOS HAL's capability and fault contracts.
- The local Arduino sketch was previously built for `m5stack.esp32.m5stack_cores3`
  and therefore proves only an Arduino build path, not an ESP-IDF build or a
  LifeOS adapter.
- The connected `/dev/cu.usbmodem101` was queried with esptool: ESP32-S3 rev 0.2,
  MAC `1c:db:d4:ba:43:40`, 16 MiB flash. The existing firmware answered
  `OK READY StackChan USB controller v1` to a read-only status probe.
- The LifeOS ESP-IDF HIL shell builds with ESP-IDF v5.5.4 for `esp32s3`; its
  image keeps motion disabled and has not been flashed.

Official references:

- [M5Stack StackChan product documentation](https://docs.m5stack.com/en/StackChan/)
  identifies CoreS3, the camera/touch/IMU/proximity hardware, and the two
  feedback servos; it recommends keeping the vertical servo within 5–85°.
- [Official StackChan source repository](https://github.com/m5stack/StackChan)
  links the separate StackChan BSP repository and documents that the product
  uses CoreS3.
- [Official StackChan-BSP repository](https://github.com/m5stack/StackChan-BSP)
  describes itself as a BSP for **Arduino** development and lists Arduino
  library metadata (`library.json`/`library.properties`), so it cannot be
  included as an ESP-IDF component without an explicit port and dependency
  pinning.

## Why no adapter was added

Adding `firmware/idf/components/stackchan_hal/**` now would require guessing
component names, include paths, bus pins, servo calibration, camera/IMU driver
availability, and fault semantics. That would violate the requirement not to
pretend that the real M5 BSP is connected. The real device was identified and
its flash was backed up, but no LifeOS image was flashed because the write
operation was rejected by the current account approval/usage limit. No
target-specific actuator adapter was guessed or enabled.

## Executable next steps

Run these on a machine with the ESP-IDF toolchain and the intended hardware:

```sh
source "$IDF_PATH/export.sh"
idf.py --version
idf.py -C firmware/idf set-target esp32s3
idf.py -C firmware/idf reconfigure
idf.py -C firmware/idf build
```

Before implementing the target component, obtain and pin the official
StackChan BSP plus its dependency versions, then record the exact commit and
the CoreS3 board/USB identity. Implement adapters behind compile guards and
add a target compile-only job before any flashing. A local self-test must
prove startup torque-off, feedback reads, hard/soft limits, emergency stop,
and fault clearing before enabling servo power.
