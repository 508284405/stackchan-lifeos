idf_component_register(
  SRCS "app_main.cpp" "firmware_update_idf.cpp"
  REQUIRES lifeos_core stackchan_hal log esp_timer esp_hw_support heap esp_driver_usb_serial_jtag vfs app_update mbedtls nvs_flash bootloader_support
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE LIFEOS_MANUAL_CONTROL_V1=1)

set(LIFEOS_OTA_TRUSTED_PUBLIC_KEY_FILE "" CACHE FILEPATH "Provisioned OTA P-256 public PEM; empty disables firmware updates")
set(LIFEOS_OTA_TRUSTED_PUBLIC_KEY_ESCAPED "")
if(LIFEOS_OTA_TRUSTED_PUBLIC_KEY_FILE)
  if(NOT EXISTS "${LIFEOS_OTA_TRUSTED_PUBLIC_KEY_FILE}")
    message(FATAL_ERROR "LIFEOS_OTA_TRUSTED_PUBLIC_KEY_FILE does not exist")
  endif()
  file(SIZE "${LIFEOS_OTA_TRUSTED_PUBLIC_KEY_FILE}" ota_key_bytes)
  if(ota_key_bytes GREATER 4096)
    message(FATAL_ERROR "OTA verification PEM exceeds its bounded size")
  endif()
  file(READ "${LIFEOS_OTA_TRUSTED_PUBLIC_KEY_FILE}" ota_public_key)
  if(ota_public_key MATCHES "PRIVATE KEY" OR NOT ota_public_key MATCHES "BEGIN PUBLIC KEY")
    message(FATAL_ERROR "OTA firmware build accepts PUBLIC KEY PEM only")
  endif()
  string(REPLACE "\\" "\\\\" ota_public_key "${ota_public_key}")
  string(REPLACE "\"" "\\\"" ota_public_key "${ota_public_key}")
  string(REPLACE "\r" "" ota_public_key "${ota_public_key}")
  string(REPLACE "\n" "\\n" LIFEOS_OTA_TRUSTED_PUBLIC_KEY_ESCAPED "${ota_public_key}")
endif()
configure_file(lifeos_ota_trust_key.hpp.in "${CMAKE_CURRENT_BINARY_DIR}/lifeos_ota_trust_key.hpp" @ONLY)
target_include_directories(${COMPONENT_LIB} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
