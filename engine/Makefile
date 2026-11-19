# DeepStream engine (C). Build inside the DeepStream container:
#   make CUDA_VER=12.6        -> build/ds_engine
# Tests for the pure-C code (no DeepStream/GPU needed), run on the host with gcc:
#   make test-core            -> build and run build/test_core under ASan/UBSan
# Fake RTSP camera for testing reconnect (inside the container):
#   make rtsp-test-server     -> build/rtsp_test_server
# Check/time the TensorRT skeleton model against reference probabilities (V2-5):
#   make stgcn-check          -> build/stgcn_check

CUDA_VER ?= 12.6
DS_DIR   ?= /opt/nvidia/deepstream/deepstream
BUILD    ?= build
CC       ?= gcc

APP       := $(BUILD)/ds_engine
TEST_BIN  := $(BUILD)/test_core

PKGS      := gstreamer-1.0 gstreamer-app-1.0 libcurl json-glib-1.0

CORE_SRCS := $(wildcard src/core/*.c)
# The engine modules (ds_engine.c, config.c, sources.c, probes.c, delivery.c, stream_out.c, report.c)
DS_SRCS   := $(wildcard src/*.c) $(wildcard src/behavior/*.c)
TEST_SRCS := $(wildcard tests/*.c)

WARN      := -Wall -Wextra

# ---- engine (needs GStreamer + DeepStream) ----
# Recursive `=` (not `:=`) so pkg-config only runs when building the engine, not for `make test-core`.
APP_CFLAGS = -std=gnu11 -g -O2 $(WARN) -Iinclude -Isrc/core -Isrc/behavior \
             -I$(DS_DIR)/sources/includes \
             -I/usr/local/cuda-$(CUDA_VER)/include \
             $(shell pkg-config --cflags $(PKGS))
APP_LIBS   = $(shell pkg-config --libs $(PKGS)) \
             -L/usr/local/cuda-$(CUDA_VER)/lib64 -lcudart -lcuda \
             -L$(DS_DIR)/lib -lnvdsgst_meta -lnvds_meta -lnvbufsurface -lnvds_batch_jpegenc \
             -Wl,-rpath,$(DS_DIR)/lib -lm

APP_OBJS := $(patsubst src/%.c,$(BUILD)/%.o,$(DS_SRCS) $(CORE_SRCS))

# ---- test-core (pure C, host) ----
# VECTOR_DIR: the test vectors shared with the Python tests (relative to engine/, where make runs)
VECTOR_DIR  ?= ../tests/vectors
TEST_CFLAGS := -std=gnu11 -g -O1 $(WARN) -fsanitize=address,undefined \
               -fno-omit-frame-pointer -Iinclude -Isrc/core -Itests \
               -DVECTOR_DIR='"$(VECTOR_DIR)"'

.DEFAULT_GOAL := all
.PHONY: all test-core rtsp-test-server stgcn-check clean

# Test tool, not part of the engine: a fake RTSP camera (see tools/rtsp_test_server.c).
rtsp-test-server: $(BUILD)/rtsp_test_server

$(BUILD)/rtsp_test_server: tools/rtsp_test_server.c Makefile
	@mkdir -p $(BUILD)
	$(CC) -std=gnu11 -O2 $(WARN) -o $@ $< $(shell pkg-config --cflags --libs gstreamer-rtsp-server-1.0 gstreamer-1.0)

# V2-5: the ST-GCN runner is C++ (TensorRT C++ API). Linked into ds_engine with g++ so that libstdc++ comes along.
CXX      ?= g++
TRT_LIBS  = -lnvinfer -lnvonnxparser -L/usr/local/cuda-$(CUDA_VER)/lib64 -lcudart
CXX_SRCS := $(wildcard src/behavior/*.cpp)
CXX_OBJS := $(patsubst src/%.cpp,$(BUILD)/%.o,$(CXX_SRCS))

$(BUILD)/behavior/%.o: src/behavior/%.cpp src/behavior/stgcn.h Makefile
	@mkdir -p $(dir $@)
	$(CXX) -c -o $@ -std=c++17 -O2 $(WARN) -I/usr/local/cuda-$(CUDA_VER)/include $<

stgcn-check: $(BUILD)/stgcn_check

$(BUILD)/stgcn_check: tools/stgcn_check.c $(CXX_OBJS) Makefile
	$(CC) -std=gnu11 -O2 $(WARN) -c -o $(BUILD)/stgcn_check.o $<
	$(CXX) -o $@ $(BUILD)/stgcn_check.o $(CXX_OBJS) $(TRT_LIBS) -lm

all: $(APP)

$(APP): $(APP_OBJS) $(CXX_OBJS)
	$(CXX) -o $@ $^ $(APP_LIBS) $(TRT_LIBS)

$(BUILD)/%.o: src/%.c Makefile
	@mkdir -p $(dir $@)
	$(CC) -c -o $@ $(APP_CFLAGS) -MMD -MP $<

# -MMD writes a .d file per object listing the headers it used, so that changing app.h rebuilds every
# module that includes it (the modules share it).
-include $(APP_OBJS:.o=.d)

test-core: $(TEST_BIN)
	./$(TEST_BIN)

$(TEST_BIN): $(CORE_SRCS) $(TEST_SRCS) Makefile
	@mkdir -p $(BUILD)
	$(CC) $(TEST_CFLAGS) -o $@ $(CORE_SRCS) $(TEST_SRCS) -lm

clean:
	rm -rf $(BUILD)
