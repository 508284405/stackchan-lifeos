# ESP-IDF target shell

This is the ESP32-S3 target firmware for StackChan/CoreS3. It compiles the same
fixed-capacity runtime, behavior and protocol sources used by host tests and
adds a small ESP-IDF HAL for the real board. Production builds keep the
maintainer-only HIL commands disabled; the HIL profile enables them only for a
physically supervised acceptance run.

Required toolchain: ESP-IDF 5.5.4 with its supported CMake and Ninja versions.

```bash
IDF_PATH=/path/to/esp-idf-5.5.4 \
  tools/build_target.sh production build size-components

# Only for a supervised hardware acceptance run.
IDF_PATH=/path/to/esp-idf-5.5.4 \
  tools/build_target.sh hil build
```

`tools/build_target.sh` isolates each profile's generated `sdkconfig` inside its
ignored build directory and passes the pinned `sdkconfig.defaults` explicitly;
this prevents a previous HIL configuration from silently becoming a production
image. The build also pins `espressif/esp32-camera` 2.1.5 in
`firmware/idf/dependencies.lock`.

The target has been compiled with ESP-IDF v5.5.4 and the ESP32-S3 toolchain.
The real-HAL HIL image has been flashed and boots stably on the target. Its
board bring-up confirms PSRAM, camera, touch, IMU, proximity, display, AW9523
and PY32, but both SCS servos currently return no ping/feedback, so the image
holds `motion=disabled`. Do not claim actuator acceptance until the physical
servo path is restored and `tools/phase1_hil.py --allow-hardware` completes.

Target-specific notes baked into `main/app_main.cpp`,
`components/stackchan_hal/`, and `sdkconfig.defaults`:

- stdin must not be read through stdio: the startup console VFS reads
  USB-Serial/JTAG non-blocking (immediate EOF) and the newlib stdin lock trips
  an assertion once real bytes arrive. The gateway loop reads the
  `usb_serial_jtag` driver directly with its own bounded line buffer.
- The main task stack is 64 KiB: the gateway ingest path holds bounded 8 KiB
  payload buffers across parse/ack temporaries and was measured peaking at
  ~44 KiB on hardware. Smaller stacks panic with a stack overflow as soon as
  the first request arrives.
- `LIFEOS_HIL_TEST_MODE` is absent from the production defaults. Its only
  enabled commands are maintainer-authorized motion and feedback-freeze probes,
  and its configuration is isolated in `sdkconfig.hil.defaults`.
- The board adapter keeps the 20 Hz safety task independent from the 10 Hz
  sensor/display graph. Servo power is gated by PY32 `VM_EN`, startup torque is
  off, and every failure path cuts `VM_EN` before reporting the fault.
