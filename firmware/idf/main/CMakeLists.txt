idf_component_register(
  SRCS "app_main.cpp"
  REQUIRES lifeos_core stackchan_hal log esp_timer esp_hw_support heap esp_driver_usb_serial_jtag vfs
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE LIFEOS_MANUAL_CONTROL_V1=1)
