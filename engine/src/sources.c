/*
 * sources.c - the camera slots and their life in the running pipeline. A slot holds one camera (SourceCtx);
 * there are as many slots as nvstreammux has batch places. See sources.h.
 */
#include "sources.h"

#include <stdatomic.h>
#include <string.h>

#include "config.h"
#include "stream_out.h"

/*
 * Put a camera into a free slot: its settings, its EventManager and fresh counters. `spec` is "ID=URI";
 * `settings` is the camera-id -> CameraSettings table of the cameras file (NULL for a --camera camera).
 * The slot is not active yet. Prints why (never the address) and returns FALSE if the spec is bad or
 * the camera id is already in use. Call with app->slot_lock held once the pipeline runs.
 */
static gboolean slot_fill(AppCtx *app, SourceCtx *src, const gchar *spec, gboolean from_file,
                          const GHashTable *settings)
{
    gint camera_id = 0;
    gchar *uri = NULL;
    if (!parse_camera_arg(spec, &camera_id, &uri)) {
        g_printerr("Source #%u has a bad format, expected ID=URI with ID an integer >= 0\n", src->source_id);
        return FALSE;
    }
    for (guint j = 0; j < app->num_sources; j++) {
        if (app->sources[j].uri != NULL && app->sources[j].camera_id == camera_id) {
            g_printerr("camera_id %d is declared more than once\n", camera_id);
            g_free(uri);
            return FALSE;
        }
    }

    src->camera_id = camera_id;
    src->uri = uri;
    src->from_file = from_file;
    gdouble conf, absence;
    gboolean ai;
    settings_of(settings, camera_id, &conf, &absence, &ai);
    src->conf_threshold = conf;
    src->ai_enabled = ai;
    src->absence_timeout = absence;
    src->em_timeout = absence;
    event_manager_init(&src->events, absence);
    src->track_frames = g_hash_table_new(g_direct_hash, g_direct_equal);
    src->pose_hist = g_hash_table_new_full(g_direct_hash, g_direct_equal, NULL, (GDestroyNotify)pose_history_free);
    src->pose_count = 0;
    src->skeleton_overflow = 0;

    BehaviorSettings bs;
    behavior_settings_of(settings, camera_id, &bs);
    src->behavior = behavior_new(&bs.cfg, MUXER_WIDTH, MUXER_HEIGHT);
    src->beh_people = g_new0(BehaviorPerson, BEH_MAX_PEOPLE);
    if (src->behavior == NULL || behavior_set_zones(src->behavior, bs.zones, bs.num_zones) != 0) {
        g_printerr("Could not set up the behaviour rules of camera %d\n", camera_id);
        return FALSE;
    }
    src->beh_pending = bs;
    src->fall_net_on = bs.cfg.fall && bs.cfg.fall_net != 0.0 && src->app->fallnet != NULL;
    memcpy(src->draw_zones, bs.zones, sizeof(src->draw_zones));
    src->num_draw_zones = bs.num_zones;
    src->alert_labels = g_hash_table_new_full(g_direct_hash, g_direct_equal, NULL, g_free);
    atomic_store_explicit(&src->beh_dirty, FALSE, memory_order_relaxed);
    atomic_store_explicit(&src->beh_active, behavior_settings_active(&bs), memory_order_relaxed);
    memset(src->behavior_events, 0, sizeof(src->behavior_events));

    gchar *scheme = g_uri_parse_scheme(src->uri);
    src->is_rtsp = (scheme != NULL && g_ascii_strcasecmp(scheme, "rtsp") == 0);
    g_free(scheme);

    src->state = SOURCE_RUNNING;
    src->lost_us = 0;
    src->reconnects = 0;
    atomic_store_explicit(&src->last_frame_us, g_get_monotonic_time(), memory_order_relaxed);
    atomic_store_explicit(&src->reanchor, FALSE, memory_order_relaxed);
    atomic_store_explicit(&src->eos_seen, FALSE, memory_order_relaxed);
    atomic_store_explicit(&src->frame_count, 0, memory_order_relaxed);
    atomic_store_explicit(&src->people_now, 0, memory_order_relaxed);
    src->fps_prev_frames = 0;
    src->hb_prev_frames = 0;
    src->object_count = 0;
    src->untracked_count = 0;
    src->stream_start_ns = 0;
    src->have_stream_start = FALSE;
    src->event_count = 0;
    src->event_errors = 0;
    src->pub_failed = FALSE;
    src->pub_failed_us = 0;
    src->pub_failures = 0;
    return TRUE;
}

/* Empty a slot (the counterpart of slot_fill). The stream branch and the mutex stay: they belong to the slot. */
static void slot_clear(SourceCtx *src)
{
    g_free(src->uri);
    src->uri = NULL;
    event_manager_destroy(&src->events);
    if (src->track_frames != NULL) {
        g_hash_table_destroy(src->track_frames);
        src->track_frames = NULL;
    }
    g_clear_pointer(&src->pose_hist, g_hash_table_destroy);
    if (src->app != NULL && src->app->fallnet != NULL)
        fallnet_forget_source(src->app->fallnet, (int)src->source_id);
    src->fall_net_on = FALSE;
    g_clear_pointer(&src->behavior, behavior_free);
    g_clear_pointer(&src->beh_people, g_free);
    g_clear_pointer(&src->alert_labels, g_hash_table_destroy);
    atomic_store_explicit(&src->beh_active, FALSE, memory_order_relaxed);
}

/*
 * Build SourceCtx[slots] and fill the first slots from the "ID=URI" specs (--camera first, then the cameras
 * file). There are max(cameras, --max-cameras) slots. Returns FALSE (and prints why) on error.
 */
gboolean init_sources(AppCtx *app, const GPtrArray *specs)
{
    app->num_sources = MAX(specs->len, (guint)MAX(opt_max_cameras, 0));
    app->sources = g_new0(SourceCtx, app->num_sources);
    const guint n_cli = opt_cameras != NULL ? g_strv_length(opt_cameras) : 0;   /* --camera comes first in specs */

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        src->app = app;
        src->source_id = i;
        g_mutex_init(&src->pub_lock);
        g_mutex_init(&src->beh_lock);
        src->pub_pts_base = GST_CLOCK_TIME_NONE;
    }
    for (guint i = 0; i < specs->len; i++) {
        /* A camera from the cameras file may carry its own settings; every other one gets the defaults. */
        gboolean from_file = (i >= n_cli);
        if (!slot_fill(app, &app->sources[i], g_ptr_array_index(specs, i), from_file,
                       from_file ? file_settings : NULL))
            return FALSE;
    }
    return TRUE;
}

void free_sources(AppCtx *app)
{
    fallnet_free(app->fallnet);
    app->fallnet = NULL;
    g_clear_pointer(&app->fallnet_window, g_free);
    for (guint i = 0; i < app->num_sources; i++) {
        g_free(app->sources[i].uri);
        event_manager_destroy(&app->sources[i].events);
        if (app->sources[i].track_frames != NULL)
            g_hash_table_destroy(app->sources[i].track_frames);
        g_clear_pointer(&app->sources[i].pose_hist, g_hash_table_destroy);
        g_clear_pointer(&app->sources[i].behavior, behavior_free);
        g_clear_pointer(&app->sources[i].beh_people, g_free);
        g_clear_pointer(&app->sources[i].alert_labels, g_hash_table_destroy);
        if (app->sources[i].mux_sinkpad != NULL)
            gst_object_unref(app->sources[i].mux_sinkpad);
        if (app->sources[i].demux_pad != NULL)
            gst_object_unref(app->sources[i].demux_pad);
        g_mutex_clear(&app->sources[i].pub_lock);
        g_mutex_clear(&app->sources[i].beh_lock);
    }
    g_free(app->sources);
    app->sources = NULL;
    app->num_sources = 0;
}

/* The source whose bin contains the element that posted `msg`, or NULL (nvinfer, the muxer...).
 * `debug` is the debug text of the message, or NULL. A message that is handled late may come from an
 * element that the bin already took apart (it has no parent then), and only the element path in the
 * debug text, ".../GstDsNvUriSrcBin:source-<id>/...", still says where it came from. */
SourceCtx *source_of_message(AppCtx *app, GstMessage *msg, const gchar *debug)
{
    GstObject *obj = GST_MESSAGE_SRC(msg) != NULL ? gst_object_ref(GST_MESSAGE_SRC(msg)) : NULL;
    while (obj != NULL) {
        for (guint i = 0; i < app->num_sources; i++) {
            if (app->sources[i].active && GST_OBJECT(app->sources[i].bin) == obj) {
                gst_object_unref(obj);
                return &app->sources[i];
            }
        }
        GstObject *parent = gst_object_get_parent(obj);
        gst_object_unref(obj);
        obj = parent;
    }
    for (guint i = 0; debug != NULL && i < app->num_sources; i++) {
        if (!app->sources[i].active)
            continue;
        gchar *needle = g_strdup_printf("/GstDsNvUriSrcBin:source-%u/", app->sources[i].source_id);
        gboolean found = strstr(debug, needle) != NULL;
        g_free(needle);
        if (found)
            return &app->sources[i];
    }
    return NULL;
}

/*
 * An RTSP source lost its camera: an error on the bus, no frame for --rtsp-timeout seconds, or the
 * stream ended. The engine only notes it (state RECONNECTING, a log line, a new time anchor for the
 * events): nvurisrcbin, told to with rtsp-reconnect-interval, restarts its RTSP client by itself
 * until the camera answers. (Switching the whole bin off and on does not work: it then adds its
 * "vsrc_0" pad a second time and fails.) Only this source is affected; the others keep running
 * because nvstreammux, with live-source set, batches whatever frames it has after a timeout.
 * Called from the main thread.
 */
void rtsp_reconnect(SourceCtx *src, const char *why)
{
    if (src->state != SOURCE_RUNNING)
        return;
    g_printerr("source_id=%u camera_id=%d: %s, reconnecting\n", src->source_id, src->camera_id, why);
    src->state = SOURCE_RECONNECTING;
    src->lost_us = g_get_monotonic_time();
    src->reconnects++;
    atomic_store_explicit(&src->reanchor, TRUE, memory_order_relaxed);
}

/* Timer (main thread): finds RTSP sources that went quiet without any error, and notes when a lost
 * source delivers frames again. */
gboolean watchdog_tick(gpointer data)
{
    AppCtx *app = (AppCtx *)data;
    gint64 now = g_get_monotonic_time();

    for (guint i = 0; i < app->num_sources; i++) {
        SourceCtx *src = &app->sources[i];
        if (!src->active || !src->is_rtsp)
            continue;
        gint64 last = atomic_load_explicit(&src->last_frame_us, memory_order_relaxed);
        if (src->state == SOURCE_RECONNECTING) {
            if (last > src->lost_us) {
                src->state = SOURCE_RUNNING;
                g_printerr("source_id=%u camera_id=%d: reconnected\n", src->source_id,
                           src->camera_id);
            }
        } else if (opt_rtsp_timeout > 0 && now - last > (gint64)opt_rtsp_timeout * G_USEC_PER_SEC) {
            rtsp_reconnect(src, "no frames");
        }
    }
    return G_SOURCE_CONTINUE;
}

/* Pad probe on the nvstreammux sink pad of a file source: notes that the file has ended. (An RTSP
 * nvurisrcbin swallows the EOS of its stream, so this is not installed for those.) Runs on the
 * streaming thread, once per source, and only sets a flag that the main thread reads. */
static GstPadProbeReturn mux_eos_probe(GstPad *pad, GstPadProbeInfo *info, gpointer data)
{
    (void)pad;
    SourceCtx *src = (SourceCtx *)data;
    GstEvent *event = GST_PAD_PROBE_INFO_EVENT(info);

    if (event != NULL && GST_EVENT_TYPE(event) == GST_EVENT_EOS &&
        !atomic_exchange_explicit(&src->eos_seen, TRUE, memory_order_acq_rel))
        g_print("source_id=%u camera_id=%d: ended (end of file)\n", src->source_id, src->camera_id);
    return GST_PAD_PROBE_OK;
}

static gboolean add_source(AppCtx *app, SourceCtx *src);
static void detach_source(AppCtx *app, SourceCtx *src);

/*
 * SIGHUP: read the cameras file again and bring the running engine in line with it, without stopping the
 * pipeline:
 *  - a camera that is in the file but not running is added (to a free slot: the engine has as many as
 *    --max-cameras, or as many cameras as it started with);
 *  - a running camera that left the file, or whose address changed, is taken out (the second case is a
 *    removal plus an addition);
 *  - the settings (conf_threshold, absence_timeout, ai_enabled) of every camera that stays are updated.
 * It is all or nothing for the list: if the file does not parse, names a camera twice, takes a camera id
 * a --camera camera has, needs more slots than are free or the engine writes --output, nothing at all is
 * changed and the engine says why (the engine manager restarts it for those). Cameras given with --camera
 * are not in the file and are left alone. Messages never contain an address.
 */
gboolean on_reload(gpointer data)
{
    AppCtx *app = (AppCtx *)data;

    if (opt_cameras_file == NULL) {
        g_print("reload: ignored, the engine was not started with --cameras-file\n");
        return G_SOURCE_CONTINUE;
    }

    GPtrArray *specs = g_ptr_array_new_with_free_func(g_free);
    GHashTable *settings = new_settings_table();
    gboolean ok = load_cameras_file(opt_cameras_file, specs, settings);
    gint *ids = g_new0(gint, specs->len + 1);
    gchar **uris = g_new0(gchar *, specs->len + 1);
    gboolean *keep = g_new0(gboolean, app->num_sources);   /* per slot: the camera stays as it is */
    gboolean *add = g_new0(gboolean, specs->len + 1);      /* per file entry: to be added */
    const char *refusal = NULL;

    for (guint i = 0; ok && i < specs->len; i++) {
        if (!parse_camera_arg(g_ptr_array_index(specs, i), &ids[i], &uris[i])) {
            ok = FALSE;
            break;
        }
        for (guint k = 0; k < i; k++)
            if (ids[k] == ids[i])
                refusal = "a camera is named twice in the cameras file";
    }

    guint adds = 0, removals = 0, free_slots = 0;
    for (guint i = 0; ok && refusal == NULL && i < specs->len; i++) {
        const SourceCtx *same_id = NULL;
        for (guint j = 0; j < app->num_sources; j++)
            if (app->sources[j].active && app->sources[j].camera_id == ids[i])
                same_id = &app->sources[j];
        if (same_id != NULL && !same_id->from_file) {
            refusal = "the file names a camera that was given with --camera";
        } else if (same_id != NULL && strcmp(same_id->uri, uris[i]) == 0) {
            keep[same_id->source_id] = TRUE;
        } else {
            add[i] = TRUE;
            adds++;
        }
    }
    for (guint j = 0; ok && refusal == NULL && j < app->num_sources; j++) {
        if (app->sources[j].uri == NULL)
            free_slots++;
        else if (app->sources[j].active && app->sources[j].from_file && !keep[j])
            removals++;
    }
    if (ok && refusal == NULL && (adds > 0 || removals > 0)) {
        if (opt_output != NULL)
            refusal = "cameras cannot be added or removed while --output is written";
        else if (adds > free_slots + removals)
            refusal = "there is no free place for the new cameras (see --max-cameras)";
    }

    if (!ok) {
        g_print("reload: the cameras file could not be used, nothing changed\n");
    } else if (refusal != NULL) {
        g_print("reload: nothing applied, %s (the engine has to be restarted for that)\n", refusal);
    } else {
        guint removed = 0, added = 0, changed = 0;

        for (guint j = 0; j < app->num_sources; j++) {
            SourceCtx *src = &app->sources[j];
            if (src->active && src->from_file && !keep[j]) {
                detach_source(app, src);
                removed++;
            }
        }
        for (guint i = 0; i < specs->len; i++) {
            if (!add[i])
                continue;
            SourceCtx *slot = NULL;   /* the free slot that has been free the longest: a frame of the previous camera may still be on its way */
            for (guint j = 0; j < app->num_sources; j++)
                if (app->sources[j].uri == NULL && (slot == NULL || app->sources[j].freed_us < slot->freed_us))
                    slot = &app->sources[j];
            if (slot == NULL)
                break;   /* cannot happen: counted above */
            g_mutex_lock(&app->slot_lock);
            gboolean filled = slot_fill(app, slot, g_ptr_array_index(specs, i), TRUE, settings);
            g_mutex_unlock(&app->slot_lock);
            if (!filled)
                continue;
            if (add_source(app, slot)) {
                added++;
                g_print("reload: camera_id=%d added in slot %u\n", slot->camera_id, slot->source_id);
            } else {
                g_printerr("reload: camera_id=%d could not be added\n", slot->camera_id);
                detach_source(app, slot);
            }
        }
        for (guint j = 0; j < app->num_sources; j++) {
            SourceCtx *src = &app->sources[j];
            if (!keep[j] || !src->active)
                continue;
            gdouble conf, absence;
            gboolean ai;
            settings_of(settings, src->camera_id, &conf, &absence, &ai);
            BehaviorSettings bs;
            behavior_settings_of(settings, src->camera_id, &bs);
            gboolean beh_changed = !behavior_settings_equal(&bs, &src->beh_pending);
            gboolean base_changed = !(conf == src->conf_threshold && absence == src->absence_timeout && ai == src->ai_enabled);
            if (!base_changed && !beh_changed)
                continue;
            if (base_changed) {
                src->conf_threshold = conf;
                src->absence_timeout = absence;
                src->ai_enabled = ai;
                g_print("reload: camera_id=%d conf_threshold=%.2f absence_timeout=%.1f ai=%s\n",
                        src->camera_id, conf, absence, ai ? "on" : "off");
            }
            if (beh_changed) {
                g_mutex_lock(&src->beh_lock);
                src->beh_pending = bs;
                g_mutex_unlock(&src->beh_lock);
                atomic_store(&src->beh_active, behavior_settings_active(&bs));
                atomic_store(&src->beh_dirty, TRUE);
                g_print("reload: camera_id=%d behaviors fall=%s fight=%s zones=%s, %zu zone(s)\n", src->camera_id,
                        bs.cfg.fall ? "on" : "off", bs.cfg.fight ? "on" : "off", bs.cfg.zones ? "on" : "off", bs.num_zones);
            }
            changed++;
        }
        g_print("reload: done, %u added, %u removed, %u with new settings\n", added, removed, changed);
    }

    for (guint i = 0; i < specs->len; i++)
        g_free(uris[i]);
    g_free(uris);
    g_free(ids);
    g_free(keep);
    g_free(add);
    g_hash_table_destroy(settings);
    g_ptr_array_free(specs, TRUE);
    return G_SOURCE_CONTINUE;
}

/* nvurisrcbin creates its video pad after it knows the stream: link it to the muxer pad that
 * was requested for this source. Audio (and any other non-video pad) is ignored. Called again
 * if the bin makes a new pad: the old link is dropped first. */
static void on_source_pad_added(GstElement *bin, GstPad *new_pad, gpointer user_data)
{
    SourceCtx *src = (SourceCtx *)user_data;

    GstCaps *caps = gst_pad_get_current_caps(new_pad);
    if (caps == NULL)
        caps = gst_pad_query_caps(new_pad, NULL);
    const gchar *media = gst_structure_get_name(gst_caps_get_structure(caps, 0));
    gboolean is_video = (media != NULL && g_str_has_prefix(media, "video"));
    gst_caps_unref(caps);
    if (!is_video)
        return;

    GstPad *old_peer = gst_pad_get_peer(src->mux_sinkpad);
    if (old_peer != NULL) {
        gst_pad_unlink(old_peer, src->mux_sinkpad);
        gst_object_unref(old_peer);
    }
    if (gst_pad_link(new_pad, src->mux_sinkpad) != GST_PAD_LINK_OK)
        GST_ELEMENT_ERROR(bin, CORE, PAD, (NULL),
                          ("Could not link source_id=%u to nvstreammux", src->source_id));
}

/*
 * Put the source bin of a camera into the pipeline: nvurisrcbin, and the muxer pad "sink_<source_id>" it is
 * linked to when it has found its video (on_source_pad_added). The bin starts with the pipeline, or with
 * add_source() when the pipeline already runs. Returns FALSE (after printing why) on error; detach_source()
 * undoes whatever was done.
 */
gboolean attach_source(AppCtx *app, GstElement *pipeline, SourceCtx *src)
{
    gchar *bin_name = g_strdup_printf("source-%u", src->source_id);
    src->bin = make_element(pipeline, "nvurisrcbin", bin_name);
    g_free(bin_name);
    if (src->bin == NULL)
        return FALSE;
    g_object_set(src->bin, "uri", src->uri, NULL);
    if (src->is_rtsp)
        g_object_set(src->bin, "rtsp-reconnect-interval", (guint)opt_rtsp_timeout,
                     "rtsp-reconnect-attempts", -1, NULL);

    /* Request "sink_<source_id>" so that source_id == the muxer pad index == the
     * frame_meta->source_id seen in the probe. */
    gchar *pad_name = g_strdup_printf("sink_%u", src->source_id);
    src->mux_sinkpad = gst_element_request_pad_simple(app->streammux, pad_name);
    if (src->mux_sinkpad == NULL)
        g_printerr("Could not request pad '%s' from nvstreammux\n", pad_name);
    g_free(pad_name);
    if (src->mux_sinkpad == NULL)
        return FALSE;
    if (!src->is_rtsp)
        gst_pad_add_probe(src->mux_sinkpad, GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM, mux_eos_probe, src, NULL);

    g_signal_connect(src->bin, "pad-added", G_CALLBACK(on_source_pad_added), src);
    atomic_store(&src->active, TRUE);
    return TRUE;
}

/*
 * The encoding branch of a slot, once. It stays in the pipeline when the camera goes: nvstreamdemux cannot
 * give the same pad out twice, so the next camera of the slot uses the branch that is there (its encoder idles
 * meanwhile). Returns TRUE and sets *built when it had to be made.
 */
gboolean attach_branch(AppCtx *app, GstElement *pipeline, SourceCtx *src, gboolean *built)
{
    *built = FALSE;
    if (app->demux == NULL || src->stream_bin != NULL)
        return TRUE;
    if (!build_stream_branch(pipeline, app->demux, src, app->live))
        return FALSE;
    *built = TRUE;
    return TRUE;
}

/*
 * Add a camera to the running pipeline (main thread). The slot has been filled with slot_fill(). The stream
 * branch is brought up first, so that the first frames have somewhere to go, then the source.
 */
static gboolean add_source(AppCtx *app, SourceCtx *src)
{
    gboolean built = FALSE;
    if (!attach_branch(app, app->pipeline, src, &built))
        return FALSE;
    if (built && !gst_element_sync_state_with_parent(src->stream_bin))
        return FALSE;
    if (!attach_source(app, app->pipeline, src))
        return FALSE;
    if (!gst_element_sync_state_with_parent(src->bin)) {
        g_printerr("Could not start the source of camera %d\n", src->camera_id);
        return FALSE;
    }
    atomic_store_explicit(&src->last_frame_us, g_get_monotonic_time(), memory_order_relaxed);
    if (opt_stream_url != NULL && !pub_create(src)) {   /* tried again by the timer */
        src->pub_failed = TRUE;
        src->pub_failed_us = g_get_monotonic_time();
    }
    return TRUE;
}

/*
 * Take a camera out of the running pipeline and free its slot (main thread). Safe on a camera that is only
 * partly added. The muxer pad is released the way NVIDIA's runtime add/delete sample does: the source is
 * stopped, the pad gets a flush-stop, then it is released. The stream branch stays (see attach_branch).
 */
static void detach_source(AppCtx *app, SourceCtx *src)
{
    atomic_store(&src->active, FALSE);   /* the probes and timers leave it alone from now on */
    if (opt_stream_url != NULL) {
        pub_destroy(src);
        src->pub_failed = FALSE;
    }
    if (src->bin != NULL)
        gst_element_set_state(src->bin, GST_STATE_NULL);
    if (src->mux_sinkpad != NULL) {
        GstPad *peer = gst_pad_get_peer(src->mux_sinkpad);
        if (peer != NULL) {
            gst_pad_unlink(peer, src->mux_sinkpad);
            gst_object_unref(peer);
        }
        gst_pad_send_event(src->mux_sinkpad, gst_event_new_flush_stop(FALSE));
        gst_element_release_request_pad(app->streammux, src->mux_sinkpad);
        gst_object_unref(src->mux_sinkpad);
        src->mux_sinkpad = NULL;
    }
    if (src->bin != NULL) {
        gst_bin_remove(GST_BIN(app->pipeline), src->bin);
        src->bin = NULL;
    }
    g_print("source_id=%u camera_id=%d: removed (frames=%" G_GUINT64_FORMAT " events=%" G_GUINT64_FORMAT ")\n",
            src->source_id, src->camera_id, (guint64)src->frame_count, src->event_count);
    g_mutex_lock(&app->slot_lock);   /* not while the probe is working on a batch */
    slot_clear(src);
    g_mutex_unlock(&app->slot_lock);
    src->freed_us = g_get_monotonic_time();
}
