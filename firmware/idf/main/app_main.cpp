#include "esp_log.h"

#include "lifeos/runtime/runtime.hpp"

namespace {
constexpr const char* kTag = "stackchan-lifeos";
}
extern "C" void app_main() {
  // Compile/boot probe only. Actuators remain disabled until a target-specific
  // M5Stack BSP adapter and local self-test are available.
  lifeos::runtime::FastSafetyLoop safety;
  (void)safety;
  ESP_LOGW(kTag,
           "Phase 1 core loaded; target HAL is unavailable, motion stays disabled");
}
