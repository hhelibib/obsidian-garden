import json
from pathlib import Path

from mkimport.award_notes import merge_award_markdown
from mkimport.films import _read_frontmatter, fill_film_note, films_from_records, render_film_note
from mkimport.images import download_image, file_stem
from mkimport.packet import KNOWN_AWARDS, validate_packet
from mkimport.review import partition_uncertainties

VAULT = Path(__file__).resolve().parents[2]
CHANGES = {"add-row", "add-chronos", "update-chronos", "fill-edition", "link-person", "embed"}

HELP = """用法
  python 工具/import_packet.py <数据包.json>
  python 工具/import_packet.py <数据包.json> --apply
  python 工具/import_packet.py <数据包.json> --apply --no-images

默认只生成 收件箱/预览-*.md，不改笔记。--apply 才写入奖项和影片骨架。
待核如果涉及检索名、奖项名、结果或中文名，即使 --apply 也停在预览。
图片默认随 --apply 下载；--no-images 只写文字。
"""


def import_packet(packet_path, apply=False, images=True, vault=None, timeout_ms=45000):
    vault = Path(vault or VAULT)
    packet_path = Path(packet_path)
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"JSON 无法解析：{error}") from error
    checked = validate_packet(packet)
    if checked["errors"]:
        raise RuntimeError("\n".join(checked["errors"]))
    review = partition_uncertainties(checked["uncertainties"])
    held = bool(review["blocking"])

    _resolve_people(vault, checked["records"])
    by_award = {name: [] for name in KNOWN_AWARDS}
    for record in checked["records"]:
        by_award[record["奖项"]].append(record)

    file_plans = []
    for award, records in by_award.items():
        if not records:
            continue
        relative = f"奖项/{award}.md"
        absolute = vault.joinpath(*relative.split("/"))
        if not absolute.is_file():
            raise RuntimeError(f"找不到已有笔记：{relative}。本阶段只更新已有奖项，不新建。")
        original = absolute.read_text(encoding="utf-8")
        merged = merge_award_markdown(original, records)
        file_plans.append(
            {"award": award, "relative": relative, "absolute": absolute, "original": original, "records": records, "merged": merged}
        )

    film_plans = _plan_films(vault, checked["records"])
    image_plans = [
        {**image, "destination": f"附件/{image['ownerType']}/{image['owner']}/{file_stem(image)}.(jpg|png|webp|gif)"}
        for image in checked["images"]
    ]
    preview = _render_report(
        title="导入报告" if apply else "导入预览",
        query=checked["query"],
        warnings=checked["warnings"],
        uncertainties=checked["uncertainties"],
        blocking=review["blocking"],
        file_plans=file_plans,
        film_plans=film_plans,
        image_plans=image_plans,
        apply=apply and not held,
        images=images,
        held=held,
    )
    report_name = f"{'报告' if apply and not held else '预览'}-{packet_path.stem}.md"
    report_path = vault / "收件箱" / report_name
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if not apply or held:
        report_path.write_text(preview, encoding="utf-8")
        return {"reportPath": report_path, "wroteNotes": False, "held": held, "preview": preview}

    downloaded = []
    if images:
        for image in checked["images"]:
            try:
                downloaded.append({"image": image, **download_image(vault, image, timeout_ms=timeout_ms)})
            except Exception as error:
                downloaded.append({"image": image, "error": str(error)})

    written = []
    for plan in film_plans:
        if plan["action"] != "skip-film":
            plan["absolute"].parent.mkdir(parents=True, exist_ok=True)
            plan["absolute"].write_text(plan["markdown"], encoding="utf-8")
            written.append(plan["relative"])
        if plan["removeRoot"]:
            plan["rootStub"].unlink()
    for plan in file_plans:
        embed = next(
            (
                item.get("embed")
                for item in downloaded
                if item.get("embed") and item["image"]["owner"] == plan["award"] and item["image"]["ownerType"] == "奖项"
            ),
            None,
        )
        final = merge_award_markdown(plan["original"], plan["records"], embed)
        if not any(action["type"] in CHANGES for action in final["actions"]):
            continue
        plan["absolute"].write_text(final["markdown"], encoding="utf-8")
        written.append(plan["relative"])

    applied = _render_report(
        title="导入报告",
        query=checked["query"],
        warnings=checked["warnings"],
        uncertainties=checked["uncertainties"],
        blocking=review["blocking"],
        file_plans=file_plans,
        film_plans=film_plans,
        image_plans=[
            {
                **item["image"],
                "destination": item.get("relativePath") or item["image"]["url"],
                "skipped": item.get("skipped"),
                "error": item.get("error"),
            }
            for item in downloaded
        ],
        apply=True,
        images=images,
        written=written,
    )
    report_path.write_text(applied, encoding="utf-8")
    return {"reportPath": report_path, "wroteNotes": bool(written), "written": written, "preview": applied, "held": False}


def _plan_films(vault, records):
    plans = []
    for film in films_from_records(records):
        relative = f"影片/{film['检索名']}.md"
        absolute = vault / "影片" / f"{film['检索名']}.md"
        root_stub = vault / f"{film['检索名']}.md"
        remove_root = False
        try:
            remove_root = root_stub.read_text(encoding="utf-8").strip() == ""
        except OSError:
            remove_root = False
        if absolute.is_file():
            filled = fill_film_note(absolute.read_text(encoding="utf-8"), film)
            action = "fill-film" if any(item["type"] == "fill-film" for item in filled["actions"]) else "skip-film"
            markdown = filled["markdown"]
        else:
            markdown = render_film_note(film)
            action = "create-film"
        plans.append({**film, "relative": relative, "absolute": absolute, "rootStub": root_stub, "removeRoot": remove_root, "markdown": markdown, "action": action})
    return plans


def _render_report(title, query, warnings, uncertainties, blocking, file_plans, film_plans, image_plans, apply, images, written=None, held=False):
    written = written or []
    blocking = blocking or []
    if held:
        lead = "待核涉及检索名、奖项名、结果或中文名，笔记没有写入。"
    elif apply:
        lead = "笔记已按下面的结果写入。" if written else "文字没有新改动。"
    else:
        lead = "这是预览。笔记还没有修改。确认后执行 `工具\\执行导入.bat`。"
    lines = ["---", "类型: 导入报告", "---", "", f"# {title}", "", lead, "", f"检索：{query}" if query else "", "", "## 奖项", ""]
    if not file_plans:
        lines.extend(["没有可写入的奖项记录。", ""])
    for plan in file_plans:
        lines.extend([f"### {plan['award']}", ""])
        changes = [action for action in plan["merged"]["actions"] if action["type"] in ("add-row", "fill-edition", "link-person")]
        skipped = [action for action in plan["merged"]["actions"] if action["type"] == "skip-row"]
        chronos_adds = sum(1 for action in plan["merged"]["actions"] if action["type"] == "add-chronos")
        chronos_updates = sum(1 for action in plan["merged"]["actions"] if action["type"] == "update-chronos")
        if not changes:
            lines.append("表格没有新行。")
        for action in changes:
            edition = "" if action["届次"] is None else f"第{action['届次']}届 "
            if action["type"] == "fill-edition":
                verb = "补届次"
            elif action["type"] == "link-person":
                verb = "补人物链接"
            else:
                verb = "追加"
            person = action.get("人物检索名") or ""
            person_bit = f" `{person}`" if person else ""
            lines.append(
                f"- {verb}：{action['颁奖年']} {edition}{action['奖项名']} {action['结果']} `{action['影片检索名']}`{person_bit}"
            )
        if chronos_adds:
            lines.append(f"- 时间线追加 {chronos_adds} 条")
        if chronos_updates:
            lines.append(f"- 时间线更新 {chronos_updates} 条")
        if skipped:
            lines.append(f"- 已存在，跳过 {len(skipped)} 行")
        lines.append("")
    lines.extend(["## 影片", ""])
    if not film_plans:
        lines.extend(["数据包没有影片。", ""])
    else:
        for plan in film_plans:
            if plan["action"] == "create-film":
                verb = "已新建" if apply else "将新建"
            elif plan["action"] == "fill-film":
                verb = "已补空字段" if apply else "将补空字段"
            else:
                verb = "已存在，跳过"
            lines.append(f"- {verb}：`影片/{plan['检索名']}.md`")
        lines.append("")
    lines.extend(["## 图片", ""])
    if not images:
        lines.extend(["本次不下载图片。", ""])
    elif not image_plans:
        lines.extend(["数据包没有图片。", ""])
    else:
        for image in image_plans:
            if image.get("error"):
                state = f"失败：{image['error']}"
            elif image.get("skipped"):
                state = "已存在，跳过"
            elif apply:
                state = "已下载"
            else:
                state = "将下载"
            lines.append(f"- {image['ownerType']} / {image['owner']} / {image['role']} → `{image['destination']}`（{state}）")
        lines.append("")
    if written:
        lines.extend(["## 已写入", ""])
        lines.extend(f"- `{item}`" for item in written)
        lines.append("")
    if warnings:
        lines.extend(["## 警告", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    if blocking:
        lines.extend(["## 拦住的待核", ""])
        lines.extend(f"- {item.get('主题') or '未命名'}" for item in blocking)
        lines.append("")
    noted = [item for item in (uncertainties or []) if item not in blocking]
    if noted:
        lines.extend(["## 待核", ""])
        for item in noted:
            topic = item.get("主题") or "未命名"
            says = "；".join(item.get("说法") or []) if isinstance(item.get("说法"), list) else ""
            lines.append(f"- {topic}：{says}")
        lines.extend(["这些待核不写入影片的上映日期。骨架只保留检索名里的年份。", ""])
    lines.extend(["观影评分、观影笔记和电影分析不会被覆盖。", ""])
    compacted = []
    for index, line in enumerate(lines):
        if line != "" or (index > 0 and lines[index - 1] != ""):
            compacted.append(line)
    return "\n".join(compacted) + "\n"


def _resolve_people(vault, records):
    exact, by_chinese = _person_index(vault)
    for record in records:
        raw = record.get("人物检索名") or ""
        if not raw:
            record["人物检索名"] = ""
            record["人物可链接"] = False
            continue
        if raw in exact:
            record["人物检索名"] = exact[raw]
            record["人物可链接"] = True
            continue
        stems = by_chinese.get(raw) or set()
        if len(stems) == 1:
            record["人物检索名"] = next(iter(stems))
            record["人物可链接"] = True
            continue
        record["人物可链接"] = False


def _person_index(vault):
    exact = {}
    by_chinese = {}
    folder = Path(vault) / "人物"
    if not folder.is_dir():
        return exact, by_chinese
    for path in folder.glob("*.md"):
        if path.stem == "索引":
            continue
        exact[path.stem] = path.stem
        fields = _read_frontmatter(path.read_text(encoding="utf-8"))
        chinese = (fields.get("中文名") or "").strip()
        if chinese:
            by_chinese.setdefault(chinese, set()).add(path.stem)
    return exact, by_chinese
