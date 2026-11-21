package desktop

import (
	"fmt"
	"path/filepath"
	"runtime"
	"strings"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
)

type openFileName struct {
	Size                   uint32
	Owner, Instance        uintptr
	Filter, CustomFilter   *uint16
	MaxCustom, FilterIndex uint32
	File                   *uint16
	MaxFile                uint32
	FileTitle              *uint16
	MaxTitle               uint32
	InitialDir, Title      *uint16
	Flags                  uint32
	FileOffset, Extension  uint16
	DefaultExt             *uint16
	Data, Hook             uintptr
	Template               *uint16
	Reserved               uintptr
	ReservedFlags, FlagsEx uint32
}

func chooseFile(save bool, title, path, filter string) (any, error) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	dll := windows.NewLazySystemDLL("comdlg32.dll")
	buf := make([]uint16, 65536)
	copy(buf, utf16.Encode([]rune(path)))
	pattern := "所有文件\x00*.*\x00"
	if filter != "" {
		pattern = title + "\x00" + filter + "\x00" + pattern
	}
	filters := utf16.Encode([]rune(pattern + "\x00"))
	caption, _ := windows.UTF16PtrFromString(title)
	of := openFileName{Title: caption, File: &buf[0], MaxFile: uint32(len(buf)), Filter: &filters[0], FilterIndex: 1, Flags: 0x80000 | 0x8 | 0x800}
	of.Size = uint32(unsafe.Sizeof(of))
	name := "GetOpenFileNameW"
	if save {
		of.Flags |= 2
		of.DefaultExt, _ = windows.UTF16PtrFromString(strings.TrimPrefix(filepath.Ext(path), "."))
		name = "GetSaveFileNameW"
	} else {
		of.Flags |= 0x200 | 0x1000
	}
	ok, _, _ := dll.NewProc(name).Call(uintptr(unsafe.Pointer(&of)))
	runtime.KeepAlive(of)
	runtime.KeepAlive(filters)
	if ok == 0 {
		code, _, _ := dll.NewProc("CommDlgExtendedError").Call()
		if code == 0 {
			return nil, nil
		}
		return nil, fmt.Errorf("文件选择器错误 0x%x", code)
	}
	var names []string
	for start := 0; start < len(buf) && buf[start] != 0; {
		end := start
		for end < len(buf) && buf[end] != 0 {
			end++
		}
		names = append(names, string(utf16.Decode(buf[start:end])))
		start = end + 1
	}
	if save {
		return names[0], nil
	}
	if len(names) > 1 {
		for i := 1; i < len(names); i++ {
			names[i] = filepath.Join(names[0], names[i])
		}
		names = names[1:]
	}
	return names, nil
}
