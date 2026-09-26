.PHONY: setup setup-mac setup-pi test run clean

# Biến môi trường
VENV = .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip

# Tự động chọn setup dựa trên OS
setup:
	@if [ "$$(uname)" = "Darwin" ]; then \
		make setup-mac; \
	else \
		make setup-pi; \
	fi

setup-mac:
	bash scripts/setup_macbook.sh
	$(PIP) install -r requirement.txt

setup-pi:
	bash scripts/setup_pi4.sh
	$(PIP) install -r requirement.txt

test:
	# Thêm lệnh chạy unit tests ở đây (ví dụ: pytest tests/)
	@echo "Running tests..."
	PYTHONPATH=src $(PYTHON) -m unittest discover tests/

run:
	@echo "Starting Edge Orchestrator in simulation mode..."
	PYTHONPATH=src $(PYTHON) -m edge_orchestrator.main

run-gns3:
	@echo "Starting Edge Orchestrator with real GNS3 connection..."
	PYTHONPATH=src $(PYTHON) -m edge_orchestrator.main --gns3

run-real:
	@echo "Starting Edge Orchestrator with REAL Telemetry (requires sudo password)..."
	sudo PYTHONPATH=src $(PYTHON) -m edge_orchestrator.main --no-sim

clean:
	rm -rf __pycache__
	rm -rf src/*/__pycache__
	rm -rf src/*/*/__pycache__
	rm -rf .pytest_cache
