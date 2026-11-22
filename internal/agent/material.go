package agent

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"mime"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

type library struct{ root, scope string }

var digestRE = regexp.MustCompile("^[a-f0-9]{64}$")

func digest(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }
func (l library) dir() string {
	return filepath.Join(l.root, "attachments", digest("meeting:" + l.scope)[:16])
}

type material struct {
	path   string
	info   M
	source string
}

func describe(path, id string) (material, error) {
	i, e := os.Lstat(path)
	if e != nil {
		return material{}, e
	}
	if !i.Mode().IsRegular() {
		return material{}, errors.New("请选择普通文件")
	}
	m := strings.Split(mime.TypeByExtension(strings.ToLower(filepath.Ext(path))), ";")[0]
	return material{path: path, info: M{"id": id, "name": i.Name(), "size_bytes": i.Size(), "mime_type": first(m, "application/octet-stream")}}, nil
}
func (l library) stage(ctx context.Context, source string) (material, error) {
	f, e := os.Open(source)
	if e != nil {
		return material{}, e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return material{}, e
	}
	if !info.Mode().IsRegular() {
		return material{}, errors.New("请选择普通文件")
	}
	dir := l.dir()
	if e = os.MkdirAll(dir, 0700); e != nil {
		return material{}, e
	}
	tmp, e := os.MkdirTemp(dir, ".adding-")
	if e != nil {
		return material{}, e
	}
	defer os.RemoveAll(tmp)
	name := filepath.Base(source)
	w, e := os.OpenFile(filepath.Join(tmp, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return material{}, e
	}
	h := sha256.New()
	io.WriteString(h, name+"\x00")
	_, e = io.Copy(io.MultiWriter(w, h), ctxReader{ctx, f})
	ce := w.Close()
	if e != nil {
		return material{}, e
	}
	if ce != nil {
		return material{}, ce
	}
	id := hex.EncodeToString(h.Sum(nil))
	dest := filepath.Join(dir, id)
	if e = os.Rename(tmp, dest); e != nil {
		if _, err := os.Stat(filepath.Join(dest, name)); err != nil {
			return material{}, e
		}
	}
	return describe(filepath.Join(dest, name), id)
}

type ctxReader struct {
	ctx context.Context
	r   io.Reader
}

func (r ctxReader) Read(b []byte) (int, error) {
	if e := r.ctx.Err(); e != nil {
		return 0, e
	}
	return r.r.Read(b)
}
func (l library) stageAll(ctx context.Context, paths []string) ([]material, error) {
	selected := make([]material, 0, len(paths))
	for _, p := range paths {
		if e := ctx.Err(); e != nil {
			return nil, e
		}
		m, e := l.stage(ctx, p)
		if e != nil {
			return nil, e
		}
		m.source = p
		selected = append(selected, m)
	}
	return selected, nil
}
func (l library) inputs(ctx context.Context, provider, text string, files []string, frame string, meeting M) ([]M, error) {
	staged, e := l.stageAll(ctx, files)
	if e != nil {
		return nil, e
	}
	in, _, err := l.blocks(provider, text, staged, frame, meeting, "")
	return in, err
}
func (l library) blocks(provider, text string, files []material, frame string, meeting M, previousFrame string) ([]M, string, error) {
	request := M{"request": text, "meeting_context": meeting}
	if len(files) > 0 && runtime.GOOS == "windows" && provider == "codex" {
		request["workspace"] = l.root
	}
	native := []material{}
	manifest := []M{}
	if frame != "" {
		m, e := describe(frame, "")
		if e != nil {
			return nil, "", e
		}
		native = append(native, m)
	}
	for _, m := range files {
		p := m.source
		rel, e := filepath.Rel(l.root, m.path)
		if e != nil {
			return nil, "", e
		}
		m.info["path"] = filepath.ToSlash(rel)
		if provider == "codex" && runtime.GOOS == "windows" {
			m.info["absolute_path"] = m.path
			m.info["absolute_path_utf16"] = utf16.Encode([]rune(m.path))
		}
		if strings.HasPrefix(str(m.info["mime_type"]), "image/") && p != frame {
			native = append(native, m)
		}
		manifest = append(manifest, m.info)
	}
	if len(files) > 0 {
		request["attachments"] = manifest
	}
	in := []M{{"type": "text"}}
	frameDigest := previousFrame
	remaining := int64(24 << 20)
	for _, m := range native {
		mime := str(m.info["mime_type"])
		image := mime == "image/png" || mime == "image/jpeg" || mime == "image/webp" || mime == "image/gif"
		if !image {
			continue
		}
		size := m.info["size_bytes"].(int64)
		if size > remaining {
			if frame == m.path {
				return nil, "", errors.New("截图超过图像请求容量")
			}
			continue
		}
		remaining -= size
		b, e := os.ReadFile(m.path)
		if e != nil {
			return nil, "", e
		}
		if frame != "" && m.path == frame {
			sum := sha256.Sum256(b)
			frameDigest = hex.EncodeToString(sum[:])
			if frameDigest == previousFrame {
				request["共享画面"] = "与本会话上次送达的共享画面完全相同"
				continue
			}
			request["共享画面"] = "本轮第一张图"
		}
		url := "data:" + mime + ";base64," + base64.StdEncoding.EncodeToString(b)
		in = append(in, M{"type": "image", "url": url})
	}
	in[0]["text"] = encode(request)
	return in, frameDigest, nil
}
func (l library) get(id string) (material, error) {
	if digestRE.MatchString(id) {
		dir := filepath.Join(l.dir(), id)
		if i, e := os.Lstat(dir); e == nil && i.IsDir() {
			files, _ := os.ReadDir(dir)
			if len(files) == 1 {
				return describe(filepath.Join(dir, files[0].Name()), id)
			}
		}
	}
	return material{}, errors.New("本场会议没有该附件")
}
func (l library) tool(name string, a M) (any, error) {
	if name == "list_attachments" {
		ds, e := os.ReadDir(l.dir())
		if e != nil && !os.IsNotExist(e) {
			return nil, e
		}
		ids := []string{}
		for _, d := range ds {
			if d.IsDir() && digestRE.MatchString(d.Name()) {
				ids = append(ids, d.Name())
			}
		}
		o := min(len(ids), max(0, integer(a["offset"])))
		n := max(1, min(100, integer(a["limit"])))
		items := []M{}
		for _, id := range ids[o:min(len(ids), o+n)] {
			m, e := l.get(id)
			if e != nil {
				return nil, e
			}
			items = append(items, m.info)
		}
		return M{"items": items, "total": len(ids)}, nil
	}
	m, e := l.get(str(a["id"]))
	if e != nil {
		return nil, e
	}
	format := str(a["format"])
	path := m.path
	if format == "text" && isOffice(path) {
		path, e = l.officeView(m)
		if e != nil {
			return nil, e
		}
	}
	reader, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer reader.Close()
	size, e := reader.Seek(0, io.SeekEnd)
	if e != nil {
		return nil, e
	}
	start := int64(integer(a["offset"]))
	if start < 0 {
		start = max(int64(0), size+start)
	}
	start = min(start, size)
	if _, e = reader.Seek(start, 0); e != nil {
		return nil, e
	}
	query := str(a["query"])
	if query != "" {
		if format != "text" {
			return nil, errors.New("关键词查找请使用 text 格式")
		}
		q := []byte(query)
		if len(q) > 65536 {
			return nil, errors.New("关键词太长")
		}
		tail := []byte{}
		buf := make([]byte, 65536)
		for {
			n, err := reader.Read(buf)
			sl := append(tail, buf[:n]...)
			index := bytes.Index(sl, q)
			if index >= 0 {
				start += int64(index - len(tail))
				break
			}
			start += int64(n)
			tail = append([]byte{}, sl[max(0, len(sl)-len(q)+1):]...)
			if err == io.EOF {
				return M{"filename": m.info["name"], "found": false, "eof": true, "view_bytes": size}, nil
			}
			if err != nil {
				return nil, err
			}
		}
		_, _ = reader.Seek(start, 0)
	}
	b, e := io.ReadAll(io.LimitReader(reader, int64(max(4, min(65536, integer(a["limit"]))))))
	if e != nil {
		return nil, e
	}
	content := ""
	if format == "base64" {
		content = base64.StdEncoding.EncodeToString(b)
	} else {
		for len(b) > 0 && !utf8.RuneStart(b[0]) {
			start++
			b = b[1:]
		}
		for i := 0; i < len(b); {
			if !utf8.FullRune(b[i:]) {
				b = b[:i]
				break
			}
			r, n := utf8.DecodeRune(b[i:])
			if r == utf8.RuneError && n == 1 {
				return nil, errors.New("非 UTF-8 附件可使用 base64 读取原件")
			}
			i += n
		}
		content = string(b)
	}
	end := start + int64(len(b))
	return M{"filename": m.info["name"], "format": format, "offset": start, "next_offset": end, "view_bytes": size, "eof": end >= size, "content": content}, nil
}
func within(root, path string) (string, error) {
	// Native tools reopen the original name. A symlink before ".." may resolve
	// differently from filepath.Join's lexical simplification.
	for _, component := range strings.Split(filepath.ToSlash(path), "/") {
		if component == ".." {
			return "", errors.New("请选择会议工作目录中的文件")
		}
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(root, path)
	}
	real, e := filepath.EvalSymlinks(path)
	if e != nil {
		return "", e
	}
	root, e = filepath.EvalSymlinks(root)
	if e != nil {
		return "", e
	}
	rel, e := filepath.Rel(root, real)
	if e != nil || !filepath.IsLocal(rel) {
		return "", fmt.Errorf("请选择会议工作目录中的文件")
	}
	return real, nil
}

// Cache the UTF-8 view only when the cloud provider explicitly asks for one.
// Native agents continue to work directly on the original.
func (l library) officeView(m material) (string, error) {
	dir := filepath.Join(l.root, ".text-cache")
	if e := os.MkdirAll(dir, 0700); e != nil {
		return "", e
	}
	dest := filepath.Join(dir, str(m.info["id"]))
	if _, e := os.Stat(dest); e == nil {
		return dest, nil
	}
	tmp, e := os.CreateTemp(dir, ".extracting-")
	if e != nil {
		return "", e
	}
	defer os.Remove(tmp.Name())
	e = officeText(m.path, tmp)
	ce := tmp.Close()
	if e != nil {
		return "", e
	}
	if ce != nil {
		return "", ce
	}
	if e = os.Rename(tmp.Name(), dest); e != nil {
		if _, err := os.Stat(dest); err != nil {
			return "", e
		}
	}
	return dest, nil
}
