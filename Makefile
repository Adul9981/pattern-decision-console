.PHONY: test serve refresh

test:
	PYTHONPATH=tools python3 -m pytest -q tests

serve:
	python3 -m http.server 8765 --directory site

refresh:
	python3 tools/update_pattern_decision_live.py \
		--base-data data/pattern_decision_data.json \
		--template tools/pattern_decision_workspace.template.html \
		--watchlist config/market_watchlist.json \
		--out site/index.html \
		--json-out state/latest.json
