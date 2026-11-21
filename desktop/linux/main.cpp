// GTK3 hosts the desktop's WebKitGTK 4.1 or 4.0 runtime. Only JSON crosses stdio.
#include <gtk/gtk.h>
#include <gio/gunixinputstream.h>
#include <json-glib/json-glib.h>
#include <webkit2/webkit2.h>
#include <dlfcn.h>
#include <cstdio>
#include <initializer_list>
#include <cstring>
#include <unistd.h>

#define TINGSU_WEBKIT_SYMBOLS(X) \
    X(webkit_web_view_get_type) X(webkit_web_context_get_default) \
    X(webkit_web_context_set_automation_allowed) X(webkit_application_info_new) \
    X(webkit_application_info_set_name) X(webkit_application_info_unref) X(webkit_automation_session_set_application_info) \
    X(webkit_web_view_get_user_content_manager) X(webkit_user_content_manager_register_script_message_handler) \
    X(webkit_web_view_get_settings) X(webkit_settings_set_allow_file_access_from_file_urls) \
    X(webkit_javascript_result_get_js_value) X(jsc_value_to_string) X(webkit_web_view_load_uri) \
    X(webkit_web_view_run_javascript) X(webkit_navigation_policy_decision_get_navigation_action) \
    X(webkit_navigation_action_get_request) X(webkit_uri_request_get_uri) X(webkit_policy_decision_ignore)

struct Engine {
#define DECLARE(name) decltype(&::name) name;
    TINGSU_WEBKIT_SYMBOLS(DECLARE)
#undef DECLARE
    bool load() {
        for (const char *soname : {"libwebkit2gtk-4.1.so.0", "libwebkit2gtk-4.0.so.37"}) {
            void *lib = dlopen(soname, RTLD_NOW | RTLD_LOCAL);
            if (!lib) continue;
#define RESOLVE(name) name = reinterpret_cast<decltype(name)>(dlsym(lib, #name)); if (!name) return false;
            TINGSU_WEBKIT_SYMBOLS(RESOLVE)
#undef RESOLVE
            return true;
        }
        return false;
    }
} engine;

static GtkWidget *window;
static WebKitWebView *view;
static gchar *entry_uri;

static void send_json(JsonBuilder *builder) {
    JsonNode *node = json_builder_get_root(builder);
    gchar *data = json_to_string(node, FALSE);
    puts(data);
    fflush(stdout);
    g_free(data);
    json_node_free(node);
    g_object_unref(builder);
}

static void on_js_message(WebKitUserContentManager *, WebKitJavascriptResult *result, gpointer) {
    gchar *text = engine.jsc_value_to_string(engine.webkit_javascript_result_get_js_value(result));
    // The private page supplies JSON.stringify(packet). Parse before forwarding to preserve framing.
    JsonParser *parser = json_parser_new();
    if (text && json_parser_load_from_data(parser, text, -1, nullptr)) {
        JsonNode *node = json_parser_get_root(parser);
        if (JSON_NODE_HOLDS_OBJECT(node)) {
            gchar *line = json_to_string(node, FALSE);
            puts(line);
            fflush(stdout);
            g_free(line);
        }
    }
    g_object_unref(parser);
    g_free(text);
}

static gboolean allow_navigation(WebKitWebView *, WebKitPolicyDecision *decision, WebKitPolicyDecisionType type, gpointer) {
    if (type == WEBKIT_POLICY_DECISION_TYPE_NEW_WINDOW_ACTION) {
        engine.webkit_policy_decision_ignore(decision);
        return TRUE;
    }
    if (type == WEBKIT_POLICY_DECISION_TYPE_NAVIGATION_ACTION) {
        auto *action = engine.webkit_navigation_policy_decision_get_navigation_action(
            reinterpret_cast<WebKitNavigationPolicyDecision *>(decision));
        const char *uri = engine.webkit_uri_request_get_uri(engine.webkit_navigation_action_get_request(action));
        if (g_strcmp0(uri, entry_uri) != 0) {
            engine.webkit_policy_decision_ignore(decision);
            return TRUE;
        }
    }
    return FALSE;
}

static const gchar *member(JsonObject *object, const gchar *name) {
    return json_object_has_member(object, name) ? json_object_get_string_member(object, name) : "";
}

static void add_filters(GtkFileChooser *chooser, const char *spec) {
    if (!spec || !*spec) return;
    // Accept the controller's space-separated patterns and optional labelled pattern groups.
    gchar **labels = g_strsplit(spec, ";;", -1);
    for (gchar **label = labels; *label; ++label) {
        const char *start = strrchr(*label, '('), *end = strrchr(*label, ')');
        bool labelled = start && end && end > start + 1;
        GtkFileFilter *filter = gtk_file_filter_new();
        gtk_file_filter_set_name(filter, *label);
        gchar *globs = labelled ? g_strndup(start + 1, end - start - 1) : g_strdup(*label);
        gchar **parts = g_strsplit(globs, " ", -1);
        for (gchar **part = parts; *part; ++part)
            if (**part) gtk_file_filter_add_pattern(filter, *part);
        gtk_file_chooser_add_filter(chooser, filter);
        g_strfreev(parts);
        g_free(globs);
    }
    g_strfreev(labels);
}

enum class DialogKind { Open, Save, Confirm, Message };
struct DialogReply { gint64 id; DialogKind kind; };

static void reply_dialog(GtkDialog *widget, gint response, gpointer data) {
    auto *request = static_cast<DialogReply *>(data);
    JsonBuilder *result = json_builder_new();
    json_builder_begin_object(result);
    json_builder_set_member_name(result, "type");
    json_builder_add_string_value(result, "dialog-result");
    json_builder_set_member_name(result, "id");
    json_builder_add_int_value(result, request->id);
    json_builder_set_member_name(result, "value");
    if (request->kind == DialogKind::Open) {
        json_builder_begin_array(result);
        GSList *files = response == GTK_RESPONSE_ACCEPT ? gtk_file_chooser_get_filenames(GTK_FILE_CHOOSER(widget)) : nullptr;
        for (GSList *file = files; file; file = file->next)
            json_builder_add_string_value(result, static_cast<gchar *>(file->data));
        g_slist_free_full(files, g_free);
        json_builder_end_array(result);
    } else if (request->kind == DialogKind::Save && response == GTK_RESPONSE_ACCEPT) {
        gchar *file = gtk_file_chooser_get_filename(GTK_FILE_CHOOSER(widget));
        if (file) json_builder_add_string_value(result, file);
        else json_builder_add_null_value(result);
        g_free(file);
    } else if (request->kind == DialogKind::Confirm) {
        json_builder_add_boolean_value(result, response == GTK_RESPONSE_YES);
    } else {
        json_builder_add_null_value(result);
    }
    json_builder_end_object(result);
    send_json(result);
    gtk_widget_destroy(GTK_WIDGET(widget));
}

static void dialog(JsonObject *object) {
    const char *kind = member(object, "kind");
    const char *title = member(object, "title");
    GtkWidget *widget;
    DialogKind operation;
    if (g_str_equal(kind, "open-files") || g_str_equal(kind, "save-file")) {
        bool open = g_str_equal(kind, "open-files");
        operation = open ? DialogKind::Open : DialogKind::Save;
        widget = gtk_file_chooser_dialog_new(title, GTK_WINDOW(window),
            open ? GTK_FILE_CHOOSER_ACTION_OPEN : GTK_FILE_CHOOSER_ACTION_SAVE, "取消", GTK_RESPONSE_CANCEL,
            open ? "打开" : "保存", GTK_RESPONSE_ACCEPT, nullptr);
        GtkFileChooser *chooser = GTK_FILE_CHOOSER(widget);
        gtk_file_chooser_set_select_multiple(chooser, open);
        if (!open) gtk_file_chooser_set_do_overwrite_confirmation(chooser, TRUE);
        const gchar *path = member(object, "path");
        if (path && *path) {
            if (g_file_test(path, G_FILE_TEST_IS_DIR)) gtk_file_chooser_set_current_folder(chooser, path);
            else if (!open) {
                gchar *folder = g_path_get_dirname(path), *name = g_path_get_basename(path);
                if (g_path_is_absolute(folder)) gtk_file_chooser_set_current_folder(chooser, folder);
                gtk_file_chooser_set_current_name(chooser, name);
                g_free(folder);
                g_free(name);
            }
        }
        add_filters(chooser, member(object, "filter"));
        gtk_dialog_set_default_response(GTK_DIALOG(widget), GTK_RESPONSE_ACCEPT);
    } else {
        bool confirm = g_str_equal(kind, "confirm");
        operation = confirm ? DialogKind::Confirm : DialogKind::Message;
        widget = gtk_message_dialog_new(GTK_WINDOW(window), GTK_DIALOG_MODAL,
            confirm ? GTK_MESSAGE_QUESTION : GTK_MESSAGE_INFO, confirm ? GTK_BUTTONS_YES_NO : GTK_BUTTONS_OK,
            "%s", member(object, "message"));
        gtk_window_set_title(GTK_WINDOW(widget), title);
        gtk_dialog_set_default_response(GTK_DIALOG(widget), confirm ? GTK_RESPONSE_NO : GTK_RESPONSE_OK);
    }
    gtk_window_set_destroy_with_parent(GTK_WINDOW(widget), TRUE);
    gtk_window_set_modal(GTK_WINDOW(widget), TRUE);
    auto *reply = new DialogReply{json_object_get_int_member(object, "id"), operation};
    g_signal_connect_data(widget, "response", G_CALLBACK(reply_dialog), reply,
        [](gpointer p, GClosure *) { delete static_cast<DialogReply *>(p); }, GConnectFlags(0));
    gtk_widget_show(widget);
}

static void read_packet(GObject *source, GAsyncResult *result, gpointer) {
    gsize length = 0;
    gchar *line = g_data_input_stream_read_line_finish(G_DATA_INPUT_STREAM(source), result, &length, nullptr);
    if (!line) { gtk_main_quit(); return; }
    JsonParser *parser = json_parser_new();
    bool quit = false;
    if (json_parser_load_from_data(parser, line, length, nullptr) && JSON_NODE_HOLDS_OBJECT(json_parser_get_root(parser))) {
        JsonObject *packet = json_node_get_object(json_parser_get_root(parser));
        const char *type = member(packet, "type");
        if (g_str_equal(type, "signal")) {
            gchar *script = g_strdup_printf("window.tingsuReceive(%s)", line);
            engine.webkit_web_view_run_javascript(view, script, nullptr, nullptr, nullptr);
            g_free(script);
        } else if (g_str_equal(type, "dialog")) {
            dialog(packet);
        } else if (g_str_equal(type, "quit")) quit = true;
    }
    g_object_unref(parser);
    g_free(line);
    if (quit) gtk_main_quit();
    else g_data_input_stream_read_line_async(G_DATA_INPUT_STREAM(source), G_PRIORITY_DEFAULT, nullptr, read_packet, nullptr);
}

static gboolean close_requested(GtkWidget *, GdkEvent *, gpointer) {
    puts("{\"type\":\"close-request\"}");
    fflush(stdout);
    return TRUE;
}

static WebKitWebView *automation_view(WebKitAutomationSession *, gpointer) { return view; }

static void start_automation(WebKitWebContext *, WebKitAutomationSession *session, gpointer) {
    WebKitApplicationInfo *info = engine.webkit_application_info_new();
    engine.webkit_application_info_set_name(info, "Tingsu");
    engine.webkit_automation_session_set_application_info(session, info);
    engine.webkit_application_info_unref(info);
    g_signal_connect(session, "create-web-view", G_CALLBACK(automation_view), nullptr);
}

int main(int argc, char **argv) {
    if (argc != 2) { fputs("Usage: tingsu-webview /absolute/path/to/index.html\n", stderr); return 2; }
    g_set_prgname("tingsu");
    g_set_application_name("听序");
    if (!gtk_init_check(&argc, &argv)) { fputs("无法连接桌面图形会话。\n", stderr); return 1; }
    if (!engine.load()) {
        const char *help = "请安装 WebKitGTK 4.1（或 4.0）运行库后重新启动听序。\n"
                           "Debian / Ubuntu: libwebkit2gtk-4.1-0\nFedora: webkit2gtk4.1\nArch: webkit2gtk-4.1";
        fputs(help, stderr);
        GtkWidget *message = gtk_message_dialog_new(nullptr, GTK_DIALOG_MODAL, GTK_MESSAGE_ERROR, GTK_BUTTONS_OK, "%s", help);
        gtk_dialog_run(GTK_DIALOG(message));
        gtk_widget_destroy(message);
        return 1;
    }
    entry_uri = g_filename_to_uri(argv[1], nullptr, nullptr);
    if (!entry_uri) return 2;
    bool automation = g_strcmp0(g_getenv("TINGSU_WEBVIEW_AUTOMATION"), "1") == 0;
    WebKitWebContext *context = engine.webkit_web_context_get_default();
    if (automation) {
        engine.webkit_web_context_set_automation_allowed(context, TRUE);
        g_signal_connect(context, "automation-started", G_CALLBACK(start_automation), nullptr);
    }
    view = static_cast<WebKitWebView *>(g_object_new(engine.webkit_web_view_get_type(),
        "web-context", context, "is-controlled-by-automation", automation, nullptr));
    WebKitUserContentManager *manager = engine.webkit_web_view_get_user_content_manager(view);
    g_signal_connect(manager, "script-message-received::tingsu", G_CALLBACK(on_js_message), nullptr);
    if (!engine.webkit_user_content_manager_register_script_message_handler(manager, "tingsu")) return 1;
    engine.webkit_settings_set_allow_file_access_from_file_urls(engine.webkit_web_view_get_settings(view), FALSE);
    g_signal_connect(view, "decide-policy", G_CALLBACK(allow_navigation), nullptr);
    window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window), "听序 · 会议工作台");
    GdkDisplay *display = gtk_widget_get_display(window);
    GdkMonitor *monitor = gdk_display_get_primary_monitor(display);
    if (!monitor) monitor = gdk_display_get_monitor(display, 0);
    GdkRectangle bounds = {0, 0, 1440, 960};
    if (monitor) gdk_monitor_get_workarea(monitor, &bounds);
    gtk_window_set_default_size(GTK_WINDOW(window), MIN(1420, bounds.width * 92 / 100),
                                MIN(940, bounds.height * 9 / 10));
    gchar *folder = g_path_get_dirname(argv[1]);
    gchar *icon = g_build_filename(folder, "tingsu.svg", nullptr);
    gtk_window_set_icon_from_file(GTK_WINDOW(window), icon, nullptr);
    g_free(folder); g_free(icon);
    gtk_container_add(GTK_CONTAINER(window), GTK_WIDGET(view));
    g_signal_connect(window, "delete-event", G_CALLBACK(close_requested), nullptr);
    gtk_widget_show_all(window);
    engine.webkit_web_view_load_uri(view, entry_uri);
    GInputStream *stream = g_unix_input_stream_new(STDIN_FILENO, FALSE);
    GDataInputStream *input = g_data_input_stream_new(stream);
    g_data_input_stream_read_line_async(input, G_PRIORITY_DEFAULT, nullptr, read_packet, nullptr);
    gtk_main();
    gtk_widget_destroy(window);
    g_object_unref(input);
    g_object_unref(stream);
    g_free(entry_uri);
    return 0;
}
