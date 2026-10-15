import re
from urllib.parse import urlsplit

KNOWN_AWARDS = [
    "奥斯卡金像奖",
    "金球奖",
    "英国电影学院奖",
    "戛纳电影节",
    "威尼斯电影节",
    "柏林电影节",
]
RESULTS = {"获奖", "提名"}
ROLES = {
    "奖项": {"标识", "典礼"},
    "影片": {"海报", "画面"},
    "人物": {"照片"},
}
FILM_NAME = re.compile(r"^.+ \(\d{4}\)$")
IMAGE_HOSTS = {"upload.wikimedia.org", "commons.wikimedia.org", "image.tmdb.org"}


def validate_packet(packet):
    errors = []
    warnings = []
    if not isinstance(packet, dict):
        return {"errors": ["数据包必须是 JSON 对象"], "warnings": warnings, "records": [], "images": [], "uncertainties": [], "query": ""}
    if packet.get("schema") != "mk-import/1":
        errors.append('schema 必须是 "mk-import/1"')
    if not isinstance(packet.get("award_records"), list):
        errors.append("缺少 award_records 数组")
    records = []
    if isinstance(packet.get("award_records"), list):
        for index, row in enumerate(packet["award_records"]):
            result = _validate_record(row, index)
            errors.extend(result["errors"])
            if result.get("record"):
                records.append(result["record"])
    images = []
    if "images" in packet and packet["images"] is not None and not isinstance(packet["images"], list):
        errors.append("images 必须是数组")
    else:
        for index, image in enumerate(packet.get("images") or []):
            result = _validate_image(image, index)
            errors.extend(result["errors"])
            if result.get("image"):
                images.append(result["image"])
    return {
        "errors": errors,
        "warnings": warnings,
        "records": _dedupe_records(records, warnings),
        "images": images,
        "uncertainties": packet.get("uncertainties") if isinstance(packet.get("uncertainties"), list) else [],
        "query": packet.get("query") if isinstance(packet.get("query"), str) else "",
    }


def assert_image_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise RuntimeError("只允许 https")
    if parsed.hostname not in IMAGE_HOSTS:
        raise RuntimeError(f"主机不在允许列表：{parsed.hostname}")


def _validate_record(row, index):
    where = f"award_records[{index}]"
    errors = []
    if not isinstance(row, dict):
        return {"errors": [f"{where} 不是对象"]}
    award = _text(row.get("奖项"))
    category = _text(row.get("奖项名"))
    film = _text(row.get("影片检索名"))
    result = _text(row.get("结果"))
    if award not in KNOWN_AWARDS:
        errors.append(f"{where}.奖项 必须是已有笔记：{'、'.join(KNOWN_AWARDS)}")
    if not category or re.search(r"[{}|#\n\r]", category):
        errors.append(f"{where}.奖项名 不能为空，且不能含 {{}}|#")
    if not FILM_NAME.match(film) or re.search(r"[\[\]{}|#]", film):
        errors.append(f"{where}.影片检索名 必须是「中文名 (四位年份)」，例如「泰坦尼克号 (1997)」")
    if result not in RESULTS:
        errors.append(f"{where}.结果 只能是「获奖」或「提名」")
    year = _integer(row.get("颁奖年"))
    if year is None or year < 1900 or year > 2100:
        errors.append(f"{where}.颁奖年 必须是 1900 到 2100 的整数")
    edition = None
    if row.get("届次") not in (None, ""):
        edition = _integer(row.get("届次"))
        if edition is None or edition < 1 or edition > 500:
            errors.append(f"{where}.届次 必须是正整数或 null")
    person, person_error = _person_name(row.get("人物检索名"))
    if person_error:
        errors.append(f"{where}.人物检索名 必须是空或裸检索名，不能含 []{{}}|#")
    sources = _normalize_sources(row.get("sources"))
    if not sources:
        errors.append(f"{where} 缺少可打开的 http(s) 来源")
    if errors:
        return {"errors": errors}
    record = {
        "奖项": award,
        "颁奖年": year,
        "届次": edition,
        "奖项名": category,
        "结果": result,
        "影片检索名": film,
        "人物检索名": person,
        "sources": sources,
    }
    original = _text(row.get("影片原名"))
    if original:
        record["影片原名"] = original
    return {"errors": errors, "record": record}


def _validate_image(image, index):
    where = f"images[{index}]"
    errors = []
    if not isinstance(image, dict):
        return {"errors": [f"{where} 不是对象"]}
    owner_type = _text(image.get("ownerType"))
    role = _text(image.get("role"))
    owner = _text(image.get("owner"))
    if owner_type not in ROLES:
        errors.append(f"{where}.ownerType 只能是 奖项、影片、人物")
    elif role not in ROLES[owner_type]:
        errors.append(f"{where}.role「{role}」不能放在{owner_type}")
    if not owner or re.search(r'[\\/:*?"<>|\n\r]', owner):
        errors.append(f"{where}.owner 不是合法检索名")
    if owner_type == "奖项" and owner not in KNOWN_AWARDS:
        errors.append(f"{where}.owner 不是已有奖项笔记")
    year = None
    if role == "典礼":
        year = _integer(image.get("year"))
        if year is None:
            errors.append(f"{where}.year 在典礼图片上必填")
    url = _normalize_https(_text(image.get("url")))
    if not url:
        errors.append(f"{where}.url 无效")
    else:
        try:
            assert_image_url(url)
        except RuntimeError as error:
            errors.append(f"{where}.url {error}")
    if errors:
        return {"errors": errors}
    return {"errors": errors, "image": {"role": role, "ownerType": owner_type, "owner": owner, "year": year, "url": url}}


def _dedupe_records(records, warnings):
    found = {}
    for record in records:
        key = f"{record['奖项']}|{record['颁奖年']}|{record['奖项名']}|{record['影片检索名']}|{record.get('人物检索名') or ''}"
        current = found.get(key)
        if current is None:
            found[key] = record
            continue
        if current["结果"] != record["结果"]:
            warnings.append(f"{record['影片检索名']} 的「{record['奖项名']}」同时有获奖和提名，保留获奖")
            winner = record if record["结果"] == "获奖" else current
            other = current if record["结果"] == "获奖" else record
            found[key] = _merge_edition(winner, other)
            continue
        found[key] = _merge_edition(current, record)
    return list(found.values())


def _merge_edition(primary, extra):
    merged = dict(primary)
    if merged["届次"] is None:
        merged["届次"] = extra["届次"]
    sources = []
    seen = set()
    for source in [*primary["sources"], *extra["sources"]]:
        if source["url"] in seen:
            continue
        seen.add(source["url"])
        sources.append(source)
    merged["sources"] = sources
    if not merged.get("影片原名") and extra.get("影片原名"):
        merged["影片原名"] = extra["影片原名"]
    if not merged.get("人物检索名") and extra.get("人物检索名"):
        merged["人物检索名"] = extra["人物检索名"]
    return merged


def _normalize_sources(value):
    if not isinstance(value, list):
        return []
    sources = []
    for source in value:
        title = source.get("title").strip() if isinstance(source, dict) and isinstance(source.get("title"), str) else ""
        url = _normalize_https(source.get("url").strip() if isinstance(source, dict) and isinstance(source.get("url"), str) else "")
        if url.startswith("https://") or url.startswith("http://"):
            sources.append({"title": title, "url": url})
    return sources


def _normalize_https(url):
    if not url:
        return ""
    if url.startswith("http://commons.wikimedia.org/") or url.startswith("http://upload.wikimedia.org/"):
        return "https://" + url[len("http://") :]
    return url


def _person_name(value):
    if value in (None, ""):
        return "", False
    text = _text(value)
    if not text or re.search(r"[\[\]{}|#\n\r]", text):
        return "", True
    return text, False


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None
