#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <stdio.h>

/* Số lần CHECK thất bại; test_runner.c định nghĩa và quyết định mã thoát. */
extern int g_failures;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);  \
            g_failures++;                                                    \
        }                                                                    \
    } while (0)

#endif /* TEST_UTIL_H */
