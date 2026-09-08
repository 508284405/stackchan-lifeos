cmake_minimum_required(VERSION 3.20)
project(stackchan_lifeos_firmware LANGUAGES CXX)

option(STACKCHAN_BUILD_TESTS "Build host-side firmware tests" ON)

add_library(stackchan_firmware STATIC src/firmware.cpp)
target_include_directories(stackchan_firmware PUBLIC include)
target_compile_features(stackchan_firmware PUBLIC cxx_std_17)
target_compile_options(stackchan_firmware PRIVATE -Wall -Wextra -Wpedantic)

# Pure (host-testable) StackChan face rasterizer/streamer; the SPI transport
# lives in the IDF stackchan_hal component and implements FaceWriter.
add_library(stackchan_face STATIC src/hal/stackchan/face.cpp)
target_include_directories(stackchan_face PUBLIC include)
target_compile_features(stackchan_face PUBLIC cxx_std_17)
target_compile_options(stackchan_face PRIVATE -Wall -Wextra -Wpedantic)

add_library(lifeos_phase1 STATIC
  src/runtime/runtime.cpp
  src/runtime/servo_io.cpp
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

  add_executable(servo_io_tests tests/runtime/test_servo_io.cpp)
  target_link_libraries(servo_io_tests PRIVATE lifeos_phase1)
  add_test(NAME servo_io_tests COMMAND servo_io_tests)

  add_executable(manual_control_runtime_tests
    src/runtime/manual_control.cpp
    tests/runtime/test_manual_control.cpp
  )
  target_include_directories(manual_control_runtime_tests PRIVATE include)
  target_compile_features(manual_control_runtime_tests PRIVATE cxx_std_17)
  target_compile_options(manual_control_runtime_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME manual_control_runtime_tests COMMAND manual_control_runtime_tests)

  add_executable(manual_control_protocol_tests tests/protocol/test_manual_control.cpp)
  target_compile_definitions(manual_control_protocol_tests PRIVATE LIFEOS_MANUAL_CONTROL_V1=1)
  target_sources(manual_control_protocol_tests PRIVATE src/protocol/protocol.cpp)
  target_include_directories(manual_control_protocol_tests PRIVATE include)
  target_compile_features(manual_control_protocol_tests PRIVATE cxx_std_17)
  target_compile_options(manual_control_protocol_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME manual_control_protocol_tests COMMAND manual_control_protocol_tests)

  add_executable(camera_preview_protocol_tests tests/protocol/test_camera_preview.cpp)
  target_link_libraries(camera_preview_protocol_tests PRIVATE lifeos_phase1)
  target_compile_options(camera_preview_protocol_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME camera_preview_protocol_tests COMMAND camera_preview_protocol_tests)

  add_executable(face_tests tests/hal/test_face.cpp)
  target_link_libraries(face_tests PRIVATE stackchan_face)
  target_compile_options(face_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME face_tests COMMAND face_tests)
endif()
