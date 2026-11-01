#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <stdio.h>

/* Number of failed CHECKs; defined in test_runner.c, which also decides the exit code. */
extern int g_failures;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);  \
            g_failures++;                                                    \
        }                                                                    \
    } while (0)

#endif /* TEST_UTIL_H */
