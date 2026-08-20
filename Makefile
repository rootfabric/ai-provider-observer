.PHONY: status validate validate-active validate-ready hygiene selftest report demo
status:
	python3 scripts/harness/control.py status
validate:
	python3 scripts/harness/control.py validate
validate-active:
	python3 scripts/harness/control.py validate-active
validate-ready:
	python3 scripts/harness/control.py validate-ready
hygiene:
	python3 scripts/harness/control.py hygiene
selftest:
	python3 scripts/harness/control.py selftest
report:
	python3 scripts/harness/control.py report
demo:
	python3 scripts/harness/control.py demo
