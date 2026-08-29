# ESP-IDF target shell

This is the target-build shell for ESP32-S3. It compiles the same fixed-capacity
runtime, behavior and protocol sources used by host tests. It deliberately does
not enable motion: the M5Stack StackChan BSP/HAL adapter, feedback calibration and
local self-test are still hardware-gated.

Required toolchain: ESP-IDF 5.5.4 with its supported CMake and Ninja versions.

```bash
source "$IDF_PATH/export.sh"
idf.py -C firmware/idf set-target esp32s3
idf.py -C firmware/idf build
idf.py -C firmware/idf size-components
```

The target has been compiled with ESP-IDF v5.5.4 and the ESP32-S3 toolchain,
flashed onto the real board (2026-08-29, after a full-flash backup), exercised
over USB Serial/JTAG with the read-only HIL runner, and the board was then
restored to its prior firmware. The image keeps motion disabled and speaks the
LifeOS protocol over USB Serial JTAG.

Target-specific notes baked into `main/app_main.cpp` and `sdkconfig.defaults`:

- stdin must not be read through stdio: the startup console VFS reads
  USB-Serial/JTAG non-blocking (immediate EOF) and the newlib stdin lock trips
  an assertion once real bytes arrive. The gateway loop reads the
  `usb_serial_jtag` driver directly with its own bounded line buffer.
- The main task stack is 64 KiB: the gateway ingest path holds bounded 8 KiB
  payload buffers across parse/ack temporaries and was measured peaking at
  ~44 KiB on hardware. Smaller stacks panic with a stack overflow as soon as
  the first request arrives.
