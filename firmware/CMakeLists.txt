cmake_minimum_required(VERSION 3.20)
project(stackchan_lifeos_firmware LANGUAGES CXX)

option(STACKCHAN_BUILD_TESTS "Build host-side firmware tests" ON)

add_library(stackchan_firmware STATIC src/firmware.cpp)
target_include_directories(stackchan_firmware PUBLIC include)
target_compile_features(stackchan_firmware PUBLIC cxx_std_17)
target_compile_options(stackchan_firmware PRIVATE -Wall -Wextra -Wpedantic)

if(STACKCHAN_BUILD_TESTS)
  enable_testing()
  add_executable(firmware_tests tests/test_firmware.cpp)
  target_link_libraries(firmware_tests PRIVATE stackchan_firmware)
  add_test(NAME firmware_tests COMMAND firmware_tests)
endif()

