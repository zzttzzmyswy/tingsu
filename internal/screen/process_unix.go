//go:build !windows

package screen

import "os/exec"

func hideWindow(_ *exec.Cmd) {}
