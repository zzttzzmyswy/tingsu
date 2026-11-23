// A private process holds the portal grant and PipeWire FD. On stdin EOF the
// process exits even if a chooser is still open and D-Bus releases its requests.
#include <gio/gio.h>
#include <gio/gunixfdlist.h>
#include <gst/app/gstappsink.h>
#include <gst/video/video.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define BUS "org.freedesktop.portal.Desktop"
#define BASE "/org/freedesktop/portal/desktop"
#define CAST "org.freedesktop.portal.ScreenCast"
#define REQUEST "org.freedesktop.portal.Request"
#define SESSION "org.freedesktop.portal.Session"
static GDBusConnection *bus;
static gboolean closed, want_frame;
static gchar *session;
static void fail(const char *text) {
    g_printerr("%s\n", text);
    exit(1);
}
static void check(GError *error) {
    if (error)
        fail(error->message);
}
static void pump(void) {
    while (g_main_context_iteration(NULL, FALSE))
        ;
    if (closed || g_dbus_connection_is_closed(bus))
        fail("桌面已结束画面共享");
    struct pollfd watch = {STDIN_FILENO, POLLIN | POLLHUP, 0};
    if (poll(&watch, 1, 0) > 0) {
        char input[64];
        ssize_t n = read(STDIN_FILENO, input, sizeof(input));
        if (n <= 0)
            exit(0);
        want_frame = TRUE;
    }
}
static GVariant *call(const gchar *iface, const gchar *method, GVariant *args) {
    GError *error = NULL;
    GVariant *reply = g_dbus_connection_call_sync(bus, BUS, BASE, iface, method, args, NULL,
                                                  G_DBUS_CALL_FLAGS_NONE, 5000, NULL, &error);
    check(error);
    return reply;
}
// Subscribe before the call so even a synchronous portal response is retained.
static void response(GDBusConnection *c, const gchar *sender, const gchar *path, const gchar *iface,
                     const gchar *signal, GVariant *params, gpointer user) {
    g_hash_table_replace(user, g_strdup(path), g_variant_ref(params));
}
static GVariant *request(const gchar *method, GVariant *args) {
    GHashTable *results =
        g_hash_table_new_full(g_str_hash, g_str_equal, g_free, (GDestroyNotify)g_variant_unref);
    guint sub = g_dbus_connection_signal_subscribe(bus, BUS, REQUEST, "Response", NULL, NULL, 0,
                                                   response, results, NULL);
    GVariant *reply = call(CAST, method, args);
    const gchar *path = NULL;
    g_variant_get(reply, "(&o)", &path);
    GVariant *params;
    while (!(params = g_hash_table_lookup(results, path))) {
        pump();
        g_usleep(20000);
    }
    guint status;
    GVariant *data;
    g_variant_get(params, "(u@a{sv})", &status, &data);
    g_dbus_connection_signal_unsubscribe(bus, sub);
    g_hash_table_unref(results);
    g_variant_unref(reply);
    if (status == 1)
        fail("已取消选择共享画面");
    if (status)
        fail("桌面门户未能开始共享");
    return data;
}
static void session_closed(GDBusConnection *c, const gchar *s, const gchar *p, const gchar *i,
                           const gchar *n, GVariant *v, gpointer d) {
    closed = TRUE;
}
static void options(GVariantBuilder *b) {
    g_variant_builder_init(b, G_VARIANT_TYPE_VARDICT);
    g_autofree gchar *token = g_strdup_printf("tingsu_%x_%x", getpid(), g_random_int());
    g_variant_builder_add(b, "{sv}", "handle_token", g_variant_new_string(token));
}
static guint choose(guint64 *serial) {
    GVariantBuilder b;
    options(&b);
    g_autofree gchar *token = g_strdup_printf("session_%x_%x", getpid(), g_random_int());
    g_variant_builder_add(&b, "{sv}", "session_handle_token", g_variant_new_string(token));
    GVariant *created = request("CreateSession", g_variant_new("(a{sv})", &b));
    // The ScreenCast portal defines session_handle as s for historical reasons.
    if (!g_variant_lookup(created, "session_handle", "s", &session) &&
        !g_variant_lookup(created, "session_handle", "o", &session))
        fail("桌面门户未返回会话");
    g_variant_unref(created);
    g_dbus_connection_signal_subscribe(bus, BUS, SESSION, "Closed", session, NULL, 0,
                                       session_closed, NULL, NULL);
    GVariant *props = call("org.freedesktop.DBus.Properties", "Get",
                           g_variant_new("(ss)", CAST, "AvailableSourceTypes"));
    GVariant *types;
    g_variant_get(props, "(v)", &types);
    guint source_types = g_variant_get_uint32(types) & 3;
    g_variant_unref(types);
    g_variant_unref(props);
    if (!source_types)
        fail("桌面未提供窗口或显示器");
    options(&b);
    g_variant_builder_add(&b, "{sv}", "types", g_variant_new_uint32(source_types));
    g_variant_builder_add(&b, "{sv}", "multiple", g_variant_new_boolean(FALSE));
    g_variant_unref(request("SelectSources", g_variant_new("(oa{sv})", session, &b)));
    options(&b);
    GVariant *started = request("Start", g_variant_new("(osa{sv})", session, "", &b));
    GVariant *streams = g_variant_lookup_value(started, "streams", G_VARIANT_TYPE("a(ua{sv})"));
    if (!streams || !g_variant_n_children(streams))
        fail("桌面门户未返回视频流");
    guint node;
    GVariant *properties;
    g_variant_get_child(streams, 0, "(u@a{sv})", &node, &properties);
    g_variant_lookup(properties, "pipewire-serial", "t", serial);
    g_variant_unref(properties);
    g_variant_unref(streams);
    g_variant_unref(started);
    return node;
}
static int remote(void) {
    GError *error = NULL;
    GUnixFDList *fds = NULL;
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE_VARDICT);
    GVariant *v = g_dbus_connection_call_with_unix_fd_list_sync(
        bus, BUS, BASE, CAST, "OpenPipeWireRemote", g_variant_new("(oa{sv})", session, &b),
        G_VARIANT_TYPE("(h)"), G_DBUS_CALL_FLAGS_NONE, 5000, NULL, &fds, NULL, &error);
    check(error);
    gint handle;
    g_variant_get(v, "(h)", &handle);
    int fd = g_unix_fd_list_get(fds, handle, &error);
    check(error);
    g_variant_unref(v);
    g_object_unref(fds);
    return fd;
}
static void send_jpeg(GstSample *frame, GstCaps *jpeg) {
    GError *error = NULL;
    GstSample *encoded = gst_video_convert_sample(frame, jpeg, 3 * GST_SECOND, &error);
    check(error);
    if (!encoded)
        fail("无法转换共享画面");
    GstMapInfo map;
    GstBuffer *buffer = gst_sample_get_buffer(encoded);
    if (!gst_buffer_map(buffer, &map, GST_MAP_READ))
        fail("无法读取图像");
    if (map.size > 64 * 1024 * 1024)
        fail("共享画面过大");
    guint32 n = GUINT32_TO_BE((guint32)map.size);
    if (fwrite(&n, 1, 4, stdout) != 4 || fwrite(map.data, 1, map.size, stdout) != map.size ||
        fflush(stdout))
        exit(0);
    gst_buffer_unmap(buffer, &map);
    gst_sample_unref(encoded);
}
int main(int argc, char **argv) {
    GError *error = NULL;
    gst_init(&argc, &argv);
    bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &error);
    check(error);
    guint64 serial = 0;
    guint node = choose(&serial);
    int fd = remote();
    GstElement *pipe = gst_parse_launch(
        "pipewiresrc name=desktop ! video/x-raw ! identity drop-buffer-flags=corrupted ! appsink "
        "name=frames sync=false max-buffers=1 drop=true enable-last-sample=false",
        &error);
    check(error);
    gst_pipeline_use_clock(GST_PIPELINE(pipe), NULL);
    GstElement *src = gst_bin_get_by_name(GST_BIN(pipe), "desktop");
    g_object_set(src, "fd", fd, NULL);
    GObjectClass *klass = G_OBJECT_GET_CLASS(src);
    // KWin offers 2-4 buffers for a window stream. A higher floor leaves the two
    // ranges disjoint, so spa_pod_filter rejects them before the first frame
    // ("error alloc buffers: Invalid argument").
    if (g_object_class_find_property(klass, "min-buffers"))
        g_object_set(src, "min-buffers", 2, NULL);
    if (g_object_class_find_property(klass, "always-copy"))
        g_object_set(src, "always-copy", TRUE, NULL);
    if (serial && g_object_class_find_property(klass, "target-object")) {
        g_autofree gchar *id = g_strdup_printf("%" G_GUINT64_FORMAT, serial);
        g_object_set(src, "target-object", id, NULL);
    } else {
        g_autofree gchar *id = g_strdup_printf("%u", node);
        g_object_set(src, "path", id, NULL);
    }
    GstAppSink *sink = GST_APP_SINK(gst_bin_get_by_name(GST_BIN(pipe), "frames"));
    GstBus *messages = gst_element_get_bus(pipe);
    GstCaps *jpeg = gst_caps_from_string("image/jpeg");
    if (gst_element_set_state(pipe, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE)
        fail("无法启动 PipeWire 视频流");
    GstSample *latest = NULL;
    gint64 start = g_get_monotonic_time(), ready = 0;
    for (;;) {
        pump();
        GstMessage *msg = gst_bus_pop_filtered(messages, GST_MESSAGE_ERROR | GST_MESSAGE_EOS);
        if (msg) {
            if (GST_MESSAGE_TYPE(msg) == GST_MESSAGE_ERROR) {
                gst_message_parse_error(msg, &error, NULL);
                check(error);
            }
            fail("共享视频流已结束");
        }
        GstSample *sample = gst_app_sink_try_pull_sample(sink, 100 * GST_MSECOND);
        if (sample) {
            if (latest)
                gst_sample_unref(latest);
            else
                ready = g_get_monotonic_time() + 350000;
            latest = sample;
        }
        if (!latest && g_get_monotonic_time() - start > 15000000)
            fail("等待共享窗口首帧超时");
        if (latest && want_frame && g_get_monotonic_time() >= ready) {
            want_frame = FALSE;
            send_jpeg(latest, jpeg);
        }
    }
}
