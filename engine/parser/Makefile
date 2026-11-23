# @author: Ngoc Luong <ngocluongg0101@gmail.com>

# YOLOv8 parsers for nvinfer (detection and pose). Build inside the DeepStream container:
#   make -C engine/parser CUDA_VER=12.6     -> engine/build/libnvds_yolov8_parser.so
# The decoding of the pose tensor has a host test that needs only g++:
#   make -C engine/parser test

CUDA_VER ?= 12.6
DS_DIR   ?= /opt/nvidia/deepstream/deepstream
BUILD    ?= ../build
CXX      ?= g++

TARGET := $(BUILD)/libnvds_yolov8_parser.so
SRCS   := nvdsinfer_yolov8_parser.cpp nvdsinfer_yolov8_pose_parser.cpp

.PHONY: all test clean
all: $(TARGET)

$(TARGET): $(SRCS) yolov8_pose_decode.h Makefile
	mkdir -p $(BUILD)
	$(CXX) -std=c++11 -Wall -Wextra -O2 -shared -fPIC \
	    -isystem $(DS_DIR)/sources/includes -isystem /usr/local/cuda-$(CUDA_VER)/include \
	    -o $@ $(SRCS) -lnvinfer

test: $(BUILD)/test_pose_decode
	$(BUILD)/test_pose_decode

$(BUILD)/test_pose_decode: tests/test_pose_decode.cpp yolov8_pose_decode.h Makefile
	mkdir -p $(BUILD)
	$(CXX) -std=c++11 -Wall -Wextra -g -O1 -fsanitize=address,undefined -o $@ $<

clean:
	rm -f $(TARGET) $(BUILD)/test_pose_decode
