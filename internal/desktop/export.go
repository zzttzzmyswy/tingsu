package desktop

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode"

	weasy "github.com/benoitkugler/go-weasyprint"
	"github.com/benoitkugler/textprocessing/fontconfig"
	"github.com/benoitkugler/textprocessing/pango/fcfonts"
	"github.com/benoitkugler/webrender/text"
	"github.com/benoitkugler/webrender/utils"
)

var exportFormats = []choice{{"txt", "纯文本（TXT）"}, {"html", "网页（HTML）"}, {"pdf", "PDF 文档"}, {"md", "Markdown（MD）"}}

func exportLabel(section string) string {
	if section == "board" {
		return "便签看板"
	}
	return "会议记录"
}
func (u *workbench) exportForm(section string) {
	u.form("export", map[string]string{"section": section, "scope": u.boardScope, "format": "txt", "meeting": str(u.meeting()["id"])}, "")
}

type exportEntry struct {
	title, meta, body, files string
	markdown                 bool
}
type exportGroup struct {
	name    string
	entries []exportEntry
}
type exportDocument struct {
	title, meta string
	groups      []exportGroup
}

func attachmentNames(e M) string {
	var files []string
	_ = json.Unmarshal([]byte(str(e["files"])), &files)
	return strings.Join(files, "、")
}
func makeDocument(m M, records, notes []M, section, scope string) exportDocument {
	d := exportDocument{title: str(m["title"]), meta: exportLabel(section) + " · " + str(m["created"])}
	if section == "board" {
		d.meta += " · " + labelOf(boardScopes, scope)
	} else {
		group := exportGroup{name: "会议动态"}
		for _, e := range records {
			stamp := "未标时"
			if truth(e["timed"]) {
				seconds := max(0, int(number(e["at"])))
				stamp = fmt.Sprintf("%02d:%02d:%02d", seconds/3600, seconds/60%60, seconds%60)
			}
			who := str(e["speaker"])
			if who == "" {
				who = kindLabel(str(e["kind"]))
			}
			group.entries = append(group.entries, exportEntry{title: stamp + " · " + who, body: str(e["text"]), markdown: e["kind"] == "assistant", files: attachmentNames(e)})
		}
		d.groups = append(d.groups, group)
	}
	group := exportGroup{name: "会议便签"}
	if section == "board" {
		group.name = "便签"
	}
	for _, n := range notes {
		meta := labelOf(kinds, str(n["kind"]))
		if str(m["topic_id"]) != "" {
			meta += " · " + labelOf(noteScopes, noteScope(n))
		}
		if status := str(n["status"]); status != "" {
			meta += " · " + labelOf(statuses, status)
		}
		if source := str(n["source_title"]); source != "" {
			meta += " · 来自 " + source
		}
		group.entries = append(group.entries, exportEntry{title: str(n["title"]), meta: meta, body: str(n["body"])})
	}
	d.groups = append(d.groups, group)
	return d
}

type manualSavePath string

func (b *bridge) startExport(a M) error {
	section, format, scope := str(a["section"]), str(a["format"]), str(a["scope"])
	if section == "" {
		section = "meeting"
	}
	if format == "" {
		format = "md"
	}
	if (section != "board" && section != "meeting") || labelOf(exportFormats, format) == format || (scope != "" && scope != "meeting" && scope != "topic") {
		return errors.New("导出选项无效")
	}
	mid := b.mid
	if id := str(a["meeting"]); id != "" && id != mid {
		return errors.New("会议已切换，请重新选择导出内容")
	}
	board := section == "board"
	m, records, local, shared, err := b.db.ExportData(mid, board)
	if err != nil {
		return err
	}
	if board {
		local = boardCards(local, shared, scope, "", "")
	}
	doc := makeDocument(m, records, local, section, scope)
	name := strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) || unicode.IsNumber(r) || strings.ContainsRune(" -_", r) {
			return r
		}
		return '_'
	}, doc.title)
	title := "导出" + exportLabel(section)
	b.dialog("save-file", title, M{"path": name + "-" + exportLabel(section) + "." + format, "filter": "*." + format}, func(v any) {
		target := str(v)
		manual, checkOverwrite := v.(manualSavePath)
		if checkOverwrite {
			target = string(manual)
		}
		if target == "" {
			return
		}
		if filepath.Ext(target) == "" {
			target += "." + format
			checkOverwrite = true
		}
		if !strings.EqualFold(filepath.Ext(target), "."+format) {
			b.fail(fmt.Errorf("所选格式的文件扩展名应为 .%s", format))
			return
		}
		save := func() {
			b.exports.Go(func() {
				err := saveExport(target, format, doc)
				b.post(func() {
					if err != nil {
						b.fail(err)
					} else {
						b.notice(exportLabel(section)+"已导出", "ok")
					}
				})
			})
		}
		if checkOverwrite {
			if _, err := os.Stat(target); err == nil {
				b.dialog("confirm", "覆盖文件", M{"message": target + " 已存在，是否替换？"}, func(v any) {
					if truth(v) {
						save()
					}
				})
				return
			}
		}
		save()
	})
	return nil
}

// Render alongside the destination so a failed render leaves any existing file intact.
func saveExport(target, format string, d exportDocument) error {
	tmp, err := os.CreateTemp(filepath.Dir(target), ".tingsu-export-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()
	if err = d.write(tmp, format); err != nil {
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), target)
}

func (d exportDocument) write(w io.Writer, format string) error {
	if format == "html" || format == "pdf" {
		page, err := d.html(format == "pdf")
		if err != nil {
			return err
		}
		if format == "html" {
			_, err = io.WriteString(w, page)
			return err
		}
		return writePDF(w, page)
	}
	if format != "md" && format != "txt" {
		return errors.New("未知导出格式")
	}
	var out strings.Builder
	heading := func(level int, s string) {
		if format == "md" {
			out.WriteString(strings.Repeat("#", level) + " ")
		}
		out.WriteString(s + "\n\n")
	}
	heading(1, d.title)
	out.WriteString(d.meta + "\n\n")
	for _, g := range d.groups {
		heading(2, g.name)
		if len(g.entries) == 0 {
			out.WriteString("暂无记录\n\n")
		}
		for _, e := range g.entries {
			heading(3, e.title)
			if e.meta != "" {
				out.WriteString(e.meta + "\n")
			}
			body := e.body
			if e.markdown && format == "txt" {
				fragment, err := markdownHTML(body)
				if err != nil {
					return err
				}
				var plain strings.Builder
				for _, span := range markdownSpans(fragment, markdownTheme{}) {
					plain.WriteString(span.Content)
				}
				if plain.Len() > 0 {
					body = plain.String()
				}
			}
			out.WriteString(body + "\n")
			if e.files != "" {
				out.WriteString("附件：" + e.files + "\n")
			}
			out.WriteByte('\n')
		}
	}
	_, err := io.WriteString(w, out.String())
	return err
}

const reportCSS = `@page {size:A4; margin:18mm 18mm 20mm; @bottom-right {content:counter(page);color:#52677c;font-size:9pt}}
body{max-width:50em;margin:2em auto;color:#17263c;font:11pt/1.55 Tingsu,"Microsoft YaHei","Noto Sans CJK SC",sans-serif;overflow-wrap:break-word}
h1,h2,h3,h4{line-height:1.3;page-break-after:avoid}h1{font-size:24pt;color:#172c58}h2{font-size:16pt;margin:1.4em 0 .6em;border-bottom:1px solid #ccd8e7}
h3{font-size:12pt;color:#244e99;margin:1.2em 0 .3em}p{margin:.3em 0}.meta{color:#52677c;font-size:10pt}.plain,pre{white-space:pre-wrap}
blockquote{margin:.6em 0;padding-left:.8em;border-left:3px solid #abc4ec}pre{background:#f0f4f9;padding:.6em}code,pre{font-family:monospace,Tingsu}
table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{border:1px solid #d5dfeb;padding:.3em}a{color:#245edb}
@media print{body{max-width:none;margin:0}}`

func (d exportDocument) html(pdf bool) (string, error) {
	var b strings.Builder
	esc := html.EscapeString
	b.WriteString(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>` + esc(d.title) + "</title><style>")
	if pdf {
		b.WriteString(`@font-face{font-family:Tingsu;src:url("tingsu:font.ttf")}`)
	}
	b.WriteString(reportCSS + "</style></head><body><h1>" + esc(d.title) + "</h1><p class='meta'>" + esc(d.meta) + "</p>")
	for _, g := range d.groups {
		b.WriteString("<h2>" + esc(g.name) + "</h2>")
		if len(g.entries) == 0 {
			b.WriteString("<p>暂无记录</p>")
		}
		for _, e := range g.entries {
			b.WriteString("<article><h3>" + esc(e.title) + "</h3>")
			if e.meta != "" {
				b.WriteString("<p class='meta'>" + esc(e.meta) + "</p>")
			}
			if e.markdown {
				body, err := markdownHTML(e.body)
				if err != nil {
					return "", err
				}
				b.WriteString(body)
			} else {
				b.WriteString("<div class='plain'>" + esc(e.body) + "</div>")
			}
			if e.files != "" {
				b.WriteString("<p class='meta'>附件：" + esc(e.files) + "</p>")
			}
			b.WriteString("</article>")
		}
	}
	b.WriteString("</body></html>")
	return b.String(), nil
}
func writePDF(w io.Writer, page string) (err error) {
	// Keep a layout-engine failure local to this export and preserve the destination.
	defer func() {
		if issue := recover(); issue != nil {
			err = fmt.Errorf("PDF 排版失败：%v", issue)
		}
	}()
	config := text.NewFontConfigurationPango(fcfonts.NewFontMap(fontconfig.NewConfig(), nil))
	fetch := func(url string) (utils.RemoteRessource, error) {
		if url != "tingsu:font.ttf" {
			return utils.RemoteRessource{}, errors.New("仅支持内置导出字体")
		}
		return utils.RemoteRessource{Content: bytes.NewReader(interfaceFont), MimeType: "font/ttf"}, nil
	}
	return weasy.HtmlToPdfOptions(w, weasy.InputString(page), "", fetch, "print", nil, false, config, 1, nil)
}
