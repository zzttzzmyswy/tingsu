package desktop

import (
	"bytes"
	"image/color"
	"io"
	"net/url"
	"strconv"
	"strings"

	"gioui.org/font"
	"gioui.org/io/clipboard"
	"gioui.org/layout"
	"gioui.org/unit"
	"gioui.org/x/richtext"
	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/extension"
	gmtext "github.com/yuin/goldmark/text"
	"golang.org/x/net/html"
	"golang.org/x/net/html/atom"
)

var meetingMarkdown = goldmark.New(goldmark.WithExtensions(extension.Table))

func webLink(raw []byte) bool {
	u, err := url.Parse(string(raw))
	return err == nil && (u.Scheme == "https" || u.Scheme == "http" || u.Scheme == "mailto")
}

// Replies may quote arbitrary meeting material. Keep links as references and
// images as captions; viewing a record never fetches remote meeting content.
func markdownHTML(src string) (string, error) {
	data := []byte(src)
	root := meetingMarkdown.Parser().Parse(gmtext.NewReader(data))
	type caption struct {
		node  ast.Node
		label []byte
	}
	var captions []caption
	_ = ast.Walk(root, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if entering {
			switch v := n.(type) {
			case *ast.Image:
				captions = append(captions, caption{v, v.Text(data)})
				return ast.WalkSkipChildren, nil
			case *ast.AutoLink:
				if v.AutoLinkType != ast.AutoLinkEmail && !webLink(v.URL(data)) {
					captions = append(captions, caption{v, v.Label(data)})
				}
			case *ast.Link:
				if !webLink(v.Destination) {
					v.Destination = nil
				}
			}
		}
		return ast.WalkContinue, nil
	})
	for _, c := range captions {
		parent := c.node.Parent()
		parent.ReplaceChild(parent, c.node, ast.NewString(c.label))
	}
	var buf bytes.Buffer
	err := meetingMarkdown.Renderer().Render(&buf, data, root)
	return buf.String(), err
}

type markdownRow struct {
	source     string
	blocks     []W
	showSource bool
}

type markdownTheme struct {
	face                     font.Typeface
	size                     unit.Sp
	fg, secondary, highlight color.NRGBA
}

func markdownNodes(fragment string) []*html.Node {
	parent := &html.Node{Type: html.ElementNode, Data: "div", DataAtom: atom.Div}
	nodes, _ := html.ParseFragment(strings.NewReader(fragment), parent)
	return nodes
}
func markdownSpans(fragment string, theme markdownTheme) []richtext.SpanStyle {
	return spansForNodes(markdownNodes(fragment), theme)
}
func spansForNodes(nodes []*html.Node, theme markdownTheme) []richtext.SpanStyle {
	var spans []richtext.SpanStyle
	appendText := func(style richtext.SpanStyle, value string) {
		if value != "" {
			style.Content = value
			spans = append(spans, style)
		}
	}
	listNumbers := make(map[*html.Node]int)
	var visit func(*html.Node, richtext.SpanStyle, bool)
	visit = func(n *html.Node, style richtext.SpanStyle, pre bool) {
		if n.Type == html.TextNode {
			t := n.Data
			if !pre {
				if strings.Trim(t, "\r\n") == "" {
					return
				}
				t = strings.ReplaceAll(t, "\n", " ")
			}
			appendText(style, t)
			return
		}
		if n.Type != html.ElementNode {
			return
		}
		block := false
		switch n.Data {
		case "h1", "h2", "h3", "h4", "h5", "h6":
			style.Size = theme.size * 1.2
			if n.Data == "h1" {
				style.Size = theme.size * 1.4
			}
			style.Font.Weight = font.Bold
			block = true
		case "strong", "th":
			style.Font.Weight = font.Bold
		case "em":
			style.Font.Style = font.Italic
		case "blockquote":
			style.Color = theme.secondary
			appendText(style, "▏ ")
			block = true
		case "code":
			style.Font.Typeface = "monospace"
			style.Color = theme.highlight
		case "pre":
			pre = true
			block = true
		case "p", "tr":
			block = true
		case "ol":
			start := 1
			for _, attr := range n.Attr {
				if attr.Key == "start" {
					if v, err := strconv.Atoi(attr.Val); err == nil {
						start = v
					}
				}
			}
			listNumbers[n] = start
		case "li":
			bullet := "• "
			if i, ok := listNumbers[n.Parent]; ok {
				bullet = strconv.Itoa(i) + ". "
				listNumbers[n.Parent] = i + 1
			}
			appendText(style, bullet)
			block = true
		case "br", "hr":
			appendText(style, "\n")
			return
		case "a":
			style.Color = theme.highlight
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			visit(c, style, pre)
		}
		if n.Data == "a" && theme.size == 0 {
			for _, attr := range n.Attr {
				if attr.Key == "href" && attr.Val != "" && (n.FirstChild == nil || n.FirstChild.Data != attr.Val) {
					appendText(style, " ("+attr.Val+")")
				}
			}
		}
		if theme.size == 0 && (n.Data == "td" || n.Data == "th") {
			for sib := n.NextSibling; sib != nil; sib = sib.NextSibling {
				if sib.Type == html.ElementNode {
					appendText(style, "  |  ")
					break
				}
			}
		}
		if block && len(spans) > 0 && !strings.HasSuffix(spans[len(spans)-1].Content, "\n") {
			appendText(style, "\n")
		}
	}
	base := richtext.SpanStyle{Size: theme.size, Font: font.Font{Typeface: theme.face}, Color: theme.fg}
	for _, n := range nodes {
		visit(n, base, false)
	}
	for len(spans) > 0 {
		last := &spans[len(spans)-1]
		last.Content = strings.TrimRight(last.Content, "\n")
		if last.Content != "" {
			break
		}
		spans = spans[:len(spans)-1]
	}
	return spans
}
func (u *workbench) markdownBody(id, source string, fallback W) W {
	r := u.markdownRows[id]
	if r.source != source {
		fragment, err := markdownHTML(source)
		if err != nil {
			return fallback
		}
		r.source = source
		r.blocks = u.markdownBlocks(fragment)
		u.markdownRows[id] = r
	}
	if r.showSource || len(r.blocks) == 0 {
		return fallback
	}
	return col(6, r.blocks...)
}
func (u *workbench) markdownBlocks(fragment string) []W {
	theme := markdownTheme{u.th.Face, u.fontSize(15), ink, muted, accentInk}
	var blocks []W
	var pending []*html.Node
	flush := func() {
		if spans := spansForNodes(pending, theme); len(spans) > 0 {
			blocks = append(blocks, richtext.Text(nil, u.th.Shaper, spans...).Layout)
		}
		pending = nil
	}
	for _, n := range markdownNodes(fragment) {
		if n.DataAtom != atom.Table {
			pending = append(pending, n)
			continue
		}
		flush()
		var rows []W
		for section := n.FirstChild; section != nil; section = section.NextSibling {
			for tr := section.FirstChild; tr != nil; tr = tr.NextSibling {
				if tr.DataAtom != atom.Tr {
					continue
				}
				var cells []layout.FlexChild
				for cell := tr.FirstChild; cell != nil; cell = cell.NextSibling {
					if cell.DataAtom != atom.Td && cell.DataAtom != atom.Th {
						continue
					}
					spans := spansForNodes([]*html.Node{cell}, theme)
					bg := paper
					if cell.DataAtom == atom.Th {
						bg = hover
					}
					cells = append(cells, layout.Flexed(1, func(g C) D {
						return card(full(g), bg, 0, inset(5, richtext.Text(nil, u.th.Shaper, spans...).Layout))
					}))
				}
				rows = append(rows, func(g C) D { return (layout.Flex{}).Layout(g, cells...) })
			}
		}
		blocks = append(blocks, col(1, rows...))
	}
	flush()
	return blocks
}

func copyText(g C, s string) {
	g.Execute(clipboard.WriteCmd{Type: "text/plain", Data: io.NopCloser(strings.NewReader(s))})
}
