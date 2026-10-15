import json
from pathlib import Path

from mkimport.douban import (
    BROWSER,
    BLOCK_HINT,
    DoubanBlocked,
    _access_forbidden,
    field_blank,
    larger_poster,
    poster_allowed,
    same_subject,
    search_name,
    yaml_flow,
)
from mkimport.films import TEMPLATE, _extra_lines, _read_frontmatter, _set_frontmatter, render_film_note
from mkimport.http import request
from mkimport.images import EXTENSIONS

CREW_INTRO = "导演、编剧、主演已写在文首属性里，供检索和图谱使用。下表是完整演职员，含角色名。"


def import_details(vault, details, apply=False, images=True, timeout_ms=45000, label="全部", missing=None):
    vault = Path(vault)
    template = TEMPLATE.read_text(encoding="utf-8")
    missing = list(missing or [])
    actions = [_prepare(plan_detail(vault, detail, template), detail, template, images) for detail in details]
    if apply:
        for action in actions:
            if action["type"] != "ready":
                continue
            _write_detail(vault, action, images, timeout_ms)
    inbox = vault / "收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    log_path = _write_missing_log(vault, missing, label)
    counts = _counts(actions, missing)
    preview = _render_report(actions, apply, counts, log_path, vault)
    name = "报告" if apply else "预览"
    report_path = inbox / f"{name}-豆瓣-{label}.md"
    report_path.write_text(preview, encoding="utf-8")
    return {
        "preview": preview,
        "reportPath": report_path,
        "actions": actions,
        "counts": counts,
        "missingLog": log_path,
    }


def load_details(vault, list_path=None):
    folder = Path(vault) / "收件箱" / "豆瓣" / "详情"
    if list_path:
        packet = json.loads(Path(list_path).read_text(encoding="utf-8"))
        items = packet.get("items") or []
        label = Path(list_path).stem.removeprefix("列表-")
    else:
        items = [{"id": path.stem, "title": ""} for path in folder.glob("*.json")] if folder.is_dir() else []
        items.sort(key=lambda item: item["id"])
        label = "全部"
    details = []
    missing = []
    for item in items:
        subject_id = str(item.get("id") or "")
        path = folder / f"{subject_id}.json"
        if not path.is_file():
            missing.append({"id": subject_id, "title": item.get("title") or ""})
            continue
        details.append(json.loads(path.read_text(encoding="utf-8")))
    return details, missing, label


def plan_detail(vault, detail, template):
    subject_id = str(detail.get("id") or "")
    name = search_name(detail.get("中文名"), detail.get("上映年"))
    base = {
        "id": subject_id,
        "检索名": name,
        "中文名": detail.get("中文名") or "",
        "不确定": detail.get("不确定") or [],
    }
    if not name:
        return {**base, "type": "skip", "reason": "缺少中文名或上映年"}
    path = Path(vault) / "影片" / f"{name}.md"
    if not path.is_file():
        return {**base, "type": "ready", "action": "新建", "path": path, "detail": detail, "existing": ""}
    existing = path.read_text(encoding="utf-8")
    fields = _read_frontmatter(existing)
    if not same_subject(fields.get("豆瓣"), subject_id):
        return {**base, "type": "conflict", "reason": f"已有笔记的豆瓣编号与 {subject_id} 不同"}
    return {
        **base,
        "type": "ready",
        "action": "补全",
        "path": path,
        "detail": detail,
        "existing": existing,
    }


def _prepare(action, detail, template, images):
    if action["type"] != "ready":
        return action
    name = action["检索名"]
    existing = action.get("existing") or ""
    if action["action"] == "新建":
        note = render_film_note(
            {
                "检索名": name,
                "中文名": detail.get("中文名") or "",
                "原名": detail.get("原名") or "",
                "上映年": detail.get("上映年") or "",
                "上映日期": detail.get("上映日期") or "",
            },
            template,
        )
    else:
        note = existing
    note, filled, current = _merge_note(note, detail, name, action["action"], existing, template)
    action["note"] = note
    action["current"] = current
    action["filled"] = filled
    action["posterError"] = ""
    if images and detail.get("海报") and field_blank("海报", current.get("海报", "")):
        if poster_allowed(detail["海报"]):
            action["filled"] = filled + ["海报"]
        else:
            action["posterError"] = "海报地址不是 img*.doubanio.com"
    if action["action"] != "新建" and note == existing and "海报" not in action["filled"]:
        action["type"] = "unchanged"
        action["action"] = "未改"
        action["filled"] = []
    return action


def _merge_note(note, detail, name, action_name, existing, template):
    fields = {
        "中文名": detail.get("中文名") or "",
        "原名": detail.get("原名") or "",
        "检索名": name,
        "上映日期": detail.get("上映日期") or "",
        "上映年": detail.get("上映年") or "",
        "主要国家": detail.get("主要国家") or "",
        "imdb": detail.get("imdb") or "",
        "豆瓣": detail.get("豆瓣") or "",
        "制作国家": yaml_flow(detail.get("制作国家") or []),
        "导演": yaml_flow(detail.get("导演") or []),
        "编剧": yaml_flow(detail.get("编剧") or []),
        "主演": yaml_flow(detail.get("主演") or []),
        "aliases": yaml_flow(detail.get("又名") or []),
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
    synopsis = (detail.get("简介") or "").strip()
    if synopsis and (action_name == "新建" or not _extra_lines(existing, template, "简介")):
        note = _write_section(note, "简介", synopsis + "\n\n")
        filled.append("简介")
    crew = _crew_table(detail.get("导演") or [], detail.get("主演") or [], detail.get("编剧") or [])
    if crew and (action_name == "新建" or not _extra_lines(existing, template, "演职员")):
        note = _write_section(note, "演职员", crew)
        filled.append("演职员")
    return note, filled, current


def _write_detail(vault, action, images, timeout_ms):
    detail = action["detail"]
    name = action["检索名"]
    path = action["path"]
    note = action["note"]
    existing = "" if action["action"] == "新建" else action.get("existing") or ""
    current = action["current"]
    filled = [item for item in action.get("filled") or [] if item != "海报"]
    if images and "海报" in (action.get("filled") or []):
        try:
            relative = _download_poster(vault, name, detail["海报"], timeout_ms)
            if relative and field_blank("海报", current.get("海报", "")):
                note = _set_frontmatter(note, "海报", relative)
                note = note.replace(f"附件/影片/{name}/海报.jpg", relative)
                filled.append("海报")
        except DoubanBlocked:
            raise
        except Exception as error:
            action["posterError"] = str(error)
    if note == existing:
        action["type"] = "unchanged"
        action["action"] = "未改"
        action["filled"] = []
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note, encoding="utf-8")
    action["filled"] = filled


def _crew_table(directors, actors, writers=None):
    rows = []
    for name in directors:
        rows.append(f"| 导演 | {_cell(name)} | |")
    for name in writers or []:
        rows.append(f"| 编剧 | {_cell(name)} | |")
    for name in actors:
        rows.append(f"| 主演 | {_cell(name)} | |")
    if not rows:
        return ""
    return CREW_INTRO + "\n\n| 职位 | 人物 | 角色 |\n| --- | --- | --- |\n" + "\n".join(rows) + "\n\n"


def _cell(name):
    return str(name).replace("|", "\\|")


def _write_section(note, heading, body):
    import re

    if not body.endswith("\n"):
        body += "\n"
    pattern = rf"(^## {re.escape(heading)}\r?\n)[\s\S]*?(?=^## |\Z)"
    updated, count = re.subn(pattern, lambda match: match.group(1) + body, note, count=1, flags=re.M)
    return updated if count == 1 else note


def _download_poster(vault, search, url, timeout_ms, kind="影片", filename="海报"):
    if not poster_allowed(url):
        raise RuntimeError("海报地址不是 img*.doubanio.com")
    folder = Path(vault) / "附件" / kind / search
    existing = _existing_image(folder, search, kind, filename)
    if existing:
        return existing
    candidates = []
    large = larger_poster(url)
    if large != url:
        candidates.append(large)
    candidates.append(url)
    last = "海报下载失败"
    for candidate in candidates:
        response = request(
            candidate,
            timeout_ms=timeout_ms,
            headers={
                "User-Agent": BROWSER,
                "Referer": "https://movie.douban.com/",
                "Accept": "image/jpeg,image/png,image/webp,image/gif",
            },
        )
        if response["status"] == 404:
            last = "海报 HTTP 404"
            continue
        if response["status"] >= 400:
            raise RuntimeError(f"海报下载失败 HTTP {response['status']}")
        content_type = response["headers"].get("content-type", "").split(";", 1)[0].strip().lower()
        body = response["body"]
        preview = body[:4000].decode("utf-8", "replace") if body[:1] == b"<" or "html" in content_type else ""
        if _access_forbidden(preview, response.get("url") or ""):
            raise DoubanBlocked(BLOCK_HINT)
        extension = EXTENSIONS.get(content_type)
        if not extension:
            raise RuntimeError(f"不是允许的图片类型：{content_type or '未知'}")
        relative = f"附件/{kind}/{search}/{filename}.{extension}"
        absolute = Path(vault).joinpath(*relative.split("/"))
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(response["body"])
        return relative
    raise RuntimeError(last)


def _existing_image(folder, search, kind, filename):
    for extension in ("jpg", "jpeg", "png", "webp", "gif"):
        if (folder / f"{filename}.{extension}").is_file():
            return f"附件/{kind}/{search}/{filename}.{extension}"
    return ""


def _write_missing_log(vault, missing, label):
    if not missing:
        return None
    path = Path(vault) / "收件箱" / "豆瓣" / f"缺少详情-{label}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in missing:
        title = item.get("title") or ""
        lines.append(f"{item['id']}\t{title}".rstrip())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _counts(actions, missing):
    counts = {"新建": 0, "补全": 0, "未改": 0, "冲突": 0, "跳过": 0, "缺少详情": len(missing)}
    for action in actions:
        if action["type"] == "unchanged" or action.get("action") == "未改":
            counts["未改"] += 1
        elif action["type"] == "conflict":
            counts["冲突"] += 1
        elif action["type"] == "ready" and action.get("action") == "新建":
            counts["新建"] += 1
        elif action["type"] == "ready":
            counts["补全"] += 1
        else:
            counts["跳过"] += 1
    return counts


def _cite(name, apply=False):
    text = str(name or "").strip()
    if not text:
        return ""
    return f"`{text}`"


def _render_report(actions, apply, counts, log_path, vault):
    lines = [
        "# 豆瓣影片" + ("写入" if apply else "预览"),
        "",
        "豆瓣评分只出现在这里，不写入「我的评分」。",
        "",
        (
            f"新建 {counts['新建']}，补全 {counts['补全']}，未改 {counts['未改']}，"
            f"冲突 {counts['冲突']}，跳过 {counts['跳过']}，缺少详情 {counts['缺少详情']}。"
        ),
        "",
    ]
    if not apply:
        lines.extend(["这次只生成预览，没有写入影片。未改的笔记在 `--apply` 时也不会重写。", ""])
    if counts["未改"]:
        lines.extend(["未改的影片内容与现有笔记相同，没有写文件。", ""])
    if log_path:
        relative = log_path.relative_to(vault).as_posix()
        lines.extend([f"没有详情的影片没有落库，编号和片名在 `{relative}`。", ""])
    listed = False
    for action in actions:
        if action["type"] == "unchanged" and not action.get("posterError"):
            continue
        listed = True
        if action["type"] == "ready":
            filled = "、".join(action.get("filled") or []) or "无空字段"
            lines.append(f"- {action.get('action') or '处理'} {_cite(action['检索名'], apply)}：{filled}")
            if action.get("posterError"):
                lines.append(f"  - 海报：{action['posterError']}")
        elif action["type"] == "unchanged":
            lines.append(f"- 未改 {_cite(action['检索名'], apply)}")
            lines.append(f"  - 海报：{action['posterError']}")
        elif action["type"] == "conflict":
            lines.append(f"- 冲突，未覆盖 {_cite(action['检索名'], apply)}：{action['reason']}")
        else:
            lines.append(f"- 跳过 {action.get('中文名') or action.get('id')}：{action.get('reason')}")
        for item in action.get("不确定") or []:
            lines.append(f"  - 待核 {item.get('主题')}：{'；'.join(item.get('说法') or [])}")
    if not listed and not actions:
        lines.append("- 没有可整理的详情")
    lines.append("")
    return "\n".join(lines)
