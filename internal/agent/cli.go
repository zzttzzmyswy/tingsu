package agent

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
)

func hostEnv() map[string]string {
	env := map[string]string{}
	for _, line := range os.Environ() {
		k, v, ok := strings.Cut(line, "=")
		if ok {
			if strings.EqualFold(k, "PATH") {
				k = "PATH"
			}
			env[k] = v
		}
	}
	for _, k := range strings.Fields(os.Getenv("TINGSU_HOST_VARIABLES")) {
		if v, ok := os.LookupEnv("TINGSU_HOST_" + k); ok {
			env[k] = v
		} else {
			delete(env, k)
		}
	}
	for k := range env {
		if strings.HasPrefix(k, "TINGSU_") || strings.HasPrefix(k, "SOPHNET_") || strings.HasPrefix(k, "SILICONFLOW_") {
			delete(env, k)
		}
	}
	if os.Getenv("TINGSU_APPIMAGE_RUNTIME") != "" {
		for _, k := range []string{"APPIMAGE", "APPDIR", "ARGV0", "OWD"} {
			delete(env, k)
		}
	}
	return env
}
func Command(ctx context.Context, c Config, args ...string) (*exec.Cmd, error) {
	names := map[string][2]string{"codex": {"codex", "@openai/codex"}, "claude": {"claude", "@anthropic-ai/claude-code"}, "deepseek": {"dsh", "@deepseek-ai/dsh"}}
	pkg, ok := names[c.Provider]
	if !ok {
		return nil, errors.New("未知本地 CLI")
	}
	env := hostEnv()
	home, _ := os.UserHomeDir()
	dirs := filepath.SplitList(env["PATH"])
	dirs = append(dirs, filepath.Join(home, ".local", "bin"))
	if p := first(env["NPM_CONFIG_PREFIX"], env["npm_config_prefix"]); p != "" {
		if runtime.GOOS != "windows" {
			p = filepath.Join(p, "bin")
		}
		dirs = append(dirs, p)
	}
	if runtime.GOOS == "windows" {
		dirs = append(dirs, filepath.Join(env["APPDATA"], "npm"), filepath.Join(os.Getenv("ProgramFiles"), "nodejs"), filepath.Join(env["LOCALAPPDATA"], "Volta", "bin"), env["FNM_MULTISHELL_PATH"], env["NVM_SYMLINK"])
	} else {
		for _, p := range []string{".claude/local", ".npm-global/bin", ".volta/bin", ".bun/bin"} {
			dirs = append(dirs, filepath.Join(home, p))
		}
		if v := env["NVM_BIN"]; v != "" {
			dirs = append(dirs, v)
		}
		nvm := first(env["NVM_DIR"], filepath.Join(home, ".nvm"))
		fnm := first(env["FNM_DIR"], filepath.Join(first(env["XDG_DATA_HOME"], filepath.Join(home, ".local", "share")), "fnm"))
		dirs = append(dirs, filepath.Join(fnm, "aliases/default/bin"))
		for _, pattern := range []string{filepath.Join(nvm, "versions/node/v*/bin"), filepath.Join(fnm, "node-versions/v*/installation/bin")} {
			p, _ := filepath.Glob(pattern)
			slices.Reverse(p)
			dirs = append(dirs, p...)
		}
	}
	valid := []string{}
	seen := map[string]bool{}
	for _, d := range dirs {
		if filepath.IsAbs(d) && !seen[d] {
			valid = append(valid, d)
			seen[d] = true
		}
	}
	isFile := func(p string) bool {
		i, e := os.Stat(p)
		return e == nil && i.Mode().IsRegular() && (runtime.GOOS == "windows" || i.Mode()&0111 != 0)
	}
	find := func(names ...string) string {
		for _, d := range valid {
			for _, n := range names {
				p := filepath.Join(d, n)
				if isFile(p) {
					return p
				}
			}
		}
		return ""
	}
	path := c.Executable
	if path == "" {
		if runtime.GOOS == "windows" {
			path = find(pkg[0]+".exe", pkg[0]+".cmd")
		} else {
			path = find(pkg[0])
		}
	}
	if !filepath.IsAbs(path) || !isFile(path) {
		return nil, fmt.Errorf("找不到可运行的 %s，请安装本地 CLI 或在设置中指定绝对路径", pkg[0])
	}
	valid = append([]string{filepath.Dir(path)}, valid...)
	env["PATH"] = strings.Join(valid, string(os.PathListSeparator))
	if c.Provider == "claude" {
		delete(env, "CLAUDECODE")
	}
	if c.Provider == "deepseek" {
		env["DSH_PERMISSION_MODE"] = "read-only"
		delete(env, "RIPGREP_CONFIG_PATH")
	}
	argv := []string{path}
	if runtime.GOOS == "windows" {
		if !strings.EqualFold(filepath.Ext(path), ".exe") {
			if !strings.EqualFold(filepath.Base(path), pkg[0]+".cmd") && !strings.EqualFold(filepath.Base(path), pkg[0]+".ps1") {
				return nil, errors.New("请选择 CLI 的 EXE 或 npm 启动器")
			}
			folder := filepath.Dir(path)
			modules := filepath.Join(folder, "node_modules")
			if filepath.Base(folder) == ".bin" {
				modules = filepath.Dir(folder)
			}
			root := filepath.Join(modules, pkg[1])
			b, e := os.ReadFile(filepath.Join(root, "package.json"))
			if e != nil {
				return nil, e
			}
			var manifest M
			if e = json.Unmarshal(b, &manifest); e != nil {
				return nil, e
			}
			entry := first(str(manifest["bin"]), str(obj(manifest["bin"])[pkg[0]]))
			if entry == "" {
				return nil, errors.New("npm CLI 缺少入口")
			}
			script, e := within(root, entry)
			if e != nil {
				return nil, e
			}
			argv = []string{script}
			if !strings.EqualFold(filepath.Ext(script), ".exe") {
				node := find("node.exe")
				if node == "" {
					return nil, errors.New("找不到 Node.js")
				}
				argv = []string{node, script}
			}
		}
		if bundle := os.Getenv("TINGSU_BUNDLE"); bundle != "" {
			helper := filepath.Join(bundle, "bin", "tingsu-host.exe")
			if !isFile(helper) {
				return nil, errors.New("发行包缺少 Windows CLI 启动组件")
			}
			argv = append([]string{helper, strconv.Itoa(os.Getpid())}, argv...)
		}
	}
	argv = append(argv, args...)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Dir = c.Workspace
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	configureProcess(cmd)
	return cmd, nil
}

// A single goroutine consumes the protocol stream; operations exchange requests serially.
// Notifications received before an RPC response are retained by the current operation.
type wire struct {
	cmd     *exec.Cmd
	input   io.WriteCloser
	packets chan M
	done    chan struct{}
	closing chan struct{}
	stop    sync.Once
	seq     int
	acp     bool
	on      func(M) error
}

func startWire(c Config, acp bool, args ...string) (*wire, error) {
	cmd, e := Command(context.Background(), c, args...)
	if e != nil {
		return nil, e
	}
	in, e := cmd.StdinPipe()
	if e != nil {
		return nil, e
	}
	out, e := cmd.StdoutPipe()
	if e != nil {
		in.Close()
		return nil, e
	}
	if e = cmd.Start(); e != nil {
		in.Close()
		out.Close()
		return nil, e
	}
	w := &wire{cmd: cmd, input: in, packets: make(chan M, 64), done: make(chan struct{}), closing: make(chan struct{}), acp: acp}
	go func() {
		defer close(w.done)
		defer cmd.Wait()
		defer close(w.packets)
		scan := bufio.NewScanner(out)
		scan.Buffer(make([]byte, 32768), 64<<20)
		for scan.Scan() {
			var m M
			if json.Unmarshal(scan.Bytes(), &m) != nil {
				break
			}
			select {
			case w.packets <- m:
			case <-w.closing:
				return
			}
		}
	}()
	return w, nil
}
func (w *wire) write(p M) error {
	if w.acp {
		p["jsonrpc"] = "2.0"
	}
	return json.NewEncoder(w.input).Encode(p)
}
func (w *wire) next(ctx context.Context) (M, error) {
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case p, ok := <-w.packets:
		if !ok {
			return nil, errors.New("本地 CLI 连接已关闭，请检查 CLI 的安装和身份配置")
		}
		return p, nil
	}
}
func (w *wire) dispatch(p M) error {
	if w.on != nil {
		return w.on(p)
	}
	if _, ok := p["id"]; ok && p["method"] != nil {
		return w.write(M{"id": p["id"], "error": M{"code": -32601, "message": "Unsupported method"}})
	}
	return nil
}
func (w *wire) rpc(ctx context.Context, method string, args M) (M, error) {
	w.seq++
	id := w.seq
	if e := w.write(M{"method": method, "id": id, "params": args}); e != nil {
		return nil, e
	}
	for {
		p, e := w.next(ctx)
		if e != nil {
			return nil, e
		}
		if p["method"] != nil {
			if e = w.dispatch(p); e != nil {
				return nil, e
			}
			continue
		}
		if integer(p["id"]) != id {
			continue
		}
		if err := obj(p["error"]); err != nil {
			return nil, fmt.Errorf("%s：%s", method, cut(str(err["message"]), 550))
		}
		return obj(p["result"]), nil
	}
}
func (w *wire) call(ctx context.Context, method string, args M) (M, error) {
	ctx, stop := context.WithTimeout(ctx, 45*time.Second)
	defer stop()
	return w.rpc(ctx, method, args)
}
func (w *wire) close() {
	if w == nil {
		return
	}
	w.stop.Do(func() {
		w.input.Close()
		close(w.closing)
		select {
		case <-w.done:
		case <-time.After(2 * time.Second):
			killProcess(w.cmd)
			<-w.done
		}
		killProcess(w.cmd)
	})
}
