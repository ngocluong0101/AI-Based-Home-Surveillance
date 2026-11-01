/* Tests of the spool file (src/core/event_spool.c) and of the retry delays (src/core/backoff.c). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "backoff.h"
#include "event_spool.h"
#include "test_util.h"

/* A fresh path in the temp directory; the caller removes it. */
static void temp_path(char *out, size_t cap)
{
    snprintf(out, cap, "/tmp/test_event_spool_XXXXXX");
    int fd = mkstemp(out);
    if (fd >= 0)
        close(fd);
}

static long file_size(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 ? (long)st.st_size : -1;
}

void test_event_spool_keeps_order_and_empties_the_file(void)
{
    char path[64], line[128];
    temp_path(path, sizeof(path));
    EventSpool *s = event_spool_open(path, 1 << 20);
    CHECK(s != NULL);
    if (s == NULL)
        return;

    CHECK(event_spool_empty(s));
    CHECK(event_spool_peek(s, line, sizeof(line)) == 0);

    CHECK(event_spool_append(s, "{\"n\":1}") == 0);
    CHECK(event_spool_append(s, "{\"n\":2}") == 0);
    CHECK(!event_spool_empty(s));

    /* peek does not consume */
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "{\"n\":1}") == 0);
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "{\"n\":1}") == 0);
    event_spool_pop(s);
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "{\"n\":2}") == 0);
    CHECK(file_size(path) > 0);   /* one line is still waiting */
    event_spool_pop(s);

    CHECK(event_spool_empty(s));
    CHECK(file_size(path) == 0);                       /* space given back */
    CHECK(event_spool_peek(s, line, sizeof(line)) == 0);

    CHECK(event_spool_append(s, "{\"n\":3}") == 0);    /* usable again after the reset */
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "{\"n\":3}") == 0);

    event_spool_close(s);
    unlink(path);
}

void test_event_spool_survives_a_restart(void)
{
    char path[64], line[128];
    temp_path(path, sizeof(path));

    EventSpool *s = event_spool_open(path, 1 << 20);
    CHECK(s != NULL);
    if (s == NULL)
        return;
    CHECK(event_spool_append(s, "a") == 0);
    CHECK(event_spool_append(s, "b") == 0);
    event_spool_close(s);

    s = event_spool_open(path, 1 << 20);
    CHECK(s != NULL);
    if (s == NULL)
        return;
    CHECK(!event_spool_empty(s));
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "a") == 0);
    event_spool_pop(s);
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "b") == 0);
    event_spool_close(s);
    unlink(path);
}

void test_event_spool_repairs_a_torn_last_line(void)
{
    char path[64], line[128];
    temp_path(path, sizeof(path));
    FILE *f = fopen(path, "wb");
    CHECK(f != NULL);
    if (f == NULL)
        return;
    fputs("whole\n{\"torn\":", f);   /* the engine died in the middle of the second line */
    fclose(f);

    EventSpool *s = event_spool_open(path, 1 << 20);
    CHECK(s != NULL);
    if (s == NULL)
        return;
    CHECK(event_spool_append(s, "next") == 0);
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "whole") == 0);
    event_spool_pop(s);
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "{\"torn\":") == 0);
    event_spool_pop(s);
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "next") == 0);   /* not glued */
    event_spool_close(s);
    unlink(path);
}

void test_event_spool_refuses_when_full(void)
{
    char path[64];
    temp_path(path, sizeof(path));
    EventSpool *s = event_spool_open(path, 10);
    CHECK(s != NULL);
    if (s == NULL)
        return;
    CHECK(event_spool_append(s, "12345678") == 0);    /* 9 bytes with the newline */
    CHECK(event_spool_append(s, "x") == -2);          /* would be 11 */
    CHECK(file_size(path) == 9);
    event_spool_close(s);
    unlink(path);
}

void test_event_spool_skips_unsendable_lines(void)
{
    char path[64], line[16];
    temp_path(path, sizeof(path));
    FILE *f = fopen(path, "wb");
    CHECK(f != NULL);
    if (f == NULL)
        return;
    fputs("\nthis line is far too long for the buffer\nok\n", f);
    fclose(f);

    EventSpool *s = event_spool_open(path, 1 << 20);
    CHECK(s != NULL);
    if (s == NULL)
        return;
    CHECK(event_spool_peek(s, line, sizeof(line)) == 1 && strcmp(line, "ok") == 0);
    CHECK(event_spool_skipped(s) == 1);
    event_spool_pop(s);
    CHECK(event_spool_empty(s));

    /* only unsendable lines left: reported as empty, not as an error */
    CHECK(event_spool_append(s, "another line that is much too long") == 0);
    CHECK(event_spool_peek(s, line, sizeof(line)) == 0);
    CHECK(event_spool_empty(s));
    event_spool_close(s);
    unlink(path);
}

void test_event_spool_open_fails_on_bad_path(void)
{
    CHECK(event_spool_open("/nonexistent-dir/spool.jsonl", 100) == NULL);
}

void test_backoff_doubles_and_is_capped(void)
{
    CHECK(backoff_next_ms(0) == 1000);
    CHECK(backoff_next_ms(1000) == 2000);
    CHECK(backoff_next_ms(2000) == 4000);
    CHECK(backoff_next_ms(16000) == 32000);
    CHECK(backoff_next_ms(32000) == 60000);
    CHECK(backoff_next_ms(60000) == 60000);
    CHECK(backoff_next_ms(4000000000u) == 60000);   /* no overflow */

    uint32_t d = 0;
    for (int i = 0; i < 100; i++)
        d = backoff_next_ms(d);
    CHECK(d == BACKOFF_MAX_MS);
}
