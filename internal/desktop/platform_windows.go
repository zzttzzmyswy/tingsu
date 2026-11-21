package desktop

import (
	"golang.org/x/sys/windows"
	"os"
	"os/exec"
	"syscall"
)

func lockProfile(f *os.File) error {
	return windows.LockFileEx(windows.Handle(f.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, &windows.Overlapped{})
}
func background(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
}
func openCmd(target string) *exec.Cmd {
	return exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", target)
}
