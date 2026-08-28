cmake_minimum_required(VERSION 3.20)
project(stackchan_lifeos_firmware LANGUAGES CXX)

option(STACKCHAN_BUILD_TESTS "Build host-side firmware tests" ON)

add_library(stackchan_firmware STATIC src/firmware.cpp)
target_include_directories(stackchan_firmware PUBLIC include)
target_compile_features(stackchan_firmware PUBLIC cxx_std_17)
target_compile_options(stackchan_firmware PRIVATE -Wall -Wextra -Wpedantic)

add_library(lifeos_phase1 STATIC
  src/runtime/runtime.cpp
  src/behavior/behavior.cpp
  src/hal/fake.cpp
  src/protocol/protocol.cpp
  src/phase1/controller.cpp
)
target_include_directories(lifeos_phase1 PUBLIC include)
target_compile_features(lifeos_phase1 PUBLIC cxx_std_17)
target_compile_options(lifeos_phase1 PRIVATE -Wall -Wextra -Wpedantic)

if(STACKCHAN_BUILD_TESTS)
  enable_testing()
  add_executable(firmware_tests tests/test_firmware.cpp)
  target_link_libraries(firmware_tests PRIVATE stackchan_firmware)
  add_test(NAME firmware_tests COMMAND firmware_tests)

  foreach(module runtime behavior hal protocol)
    add_executable(${module}_tests tests/${module}/test_${module}.cpp)
    target_link_libraries(${module}_tests PRIVATE lifeos_phase1)
    add_test(NAME ${module}_tests COMMAND ${module}_tests)
  endforeach()

  add_executable(phase1_controller_tests tests/phase1/test_controller.cpp)
  target_link_libraries(phase1_controller_tests PRIVATE lifeos_phase1)
  add_test(NAME phase1_controller_tests COMMAND phase1_controller_tests)
endif()
