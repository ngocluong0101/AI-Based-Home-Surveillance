#include "event_spool.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

struct EventSpool {
    char *path;
    FILE *out;            /* append handle, kept open */
    uint64_t max_bytes;
    uint64_t size;        /* bytes in the file */
    uint64_t read_off;    /* start of the oldest undelivered line */
    uint64_t next_off;    /* where the line returned by the last peek ends */
    int peeked;
    uint64_t skipped;
};

/* The last byte of the file, or '\n' if it is empty or unreadable. */
static int last_byte(const char *path, uint64_t size)
{
    int c = '\n';
    FILE *f = fopen(path, "rb");
    if (f == NULL)
        return c;
    if (size > 0 && fseek(f, -1, SEEK_END) == 0)
        c = fgetc(f);
    fclose(f);
    return c == EOF ? '\n' : c;
}

EventSpool *event_spool_open(const char *path, uint64_t max_bytes)
{
    EventSpool *s = calloc(1, sizeof(*s));
    if (s == NULL)
        return NULL;
    s->path = strdup(path);
    s->max_bytes = max_bytes;
    s->out = s->path != NULL ? fopen(path, "ab") : NULL;
    struct stat st;
    if (s->out == NULL || stat(path, &st) != 0) {
        event_spool_close(s);
        return NULL;
    }
    s->size = (uint64_t)st.st_size;
    /* A crash in the middle of a write leaves a line without its newline; end it so that the next
     * event does not get glued to it. */
    if (s->size > 0 && last_byte(path, s->size) != '\n') {
        if (fputc('\n', s->out) == EOF || fflush(s->out) != 0) {
            event_spool_close(s);
            return NULL;
        }
        s->size++;
    }
    return s;
}

void event_spool_close(EventSpool *s)
{
    if (s == NULL)
        return;
    if (s->out != NULL)
        fclose(s->out);
    free(s->path);
    free(s);
}

int event_spool_append(EventSpool *s, const char *line)
{
    size_t len = strlen(line);
    if (s->size + len + 1 > s->max_bytes)
        return -2;
    if (fwrite(line, 1, len, s->out) != len || fputc('\n', s->out) == EOF ||
        fflush(s->out) != 0 || fsync(fileno(s->out)) != 0)
        return -1;
    s->size += len + 1;
    return 0;
}

int event_spool_empty(const EventSpool *s)
{
    return s->read_off >= s->size;
}

uint64_t event_spool_skipped(const EventSpool *s)
{
    return s->skipped;
}

int event_spool_peek(EventSpool *s, char *line, size_t cap)
{
    s->peeked = 0;
    if (cap < 2)
        return -1;
    FILE *in = fopen(s->path, "rb");
    if (in == NULL)
        return -1;

    uint64_t off = s->read_off;
    int result = 0;
    if (fseek(in, (long)off, SEEK_SET) != 0) {
        fclose(in);
        return -1;
    }
    while (off < s->size) {
        size_t len = 0;
        int too_long = 0, c;
        while ((c = fgetc(in)) != EOF && c != '\n') {
            if (len + 1 < cap)
                line[len] = (char)c;
            else
                too_long = 1;
            len++;
        }
        if (c == EOF && ferror(in)) {
            result = -1;
            break;
        }
        off += len + (c == '\n' ? 1 : 0);
        if (too_long || len == 0) {
            if (too_long)
                s->skipped++;
            s->read_off = off;   /* cannot be sent: drop it for good */
            continue;
        }
        line[len] = '\0';
        s->next_off = off;
        s->peeked = 1;
        result = 1;
        break;
    }
    fclose(in);
    if (result == 0)
        event_spool_pop(s);   /* nothing but skipped lines: release them */
    return result;
}

void event_spool_pop(EventSpool *s)
{
    if (s->peeked) {
        s->read_off = s->next_off;
        s->peeked = 0;
    }
    if (s->read_off >= s->size && s->size > 0) {
        /* Everything is delivered: give the disk space back. The append handle is in O_APPEND mode,
         * so the next write goes to the new end of file. */
        if (ftruncate(fileno(s->out), 0) == 0) {
            s->size = 0;
            s->read_off = 0;
        }
    }
}
