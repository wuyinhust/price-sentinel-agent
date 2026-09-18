# android-harness local helper.
# Uses the project-local virtualenv so nothing leaks into the system Python.

PY := .venv/bin/python
CLI := .venv/bin/android-harness
PYVER := /Users/y/.workbuddy/binaries/python/versions/3.13.12/bin/python3

.PHONY: help venv test doctor tree labels shot mirror run clean

help:
	@echo "make venv     创建本地虚拟环境并安装 android-harness"
	@echo "make test     运行离线单元测试"
	@echo "make doctor   对已连接设备做自检(先跑这个)"
	@echo "make tree     打印屏幕无障碍树"
	@echo "make labels   列出可点击的标签"
	@echo "make shot     截图到 /tmp/android-shot.png"
	@echo "make mirror   打开 scrcpy 镜像窗口"
	@echo "make clean    删除虚拟环境"

venv:
	$(PYVER) -m venv .venv
	.venv/bin/python -m pip install -q --upgrade pip
	.venv/bin/python -m pip install -q -e .

test:
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

doctor:
	$(CLI) doctor

tree:
	$(CLI) tree --clickable --limit 30

labels:
	$(CLI) labels

shot:
	$(CLI) shot --out /tmp/android-shot.png

mirror:
	$(CLI) mirror

clean:
	rm -rf .venv
