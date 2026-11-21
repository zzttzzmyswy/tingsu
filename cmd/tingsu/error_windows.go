package main

import (
	"errors"
	"strings"

	"golang.org/x/sys/windows"
)

// This dialog must work even when the WebView host itself can't start.
func showStartupError(err error) {
	message := err.Error()
	if errors.Is(err, windows.ERROR_VIRUS_INFECTED) || errors.Is(err, windows.ERROR_VIRUS_DELETED) {
		message += "\n\nWindows 报告安全软件已阻止访问。请保留隔离记录，联系安全软件厂商复核。"
	}
	text, _ := windows.UTF16PtrFromString(strings.ToValidUTF8(strings.ReplaceAll(message, "\x00", "�"), "�"))
	title, _ := windows.UTF16PtrFromString("听序启动失败")
	windows.MessageBox(0, text, title, windows.MB_OK|windows.MB_ICONERROR)
}
