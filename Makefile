.PHONY: db web test clean

db:
	python3 -m etl.run_import --offline

fetch:
	python3 -m etl.run_import --years 2023 2024 2025 2026

web:
	python3 -m web.app

test:
	python3 -m pytest tests/ -q

clean:
	rm -f data/db/procurement.db*
