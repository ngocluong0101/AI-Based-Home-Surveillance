# Engine DeepStream (C). Build trong DeepStream container:
#   make CUDA_VER=12.6        -> build/ds_engine
# Test C thuần (không cần DeepStream/GPU), chạy được trên host bằng gcc:
#   make test-core            -> build + chạy build/test_core dưới ASan/UBSan

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

# ---- engine (cần GStreamer + DeepStream) ----
# Dùng `=` (không phải `:=`) để pkg-config chỉ chạy khi build engine, không chạy với `make test-core`.
APP_CFLAGS = -std=gnu11 -g -O2 $(WARN) -Iinclude -Isrc/core \
              -I$(DS_DIR)/sources/includes \
              -I/usr/local/cuda-$(CUDA_VER)/include \
              $(shell pkg-config --cflags $(PKGS))
APP_LIBS   = $(shell pkg-config --libs $(PKGS)) \
              -L/usr/local/cuda-$(CUDA_VER)/lib64 -lcudart -lcuda \
              -L$(DS_DIR)/lib -lnvdsgst_meta -lnvds_meta \
              -Wl,-rpath,$(DS_DIR)/lib

APP_OBJS := $(patsubst src/%.c,$(BUILD)/%.o,$(DS_SRCS) $(CORE_SRCS))

# ---- test core (C thuần, host) ----
TEST_CFLAGS := -std=gnu11 -g -O1 $(WARN) -fsanitize=address,undefined \
               -fno-omit-frame-pointer -Iinclude -Isrc/core -Itests

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
