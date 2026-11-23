/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

/*
 * json_mini.h - a small JSON reader for the tests, so `make test-core` needs no library (the shared vectors are plain JSON).
 * Parses what json.dumps writes, keeps integers exact in int64 (ns clocks do not fit a double), and does not validate: it is for
 * files we generate, not untrusted data.
 */
#ifndef JSON_MINI_H
#define JSON_MINI_H

#include <stddef.h>
#include <stdint.h>

typedef enum { JSON_NULL, JSON_BOOL, JSON_NUMBER, JSON_STRING, JSON_ARRAY, JSON_OBJECT } JsonType;

typedef struct JsonValue {
    JsonType type;
    double number;        /* JSON_NUMBER (also for integers); JSON_BOOL: 0 or 1 */
    int64_t integer;      /* JSON_NUMBER written without fraction or exponent */
    int is_integer;
    char *string;         /* JSON_STRING */
    size_t count;         /* JSON_ARRAY: elements, JSON_OBJECT: members */
    struct JsonValue *items;  /* the elements / the member values */
    char **keys;          /* JSON_OBJECT: the member names, parallel to items */
} JsonValue;

/* Parse a whole document. Returns NULL on a syntax error or when memory runs out. */
JsonValue *json_parse(const char *text);

/* Free a value returned by json_parse() with everything below it. */
void json_free(JsonValue *value);

/* Member `key` of an object, or NULL if value is not an object or has no such member. */
const JsonValue *json_get(const JsonValue *object, const char *key);

#endif /* JSON_MINI_H */
