/*
 * config.h - the command line (--camera, --cameras-file, --config, ...) and the cameras file.
 *
 * The options are plain globals: they are filled in once, by config_load(), before any thread exists, and
 * only read afterwards (the one exception, the trailing slash of --stream-url, is cut inside config_load()).
 */
#ifndef DS_CONFIG_H
#define DS_CONFIG_H

#include <glib.h>

#include "behavior.h"

extern gchar **opt_cameras;
extern gchar *opt_cameras_file;
extern gchar *opt_config;
extern gchar *opt_tracker_config;
extern gchar *opt_output;
extern gchar *opt_dump_detections;
extern gint opt_fps_interval;
extern gchar *opt_snapshot_dir;
extern gint opt_snapshot_quality;
extern gchar *opt_backend_url;
extern gchar *opt_spool_file;
extern gchar *opt_heartbeat_url;
extern gchar *opt_stream_url;
extern gint opt_stream_bitrate;
extern gint opt_heartbeat_interval;
extern gint opt_rtsp_timeout;
extern gint opt_max_cameras;
extern gdouble opt_absence_timeout;

/* camera id -> CameraSettings (private to config.c), for the cameras that came from the cameras file */
extern GHashTable *file_settings;

/*
 * Parse the command line, check every option, read --cameras-file, and put the "ID=URI" specs into `specs`
 * (--camera ones first, then the file's). Returns FALSE after printing why; the caller exits with
 * EXIT_BAD_ARGS and frees `specs`.
 */
gboolean config_load(int *argc, char ***argv, GPtrArray *specs);

/* Free the options and the settings table. */
void config_free(void);

/* The settings a camera runs with: what the table says, else the defaults. `table` may hold no entry for it. */
void settings_of(const GHashTable *table, gint camera_id, gdouble *conf, gdouble *absence, gboolean *ai);

GHashTable *new_settings_table(void);

/* What the cameras file says about the behaviour rules of one camera (ROADMAP_V2 V2-3). */
typedef struct {
    BehaviorConfig cfg;                       /* which rules run (behaviors=) and their thresholds */
    BehaviorZone zones[BEHAVIOR_MAX_ZONES];   /* zone.<n>= */
    size_t num_zones;
} BehaviorSettings;

/* The behaviour settings a camera runs with: the file's, else every rule off and no zones. */
void behavior_settings_of(const GHashTable *table, gint camera_id, BehaviorSettings *out);

/* TRUE when two settings are the same (a reload that changes nothing is not announced). */
gboolean behavior_settings_equal(const BehaviorSettings *a, const BehaviorSettings *b);

/* TRUE when any rule runs or any zone exists, so the probe has something to do. */
gboolean behavior_settings_active(const BehaviorSettings *s);

/* Split "ID=URI". Returns FALSE on a bad format or when ID is not a non-negative integer. */
gboolean parse_camera_arg(const gchar *arg, gint *camera_id, gchar **uri);

/* Read a cameras file again (SIGHUP): the specs and settings go into the given containers. */
gboolean load_cameras_file(const gchar *path, GPtrArray *specs, GHashTable *settings);

#endif /* DS_CONFIG_H */
