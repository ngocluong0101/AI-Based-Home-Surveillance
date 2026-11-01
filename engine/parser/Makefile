# YOLOv8 bbox parser for nvinfer. Build inside the DeepStream container:
#   make -C engine/parser CUDA_VER=12.6     -> engine/build/libnvds_yolov8_parser.so

CUDA_VER ?= 12.6
DS_DIR   ?= /opt/nvidia/deepstream/deepstream
BUILD    ?= ../build
CXX      ?= g++

TARGET := $(BUILD)/libnvds_yolov8_parser.so

.PHONY: all clean
all: $(TARGET)

$(TARGET): nvdsinfer_yolov8_parser.cpp Makefile
	mkdir -p $(BUILD)
	$(CXX) -std=c++11 -Wall -Wextra -O2 -shared -fPIC \
	    -isystem $(DS_DIR)/sources/includes -isystem /usr/local/cuda-$(CUDA_VER)/include \
	    -o $@ $< -lnvinfer

clean:
	rm -f $(TARGET)
