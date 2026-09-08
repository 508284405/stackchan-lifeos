idf_component_register(
  SRCS
    "../../../src/hal/stackchan/stackchan.cpp"
    "../../../src/hal/stackchan/face.cpp"
  INCLUDE_DIRS "../../../include"
  REQUIRES driver esp_timer esp_system esp_hw_support freertos esp32-camera
)

target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Wpedantic)
