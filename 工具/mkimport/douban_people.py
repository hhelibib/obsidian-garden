import json
import re
from pathlib import Path

from mkimport.douban import (
    DoubanBlocked,
    field_blank,
    note_stem,
    personage_id,
    poster_allowed,
    yaml_flow,
)
from mkimport.douban_fetch import re_search_subject
from mkimport.douban_import import _cell, _cite, _download_poster, _prepare, _write_detail, plan_detail
from mkimport.films import TEMPLATE, _read_frontmatter, _section_body, _set_frontmatter

PERSON_TEMPLATE = TEMPLATE.parent / "人物.md"
CREW_KEYS = (("导演", "导演"), ("编剧", "编剧"), ("主演", "主演"))


def import_people(vault, apply=False, images=True, timeout_ms=45000):
    vault = Path(vault)
    person_template = PERSON_TEMPLATE.read_text(encoding="utf-8")
    film_template = TEMPLATE.read_text(encoding="utf-8")
    people = _load_people(vault)
    assigned, conflicts = assign_search_names(people, vault)
    person_actions = [
        _prepare_person(vault, person, assigned.get(person["id"], ""), person_template, images)
        for person in people
    ]
    reasons = {str(item.get("id") or ""): item.get("reason") or "同名笔记的豆瓣人物编号不同" for item in conflicts}
    for action in person_actions:
        reason = reasons.get(action["id"])
        if not reason:
            continue
        action["type"] = "conflict"
        action["reason"] = reason
    film_actions, missing_people, pending_credits, missing_details = _plan_films(
        vault, assigned, film_template, images
    )
    if apply:
        for action in person_actions:
            if action["type"] != "ready":
                continue
            _write_person(vault, action, images, timeout_ms)
        for action in film_actions:
            if action["type"] != "ready":
                continue
            _write_detail(vault, action, images, timeout_ms)
        sync_appearances(vault)
    log_path = _write_missing_people(vault, missing_people)
    counts = _counts(person_actions, film_actions, missing_people, pending_credits, missing_details, conflicts)
    preview = _render_report(person_actions, film_actions, counts, log_path, vault, apply)
    inbox = vault / "收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    name = "报告" if apply else "预览"
    report_path = inbox / f"{name}-豆瓣人物.md"
    report_path.write_text(preview, encoding="utf-8")
    return {
        "preview": preview,
        "reportPath": report_path,
        "counts": counts,
        "missingLog": log_path,
        "personActions": person_actions,
        "filmActions": film_actions,
    }


def assign_search_names(people, vault):
    existing = _existing_people(vault)
    groups = {}
    for person in people:
        groups.setdefault(person.get("中文名") or "", []).append(person)
    assigned = {}
    conflicts = []
    for person in people:
        person_id = str(person.get("id") or "")
        chinese = person.get("中文名") or ""
        if not person_id or not chinese:
            conflicts.append({**person, "reason": "缺少中文名或人物编号"})
            continue
        matched = [item for item in existing.values() if item["id"] == person_id]
        if matched:
            assigned[person_id] = matched[0]["stem"]
            continue
        siblings = groups.get(chinese) or [person]
        taken = existing.get(note_stem(chinese))
        taken_by_other = bool(taken and taken["id"] not in ("", person_id))
        needs_suffix = len(siblings) > 1 or taken_by_other
        if taken and taken["id"] == "" and len(siblings) > 1:
            conflicts.append({**person, "reason": f"已有 {note_stem(chinese)} 没有人物编号，重名时不能判断是谁"})
            continue
        stem = _suffixed_stem(person) if needs_suffix else note_stem(chinese)
        occupant = existing.get(stem)
        if occupant and occupant["id"] not in ("", person_id):
            conflicts.append({**person, "reason": f"检索名 {stem} 已被其他人物占用"})
            continue
        assigned[person_id] = stem
        existing[stem] = {"stem": stem, "id": person_id, "path": None}
    return assigned, conflicts


def _existing_people(vault):
    found = {}
    folder = Path(vault) / "人物"
    if not folder.is_dir():
        return found
    for path in folder.glob("*.md"):
        if path.stem == "索引":
            continue
        fields = _read_frontmatter(path.read_text(encoding="utf-8"))
        found[path.stem] = {
            "stem": path.stem,
            "id": personage_id(fields.get("豆瓣")),
            "path": path,
        }
    return found


def _suffixed_stem(person):
    year = str(person.get("出生") or "")[:4]
    if re.fullmatch(r"\d{4}", year):
        return note_stem(f"{person['中文名']} ({year})")
    return note_stem(f"{person['中文名']} ({person['id']})")


def _load_people(vault):
    folder = Path(vault) / "收件箱" / "豆瓣" / "人物"
    people = []
    if not folder.is_dir():
        return people
    for path in sorted(folder.glob("*.json")):
        try:
            people.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return people


def _prepare_person(vault, person, stem, template, images):
    person_id = str(person.get("id") or "")
    base = {"id": person_id, "检索名": stem, "中文名": person.get("中文名") or "", "filled": [], "posterError": ""}
    if not stem:
        return {**base, "type": "skip", "reason": "缺少中文名或人物编号"}
    path = Path(vault) / "人物" / f"{stem}.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if existing:
        stored = personage_id(_read_frontmatter(existing).get("豆瓣"))
        if stored and stored != person_id:
            return {**base, "type": "conflict", "reason": f"已有笔记的人物编号与 {person_id} 不同", "path": path}
    note = existing or template.replace("附件/人物/检索名/", f"附件/人物/{stem}/")
    fields = {
        "中文名": person.get("中文名") or "",
        "原名": person.get("原名") or "",
        "检索名": stem,
        "身份": yaml_flow(person.get("身份") or []),
        "出生": person.get("出生") or "",
        "国籍": yaml_flow(person.get("国籍") or []),
        "imdb": person.get("imdb") or "",
        "豆瓣": person.get("豆瓣") or "",
    }
    current = _read_frontmatter(note)
    filled = []
    for key, value in fields.items():
        if not value or value == "[]":
            continue
        if not field_blank(key, current.get(key, "")):
            continue
        note = _set_frontmatter(note, key, value)
        current[key] = value
        filled.append(key)
    intro = (person.get("简介") or "").strip()
    note, intro_changed = _fill_subsection(note, "生平", intro)
    if intro_changed:
        filled.append("生平")
    action_name = "补全" if existing else "新建"
    if images and person.get("照片") and field_blank("照片", current.get("照片", "")):
        if poster_allowed(person["照片"]):
            filled.append("照片")
        else:
            base["posterError"] = "照片地址不是 img*.doubanio.com"
    if existing and note == existing and "照片" not in filled:
        return {**base, "type": "unchanged", "action": "未改", "path": path, "existing": existing, "note": note}
    return {
        **base,
        "type": "ready",
        "action": action_name,
        "path": path,
        "existing": existing,
        "note": note,
        "filled": filled,
        "person": person,
        "current": current,
    }


def _write_person(vault, action, images, timeout_ms):
    note = action["note"]
    existing = action.get("existing") or ""
    current = action.get("current") or {}
    filled = [item for item in action.get("filled") or [] if item != "照片"]
    person = action.get("person") or {}
    if images and "照片" in (action.get("filled") or []):
        try:
            relative = _download_poster(vault, action["检索名"], person["照片"], timeout_ms, "人物", "照片")
            if relative and field_blank("照片", current.get("照片", "")):
                note = _set_frontmatter(note, "照片", relative)
                note = note.replace(f"附件/人物/{action['检索名']}/照片.jpg", relative)
                filled.append("照片")
        except DoubanBlocked:
            raise
        except Exception as error:
            action["posterError"] = str(error)
    if note == existing:
        action["type"] = "unchanged"
        action["action"] = "未改"
        action["filled"] = []
        return
    action["path"].parent.mkdir(parents=True, exist_ok=True)
    action["path"].write_text(note, encoding="utf-8")
    action["filled"] = filled


def _fill_subsection(note, heading, body):
    if not body:
        return note, False
    if not body.endswith("\n"):
        body += "\n"
    pattern = rf"(^### {re.escape(heading)}\r?\n)([\s\S]*?)(?=^### |\Z)"
    match = re.search(pattern, note, re.M)
    if not match or match.group(2).strip():
        return note, False
    updated, count = re.subn(pattern, lambda found: found.group(1) + body + "\n", note, count=1, flags=re.M)
    return (updated, True) if count else (note, False)


def _plan_films(vault, assigned, template, images):
    actions = []
    missing = []
    pending = 0
    absent = 0
    seen_missing = set()
    folder = Path(vault) / "影片"
    detail_folder = Path(vault) / "收件箱" / "豆瓣" / "详情"
    if not folder.is_dir():
        return actions, missing, pending, absent
    for path in sorted(folder.glob("*.md")):
        if path.stem == "索引":
            continue
        fields = _read_frontmatter(path.read_text(encoding="utf-8"))
        subject_id = re_search_subject(fields.get("豆瓣") or "")
        if not subject_id:
            continue
        detail_path = detail_folder / f"{subject_id}.json"
        if not detail_path.is_file():
            absent += 1
            continue
        try:
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            absent += 1
            continue
        if "人物" not in detail:
            pending += 1
            continue
        for person in detail.get("人物") or []:
            person_id = str(person.get("id") or "")
            if not person_id or person_id in assigned or person_id in seen_missing:
                continue
            seen_missing.add(person_id)
            missing.append({"id": person_id, "姓名": person.get("姓名") or ""})
        action = _prepare(plan_detail(vault, detail, template), detail, template, images)
        if action.get("type") == "skip" or "note" not in action:
            actions.append(action)
            continue
        note, linked = _apply_links(action["note"], detail, assigned)
        action["note"] = note
        if linked:
            action["type"] = "ready"
            action["action"] = "补全" if action.get("existing") else action.get("action") or "补全"
            action["filled"] = list(action.get("filled") or []) + linked
        elif action.get("type") == "unchanged":
            action["filled"] = []
        actions.append(action)
    return actions, missing, pending, absent


def _apply_links(note, detail, name_map):
    filled = []
    updated = note
    credits = detail.get("人物") or []
    for role, key in CREW_KEYS:
        people = [person for person in credits if person.get("职位") == role]
        next_note, changed = _link_field(updated, key, people, name_map)
        updated = next_note
        if changed:
            filled.append(key)
    updated, changed = _link_table(updated, credits, name_map)
    if changed:
        filled.append("演职员")
    return updated, filled


def _link_field(note, key, people, name_map):
    current = _read_frontmatter(note).get(key, "")
    file_items = _parse_yaml_flow(current)
    source = file_items or [person.get("姓名") or "" for person in people if person.get("姓名")]
    if not source:
        return note, False
    linked = []
    for item in source:
        plain = _plain_name(item)
        if item.startswith("[[") and item.endswith("]]"):
            linked.append(item)
            continue
        stem = _resolve(plain, people, name_map)
        linked.append(f"[[{stem}]]" if stem else plain)
    if linked == file_items:
        return note, False
    return _set_frontmatter(note, key, yaml_flow(linked)), True


def _link_table(note, credits, name_map):
    body = _section_body(note, "演职员")
    if not body:
        return note, False
    lines = body.splitlines()
    header_at = next((index for index, line in enumerate(lines) if line.strip().startswith("| 职位 |")), -1)
    if header_at < 0:
        return note, False
    prefix = "\n".join(lines[:header_at]).strip()
    rows = []
    for line in lines[header_at + 1 :]:
        if not line.strip().startswith("|") or set(line.replace("|", "").strip()) <= {"-", " "}:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        while len(cells) < 3:
            cells.append("")
        if not _plain_name(cells[1]):
            continue
        rows.append(cells[:3])
    changed = False
    if not rows:
        rows = _rows_from_credits(credits, name_map)
        changed = bool(rows)
    else:
        rewritten = []
        for job, person, role in rows:
            plain = _plain_name(person)
            if person.startswith("[[") and person.endswith("]]"):
                rewritten.append([job, person, role])
                continue
            people = [item for item in credits if item.get("职位") == job and item.get("姓名") == plain]
            stem = _resolve(plain, people, name_map)
            if stem:
                rewritten.append([job, f"[[{stem}]]", role])
                changed = True
            else:
                rewritten.append([job, plain, role])
        rows = rewritten
        known = {_plain_name(person) for job, person, _role in rows if job == "编剧"}
        for person in credits:
            if person.get("职位") != "编剧" or not person.get("姓名"):
                continue
            display = _display_person(person, name_map)
            if _plain_name(display) in known:
                continue
            rows.append(["编剧", display, ""])
            known.add(_plain_name(display))
            changed = True
    if not changed:
        return note, False
    rendered = _render_rows(prefix, rows)
    pattern = r"(^## 演职员\r?\n)[\s\S]*?(?=^## |\Z)"
    updated, count = re.subn(pattern, lambda found: found.group(1) + rendered, note, count=1, flags=re.M)
    return (updated, updated != note) if count else (note, False)


def _rows_from_credits(credits, name_map):
    rows = []
    for role in ("导演", "编剧", "主演"):
        for person in credits:
            if person.get("职位") == role and person.get("姓名"):
                rows.append([role, _display_person(person, name_map), ""])
    return rows


def _display_person(person, name_map):
    stem = name_map.get(str(person.get("id") or ""))
    if stem:
        return f"[[{stem}]]"
    return person.get("姓名") or ""


def _render_rows(prefix, rows):
    lines = []
    if prefix:
        lines.extend([prefix, ""])
    lines.extend(["| 职位 | 人物 | 角色 |", "| --- | --- | --- |"])
    for job, person, role in rows:
        lines.append(f"| {job} | {_cell(person)} | {role} |")
    return "\n".join(lines) + "\n\n"


def _resolve(plain, people, name_map):
    stems = []
    for person in people:
        if person.get("姓名") != plain:
            continue
        stem = name_map.get(str(person.get("id") or ""))
        if stem and stem not in stems:
            stems.append(stem)
    if len(stems) == 1:
        return stems[0]
    return ""


def _plain_name(value):
    text = str(value or "").strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
        if "|" in text:
            text = text.split("|", 1)[0]
    return text.strip()


def _parse_yaml_flow(value):
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if not text:
        return []
    items = []
    current = []
    quote = ""
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
            continue
        if char in "\"'":
            quote = char
            continue
        if char == ",":
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def sync_appearances(vault):
    return repair_bidirectional_links(vault)["出演"]


def repair_bidirectional_links(vault):
    vault = Path(vault)
    people = _cast_people(vault)
    film_claims = {}
    for stem in people["stems"]:
        path = vault / "人物" / f"{stem}.md"
        if not path.is_file():
            continue
        for film in _linked_films(path.read_text(encoding="utf-8")):
            film_claims.setdefault(film, set()).add(stem)
    appearances = {stem: {} for stem in people["stems"]}
    films_changed = 0
    folder = vault / "影片"
    if folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            if path.stem == "索引":
                continue
            note = _read_note(path)
            if note is None:
                continue
            roles = _cast_roles(note)
            updated, linked = _bind_film_cast(note, people)
            for stem in _table_cast(updated, people):
                if stem not in linked:
                    linked.append(stem)
            for stem in sorted(film_claims.get(path.stem) or ()):
                if stem in linked or stem not in people["exact"]:
                    continue
                updated = _ensure_cast_member(updated, stem)
                linked.append(stem)
            if updated != note and _write_note(path, updated):
                films_changed += 1
            for stem in linked:
                role = ""
                for name, value in roles.items():
                    if name == stem or _resolve_cast(name, people) == stem:
                        role = value
                        break
                appearances[stem][path.stem] = role
    people_changed = 0
    for stem, films in appearances.items():
        path = vault / "人物" / f"{stem}.md"
        if not path.is_file():
            continue
        note = _read_note(path)
        if note is None:
            continue
        rows = sorted(films.items(), key=lambda item: item[0])
        updated = _write_appearance_section(note, rows)
        if updated != note and _write_note(path, updated):
            people_changed += 1
    return {"影片": films_changed, "人物": people_changed, "出演": appearances}


def _read_note(path):
    for _ in range(5):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _write_note(path, text):
    for _ in range(5):
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
            return True
        except OSError:
            continue
    print(f"写入失败 {path}")
    return False


def _linked_films(note):
    films = []
    body = note.split("### 出演", 1)[-1] if "### 出演" in note else ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or cells[0] in ("影片", "---"):
            continue
        film = _plain_name(cells[0])
        if film and film not in films:
            films.append(film)
    return films


def _table_cast(note, people):
    found = []
    for line in _section_body(note, "演职员").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0] != "主演":
            continue
        stem = _resolve_cast(_plain_name(cells[1]), people)
        if stem and stem not in found:
            found.append(stem)
    return found


def _ensure_cast_member(note, stem):
    target = f"[[{stem}]]"
    current = _parse_yaml_flow(_read_frontmatter(note).get("主演", ""))
    if target not in current and stem not in {_plain_name(item) for item in current}:
        current.append(target)
        note = _set_frontmatter(note, "主演", yaml_flow(current))
    if target in _section_body(note, "演职员"):
        return note
    row = f"| 主演 | {target} |  |"
    lines = note.splitlines(keepends=True)
    inserted = False
    for index, line in enumerate(lines):
        if line.strip() == "| --- | --- | --- |":
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines.insert(index + 1, row + newline)
            inserted = True
            break
    if not inserted:
        return note
    return "".join(lines)


def _cast_people(vault):
    exact = {}
    by_name = {}
    stems = []
    folder = Path(vault) / "人物"
    if not folder.is_dir():
        return {"exact": exact, "by_name": by_name, "stems": stems}
    for path in folder.glob("*.md"):
        if path.stem == "索引":
            continue
        stems.append(path.stem)
        exact[path.stem] = path.stem
        chinese = (_read_frontmatter(path.read_text(encoding="utf-8")).get("中文名") or "").strip()
        if chinese:
            by_name.setdefault(chinese, set()).add(path.stem)
    return {"exact": exact, "by_name": by_name, "stems": stems}


def _resolve_cast(plain, people):
    if plain in people["exact"]:
        return people["exact"][plain]
    stems = people["by_name"].get(plain) or set()
    if len(stems) == 1:
        return next(iter(stems))
    return ""


def _bind_film_cast(note, people):
    current = _parse_yaml_flow(_read_frontmatter(note).get("主演", ""))
    linked = []
    rewritten = []
    changed = False
    for item in current:
        plain = _plain_name(item)
        stem = _resolve_cast(plain, people)
        if not stem:
            rewritten.append(item)
            continue
        linked.append(stem)
        target = f"[[{stem}]]"
        if item == target:
            rewritten.append(item)
            continue
        rewritten.append(target)
        changed = True
    updated = note
    if changed:
        updated = _set_frontmatter(note, "主演", yaml_flow(rewritten))
    table, table_changed = _upgrade_cast_table(updated, people)
    if table_changed:
        updated = table
    return updated, linked


def _upgrade_cast_table(note, people):
    lines = note.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0] != "主演":
            continue
        plain = _plain_name(cells[1])
        stem = _resolve_cast(plain, people)
        if not stem or cells[1] == f"[[{stem}]]":
            continue
        lines[index] = line.replace(cells[1], f"[[{stem}]]", 1)
        changed = True
    return ("".join(lines), True) if changed else (note, False)


def _cast_roles(note):
    roles = {}
    body = _section_body(note, "演职员")
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0] != "主演":
            continue
        plain = _plain_name(cells[1])
        role = cells[2] if len(cells) > 2 else ""
        stem_key = plain
        if role and stem_key:
            found = roles.setdefault(stem_key, [])
            if role not in found:
                found.append(role)
    return {name: "、".join(parts) for name, parts in roles.items()}


def _write_appearance_section(note, rows):
    newline = "\r\n" if "\r\n" in note else "\n"
    table = ["| 影片 | 角色 |", "| --- | --- |"]
    if rows:
        for film, role in rows:
            table.append(f"| [[{film}]] | {role} |")
    else:
        table.append("| | |")
    body = newline.join(
        [
            "奖项不在这里手抄。奖项笔记的人物列链到本篇后，反向链接会汇总。",
            "",
            "### 出演",
            "",
            "影片「主演」链到本篇时，在这里链回该片。",
            "",
            *table,
            "",
        ]
    )
    pattern = r"^## 作品\r?\n[\s\S]*\Z"
    if not re.search(pattern, note, re.M):
        suffix = "" if note.endswith(("\n", "\r")) else newline
        return note + suffix + "## 作品" + newline + newline + body
    updated, count = re.subn(pattern, "## 作品" + newline + newline + body, note, count=1, flags=re.M)
    return updated if count else note


def _write_missing_people(vault, missing):
    if not missing:
        return None
    path = Path(vault) / "收件箱" / "豆瓣" / "缺少人物.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{item['id']}\t{item['姓名']}".rstrip() for item in missing]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _counts(person_actions, film_actions, missing, pending, absent, conflicts):
    del conflicts
    counts = {
        "新建人物": 0,
        "补全人物": 0,
        "未改人物": 0,
        "冲突人物": 0,
        "跳过人物": 0,
        "补全影片": 0,
        "未改影片": 0,
        "冲突影片": 0,
        "缺少人物": len(missing),
        "待抓演职员": pending,
        "缺少详情": absent,
    }
    for action in person_actions:
        if action.get("type") == "conflict":
            counts["冲突人物"] += 1
        elif action.get("action") == "新建" and action.get("type") == "ready":
            counts["新建人物"] += 1
        elif action.get("type") == "ready":
            counts["补全人物"] += 1
        elif action.get("type") == "unchanged":
            counts["未改人物"] += 1
        else:
            counts["跳过人物"] += 1
    for action in film_actions:
        if action.get("type") == "conflict":
            counts["冲突影片"] += 1
        elif action.get("type") == "ready":
            counts["补全影片"] += 1
        elif action.get("type") == "unchanged":
            counts["未改影片"] += 1
    return counts


def _render_report(person_actions, film_actions, counts, log_path, vault, apply):
    lines = [
        "# 豆瓣人物" + ("写入" if apply else "预览"),
        "",
        (
            f"新建人物 {counts['新建人物']}，补全人物 {counts['补全人物']}，未改人物 {counts['未改人物']}，"
            f"冲突人物 {counts['冲突人物']}，跳过人物 {counts['跳过人物']}。"
        ),
        (
            f"补全影片 {counts['补全影片']}，未改影片 {counts['未改影片']}，冲突影片 {counts['冲突影片']}，"
            f"缺少人物 {counts['缺少人物']}，待抓演职员 {counts['待抓演职员']}，缺少详情 {counts['缺少详情']}。"
        ),
        "",
    ]
    if not apply:
        lines.extend(["这次只生成预览，没有写入人物或影片。未改的笔记在 `--apply` 时也不会重写。", ""])
    if log_path:
        lines.extend([f"还没有人物页缓存的编号在 `{log_path.relative_to(vault).as_posix()}`。", ""])
    for action in person_actions:
        if action.get("type") == "unchanged" and not action.get("posterError"):
            continue
        if action.get("type") == "ready":
            filled = "、".join(action.get("filled") or []) or "无空字段"
            lines.append(f"- {action.get('action') or '处理'}人物 {_cite(action.get('检索名') or action.get('中文名'), apply)}：{filled}")
        elif action.get("type") == "conflict":
            lines.append(f"- 人物冲突 {action.get('中文名') or action.get('id')}：{action.get('reason')}")
        elif action.get("type") == "unchanged":
            lines.append(f"- 未改人物 {_cite(action.get('检索名'), apply)}")
        else:
            lines.append(f"- 跳过人物 {action.get('中文名') or action.get('id')}：{action.get('reason')}")
        if action.get("posterError"):
            lines.append(f"  - 照片：{action['posterError']}")
    for action in film_actions:
        if action.get("type") == "unchanged":
            continue
        title = action.get("检索名") or action.get("中文名") or action.get("id")
        if action.get("type") == "ready":
            filled = "、".join(action.get("filled") or []) or "无空字段"
            lines.append(f"- {action.get('action') or '处理'}影片 {_cite(title, apply)}：{filled}")
        elif action.get("type") == "conflict":
            lines.append(f"- 影片冲突 {_cite(title, apply)}：{action.get('reason')}")
        if action.get("posterError"):
            lines.append(f"  - 海报：{action['posterError']}")
    lines.append("")
    return "\n".join(lines)
