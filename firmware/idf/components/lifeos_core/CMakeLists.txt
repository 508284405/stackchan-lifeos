idf_component_register(
  SRCS
    "../../../src/runtime/runtime.cpp"
    "../../../src/behavior/behavior.cpp"
    "../../../src/protocol/protocol.cpp"
    "../../../src/phase1/controller.cpp"
  INCLUDE_DIRS "../../../include"
)

target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Wpedantic)
