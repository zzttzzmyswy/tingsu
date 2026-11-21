package agent

import (
	"context"
	"errors"
	"golang.org/x/sys/windows"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

func configureProcess(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true} }
func killProcess(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}
func refreshReadACL(root string) error {
	system := filepath.Join(os.Getenv("SystemRoot"), "System32")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	groups, e := windows.GetCurrentProcessToken().GetTokenGroups()
	if e != nil {
		return e
	}
	sid := ""
	for _, g := range groups.AllGroups() {
		if g.Attributes&windows.SE_GROUP_LOGON_ID == windows.SE_GROUP_LOGON_ID && strings.HasPrefix(g.Sid.String(), "S-1-5-5-") {
			sid = g.Sid.String()
			break
		}
	}
	if sid == "" {
		return errors.New("无法读取 Windows 登录 SID")
	}
	return filepath.WalkDir(root, func(path string, d os.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if path != root && filepath.Dir(path) == root && d.Name() != "attachments" {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		p, e := windows.UTF16PtrFromString(path)
		if e != nil {
			return e
		}
		attrs, e := windows.GetFileAttributes(p)
		if e != nil {
			return e
		}
		if attrs&windows.FILE_ATTRIBUTE_REPARSE_POINT != 0 {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		rights := "(RX)"
		if !d.IsDir() {
			h, e := windows.CreateFile(p, 0, windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE, nil, windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL, 0)
			if e != nil {
				return e
			}
			var info windows.ByHandleFileInformation
			e = windows.GetFileInformationByHandle(h, &info)
			windows.CloseHandle(h)
			if e != nil {
				return e
			}
			if info.NumberOfLinks > 1 {
				return nil
			}
			rights = "(R)"
		}
		cmd := exec.CommandContext(ctx, filepath.Join(system, "icacls.exe"), path, "/grant", "*"+sid+":"+rights, "/L", "/Q")
		configureProcess(cmd)
		return cmd.Run()
	})
}
