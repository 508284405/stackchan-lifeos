.PHONY: test brain-test firmware-test brain-demo schemas

test: brain-test firmware-test schemas

brain-test:
	PYTHONPATH=. python3 -m pytest brain/tests -q

firmware-test:
	@if command -v cmake >/dev/null 2>&1; then \
		cmake -S firmware -B firmware/build -DSTACKCHAN_BUILD_TESTS=ON && \
		cmake --build firmware/build && \
		ctest --test-dir firmware/build --output-on-failure; \
	else \
		c++ -std=c++17 -Wall -Wextra -Werror -Ifirmware/include \
			firmware/src/firmware.cpp firmware/tests/test_firmware.cpp \
			-o /tmp/stackchan-firmware-tests && /tmp/stackchan-firmware-tests; \
	fi

brain-demo:
	PYTHONPATH=. python3 -m brain.cli simulate --text "你好"

schemas:
	python3 tools/validate_schemas.py
