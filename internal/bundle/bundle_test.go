package bundle

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVerifyCachedComponents(t *testing.T) {
	var data bytes.Buffer
	w := zip.NewWriter(&data)
	for _, name := range []string{"tingsu-host.exe", "tingsu-capture.exe"} {
		entry, err := w.Create("bin/" + name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(name)); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	archive, err := zip.NewReader(bytes.NewReader(data.Bytes()), int64(data.Len()))
	if err != nil {
		t.Fatal(err)
	}
	parent := t.TempDir()
	dir := filepath.Join(parent, "runtime")
	if err := install(archive, parent, dir); err != nil {
		t.Fatal(err)
	}
	if err := verifyComponents(archive, dir); err != nil {
		t.Fatal(err)
	}
	name := "tingsu-capture.exe"
	path := filepath.Join(dir, "bin", name)
	for _, replacement := range [][]byte{nil, {}, []byte("not the original!")} {
		if replacement == nil {
			err = os.Remove(path)
		} else {
			err = os.WriteFile(path, replacement, 0600)
		}
		if err != nil {
			t.Fatal(err)
		}
		err := verifyComponents(archive, dir)
		wantHash := fmt.Sprintf("%x", sha256.Sum256([]byte(name)))
		if err == nil || !strings.Contains(err.Error(), wantHash) || !strings.Contains(err.Error(), path) {
			t.Fatalf("missing diagnostic for %q: %v", name, err)
		}
		after, err := os.ReadFile(path)
		if replacement == nil && !os.IsNotExist(err) || replacement != nil && (err != nil || !bytes.Equal(after, replacement)) {
			t.Fatal("verification changed the cached component")
		}
	}
}
