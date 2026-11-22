package desktop

/*
#cgo LDFLAGS: -ldl
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {double r,g,b,a;} RGBA;
typedef struct {unsigned colors[8]; char family[256]; double size;} GtkAppearance;

// Query the host's GTK in a short-lived process. GTK remains optional on
// minimal desktops and cannot change the GUI process's input method or locale.
static int gtk_appearance(GtkAppearance *out, int scheme) {
	void *gtk = dlopen("libgtk-3.so.0", RTLD_LAZY|RTLD_LOCAL);
	if (!gtk) return 0;
#define LOAD(T, N, ARGS) T (*N) ARGS = dlsym(gtk,#N); if (!N) return 0
	LOAD(int, gtk_init_check,(int*,char***));
	LOAD(void*, gtk_settings_get_default,(void));
	LOAD(void*, gtk_window_new,(int));
	LOAD(void*, gtk_widget_get_style_context,(void*));
	LOAD(int, gtk_style_context_lookup_color,(void*,const char*,RGBA*));
	LOAD(void, g_object_get,(void*,const char*,...));
	LOAD(void, g_object_set,(void*,const char*,...));
	LOAD(void*,pango_font_description_from_string,(const char*));
	LOAD(const char*,pango_font_description_get_family,(void*));
	LOAD(int,pango_font_description_get_size,(void*));
	LOAD(int,pango_font_description_get_size_is_absolute,(void*));
	if (!gtk_init_check(NULL,NULL)) return 0;
	void *settings=gtk_settings_get_default();
	if (scheme==1) g_object_set(settings,"gtk-application-prefer-dark-theme",1,NULL);
	void *ctx=gtk_widget_get_style_context(gtk_window_new(0));
	const char* names[]={"theme_text_color","theme_base_color","theme_bg_color","theme_bg_color","theme_fg_color","borders","theme_selected_bg_color","theme_selected_fg_color"};
	for (int i=0;i<8;i++) {
		RGBA c;
		if (!gtk_style_context_lookup_color(ctx,names[i],&c)) continue;
		out->colors[i]=0x1000000 | ((unsigned)(c.r*255+.5)<<16) | ((unsigned)(c.g*255+.5)<<8) | (unsigned)(c.b*255+.5);
	}
	char *spec=NULL;
	g_object_get(settings,"gtk-font-name",&spec,NULL);
	if (spec) {
		void *font=pango_font_description_from_string(spec);
		const char *family=pango_font_description_get_family(font);
		if (family) snprintf(out->family,sizeof(out->family),"%s",family);
		out->size=pango_font_description_get_size(font)/1024.0;
		if (!pango_font_description_get_size_is_absolute(font)) out->size*=96.0/72.0;
	}
	return 1;
}
*/
import "C"

import (
	"context"
	"encoding/json"
	"image/color"
	"os"
	"os/exec"
	"time"

	"github.com/godbus/dbus/v5"
)

func probeAppearance() appearance {
	var scheme uint32
	var accent color.NRGBA
	// Read the portal once; a desktop without the Settings portal still uses GTK.
	if conn, err := dbus.ConnectSessionBus(); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
		var prefs map[string]map[string]dbus.Variant
		obj := conn.Object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
		if obj.CallWithContext(ctx, "org.freedesktop.portal.Settings.ReadAll", 0, []string{"org.freedesktop.appearance"}).Store(&prefs) == nil {
			settings := prefs["org.freedesktop.appearance"]
			scheme, _ = settings["color-scheme"].Value().(uint32)
			var channels struct{ R, G, B float64 }
			if v, ok := settings["accent-color"]; ok && v.Store(&channels) == nil {
				if channels.R >= 0 && channels.R <= 1 && channels.G >= 0 && channels.G <= 1 && channels.B >= 0 && channels.B <= 1 {
					accent = color.NRGBA{uint8(channels.R*255 + .5), uint8(channels.G*255 + .5), uint8(channels.B*255 + .5), 255}
				}
			}
		}
		cancel()
		conn.Close()
	}
	a := defaultAppearance(scheme == 1)
	var p C.GtkAppearance
	if C.gtk_appearance(&p, C.int(scheme)) != 0 {
		for i, dst := range []*color.NRGBA{&a.Text, &a.Window, &a.Panel, &a.Button, &a.ButtonText, &a.Border, &a.Highlight, &a.HighlightText} {
			if value := uint32(p.colors[i]); value != 0 {
				*dst = rgb(value)
			}
		}
		if family := C.GoString(&p.family[0]); family != "" {
			a.Family = family
		}
		if p.size > 0 {
			a.TextPx = float32(p.size)
		}
	} else if accent.A != 0 {
		a.Highlight = accent
		a.HighlightText = onHighlight(accent)
	}
	return a
}

func systemAppearance() appearance {
	a := defaultAppearance(false)
	path, err := os.Executable()
	if err != nil {
		return a
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	data, err := exec.CommandContext(ctx, path, "--read-system-appearance").Output()
	if err == nil {
		var p appearance
		if json.Unmarshal(data, &p) == nil && p.TextPx > 0 {
			return p
		}
	}
	return a
}
