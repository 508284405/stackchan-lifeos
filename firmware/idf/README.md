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

The current development host does not have ESP-IDF installed, so this target is
present but not claimed as compiled or flashed.
