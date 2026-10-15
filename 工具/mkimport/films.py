import re
from pathlib import Path

FILM_NAME = re.compile(r"^(.+) \((\d{4})\)$")
FILLABLE = ["中文名", "原名", "检索名", "上映年"]
TEMPLATE = Path(__file__).resolve().parents[2] / "模板" / "影片.md"
AUTO_SENTENCE = "海报和画面下载成功后再嵌入"


def films_from_records(records):
    films = {}
    for record in records:
        match = FILM_NAME.match(str(record.get("影片检索名") or ""))
        if not match:
            continue
        current = films.get(record["影片检索名"]) or {
            "检索名": record["影片检索名"],
            "中文名": match.group(1),
            "上映年": match.group(2),
            "原名": "",
        }
        original = record.get("影片原名").strip() if isinstance(record.get("影片原名"), str) else ""
        if not current["原名"] and original:
            current["原名"] = original
        films[record["影片检索名"]] = current
    return list(films.values())


def render_film_note(film, template=None):
    source = template if template is not None else TEMPLATE.read_text(encoding="utf-8")
    text = source.replace("附件/影片/检索名/", f"附件/影片/{film['检索名']}/")
    for key in ("中文名", "原名", "检索名", "上映日期", "上映年"):
        value = film.get(key) or ""
        text = _set_frontmatter(text, key, value)
    if not text.endswith("\n"):
        text += "\n"
    return text


def reorganize_film(path, template=None):
    path = Path(path)
    existing = path.read_text(encoding="utf-8")
    source = template if template is not None else TEMPLATE.read_text(encoding="utf-8")
    fields = _read_frontmatter(existing)
    if not fields.get("检索名"):
        fields["检索名"] = path.stem
    match = FILM_NAME.match(fields["检索名"])
    if match:
        fields.setdefault("中文名", match.group(1))
        if not fields.get("上映年"):
            fields["上映年"] = match.group(2)
    note = render_film_note(fields, source)
    for key, value in fields.items():
        if key == "类型" or value == "":
            continue
        note = _set_frontmatter(note, key, value)
    for heading in ("简介", "电影分析", "观影笔记", "演职员"):
        extras = _extra_lines(existing, source, heading)
        if extras:
            note = _replace_section(note, existing, heading)
    if note == existing:
        return "skip"
    path.write_text(note, encoding="utf-8")
    return "rewritten"


def _read_frontmatter(text):
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---", text)
    fields = {}
    if not match:
        return fields
    for line in match.group(1).splitlines():
        if not line or line.startswith(" ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def _set_frontmatter(text, key, value):
    match = re.match(r"^(---\r?\n)([\s\S]*?)(\r?\n---)", text)
    if not match:
        return text
    newline = "\r\n" if "\r\n" in match.group(0) else "\n"
    lines = match.group(2).splitlines()
    replacement = f"{key}: {value}" if str(value) != "" else f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            lines[index] = replacement
            break
    else:
        insert_at = len(lines)
        for index, line in enumerate(lines):
            if line.startswith("主演:"):
                insert_at = index + 1
                break
        lines.insert(insert_at, replacement)
    return f"{match.group(1)}{newline.join(lines)}{match.group(3)}{text[match.end() :]}"


def _section_body(text, heading):
    match = re.search(rf"^## {re.escape(heading)}\r?\n([\s\S]*?)(?=^## |\Z)", text, re.MULTILINE)
    return match.group(1) if match else ""


def _extra_lines(existing, template, heading):
    template_lines = {line.strip() for line in _section_body(template, heading).splitlines() if line.strip()}
    extras = []
    for line in _section_body(existing, heading).splitlines():
        stripped = line.strip()
        if not stripped or stripped in template_lines or AUTO_SENTENCE in stripped:
            continue
        extras.append(stripped)
    return extras


def _replace_section(note, existing, heading):
    pattern = rf"(^## {re.escape(heading)}\r?\n)[\s\S]*?(?=^## |\Z)"
    body = _section_body(existing, heading)
    return re.sub(pattern, rf"\1{body}", note, count=1, flags=re.MULTILINE)


def fill_film_note(markdown, film):
    match = re.match(r"^(---\r?\n)([\s\S]*?)(\r?\n---)", markdown)
    if not match:
        return {"markdown": markdown, "actions": [{"type": "skip-film", **film}]}
    newline = "\r\n" if "\r\n" in markdown else "\n"
    lines = re.split(r"\r?\n", match.group(2))
    actions = []
    for key in FILLABLE:
        value = film.get(key)
        if not value:
            continue
        index = next((i for i, line in enumerate(lines) if line.startswith(f"{key}:")), -1)
        if index < 0:
            continue
        current = lines[index][len(key) + 1 :].strip()
        if current:
            continue
        lines[index] = f"{key}: {value}"
        actions.append({"type": "fill-film", "field": key, **film})
    if not actions:
        return {"markdown": markdown, "actions": [{"type": "skip-film", **film}]}
    next_text = f"{match.group(1)}{newline.join(lines)}{match.group(3)}{markdown[match.end() :]}"
    return {"markdown": next_text, "actions": actions}
