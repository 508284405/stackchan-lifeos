idf_component_register(
  SRCS "app_main.cpp"
  REQUIRES lifeos_core log esp_timer esp_hw_support heap esp_driver_usb_serial_jtag vfs
)
