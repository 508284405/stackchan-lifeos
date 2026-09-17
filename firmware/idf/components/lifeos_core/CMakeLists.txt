idf_component_register(
  SRCS
    "../../../src/runtime/runtime.cpp"
    "../../../src/runtime/servo_io.cpp"
    "../../../src/runtime/manual_control.cpp"
    "../../../src/runtime/firmware_update.cpp"
    "../../../src/behavior/behavior.cpp"
    "../../../src/protocol/protocol.cpp"
    "../../../src/phase1/controller.cpp"
  INCLUDE_DIRS "../../../include"
)

target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Wpedantic)
# The parser and device-side state machine are compiled into the reviewed
# firmware image. The host Bridge still keeps its real-transport gate closed
# until supervised motion evidence is available.
target_compile_definitions(${COMPONENT_LIB} PRIVATE LIFEOS_MANUAL_CONTROL_V1=1)
