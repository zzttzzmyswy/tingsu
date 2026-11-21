// Package bundle makes the platform helpers available to the desktop backend.
package bundle

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
)

// Windows release executables end with a ZIP followed by this small footer.
// Source builds and the Linux AppImage configure their own helper paths.
const marker = "TINGSU-ZIP-v1"
const footerSize = int64(sha256.Size + 8 + len(marker))

func Prepare() error {
	if runtime.GOOS != "windows" {
		return nil
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	f, err := os.Open(exe)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	var footer [footerSize]byte
	if info.Size() < footerSize {
		return nil
	}
	if _, err = f.ReadAt(footer[:], info.Size()-footerSize); err != nil {
		return err
	}
	if string(footer[40:]) != marker {
		return nil
	}
	length := int64(binary.LittleEndian.Uint64(footer[32:40]))
	if length <= 0 || length > info.Size()-footerSize {
		return fmt.Errorf("发行包长度无效")
	}
	payload := io.NewSectionReader(f, info.Size()-footerSize-length, length)
	h := sha256.New()
	if _, err := io.Copy(h, payload); err != nil {
		return err
	}
	if !bytes.Equal(h.Sum(nil), footer[:32]) {
		return fmt.Errorf("发行包内容校验失败")
	}
	cache, err := os.UserCacheDir()
	if err != nil {
		return err
	}
	parent := filepath.Join(cache, "Tingsu", "runtime")
	if err := os.MkdirAll(parent, 0700); err != nil {
		return err
	}
	dir := filepath.Join(parent, hex.EncodeToString(footer[:32]))
	if _, err := os.Stat(dir); os.IsNotExist(err) {
		archive, err := zip.NewReader(payload, length)
		if err != nil {
			return err
		}
		if err := install(archive, parent, dir); err != nil {
			return err
		}
	} else if err != nil {
		return err
	}
	if os.Getenv("TINGSU_APP_DIR") == "" {
		os.Setenv("TINGSU_APP_DIR", filepath.Dir(exe))
	}
	os.Setenv("TINGSU_BUNDLE", dir)
	if os.Getenv("TINGSU_DESKTOP_HOST") == "" {
		os.Setenv("TINGSU_DESKTOP_HOST", filepath.Join(dir, "bin", "tingsu-webview.exe"))
	}
	os.Setenv("TINGSU_HOST_PATH", os.Getenv("PATH"))
	os.Setenv("TINGSU_HOST_VARIABLES", "PATH")
	return os.Setenv("PATH", filepath.Join(dir, "bin")+string(os.PathListSeparator)+os.Getenv("PATH"))
}

func install(archive *zip.Reader, parent, dir string) error {
	stage, err := os.MkdirTemp(parent, ".install-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	for _, entry := range archive.File {
		name := filepath.FromSlash(entry.Name)
		if !filepath.IsLocal(name) || !entry.Mode().IsRegular() {
			return fmt.Errorf("无效的文件名 %q", name)
		}
		output := filepath.Join(stage, name)
		if err := os.MkdirAll(filepath.Dir(output), 0700); err != nil {
			return err
		}
		if err := extract(entry, output); err != nil {
			return err
		}
	}
	if err := os.Rename(stage, dir); err != nil {
		// Another launch may have finished installing this identical payload.
		if _, other := os.Stat(dir); other != nil {
			return err
		}
	}
	return nil
}
func extract(entry *zip.File, path string) error {
	src, err := entry.Open()
	if err != nil {
		return err
	}
	defer src.Close()
	dst, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(dst, src)
	closeErr := dst.Close()
	if copyErr != nil {
		return copyErr
	}
	return closeErr
}
