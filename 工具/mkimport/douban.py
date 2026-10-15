import html
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from mkimport.http import _unwrap_cookie_value

PARTIAL_NOTE = "这是豆瓣按年份、地区、类型筛出来的列表，不是该年全部电影。"
LIST_URL = "https://movie.douban.com/j/new_search_subjects"
BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
BLOCK_MARKERS = ("sec.douban.com", "异常请求", "验证码", "登录跳转")
LIST_FIELDS = {"制作国家", "导演", "编剧", "主演", "aliases", "身份", "国籍"}
PROTECTED_FIELDS = {"我的评分", "观影状态", "观影日期", "观影次数"}
POSTER_HOST = re.compile(r"img\d+\.doubanio\.com")
SAFE_TOKEN = re.compile(r"^[\w·・.\-]+$", re.UNICODE)


class DoubanBlocked(RuntimeError):
    pass


class DoubanRateLimited(DoubanBlocked):
    pass


PASTE_HINT = "把浏览器里 movie.douban.com 的 Cookie 存成 工具/douban_cookie.txt 后再试。"
BLOCK_HINT = "豆瓣返回了「禁止访问」。已立即停止，请过一段时间再运行，不要马上重试，以免封号。"
LOGIN_HINT = (
    "豆瓣要求登录后再查列表。请在浏览器打开 https://movie.douban.com/ 并登录，"
    "然后从该页的请求头复制整行 Cookie，覆盖 工具/douban_cookie.txt。"
    "登录后的 Cookie 里必须有 dbcl2。"
)


def cookie_path(vault):
    return Path(vault) / "工具" / "douban_cookie.txt"


def load_cookie(vault):
    path = cookie_path(vault)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if any(char in text for char in "\r\n"):
        raise RuntimeError("Cookie 文件只能有一行")
    normalized = normalize_cookie_header(text)
    if normalized and normalized != text:
        path.write_text(normalized + "\n", encoding="utf-8")
    return normalized


def normalize_cookie_header(text):
    parts = [part.strip() for part in str(text).split(";") if part.strip()]
    return cookie_header_from_values(parts)


def cookie_header_from_values(values):
    if isinstance(values, str):
        values = [values]
    jar = {}
    for item in values or []:
        pair = str(item).split(";", 1)[0].strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name, value = name.strip(), _unwrap_cookie_value(value.strip())
        if not name or not value or not name.isascii() or not value.isascii():
            continue
        jar[name] = value
    return "; ".join(f"{name}={value}" for name, value in jar.items())


_COOKIE_LOCK = threading.Lock()


def read_cookie(vault):
    with _COOKIE_LOCK:
        return load_cookie(vault)


def save_latest_cookie(vault, updates):
    with _COOKIE_LOCK:
        current = load_cookie(vault)
        parts = [part.strip() for part in str(current or "").split(";") if part.strip()]
        merged = cookie_header_from_values([*parts, *(updates or [])])
        if not merged:
            return current, False
        if "dbcl2" in cookie_names(current) and "dbcl2" not in cookie_names(merged):
            return current, False
        if merged == current:
            return current, False
        path = cookie_path(vault)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(merged + "\n", encoding="utf-8")
        return merged, True


def store_anonymous_cookie(vault, status, headers, text):
    try:
        ensure_allowed(status, text)
    except DoubanBlocked as error:
        raise DoubanBlocked(f"领取匿名 Cookie 时被拦截：{error}。{PASTE_HINT}") from error
    cookie = cookie_header_from_values(headers.get("set-cookie") or [])
    if not cookie:
        raise DoubanBlocked(f"首页没有返回 Cookie。{PASTE_HINT}")
    path = cookie_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cookie + "\n", encoding="utf-8")
    return cookie


def douban_headers(cookie, referer):
    headers = {
        "User-Agent": BROWSER,
        "Referer": referer,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "identity",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def cookie_names(header):
    names = []
    for part in str(header or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        names.append(part.split("=", 1)[0].strip())
    return names


def require_login(cookie):
    if "dbcl2" not in cookie_names(cookie):
        raise DoubanBlocked(LOGIN_HINT)


def ensure_allowed(status, text, url=""):
    sample = text[:4000]
    if _access_forbidden(sample, url):
        raise DoubanBlocked(BLOCK_HINT)
    if status in (401, 403, 429):
        raise DoubanBlocked(f"豆瓣拒绝访问 HTTP {status}")
    if "请登录" in sample:
        raise DoubanBlocked(LOGIN_HINT)
    if "请求暂时被拒绝" in sample:
        raise DoubanRateLimited(
            "豆瓣暂时拒绝继续请求。已保存目前拉到的影片。请过一段时间再运行同一条命令，脚本会从上次的页继续。"
        )
    for marker in BLOCK_MARKERS:
        index = sample.find(marker)
        if index < 0:
            continue
        start = max(0, index - 30)
        snippet = " ".join(sample[start : index + len(marker) + 30].split())
        raise DoubanBlocked(f"豆瓣返回了验证或拦截页面：命中「{marker}」，附近文字：{snippet}")


def _access_forbidden(sample, url):
    if "/misc/sorry" in str(url):
        return True
    if "<title>禁止访问</title>" in sample:
        return True
    return "禁止访问" in sample and "TCaptcha" in sample


def list_page_url(year, region, genre, start):
    from urllib.parse import urlencode

    query = urlencode(
        {
            "sort": "U",
            "range": "0,10",
            "tags": "",
            "start": str(start),
            "genres": genre or "",
            "countries": region or "",
            "year_range": f"{year},{year}",
        }
    )
    return f"{LIST_URL}?{query}"


def parse_list_payload(payload):
    rows = payload.get("data")
    if rows is None:
        rows = payload.get("items") or []
    items = []
    for row in rows:
        subject_id = str(row.get("id") or "").strip()
        if not subject_id.isdigit():
            continue
        title = clean_text(row.get("title") or "")
        url = row.get("url") or f"https://movie.douban.com/subject/{subject_id}/"
        cover = row.get("cover") or ""
        if not cover and isinstance(row.get("pic"), dict):
            cover = row["pic"].get("large") or row["pic"].get("normal") or ""
        items.append(
            {
                "id": subject_id,
                "title": title,
                "year": str(row.get("year") or ""),
                "url": url,
                "rate": str(row.get("rate") or ""),
                "cover": cover,
            }
        )
    return items


def merge_list_items(existing, page_items):
    seen = {item["id"] for item in existing}
    added = [item for item in page_items if item["id"] not in seen]
    return existing + added, added


def list_filename(year, region, genre):
    return f"列表-{year}-{region or '全部'}-{genre or '全部'}.json"


def parse_subject_html(page, subject_id):
    subject_id = str(subject_id)
    title = _first(r'property="v:itemreviewed"[^>]*>\s*(.*?)\s*</span>', page)
    chinese, original = split_title(clean_text(title))
    year = _first(r'<span class="year">\((\d{4})\)</span>', page)
    dates = re.findall(r'property="v:initialReleaseDate" content="([^"]*)"', page)
    release, conflicts = choose_release(dates)
    if not year:
        match = re.search(r"(\d{4})", release)
        year = match.group(1) if match else ""
        if not year and dates:
            match = re.search(r"(\d{4})", dates[0])
            year = match.group(1) if match else ""
    countries = _slash_list(_first(r"制片国家/地区:</span>\s*([^<]+)", page))
    aliases = _slash_list(_first(r"又名:</span>\s*([^<]+)", page))
    summary = clean_summary(_first(r'<span property="v:summary"[^>]*>(.*?)</span>', page))
    poster = _first(
        r'<img[^>]+src="(https://img\d+\.doubanio\.com/[^"]+)"[^>]*title="点击看更多海报"',
        page,
    ) or _first(r'<meta property="og:image" content="(https://img\d+\.doubanio\.com/[^"]+)"', page)
    imdb = _first(r"IMDb:</span>\s*(tt\d+)", page)
    credits = _credits(page)
    uncertainties = []
    if conflicts:
        uncertainties.append({"主题": "上映日期", "说法": conflicts})
    return {
        "schema": "mk-douban-subject/1",
        "id": subject_id,
        "url": f"https://movie.douban.com/subject/{subject_id}/",
        "中文名": chinese,
        "原名": original,
        "上映年": year,
        "上映日期": "" if conflicts else release,
        "主要国家": countries[0] if countries else "",
        "制作国家": countries,
        "导演": _role_names(credits, "导演") or _people(page, "v:directedBy"),
        "编剧": _role_names(credits, "编剧"),
        "主演": _role_names(credits, "主演") or _people(page, "v:starring"),
        "人物": credits,
        "简介": summary,
        "又名": aliases,
        "imdb": imdb,
        "豆瓣": f"https://movie.douban.com/subject/{subject_id}/",
        "海报": poster,
        "豆瓣评分": _first(r'<strong[^>]*property="v:average"[^>]*>\s*([^<]*?)\s*</strong>', page),
        "不确定": uncertainties,
    }


def split_title(text):
    text = " ".join(text.split())
    if " " not in text or not re.search(r"[\u4e00-\u9fff]", text):
        return text, ""
    left, right = text.split(" ", 1)
    if re.search(r"[\u4e00-\u9fff]", left):
        return left.strip(), right.strip()
    return text, ""


def choose_release(dates):
    days = []
    for raw in dates:
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if match:
            days.append("-".join(match.groups()))
    unique = list(dict.fromkeys(days))
    if len(unique) == 1:
        return unique[0], []
    if len(unique) > 1:
        return "", unique
    return "", []


def note_stem(text):
    cleaned = re.sub(r'[<>:"/\\|?*]', " ", text or "").strip()
    return re.sub(r"\s+", " ", cleaned).rstrip(".")


def search_name(chinese, year):
    cleaned = note_stem(chinese)
    if not cleaned or not re.fullmatch(r"\d{4}", str(year or "")):
        return ""
    return f"{cleaned} ({year})"


def larger_poster(url):
    if not poster_allowed(url):
        return url
    return re.sub(r"/(?:s_ratio_poster|m|s)/", "/l/", url, count=1)


def poster_allowed(url):
    parts = urlsplit(url or "")
    return parts.scheme == "https" and bool(POSTER_HOST.fullmatch(parts.hostname or ""))


def yaml_flow(items):
    if not items:
        return "[]"
    return "[" + ", ".join(yaml_scalar(item) for item in items) + "]"


def yaml_scalar(text):
    text = str(text)
    if SAFE_TOKEN.fullmatch(text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def field_blank(key, value):
    if key in PROTECTED_FIELDS:
        return False
    if key in LIST_FIELDS:
        return str(value).strip() in ("", "[]")
    return str(value).strip() == ""


def same_subject(stored, subject_id):
    text = str(stored or "").strip()
    if not text:
        return True
    found = set(re.findall(r"subject/(\d+)", text))
    if text.isdigit():
        found.add(text)
    return not found or subject_id in found


def clean_text(value):
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def clean_summary(value):
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _first(pattern, page):
    match = re.search(pattern, page, re.S | re.I)
    return match.group(1).strip() if match else ""


def _people(page, rel):
    found = re.findall(rf'rel="{rel}"[^>]*>(.*?)</a>', page, re.S)
    names = []
    for name in found:
        cleaned = clean_text(name)
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


def _credits(page):
    people = []
    seen = set()
    for role in ("导演", "编剧", "主演"):
        match = re.search(
            rf"<span class=['\"]pl['\"]>\s*{role}\s*</span>:\s*<span class=['\"]attrs['\"]>(.*?)</span>",
            page,
            re.S,
        )
        if not match:
            continue
        for href, raw_name in re.findall(r'href="([^"]*)"[^>]*>(.*?)</a>', match.group(1), re.S):
            name = clean_text(raw_name)
            if not name:
                continue
            found = re.search(r"personage/(\d+)", href)
            person_id = found.group(1) if found else ""
            key = (role, person_id, name)
            if key in seen:
                continue
            seen.add(key)
            people.append({"id": person_id, "姓名": name, "职位": role})
    return people


def _role_names(credits, role):
    names = []
    for person in credits:
        if person.get("职位") != role:
            continue
        name = person.get("姓名") or ""
        if name and name not in names:
            names.append(name)
    return names


def parse_personage_html(page, person_id):
    person_id = str(person_id)
    chinese, original = split_title(clean_text(_first(r"<h1[^>]*>(.*?)</h1>", page)))
    props = _person_properties(page)
    birth = normalize_birth(props.get("出生日期") or "")
    place = props.get("出生地") or ""
    country = birth_country(place)
    jobs = _slash_list(props.get("职业") or "")
    imdb = props.get("IMDb编号") or ""
    if not re.fullmatch(r"nm\d+", imdb):
        imdb = ""
    return {
        "schema": "mk-douban-personage/1",
        "id": person_id,
        "url": f"https://www.douban.com/personage/{person_id}/",
        "中文名": chinese,
        "原名": original,
        "出生": birth,
        "出生地": place,
        "国籍": [country] if country else [],
        "身份": jobs,
        "imdb": imdb,
        "豆瓣": f"https://www.douban.com/personage/{person_id}/",
        "简介": _person_intro(page),
        "照片": _person_photo(page),
    }


def _person_properties(page):
    pairs = re.findall(
        r'<span class="label">\s*([^<:：]+)[:：]\s*</span>\s*<span class="value">\s*([\s\S]*?)\s*</span>',
        page,
    )
    return {clean_text(label): clean_text(value) for label, value in pairs}


def normalize_birth(value):
    text = clean_text(value)
    match = re.search(r"(\d{4})年(?:(\d{1,2})月(?:(\d{1,2})日)?)?", text)
    if not match:
        found = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
        return found.group(0) if found else ""
    year, month, day = match.groups()
    if month and day:
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if month:
        return f"{year}-{int(month):02d}"
    return year


def birth_country(place):
    text = clean_text(place)
    if not text:
        return ""
    return re.split(r"[,，]", text, maxsplit=1)[0].strip()


def _person_intro(page):
    match = re.search(
        r'<section class="subject-mod subject-intro">[\s\S]*?<div class="content">([\s\S]*?)</div>',
        page,
    )
    if not match:
        return ""
    fragment = re.sub(r"</p>\s*<p[^>]*>", "\n", match.group(1), flags=re.I)
    return clean_summary(fragment)


def _person_photo(page):
    return _first(
        r'<img[^>]+src="(https://img\d+\.doubanio\.com/view/personage/[^"]+)"',
        page,
    ) or _first(r'<meta property="og:image" content="(https://img\d+\.doubanio\.com/[^"]+)"', page)


def personage_id(value):
    found = re.findall(r"personage/(\d+)", str(value or ""))
    return found[-1] if found else ""


def _slash_list(value):
    text = clean_text(value)
    if not text:
        return []
    items = []
    for part in re.split(r"\s*/\s*", text):
        part = part.strip()
        if part and part not in items:
            items.append(part)
    return items
