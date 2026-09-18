.PHONY: test docs check validate bench gif build
test:      ; python -m pytest -q
docs:      ; python tools/gen_tables.py
check:     ; python -m pytest -q && python tools/gen_tables.py --check && python -m backburn validate scenarios/*.json
validate:  ; python -m backburn validate scenarios/*.json
bench:     ; python -m backburn bench
gif:       ; python -m backburn run scenarios/prairie_fire.json --ticks 900 --gif prairie_fire.gif
build:     ; sh packaging/build.sh
