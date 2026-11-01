# DeepStream engine (C). Build inside the DeepStream container:
#   make CUDA_VER=12.6        -> build/ds_engine
# Tests for the pure-C code (no DeepStream/GPU needed), run on the host with gcc:
#   make test-core            -> build and run build/test_core under ASan/UBSan

CUDA_VER ?= 12.6
DS_DIR   ?= /opt/nvidia/deepstream/deepstream
BUILD    ?= build
CC       ?= gcc

APP       := $(BUILD)/ds_engine
TEST_BIN  := $(BUILD)/test_core

PKGS      := gstreamer-1.0 libcurl json-glib-1.0

CORE_SRCS := $(wildcard src/core/*.c)
DS_SRCS   := src/ds_engine.c
TEST_SRCS := $(wildcard tests/*.c)

WARN      := -Wall -Wextra

# ---- engine (needs GStreamer + DeepStream) ----
# Recursive `=` (not `:=`) so pkg-config only runs when building the engine, not for `make test-core`.
APP_CFLAGS = -std=gnu11 -g -O2 $(WARN) -Iinclude -Isrc/core \
             -I$(DS_DIR)/sources/includes \
             -I/usr/local/cuda-$(CUDA_VER)/include \
             $(shell pkg-config --cflags $(PKGS))
APP_LIBS   = $(shell pkg-config --libs $(PKGS)) \
             -L/usr/local/cuda-$(CUDA_VER)/lib64 -lcudart -lcuda \
             -L$(DS_DIR)/lib -lnvdsgst_meta -lnvds_meta \
             -Wl,-rpath,$(DS_DIR)/lib

APP_OBJS := $(patsubst src/%.c,$(BUILD)/%.o,$(DS_SRCS) $(CORE_SRCS))

# ---- test-core (pure C, host) ----
# VECTOR_DIR: the test vectors shared with the Python tests (relative to engine/, where make runs)
VECTOR_DIR  ?= ../tests/vectors
TEST_CFLAGS := -std=gnu11 -g -O1 $(WARN) -fsanitize=address,undefined \
               -fno-omit-frame-pointer -Iinclude -Isrc/core -Itests \
               -DVECTOR_DIR='"$(VECTOR_DIR)"'

.PHONY: all test-core clean

all: $(APP)

$(APP): $(APP_OBJS)
	$(CC) -o $@ $^ $(APP_LIBS)

$(BUILD)/%.o: src/%.c Makefile
	@mkdir -p $(dir $@)
	$(CC) -c -o $@ $(APP_CFLAGS) $<

test-core: $(TEST_BIN)
	./$(TEST_BIN)

$(TEST_BIN): $(CORE_SRCS) $(TEST_SRCS) Makefile
	@mkdir -p $(BUILD)
	$(CC) $(TEST_CFLAGS) -o $@ $(CORE_SRCS) $(TEST_SRCS)

clean:
	rm -rf $(BUILD)
