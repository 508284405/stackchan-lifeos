.PHONY: test brain-test bridge-test web-check firmware-test phase1-acceptance phase2-3-acceptance phase4-acceptance codex-contract codex-contract-check codex-live-probe brain-demo schemas sub2api-contract

test: brain-test bridge-test web-check firmware-test phase1-acceptance schemas sub2api-contract codex-contract

brain-test:
	PYTHONPATH=. python3 -m pytest brain/tests -q

bridge-test:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest tests/bridge -q

web-check:
	@if command -v node >/dev/null 2>&1; then \
		if [ ! -d web/node_modules ]; then \
			(cd web && npm ci --silent) || { echo "SKIP web build: npm install failed (offline? dist/ is committed)"; exit 0; }; \
		fi; \
		(cd web && npm run build --silent); \
	else echo "SKIP web build: node executable not installed (dist/ is committed)"; fi

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

phase2-3-acceptance:
	PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' PYTHONPATH=. python3 -m pytest brain/tests -q
	python3 tools/sub2api_contract.py

phase4-acceptance: bridge-test web-check schemas
	PYTHONPATH=. python3 tools/bridge_capacity.py --pretty

codex-contract:
	python3 tools/codex_protocol_contract.py --test
	@if command -v codex >/dev/null 2>&1; then \
		python3 tools/codex_protocol_contract.py --check; \
	else \
		echo "SKIP Codex schema drift check: codex executable not installed"; \
	fi

codex-contract-check:
	python3 tools/codex_protocol_contract.py --check --test

codex-live-probe:
	python3 tools/codex_app_server_probe.py --repeat 2

sub2api-contract:
	python3 tools/sub2api_contract.py

brain-demo:
	PYTHONPATH=. python3 -m brain.cli simulate --text "你好"

schemas:
	python3 tools/validate_schemas.py
