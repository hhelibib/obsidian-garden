import re


def short_category_name(zh, en):
    original_zh = zh.strip() if isinstance(zh, str) else ""
    if original_zh:
        name = re.sub(r"^(奥斯卡金像奖|奥斯卡奖|奥斯卡|学院奖)\s*", "", original_zh)
        if name != original_zh and name.endswith("奖") and len(name) > 1:
            name = name[:-1].strip()
        if not name:
            name = original_zh
        return {"name": name, "original": original_zh, "shortened": name != original_zh, "missingChinese": False}
    original = en.strip() if isinstance(en, str) else ""
    return {"name": original or "未命名奖项", "original": original, "shortened": False, "missingChinese": True}


def person_search_name(zh, en):
    chinese = _person_stem(zh)
    english = _person_stem(en)
    title = chinese or english
    if not title:
        return None
    return {"检索名": title, "usedEnglish": not chinese}


def film_search_name(zh, en, year):
    if not isinstance(year, int):
        return None
    chinese = _clean_title(zh)
    english = _clean_title(en)
    title = chinese or english
    if not title:
        return None
    return {"检索名": f"{title} ({year})", "原名": english or chinese, "usedEnglish": not chinese}


def _person_stem(value):
    if not isinstance(value, str):
        return ""
    text = re.sub(r'[<>:"/\\|?*]', " ", value).strip()
    return re.sub(r"\s+", " ", text).rstrip(".")


def _clean_title(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    text = re.sub(r"\s*[（(](?:\d{4}年)?电影[)）]\s*$", "", text)
    text = re.sub(r"\s*\((?:film|\d{4}\s+film)\)\s*$", "", text, flags=re.IGNORECASE)
    return text.strip()
