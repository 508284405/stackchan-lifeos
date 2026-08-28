.PHONY: test brain-test firmware-test phase1-acceptance brain-demo schemas

test: brain-test firmware-test phase1-acceptance schemas

brain-test:
	PYTHONPATH=. python3 -m pytest brain/tests -q

firmware-test:
	@if command -v cmake >/dev/null 2>&1; then \
		cmake -S firmware -B firmware/build -DSTACKCHAN_BUILD_TESTS=ON && \
		cmake --build firmware/build && \
		ctest --test-dir firmware/build --output-on-failure; \
	else \
		./tools/run_firmware_tests.sh; \
	fi

phase1-acceptance:
	PYTHONPATH=. python3 -m unittest discover -s tests/phase1 -p 'test_*.py' -v
	PYTHONPATH=. python3 -m simulator.phase1.replay simulator/phase1/scenarios/nominal.jsonl

brain-demo:
	PYTHONPATH=. python3 -m brain.cli simulate --text "你好"

schemas:
	python3 tools/validate_schemas.py
