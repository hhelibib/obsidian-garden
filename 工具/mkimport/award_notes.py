import re

COLOR = {"获奖": "yellow", "提名": "blue"}


def merge_award_markdown(markdown, records, embed=None):
    if not records and not embed:
        return {"markdown": markdown, "actions": []}
    newline = "\r\n" if "\r\n" in markdown else "\n"
    next_text = markdown
    actions = []
    if records:
        table = _replace_table(next_text, records, newline, actions)
        next_text = table["markdown"]
        next_text = _replace_chronos(next_text, records, newline, actions)
    if embed and embed not in next_text:
        next_text = _insert_embed(next_text, embed, newline)
        actions.append({"type": "embed", "text": embed})
    return {"markdown": next_text, "actions": actions}


def _replace_table(markdown, records, newline, actions):
    lines = re.split(r"\r?\n", markdown)
    try:
        header_at = next(index for index, line in enumerate(lines) if line.strip() == "| 颁奖年 | 届次 | 奖项 | 结果 | 影片 | 人物 |")
    except StopIteration as error:
        raise RuntimeError("找不到奖项表格表头") from error
    end = header_at + 1
    if end >= len(lines) or not re.match(r"^\|\s*-+", lines[end] or ""):
        raise RuntimeError("奖项表格缺少分隔行")
    end += 1
    existing = []
    while end < len(lines) and lines[end].strip().startswith("|"):
        row = _parse_row(lines[end])
        if row:
            existing.append(row)
        end += 1
    merged = [dict(row) for row in existing]
    for record in records:
        index = next((i for i, row in enumerate(merged) if _same_record(row, record)), -1)
        if index < 0:
            merged.append(_row_from_record(record))
            actions.append({"type": "add-row", **record})
            continue
        if merged[index]["届次"] is None and record["届次"] is not None:
            merged[index]["届次"] = record["届次"]
            actions.append({"type": "fill-edition", **record})
        elif _upgrade_person(merged[index], record):
            actions.append({"type": "link-person", **record})
        else:
            actions.append({"type": "skip-row", **record})
    merged.sort(key=_compare_key)
    rendered = [
        "| 颁奖年 | 届次 | 奖项 | 结果 | 影片 | 人物 |",
        "| --- | --- | --- | --- | --- | --- |",
        *[_render_row(row) for row in merged],
    ]
    return {"markdown": newline.join([*lines[:header_at], *rendered, *lines[end:]])}


def _replace_chronos(markdown, records, newline, actions):
    match = re.search(r"```chronos[\s\S]*?```", markdown)
    if not match:
        raise RuntimeError("找不到 chronos 时间线")
    block = match.group(0)
    lines = re.split(r"\r?\n", block)
    close = lines.pop()
    present = {}
    for index, line in enumerate(lines):
        identity = _chronos_identity(line)
        if identity:
            present[identity] = index
    known = set(present)
    additions = []
    for record in sorted(records, key=_compare_key):
        line = _chronos_line(record)
        identity = _chronos_identity(line)
        if identity in present:
            index = present[identity]
            if lines[index].strip() != line.strip():
                lines[index] = line
                actions.append({"type": "update-chronos", **record, "line": line})
            else:
                actions.append({"type": "skip-chronos", **record})
            continue
        if identity in known:
            actions.append({"type": "skip-chronos", **record})
            continue
        additions.append(line)
        known.add(identity)
        actions.append({"type": "add-chronos", **record, "line": line})
    if additions:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.extend(additions)
    lines.append(close)
    start = markdown.find(block)
    return markdown[:start] + newline.join(lines) + markdown[start + len(block) :]


def _insert_embed(markdown, embed, newline):
    match = re.match(r"^---\r?\n[\s\S]*?\r?\n---\r?\n", markdown)
    if not match:
        raise RuntimeError("缺少文首属性，无法插入标识")
    head = match.group(0)
    rest = re.sub(r"^(?:\r?\n)+", "", markdown[len(head) :])
    return f"{head}{newline}{embed}{newline}{newline}{rest}"


def _parse_row(line):
    cells = [cell.strip() for cell in line.strip().removeprefix("|").removesuffix("|").split("|")]
    if len(cells) != 6:
        raise RuntimeError(f"无法识别的表格行：{line.strip()}")
    if all(cell in ("", "[[]]") for cell in cells):
        return None
    film = re.fullmatch(r"\[\[(.+)\]\]", cells[4])
    if not film:
        raise RuntimeError(f"影片列必须是维基链接：{line.strip()}")
    person_name, person_linked = _parse_person(cells[5], line)
    if not re.fullmatch(r"-?\d+", cells[0]):
        raise RuntimeError(f"颁奖年无法识别：{line.strip()}")
    return {
        "颁奖年": int(cells[0]),
        "届次": None if cells[1] == "" else int(cells[1]) if re.fullmatch(r"-?\d+", cells[1]) else None,
        "奖项名": cells[2],
        "结果": cells[3],
        "影片检索名": film.group(1),
        "人物检索名": person_name,
        "人物可链接": person_linked,
    }


def _render_row(row):
    edition = "" if row["届次"] is None else str(row["届次"])
    person = _format_person(row.get("人物检索名") or "", row.get("人物可链接"))
    return f"| {row['颁奖年']} | {edition} | {row['奖项名']} | {row['结果']} | [[{row['影片检索名']}]] | {person} |"


def _chronos_line(record):
    person = _format_person(record.get("人物检索名") or "", record.get("人物可链接"))
    person_bit = f" {person}" if person else ""
    return f"- [{record['颁奖年']}] #{COLOR[record['结果']]} {{{record['奖项名']}}} [[{record['影片检索名']}]]{person_bit} | {record['结果']}"


def _chronos_identity(line):
    match = re.fullmatch(
        r"- \[(\d{4})(?:-\d{2}-\d{2})?\] #(yellow|blue) \{([^}]+)\} \[\[([^\]]+)\]\](?: (.*?))? \| (获奖|提名)\s*",
        line.strip(),
    )
    if not match:
        return None
    person = (match.group(5) or "").strip()
    if person.startswith("[[") and person.endswith("]]"):
        person = person[2:-2]
    return f"{match.group(1)}|{match.group(3)}|{match.group(4)}|{person}|{match.group(6)}"


def _same_record(row, record):
    return (
        row["颁奖年"] == record["颁奖年"]
        and row["奖项名"] == record["奖项名"]
        and row["结果"] == record["结果"]
        and row["影片检索名"] == record["影片检索名"]
        and (row.get("人物检索名") or "") == (record.get("人物检索名") or "")
    )


def _compare_key(row):
    return (row["颁奖年"], 0 if row["结果"] == "获奖" else 1, row["奖项名"], row["影片检索名"], row.get("人物检索名") or "")


def _row_from_record(record):
    return {
        "颁奖年": record["颁奖年"],
        "届次": record["届次"],
        "奖项名": record["奖项名"],
        "结果": record["结果"],
        "影片检索名": record["影片检索名"],
        "人物检索名": record.get("人物检索名") or "",
        "人物可链接": bool(record.get("人物可链接")) and bool(record.get("人物检索名")),
    }


def _upgrade_person(row, record):
    if row.get("人物可链接") or not record.get("人物可链接"):
        return False
    if (row.get("人物检索名") or "") != (record.get("人物检索名") or ""):
        return False
    row["人物可链接"] = True
    return True


def _parse_person(cell, line):
    if cell in ("", "[[]]"):
        return "", False
    linked = re.fullmatch(r"\[\[(.+)\]\]", cell)
    if linked:
        return linked.group(1), True
    if "[" in cell or "]" in cell:
        raise RuntimeError(f"人物列必须是空、裸人名或维基链接：{line.strip()}")
    return cell, False


def _format_person(name, linked):
    if not name:
        return ""
    if linked:
        return f"[[{name}]]"
    return name
