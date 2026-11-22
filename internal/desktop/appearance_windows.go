package desktop

import (
	"image/color"
	"runtime"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

type logFont struct {
	Height, Width, Escapement, Orientation, Weight int32
	Flags                                          [8]byte
	Face                                           [32]uint16
}

func probeAppearance() appearance { return systemAppearance() }
func systemAppearance() appearance {
	user := windows.NewLazySystemDLL("user32.dll")
	sysColor := user.NewProc("GetSysColor")
	get := func(index uintptr) color.NRGBA {
		c, _, _ := sysColor.Call(index)
		return color.NRGBA{uint8(c), uint8(c >> 8), uint8(c >> 16), 255}
	}
	a := appearance{get(8), get(5), get(15), get(15), get(18), get(16), get(13), get(14), "Segoe UI", 14}
	spi := user.NewProc("SystemParametersInfoW")
	var hc struct {
		Size, Flags uint32
		Scheme      uintptr
	}
	hc.Size = uint32(unsafe.Sizeof(hc))
	spi.Call(0x42, uintptr(hc.Size), uintptr(unsafe.Pointer(&hc)), 0)
	if hc.Flags&1 == 0 {
		if bg, fg, accent, ok := windowsUIColors(); ok {
			if int(bg.R)+int(bg.G)+int(bg.B) < int(fg.R)+int(fg.G)+int(fg.B) {
				a.Window, a.Text = bg, fg
				a.Panel, a.Button, a.ButtonText = mix(bg, fg, 12), mix(bg, fg, 18), fg
				a.Border = mix(bg, fg, 45)
			}
			a.Highlight, a.HighlightText = accent, onHighlight(accent)
		}
	}
	var metrics struct {
		Size                    uint32
		Widths                  [5]int32
		Caption                 logFont
		SmallWidth, SmallHeight int32
		Small                   logFont
		MenuWidth, MenuHeight   int32
		Menu, Status, Message   logFont
		PaddedBorder            int32
	}
	metrics.Size = uint32(unsafe.Sizeof(metrics))
	// Ask for 96-DPI metrics so the Gio DPI scale is applied exactly once.
	proc := user.NewProc("SystemParametersInfoForDpi")
	var ok uintptr
	if proc.Find() == nil {
		ok, _, _ = proc.Call(0x29, uintptr(metrics.Size), uintptr(unsafe.Pointer(&metrics)), 0, 96)
	}
	if ok != 0 {
		if family := windows.UTF16ToString(metrics.Message.Face[:]); family != "" {
			a.Family = family
		}
		if h := metrics.Message.Height; h < 0 {
			a.TextPx = float32(-h)
		}
	}
	return a
}

// UISettings.GetColorValue includes the user's app color mode and accent.
// The Win32 system palette remains available on Windows installations without WinRT.
func windowsUIColors() (bg, fg, accent color.NRGBA, ok bool) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	rt := windows.NewLazySystemDLL("combase.dll")
	init := rt.NewProc("RoInitialize")
	if init.Find() != nil {
		return
	}
	hr, _, _ := init.Call(1)
	if int32(hr) >= 0 {
		defer rt.NewProc("RoUninitialize").Call()
	} else if uint32(hr) != 0x80010106 {
		return
	}
	name, _ := windows.UTF16FromString("Windows.UI.ViewManagement.UISettings")
	var str uintptr
	hr, _, _ = rt.NewProc("WindowsCreateString").Call(uintptr(unsafe.Pointer(&name[0])), uintptr(len(name)-1), uintptr(unsafe.Pointer(&str)))
	if int32(hr) < 0 {
		return
	}
	defer rt.NewProc("WindowsDeleteString").Call(str)
	type object struct{ table *[7]uintptr }
	var instance, settings *object
	hr, _, _ = rt.NewProc("RoActivateInstance").Call(str, uintptr(unsafe.Pointer(&instance)))
	if int32(hr) < 0 {
		return
	}
	defer syscall.SyscallN(instance.table[2], uintptr(unsafe.Pointer(instance)))
	id := windows.GUID{Data1: 0x03021be4, Data2: 0x5254, Data3: 0x4781, Data4: [8]byte{0x81, 0x94, 0x51, 0x68, 0xf7, 0xd0, 0x6d, 0x7b}}
	hr, _, _ = syscall.SyscallN(instance.table[0], uintptr(unsafe.Pointer(instance)), uintptr(unsafe.Pointer(&id)), uintptr(unsafe.Pointer(&settings)))
	if int32(hr) < 0 {
		return
	}
	defer syscall.SyscallN(settings.table[2], uintptr(unsafe.Pointer(settings)))
	for i, dst := range []*color.NRGBA{&bg, &fg, &accent} {
		var argb [4]byte
		hr, _, _ = syscall.SyscallN(settings.table[6], uintptr(unsafe.Pointer(settings)), []uintptr{0, 1, 5}[i], uintptr(unsafe.Pointer(&argb)))
		if int32(hr) < 0 {
			return
		}
		*dst = color.NRGBA{argb[1], argb[2], argb[3], 255}
	}
	return bg, fg, accent, true
}
