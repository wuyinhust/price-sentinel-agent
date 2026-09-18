# PriceSentinel local deployment helper.
# All targets use the project-local virtualenv so nothing leaks into the system Python.

PY      := .venv/bin/python
CLI     := .venv/bin/price-sentinel
DB      ?= prices.sqlite3
DEMO    ?= demo/payload.json
# The real watch. The other file in monitors/ is a probe config kept as a
# sample: its products are real but its floor prices are placeholders.
MONITOR ?= monitors/jinyin-joyinbag-watch.json
WATCHDB ?= watch.sqlite3
# adb is frequently installed only inside a GUI app bundle, where it never
# reaches PATH. Override with ADB=/path/to/adb if yours lives elsewhere.
ADB     ?= /Applications/Escrcpy.app/Contents/Resources/extra/mac-x64/scrcpy/adb

.PHONY: help venv test init ingest cases show reset clean check-monitor collect collect-dry baseline

help:
	@echo "make venv          创建本地虚拟环境并安装 price-sentinel"
	@echo "make test          运行单元测试"
	@echo "make init          初始化数据库 ($(DB))"
	@echo "make ingest        摄取样例数据 ($(DEMO))"
	@echo "make cases         列出全部案件"
	@echo "make show          直接查看库内明细(products/policies/cases/审计)"
	@echo "make reset         删除数据库后重建并重新摄取"
	@echo ""
	@echo "make check-monitor 校验监测配置 (MONITOR=$(MONITOR)),不碰设备"
	@echo "make collect       真机采集并开案 (MONITOR=..., DB=$(WATCHDB)),约2分钟"
	@echo "make collect-dry   真机采集但不写库"
	@echo "make baseline      看已攒到的报价区间(定限价用),需先跑过 collect"
	@echo "make clean         删除虚拟环境与数据库"

venv:
	/Users/y/.workbuddy/binaries/python/versions/3.13.12/bin/python3 -m venv .venv
	.venv/bin/python -m pip install -q --upgrade pip
	.venv/bin/python -m pip install -q -e .

test:
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

init:
	$(CLI) init-db $(DB)

ingest:
	$(CLI) ingest $(DB) $(DEMO)

cases:
	$(CLI) cases $(DB)

show:
	$(PY) scripts/show.py $(DB)

reset: 
	rm -f $(DB)
	$(CLI) init-db $(DB)
	$(CLI) ingest $(DB) $(DEMO)
	$(CLI) cases $(DB)

check-monitor:
	$(CLI) check-config $(MONITOR)

collect: 
	ADB=$(ADB) $(CLI) collect $(WATCHDB) $(MONITOR)

collect-dry:
	ADB=$(ADB) $(CLI) collect $(WATCHDB) $(MONITOR) --dry-run

baseline:
	$(PY) scripts/baseline.py $(WATCHDB)

clean:
	rm -rf .venv $(DB)
