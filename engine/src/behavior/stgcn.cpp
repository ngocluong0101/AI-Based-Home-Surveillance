/* stgcn.cpp - see stgcn.h. TensorRT 10 C++ API: ONNX -> FP16 engine (cached) -> enqueueV3 on one stream. */
#include "stgcn.h"

#include <NvInfer.h>
#include <NvOnnxParser.h>
#include <cuda_runtime_api.h>

#include <sys/stat.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <memory>
#include <string>
#include <vector>

namespace {

class Logger : public nvinfer1::ILogger {
public:
    void log(Severity s, const char *msg) noexcept override {
        if (s <= Severity::kWARNING)
            std::fprintf(stderr, "stgcn: [TensorRT] %s\n", msg);
    }
};

Logger g_logger;

void set_err(char *err, int len, const std::string &msg) {
    if (err && len > 0)
        std::snprintf(err, (size_t)len, "%s", msg.c_str());
}

bool file_time(const std::string &p, time_t *t) {
    struct stat st;
    if (stat(p.c_str(), &st) != 0)
        return false;
    *t = st.st_mtime;
    return true;
}

bool read_file(const std::string &p, std::vector<char> *out) {
    std::ifstream f(p, std::ios::binary);
    if (!f)
        return false;
    out->assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return !out->empty();
}

}  // namespace

struct StgcnModel {
    int T = 0, max_batch = 0;
    std::unique_ptr<nvinfer1::IRuntime> runtime;
    std::unique_ptr<nvinfer1::ICudaEngine> engine;
    std::unique_ptr<nvinfer1::IExecutionContext> ctx;
    cudaStream_t stream = nullptr;
    float *d_in = nullptr, *d_out = nullptr;
    std::vector<float> h_out;
    double last_us = 0;
    ~StgcnModel() {
        if (d_in) cudaFree(d_in);
        if (d_out) cudaFree(d_out);
        if (stream) cudaStreamDestroy(stream);
    }
};

static bool build_engine(const std::string &onnx, int T, int max_batch, const std::string &plan_path, std::string *why) {
    std::unique_ptr<nvinfer1::IBuilder> builder(nvinfer1::createInferBuilder(g_logger));
    std::unique_ptr<nvinfer1::INetworkDefinition> net(builder ? builder->createNetworkV2(0) : nullptr);
    std::unique_ptr<nvonnxparser::IParser> parser(net ? nvonnxparser::createParser(*net, g_logger) : nullptr);
    if (!parser) {
        *why = "cannot create the TensorRT builder/parser";
        return false;
    }
    if (!parser->parseFromFile(onnx.c_str(), (int)nvinfer1::ILogger::Severity::kWARNING)) {
        *why = "cannot parse " + onnx;
        return false;
    }
    std::unique_ptr<nvinfer1::IBuilderConfig> cfg(builder->createBuilderConfig());
    cfg->setFlag(nvinfer1::BuilderFlag::kFP16);
    cfg->setMemoryPoolLimit(nvinfer1::MemoryPoolType::kWORKSPACE, 256u << 20);
    nvinfer1::IOptimizationProfile *prof = builder->createOptimizationProfile();
    const int opt = max_batch < 8 ? max_batch : 8;
    prof->setDimensions("skeleton", nvinfer1::OptProfileSelector::kMIN, nvinfer1::Dims4{1, STGCN_CHANNELS, T, STGCN_JOINTS});
    prof->setDimensions("skeleton", nvinfer1::OptProfileSelector::kOPT, nvinfer1::Dims4{opt, STGCN_CHANNELS, T, STGCN_JOINTS});
    prof->setDimensions("skeleton", nvinfer1::OptProfileSelector::kMAX, nvinfer1::Dims4{max_batch, STGCN_CHANNELS, T, STGCN_JOINTS});
    cfg->addOptimizationProfile(prof);
    std::unique_ptr<nvinfer1::IHostMemory> plan(builder->buildSerializedNetwork(*net, *cfg));
    if (!plan || plan->size() == 0) {
        *why = "TensorRT could not build an engine from " + onnx;
        return false;
    }
    const std::string tmp = plan_path + ".tmp";
    {
        std::ofstream f(tmp, std::ios::binary);
        f.write(static_cast<const char *>(plan->data()), (std::streamsize)plan->size());
        if (!f) {
            *why = "cannot write " + tmp;
            return false;
        }
    }
    if (std::rename(tmp.c_str(), plan_path.c_str()) != 0) {
        *why = "cannot rename " + tmp;
        return false;
    }
    return true;
}

StgcnModel *stgcn_open(const char *onnx_path, int T, int max_batch, char *err, int err_len) {
    if (!onnx_path || T < 2 || max_batch < 1) {
        set_err(err, err_len, "bad arguments");
        return nullptr;
    }
    const std::string onnx(onnx_path);
    const std::string plan_path = onnx + ".T" + std::to_string(T) + ".b" + std::to_string(max_batch) + ".fp16.engine";
    time_t t_onnx = 0, t_plan = 0;
    if (!file_time(onnx, &t_onnx)) {
        set_err(err, err_len, "cannot open " + onnx);
        return nullptr;
    }
    std::string why;
    if (!file_time(plan_path, &t_plan) || t_plan < t_onnx) {
        std::fprintf(stderr, "stgcn: building the TensorRT engine for %s (batch 1..%d), this takes a while once\n", onnx.c_str(), max_batch);
        if (!build_engine(onnx, T, max_batch, plan_path, &why)) {
            set_err(err, err_len, why);
            return nullptr;
        }
    }
    std::vector<char> blob;
    if (!read_file(plan_path, &blob)) {
        set_err(err, err_len, "cannot read " + plan_path);
        return nullptr;
    }
    std::unique_ptr<StgcnModel> m(new StgcnModel());
    m->T = T;
    m->max_batch = max_batch;
    m->runtime.reset(nvinfer1::createInferRuntime(g_logger));
    if (m->runtime)
        m->engine.reset(m->runtime->deserializeCudaEngine(blob.data(), blob.size()));
    if (!m->engine) {
        set_err(err, err_len, "cannot load the TensorRT engine " + plan_path + " (delete it to rebuild)");
        return nullptr;
    }
    m->ctx.reset(m->engine->createExecutionContext());
    const size_t in_elems = (size_t)max_batch * STGCN_CHANNELS * T * STGCN_JOINTS;
    if (!m->ctx || cudaStreamCreate(&m->stream) != cudaSuccess || cudaMalloc(reinterpret_cast<void **>(&m->d_in), in_elems * sizeof(float)) != cudaSuccess ||
        cudaMalloc(reinterpret_cast<void **>(&m->d_out), (size_t)max_batch * 2 * sizeof(float)) != cudaSuccess) {
        set_err(err, err_len, "cannot set up the TensorRT execution context / CUDA buffers");
        return nullptr;
    }
    m->h_out.resize((size_t)max_batch * 2);
    return m.release();
}

int stgcn_window_len(const StgcnModel *m) { return m->T; }
int stgcn_max_batch(const StgcnModel *m) { return m->max_batch; }
double stgcn_last_run_us(const StgcnModel *m) { return m->last_us; }
void stgcn_close(StgcnModel *m) { delete m; }

int stgcn_run(StgcnModel *m, const float *windows, int n, float *prob_fall, char *err, int err_len) {
    if (n < 1 || n > m->max_batch) {
        set_err(err, err_len, "batch size out of range");
        return -1;
    }
    const auto t0 = std::chrono::steady_clock::now();
    const size_t in_bytes = (size_t)n * STGCN_CHANNELS * m->T * STGCN_JOINTS * sizeof(float);
    if (!m->ctx->setInputShape("skeleton", nvinfer1::Dims4{n, STGCN_CHANNELS, m->T, STGCN_JOINTS}) ||
        !m->ctx->setTensorAddress("skeleton", m->d_in) || !m->ctx->setTensorAddress("logits", m->d_out)) {
        set_err(err, err_len, "TensorRT rejected the input shape/tensors");
        return -1;
    }
    if (cudaMemcpyAsync(m->d_in, windows, in_bytes, cudaMemcpyHostToDevice, m->stream) != cudaSuccess || !m->ctx->enqueueV3(m->stream) ||
        cudaMemcpyAsync(m->h_out.data(), m->d_out, (size_t)n * 2 * sizeof(float), cudaMemcpyDeviceToHost, m->stream) != cudaSuccess ||
        cudaStreamSynchronize(m->stream) != cudaSuccess) {
        set_err(err, err_len, "TensorRT/CUDA failed while running a batch");
        return -1;
    }
    for (int i = 0; i < n; i++) {                     /* softmax of two logits = 1 / (1 + exp(l0 - l1)) */
        const float d = m->h_out[2 * i] - m->h_out[2 * i + 1];
        prob_fall[i] = 1.0f / (1.0f + std::exp(d));
    }
    m->last_us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count();
    return 0;
}
