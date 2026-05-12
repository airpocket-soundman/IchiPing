# IchiPing host build for ichiping_frame.c.
#
# Compiles the SDK-independent parts of the firmware (frame packer + CRC)
# into a shared library that Python ctypes can load. Used by
# pc/test_ctypes_packer.py to verify the C and Python frame packers emit
# byte-identical output for the same inputs — the strongest possible
# guarantee that the on-the-wire format does not drift between sides.
#
# Requires gcc (Linux/macOS) or MinGW-w64 (Windows). MCUXpresso SDK is NOT
# required: ichiping_frame.c only depends on <stdint.h>, <stddef.h>,
# <string.h>. On Windows install MSYS2 and `pacman -S mingw-w64-x86_64-gcc`.
#
# Usage:
#     cd firmware/host_build && make
#     cd ../../pc && python -m unittest test_ctypes_packer -v

CC ?= gcc
CFLAGS = -O2 -Wall -Wextra -I../include -fPIC

SRC = ../source/ichiping_frame.c

# Auto-detect platform — produce ichp.dll on Windows, libichp.so elsewhere.
ifeq ($(OS),Windows_NT)
TARGET = ichp.dll
LDFLAGS = -shared
else
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
TARGET = libichp.dylib
LDFLAGS = -dynamiclib
else
TARGET = libichp.so
LDFLAGS = -shared
endif
endif

all: $(TARGET)

$(TARGET): $(SRC) ../include/ichiping_frame.h
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRC)

clean:
	rm -f libichp.so libichp.dylib ichp.dll

.PHONY: all clean
