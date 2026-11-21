package agent

import (
	"archive/zip"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
)

func isOffice(p string) bool {
	return slices.Contains([]string{".docx", ".xlsx", ".pptx"}, strings.ToLower(filepath.Ext(p)))
}

type officeDoc struct {
	*zip.ReadCloser
	sections []string
	parts    map[string]string
	shared   string
	sheet    bool
}

func (d *officeDoc) scan(part string, fn func(*xml.Decoder, xml.StartElement) error) error {
	f, e := d.Open(part)
	if e != nil {
		return e
	}
	defer f.Close()
	rd := xml.NewDecoder(io.LimitReader(f, 256<<20))
	for {
		t, e := rd.Token()
		if e == io.EOF {
			return nil
		}
		if e != nil {
			return e
		}
		if el, ok := t.(xml.StartElement); ok {
			if e = fn(rd, el); e != nil {
				return e
			}
		}
	}
}
func attr(el xml.StartElement, name string) string {
	for _, a := range el.Attr {
		if a.Name.Local == name {
			return a.Value
		}
	}
	return ""
}
func openOffice(p string) (*officeDoc, error) {
	z, e := zip.OpenReader(p)
	if e != nil {
		return nil, e
	}
	d := &officeDoc{ReadCloser: z, parts: map[string]string{}}
	ext := strings.ToLower(filepath.Ext(p))
	if ext == ".docx" {
		d.sections = []string{"document"}
		d.parts["document"] = "word/document.xml"
		return d, nil
	}
	d.sheet = ext == ".xlsx"
	main := "ppt/presentation.xml"
	if d.sheet {
		main = "xl/workbook.xml"
	}
	dir, name := path.Split(main)
	rels := map[string]string{}
	e = d.scan(path.Join(dir, "_rels", name+".rels"), func(_ *xml.Decoder, el xml.StartElement) error {
		if el.Name.Local == "Relationship" && attr(el, "TargetMode") != "External" {
			target := attr(el, "Target")
			if strings.HasPrefix(target, "/") {
				target = strings.TrimPrefix(path.Clean(target), "/")
			} else {
				target = path.Join(dir, target)
			}
			rels[attr(el, "Id")] = target
			if strings.HasSuffix(attr(el, "Type"), "/sharedStrings") {
				d.shared = target
			}
		}
		return nil
	})
	if e != nil {
		d.Close()
		return nil, e
	}
	e = d.scan(main, func(_ *xml.Decoder, el xml.StartElement) error {
		if (d.sheet && el.Name.Local != "sheet") || (!d.sheet && el.Name.Local != "sldId") {
			return nil
		}
		id := ""
		for _, a := range el.Attr {
			if a.Name.Local == "id" && a.Name.Space != "" {
				id = a.Value
			}
		}
		if part := rels[id]; part != "" {
			n := attr(el, "name")
			if !d.sheet {
				n = strconv.Itoa(len(d.sections) + 1)
			}
			d.sections = append(d.sections, n)
			d.parts[n] = part
		}
		return nil
	})
	if e != nil {
		d.Close()
		return nil, e
	}
	return d, nil
}
func texts(rd *xml.Decoder, el xml.StartElement) (string, error) {
	var buf strings.Builder
	for {
		t, e := rd.Token()
		if e != nil {
			return "", e
		}
		switch x := t.(type) {
		case xml.EndElement:
			if x.Name == el.Name {
				return buf.String(), nil
			}
		case xml.StartElement:
			if x.Name.Local == "t" {
				var text string
				if e = rd.DecodeElement(&text, &x); e != nil {
					return "", e
				}
				buf.WriteString(text)
			}
		}
	}
}
func (d *officeDoc) each(section string, fn func(M) error) error {
	return d.scan(d.parts[section], func(rd *xml.Decoder, el xml.StartElement) error {
		if d.sheet && el.Name.Local == "c" {
			item := M{"cell": attr(el, "r")}
			typ := attr(el, "t")
			for {
				t, e := rd.Token()
				if e != nil {
					return e
				}
				switch x := t.(type) {
				case xml.StartElement:
					if x.Name.Local == "f" || x.Name.Local == "v" || x.Name.Local == "t" {
						var v string
						if e = rd.DecodeElement(&v, &x); e != nil {
							return e
						}
						switch x.Name.Local {
						case "f":
							item["formula"] = v
						case "t":
							item["value"] = str(item["value"]) + v
						case "v":
							item["value"] = v
						}
					}
				case xml.EndElement:
					if x.Name == el.Name {
						if item["formula"] == nil && str(item["value"]) == "" {
							return nil
						}
						if typ == "s" {
							item["shared"] = item["value"]
						}
						return fn(item)
					}
				}
			}
		}
		if !d.sheet && el.Name.Local == "p" {
			t, e := texts(rd, el)
			if e != nil {
				return e
			}
			return fn(M{"text": t})
		}
		return nil
	})
}
func inspectOffice(root string, a M) (any, error) {
	p, e := within(root, str(a["path"]))
	if e != nil {
		return nil, e
	}
	if !isOffice(p) {
		return nil, errors.New("支持 XLSX、DOCX、PPTX 文件")
	}
	d, e := openOffice(p)
	if e != nil {
		return nil, e
	}
	defer d.Close()
	sec := str(a["section"])
	if sec == "" {
		return M{"sections": d.sections}, nil
	}
	if d.parts[sec] == "" {
		return nil, errors.New("未知工作表或章节")
	}
	start := max(0, integer(a["offset"]))
	n := min(200, max(1, integer(a["limit"])))
	i := 0
	items := []M{}
	stop := errors.New("page complete")
	more := false
	e = d.each(sec, func(m M) error {
		if i >= start+n {
			more = true
			return stop
		}
		if i >= start {
			items = append(items, m)
		}
		i++
		return nil
	})
	if e != nil && e != stop {
		return nil, e
	}
	wanted := map[string][]M{}
	for _, m := range items {
		if s, ok := m["shared"].(string); ok {
			wanted[s] = append(wanted[s], m)
			delete(m, "shared")
		}
	}
	if len(wanted) > 0 && d.shared != "" {
		idx := 0
		e = d.scan(d.shared, func(rd *xml.Decoder, el xml.StartElement) error {
			if el.Name.Local != "si" {
				return nil
			}
			key := strconv.Itoa(idx)
			idx++
			if cells := wanted[key]; len(cells) > 0 {
				v, e := texts(rd, el)
				if e != nil {
					return e
				}
				for _, m := range cells {
					m["value"] = v
				}
				delete(wanted, key)
				if len(wanted) == 0 {
					return stop
				}
			}
			return nil
		})
		if e != nil && e != stop {
			return nil, e
		}
	}
	var next any
	if more {
		next = start + len(items)
	}
	return M{"section": sec, "items": items, "next_offset": next}, nil
}
func officeText(p string, w io.Writer) error {
	d, e := openOffice(p)
	if e != nil {
		return e
	}
	defer d.Close()
	stringsByIndex := []string{}
	if d.sheet && d.shared != "" {
		e = d.scan(d.shared, func(rd *xml.Decoder, el xml.StartElement) error {
			if el.Name.Local != "si" {
				return nil
			}
			value, e := texts(rd, el)
			if e == nil {
				stringsByIndex = append(stringsByIndex, value)
			}
			return e
		})
		if e != nil {
			return e
		}
	}
	for _, section := range d.sections {
		if d.sheet {
			if _, e := fmt.Fprintln(w, section); e != nil {
				return e
			}
		}
		e = d.each(section, func(row M) error {
			if !d.sheet {
				_, e := fmt.Fprintln(w, str(row["text"]))
				return e
			}
			value := str(row["value"])
			if _, ok := row["shared"]; ok {
				idx, e := strconv.Atoi(value)
				if e != nil || idx < 0 || idx >= len(stringsByIndex) {
					return errors.New("Excel 文件的共享字符串索引无效")
				}
				value = stringsByIndex[idx]
			}
			_, e := fmt.Fprintln(w, row["cell"], value)
			return e
		})
		if e != nil {
			return e
		}
	}
	return nil
}
