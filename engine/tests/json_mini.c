#include "json_mini.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#define MAX_DEPTH 32

typedef struct {
    const char *p;
} Parser;

static void skip_ws(Parser *ps)
{
    while (*ps->p == ' ' || *ps->p == '\t' || *ps->p == '\n' || *ps->p == '\r')
        ps->p++;
}

static int parse_value(Parser *ps, JsonValue *out, int depth);

/* Free what is inside value, not value itself (it lives in its parent's array or on the stack). */
static void free_contents(JsonValue *v)
{
    free(v->string);
    for (size_t i = 0; i < v->count; i++) {
        free_contents(&v->items[i]);
        if (v->keys != NULL)
            free(v->keys[i]);
    }
    free(v->items);
    free(v->keys);
    memset(v, 0, sizeof(*v));
}

void json_free(JsonValue *value)
{
    if (value == NULL)
        return;
    free_contents(value);
    free(value);
}

const JsonValue *json_get(const JsonValue *object, const char *key)
{
    if (object == NULL || object->type != JSON_OBJECT)
        return NULL;
    for (size_t i = 0; i < object->count; i++) {
        if (strcmp(object->keys[i], key) == 0)
            return &object->items[i];
    }
    return NULL;
}

/* Parse a string literal starting at the opening quote; returns a malloc'ed copy or NULL. */
static char *parse_string(Parser *ps)
{
    if (*ps->p != '"')
        return NULL;
    ps->p++;

    size_t cap = 16, len = 0;
    char *s = malloc(cap);
    if (s == NULL)
        return NULL;

    while (*ps->p != '"') {
        char c = *ps->p;
        if (c == '\0')
            goto fail;
        ps->p++;
        if (c == '\\') {
            c = *ps->p;
            if (c == '\0')
                goto fail;
            ps->p++;
            switch (c) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case '"': case '\\': case '/': break;
            case 'u':   /* the vectors are ASCII; keep the text, lose the character */
                for (int i = 0; i < 4; i++) {
                    if (*ps->p == '\0')
                        goto fail;
                    ps->p++;
                }
                c = '?';
                break;
            default: goto fail;
            }
        }
        if (len + 1 >= cap) {
            char *bigger = realloc(s, cap * 2);
            if (bigger == NULL)
                goto fail;
            s = bigger;
            cap *= 2;
        }
        s[len++] = c;
    }
    ps->p++;  /* closing quote */
    s[len] = '\0';
    return s;

fail:
    free(s);
    return NULL;
}

static int parse_number(Parser *ps, JsonValue *out)
{
    const char *start = ps->p;
    int integral = 1;

    if (*ps->p == '-')
        ps->p++;
    while ((*ps->p >= '0' && *ps->p <= '9') || *ps->p == '.' || *ps->p == 'e' || *ps->p == 'E' ||
           *ps->p == '+' || *ps->p == '-') {
        if (*ps->p == '.' || *ps->p == 'e' || *ps->p == 'E')
            integral = 0;
        ps->p++;
    }
    if (ps->p == start)
        return -1;

    char buf[64];
    size_t n = (size_t)(ps->p - start);
    if (n >= sizeof(buf))
        return -1;
    memcpy(buf, start, n);
    buf[n] = '\0';

    char *end = NULL;
    out->type = JSON_NUMBER;
    out->number = strtod(buf, &end);
    if (*end != '\0')
        return -1;
    if (integral) {
        errno = 0;
        out->integer = strtoll(buf, &end, 10);
        if (errno != 0 || *end != '\0')
            return -1;
        out->is_integer = 1;
    }
    return 0;
}

static int parse_array(Parser *ps, JsonValue *out, int depth)
{
    out->type = JSON_ARRAY;
    ps->p++;  /* [ */
    skip_ws(ps);
    if (*ps->p == ']') {
        ps->p++;
        return 0;
    }
    for (;;) {
        JsonValue *items = realloc(out->items, (out->count + 1) * sizeof(*items));
        if (items == NULL)
            return -1;
        out->items = items;
        memset(&out->items[out->count], 0, sizeof(*items));
        out->count++;  /* counted before parsing so that a failure frees what was built */
        if (parse_value(ps, &out->items[out->count - 1], depth + 1) != 0)
            return -1;
        skip_ws(ps);
        if (*ps->p == ',') {
            ps->p++;
            continue;
        }
        if (*ps->p == ']') {
            ps->p++;
            return 0;
        }
        return -1;
    }
}

static int parse_object(Parser *ps, JsonValue *out, int depth)
{
    out->type = JSON_OBJECT;
    ps->p++;  /* { */
    skip_ws(ps);
    if (*ps->p == '}') {
        ps->p++;
        return 0;
    }
    for (;;) {
        JsonValue *items = realloc(out->items, (out->count + 1) * sizeof(*items));
        if (items == NULL)
            return -1;
        out->items = items;
        char **keys = realloc(out->keys, (out->count + 1) * sizeof(*keys));
        if (keys == NULL)
            return -1;
        out->keys = keys;
        memset(&out->items[out->count], 0, sizeof(*items));
        out->keys[out->count] = NULL;
        out->count++;

        skip_ws(ps);
        out->keys[out->count - 1] = parse_string(ps);
        if (out->keys[out->count - 1] == NULL)
            return -1;
        skip_ws(ps);
        if (*ps->p != ':')
            return -1;
        ps->p++;
        if (parse_value(ps, &out->items[out->count - 1], depth + 1) != 0)
            return -1;
        skip_ws(ps);
        if (*ps->p == ',') {
            ps->p++;
            continue;
        }
        if (*ps->p == '}') {
            ps->p++;
            return 0;
        }
        return -1;
    }
}

static int parse_literal(Parser *ps, const char *word)
{
    size_t n = strlen(word);
    if (strncmp(ps->p, word, n) != 0)
        return -1;
    ps->p += n;
    return 0;
}

static int parse_value(Parser *ps, JsonValue *out, int depth)
{
    if (depth > MAX_DEPTH)
        return -1;
    skip_ws(ps);
    switch (*ps->p) {
    case '{': return parse_object(ps, out, depth);
    case '[': return parse_array(ps, out, depth);
    case '"':
        out->type = JSON_STRING;
        out->string = parse_string(ps);
        return out->string != NULL ? 0 : -1;
    case 't':
        out->type = JSON_BOOL;
        out->number = 1;
        return parse_literal(ps, "true");
    case 'f':
        out->type = JSON_BOOL;
        return parse_literal(ps, "false");
    case 'n':
        out->type = JSON_NULL;
        return parse_literal(ps, "null");
    default:
        return parse_number(ps, out);
    }
}

JsonValue *json_parse(const char *text)
{
    JsonValue *root = calloc(1, sizeof(*root));
    if (root == NULL)
        return NULL;

    Parser ps = { text };
    if (parse_value(&ps, root, 0) != 0) {
        json_free(root);
        return NULL;
    }
    skip_ws(&ps);
    if (*ps.p != '\0') {  /* trailing garbage */
        json_free(root);
        return NULL;
    }
    return root;
}
