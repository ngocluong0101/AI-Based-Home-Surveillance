/*
 * Bộ chạy test cho phần C thuần (engine/src/core/), không phụ thuộc DeepStream.
 *
 * Thêm test: viết hàm `void test_xxx(void)` dùng CHECK(...) (test_util.h) trong
 * tests/<tên>.c, khai báo ở đây rồi thêm vào bảng TESTS. Mã thoát != 0 nếu có test hỏng.
 */
#include <stdio.h>

#include "test_util.h"

int g_failures = 0;

typedef void (*test_fn)(void);

typedef struct {
    const char *name;
    test_fn fn;
} TestCase;

static const TestCase TESTS[] = {
    /* { "event_manager_first_seen", test_event_manager_first_seen }, */
    { NULL, NULL }, /* phần tử kết thúc bảng */
};

int main(void)
{
    int run = 0;

    for (const TestCase *t = TESTS; t->name != NULL; t++) {
        printf("RUN  %s\n", t->name);
        t->fn();
        run++;
    }

    printf("%d tests, %d failed\n", run, g_failures);
    return g_failures == 0 ? 0 : 1;
}
