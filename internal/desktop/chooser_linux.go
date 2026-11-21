package desktop

import (
	"fmt"
	"net/url"
	"strings"

	"github.com/godbus/dbus/v5"
)

// Subscribe before opening the picker. Fast portal backends can reply before
// the method call itself returns.
func chooseFile(save bool, title, path, filter string) (any, error) {
	c, err := dbus.ConnectSessionBus()
	if err != nil {
		return nil, err
	}
	defer c.Close()
	const service = "org.freedesktop.portal.Desktop"
	ch := make(chan *dbus.Signal, 16)
	c.Signal(ch)
	if err = c.AddMatchSignal(dbus.WithMatchSender(service), dbus.WithMatchInterface("org.freedesktop.portal.Request"), dbus.WithMatchMember("Response")); err != nil {
		return nil, err
	}
	opts := map[string]dbus.Variant{}
	method := "OpenFile"
	if save {
		method = "SaveFile"
		opts["current_name"] = dbus.MakeVariant(path)
	} else {
		opts["multiple"] = dbus.MakeVariant(true)
	}
	if filter != "" {
		type rule struct {
			Kind    uint32
			Pattern string
		}
		type group struct {
			Name  string
			Rules []rule
		}
		var patterns []rule
		for _, s := range strings.Split(filter, ";") {
			patterns = append(patterns, rule{0, s})
		}
		opts["filters"] = dbus.MakeVariant([]group{{title, patterns}, {"所有文件", []rule{{0, "*"}}}})
	}
	var request dbus.ObjectPath
	if err = c.Object(service, "/org/freedesktop/portal/desktop").Call("org.freedesktop.portal.FileChooser."+method, 0, "", title, opts).Store(&request); err != nil {
		return nil, err
	}
	for sig := range ch {
		if sig.Path != request {
			continue
		}
		var status uint32
		var result map[string]dbus.Variant
		if err = dbus.Store(sig.Body, &status, &result); err != nil {
			return nil, err
		}
		if status != 0 {
			return nil, nil
		}
		var files []string
		uris, _ := result["uris"].Value().([]string)
		for _, raw := range uris {
			u, e := url.Parse(raw)
			if e != nil || u.Scheme != "file" || (u.Host != "" && u.Host != "localhost") {
				return nil, fmt.Errorf("请选择本地文件")
			}
			files = append(files, u.Path)
		}
		if save && len(files) > 0 {
			return files[0], nil
		}
		return files, nil
	}
	return nil, fmt.Errorf("文件选择器已断开")
}
