CC      = gcc
CFLAGS  = -Wall -Wextra -std=c11 \
          $(shell pkg-config --cflags gtk4 webkitgtk-6.0)
LDFLAGS = $(shell pkg-config --libs gtk4 webkitgtk-6.0)

SRC     = main.c
TARGET  = build/dana

.PHONY: all clean deps install run

all:
	@mkdir -p build
	$(CC) $(CFLAGS) $(SRC) -o $(TARGET) $(LDFLAGS)

clean:
	rm -rf build

deps:
	sudo apt-get install -y libgtk-4-dev libwebkitgtk-6.0-dev build-essential
	pip3 install plotly yfinance requests pandas numpy --break-system-packages

install: all
	@mkdir -p $(HOME)/.local/bin $(HOME)/dana
	cp -f $(TARGET) $(HOME)/.local/bin/dana
	@[ "$(abspath dana_worker.py)" = "$(abspath $(HOME)/dana/dana_worker.py)" ] || \
	    cp -f dana_worker.py $(HOME)/dana/dana_worker.py
	@echo "Done. Run: dana &"

run: all
	$(TARGET) &
