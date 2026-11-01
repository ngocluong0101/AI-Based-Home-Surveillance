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

/* Floating-point comparison: |a - b| <= eps. */
#define CHECK_NEAR(a, b, eps)                                                          \
    do {                                                                               \
        double test_a_ = (double)(a), test_b_ = (double)(b);                           \
        double test_d_ = test_a_ > test_b_ ? test_a_ - test_b_ : test_b_ - test_a_;    \
        if (!(test_d_ <= (double)(eps))) {                                             \
            fprintf(stderr, "FAIL %s:%d: %s (%g) not near %s (%g)\n", __FILE__,        \
                    __LINE__, #a, test_a_, #b, test_b_);                               \
            g_failures++;                                                              \
        }                                                                              \
    } while (0)

#endif /* TEST_UTIL_H */
