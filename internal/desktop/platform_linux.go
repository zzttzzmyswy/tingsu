package desktop

import (
	"os"
	"os/exec"
	"syscall"
)

func lockProfile(f *os.File) error {
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
}
func background(c *exec.Cmd)          {}
func openCmd(target string) *exec.Cmd { return exec.Command("xdg-open", target) }
