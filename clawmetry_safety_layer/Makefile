# ClawMetry safety layer: make edge (team PC) | make brain (Spark) | make mock (everything on one machine) | make test
PY ?= .venv/bin/python
.PHONY: edge brain mock test
edge: ## venue edge: skill server + safety FSM + perception for the D1 Ultra-W on BIND_HOST:8765
	G1_CONFIG=$${G1_CONFIG:-config/d1w.yaml} $(PY) -m skills.edge
brain: ## relay + mission control + concierge on BIND_HOST:8780
	$(PY) -m brain
mock: ## kinematic twin + walkthrough video + brain, loopback only
	bash scripts/run_mock.sh
test: ## the kit's tests (fake rclpy, fake SDK, mock twin; no robot, no GPU)
	G1_CONFIG=config/fake.yaml CLAWMETRY_OTLP=http://127.0.0.1:1 $(PY) -m pytest -q
