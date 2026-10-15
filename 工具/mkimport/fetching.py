import json
import re
from datetime import date
from pathlib import Path

from mkimport.http import request_text
from mkimport.names import film_search_name, person_search_name, short_category_name
from mkimport.search_prompt import write_search_prompt

VAULT = Path(__file__).resolve().parents[2]
OSCAR = "奥斯卡金像奖"
SERIES = "Q19020"

HELP = """用法
  python 工具/fetch_awards.py --year 1998
  python 工具/fetch_awards.py --year 1998 --category 最佳影片
  不写 --award 时默认拉取奥斯卡金像奖。

目前只拉取奥斯卡金像奖。其他五个奖项用 工具/prompts/检索-奖项.md 生成同一格式的 JSON。
结果写入 收件箱/，不直接改笔记。
"""


def fetch_awards(award, year, category="", out="", timeout_ms=45000, vault=None):
    vault = Path(vault or VAULT)
    if award != OSCAR:
        raise RuntimeError(
            f"目前只从 Wikidata 拉取「{OSCAR}」。\n"
            "金球奖、英国电影学院奖、戛纳电影节、威尼斯电影节、柏林电影节请用 工具/prompts/检索-奖项.md。"
        )
    if not isinstance(year, int) or year < 1929 or year > 2100:
        raise RuntimeError("年份必须是 1929 到 2100 的整数")
    try:
        ceremony = _find_ceremony(year, timeout_ms)
    except Exception as error:
        prompt_path = _fallback_prompt(vault, award, year, category)
        raise RuntimeError(f"{error}\n无法连接 Wikidata。已写好提示词：{prompt_path}") from error
    rows = _sparql(_awards_query(ceremony["id"]), timeout_ms)
    image_rows = _sparql(_image_query(), timeout_ms)
    built = build_packet(year, category, ceremony, rows["results"]["bindings"], image_rows["results"]["bindings"])
    if not built["award_records"]:
        raise RuntimeError(f"没有找到 {year} 年「{category}」的记录" if category else f"没有找到 {year} 年的奥斯卡影片记录")
    filename = Path(out) if out else vault / "收件箱" / (f"{OSCAR}-{year}-{category}.json" if category else f"{OSCAR}-{year}.json")
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text(json.dumps(built, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"filename": filename, "count": len(built["award_records"])}


def build_packet(year, category, ceremony, rows, image_rows):
    grouped = {}
    uncertainties = []
    for row in rows:
        award_id = _qid((row.get("award") or {}).get("value"))
        film_id = _qid((row.get("film") or {}).get("value"))
        person_id = _qid((row.get("person") or {}).get("value"))
        if not award_id or not film_id:
            continue
        key = f"{award_id}|{film_id}|{person_id}"
        current = grouped.get(key) or {
            "awardId": award_id,
            "filmId": film_id,
            "personId": person_id,
            "awardZh": "",
            "awardEn": "",
            "filmZh": "",
            "filmEn": "",
            "personZh": "",
            "personEn": "",
            "years": [],
            "result": (row.get("result") or {}).get("value"),
            "sources": set(),
            "awardRank": 0,
            "filmRank": 0,
            "personRank": 0,
        }
        award_zh = _first_text(row.get("awardZhCn"), row.get("awardZh"))
        award_rank = _label_rank(row.get("awardZhCn"), row.get("awardZh"))
        if award_rank >= (current["awardRank"] or 0) and award_zh:
            current["awardZh"] = award_zh
            current["awardRank"] = award_rank
        current["awardEn"] = current["awardEn"] or (row.get("awardEn") or {}).get("value") or ""
        film_zh = _first_text(row.get("filmZhCn"), row.get("filmZhHans"), row.get("filmZh"))
        film_rank = _label_rank(row.get("filmZhCn"), row.get("filmZhHans"), row.get("filmZh"))
        if film_rank >= (current["filmRank"] or 0) and film_zh:
            current["filmZh"] = film_zh
            current["filmRank"] = film_rank
        current["filmEn"] = current["filmEn"] or (row.get("filmEn") or {}).get("value") or ""
        person_zh = _first_text(row.get("personZhCn"), row.get("personZh"))
        person_rank = _label_rank(row.get("personZhCn"), row.get("personZh"))
        if person_rank >= (current["personRank"] or 0) and person_zh:
            current["personZh"] = person_zh
            current["personRank"] = person_rank
        current["personEn"] = current["personEn"] or (row.get("personEn") or {}).get("value") or ""
        pub_year = _integer((row.get("pubYear") or {}).get("value"))
        if pub_year:
            current["years"].append(pub_year)
        if (row.get("result") or {}).get("value") == "获奖":
            current["result"] = "获奖"
        source_url = (row.get("sourceUrl") or {}).get("value")
        if source_url:
            current["sources"].add(source_url)
        grouped[key] = current

    records = []
    for item in grouped.values():
        category_name = short_category_name(item["awardZh"], item["awardEn"])
        if category and category_name["name"] != category and category_name["original"] != category:
            continue
        release_year = min(item["years"]) if item["years"] else None
        film = film_search_name(item["filmZh"], item["filmEn"], release_year)
        if not film:
            uncertainties.append(
                {
                    "主题": "影片检索名",
                    "说法": [f"{item['filmId']} 缺少中文名或上映年，未写入"],
                    "sources": [{"title": "Wikidata", "url": f"https://www.wikidata.org/wiki/{item['filmId']}"}],
                }
            )
            continue
        if category_name["missingChinese"]:
            uncertainties.append(
                {
                    "主题": "奖项中文名",
                    "说法": [f"{item['awardId']} 没有中文标签，表格使用了「{category_name['name']}」"],
                    "sources": [{"title": "Wikidata", "url": f"https://www.wikidata.org/wiki/{item['awardId']}"}],
                }
            )
        if film["usedEnglish"]:
            uncertainties.append(
                {
                    "主题": "影片中文名",
                    "说法": [f"{item['filmId']} 没有中文标签，检索名使用了「{film['检索名']}」"],
                    "sources": [{"title": "Wikidata", "url": f"https://www.wikidata.org/wiki/{item['filmId']}"}],
                }
            )
        person_name = ""
        if item["personId"]:
            person = person_search_name(item["personZh"], item["personEn"])
            if not person:
                uncertainties.append(
                    {
                        "主题": "人物姓名",
                        "说法": [f"{item['personId']} 缺少姓名，未写入"],
                        "sources": [{"title": "Wikidata", "url": f"https://www.wikidata.org/wiki/{item['personId']}"}],
                    }
                )
                continue
            if person["usedEnglish"]:
                uncertainties.append(
                    {
                        "主题": "人物姓名",
                        "说法": [f"{item['personId']} 没有中文标签，检索名使用了「{person['检索名']}」"],
                        "sources": [{"title": "Wikidata", "url": f"https://www.wikidata.org/wiki/{item['personId']}"}],
                    }
                )
            person_name = person["检索名"]
        sources = [
            {"title": f"Wikidata {item['filmId']}", "url": f"https://www.wikidata.org/wiki/{item['filmId']}"},
            {"title": f"Wikidata {ceremony['id']}", "url": f"https://www.wikidata.org/wiki/{ceremony['id']}"},
            *[{"title": "来源", "url": url} for url in item["sources"]],
        ]
        records.append(
            {
                "奖项": OSCAR,
                "颁奖年": year,
                "届次": ceremony["edition"],
                "奖项名": category_name["name"],
                "奖项名原文": category_name["original"],
                "结果": "获奖" if item["result"] == "获奖" else "提名",
                "影片检索名": film["检索名"],
                "影片原名": film["原名"],
                "上映年": release_year,
                "人物检索名": person_name,
                "sources": sources,
            }
        )
    records.sort(key=lambda item: (item["奖项名"], item["影片检索名"], item["人物检索名"]))
    images = []
    image = next((row for row in image_rows if (row.get("image") or {}).get("value")), None)
    if image:
        images.append(
            {
                "role": "标识",
                "ownerType": "奖项",
                "owner": OSCAR,
                "url": image["image"]["value"].replace("http://", "https://", 1),
            }
        )
    today = date.today()
    return {
        "schema": "mk-import/1",
        "query": f"{OSCAR} {year} {category}".strip() if category else f"{OSCAR} {year}",
        "generated_at": today.isoformat(),
        "producer": "wikidata",
        "award_records": records,
        "images": images,
        "uncertainties": uncertainties,
    }


def _find_ceremony(year, timeout_ms):
    data = _sparql(_ceremony_query(year), timeout_ms)
    ceremonies = {}
    for row in data["results"]["bindings"]:
        item_id = _qid((row.get("ceremony") or {}).get("value"))
        if not item_id:
            continue
        current = ceremonies.get(item_id) or {"id": item_id, "edition": None, "date": (row.get("date") or {}).get("value") or ""}
        edition = _integer((row.get("edition") or {}).get("value"))
        if edition is not None:
            current["edition"] = edition
        ceremonies[item_id] = current
    if len(ceremonies) != 1:
        ids = "、".join(ceremonies) or "无"
        raise RuntimeError(f"第 {year} 年的奥斯卡典礼没有唯一匹配（{ids}）")
    return next(iter(ceremonies.values()))


def _ceremony_query(year):
    return f"""
SELECT DISTINCT ?ceremony ?edition ?date WHERE {{
  {{
    ?ceremony wdt:P179 wd:{SERIES} .
  }} UNION {{
    ?ceremony wdt:P361 wd:{SERIES} .
  }} UNION {{
    ?ceremony wdt:P31 wd:Q16913666 .
  }}
  FILTER(?ceremony != wd:{SERIES})
  ?ceremony wdt:P585 ?date .
  FILTER(YEAR(?date) = {year})
  OPTIONAL {{ ?ceremony wdt:P393 ?edition . }}
}}"""


def _awards_query(ceremony_id):
    return f"""
SELECT ?award ?awardZhCn ?awardZh ?awardEn ?film ?filmZhCn ?filmZhHans ?filmZh ?filmEn ?person ?personZhCn ?personZh ?personEn ?pubYear ?result ?sourceUrl WHERE {{
  {{
    ?film p:P166 ?stmt .
    ?stmt ps:P166 ?award .
    ?stmt pq:P805 wd:{ceremony_id} .
    BIND("获奖" AS ?result)
  }} UNION {{
    ?film p:P1411 ?stmt .
    ?stmt ps:P1411 ?award .
    ?stmt pq:P805 wd:{ceremony_id} .
    BIND("提名" AS ?result)
  }} UNION {{
    ?person p:P166 ?stmt .
    ?stmt ps:P166 ?award .
    ?stmt pq:P805 wd:{ceremony_id} .
    ?stmt pq:P1686 ?film .
    BIND("获奖" AS ?result)
  }} UNION {{
    ?person p:P1411 ?stmt .
    ?stmt ps:P1411 ?award .
    ?stmt pq:P805 wd:{ceremony_id} .
    ?stmt pq:P1686 ?film .
    BIND("提名" AS ?result)
  }}
  FILTER EXISTS {{ ?film wdt:P31/wdt:P279* wd:Q11424 . }}
  OPTIONAL {{ ?award rdfs:label ?awardZhCn FILTER(LANG(?awardZhCn) = "zh-cn") }}
  OPTIONAL {{ ?award rdfs:label ?awardZh FILTER(LANG(?awardZh) = "zh") }}
  OPTIONAL {{ ?award rdfs:label ?awardEn FILTER(LANG(?awardEn) = "en") }}
  OPTIONAL {{ ?film rdfs:label ?filmZhCn FILTER(LANG(?filmZhCn) = "zh-cn") }}
  OPTIONAL {{ ?film rdfs:label ?filmZhHans FILTER(LANG(?filmZhHans) = "zh-hans") }}
  OPTIONAL {{ ?film rdfs:label ?filmZh FILTER(LANG(?filmZh) = "zh") }}
  OPTIONAL {{ ?film rdfs:label ?filmEn FILTER(LANG(?filmEn) = "en") }}
  OPTIONAL {{ ?film wdt:P577 ?pub . BIND(YEAR(?pub) AS ?pubYear) }}
  OPTIONAL {{ ?person rdfs:label ?personZhCn FILTER(LANG(?personZhCn) = "zh-cn") }}
  OPTIONAL {{ ?person rdfs:label ?personZh FILTER(LANG(?personZh) = "zh") }}
  OPTIONAL {{ ?person rdfs:label ?personEn FILTER(LANG(?personEn) = "en") }}
  OPTIONAL {{ ?stmt prov:wasDerivedFrom ?ref . ?ref pr:P854 ?sourceUrl . }}
}}"""


def _image_query():
    return f"SELECT ?image WHERE {{ wd:{SERIES} wdt:P18 ?image }} LIMIT 1"


def _sparql(query, timeout_ms):
    from urllib.parse import quote

    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    text = request_text(url, timeout_ms=timeout_ms, headers={"Accept": "application/sparql-results+json"})
    return json.loads(text)


def _first_text(*bindings):
    for binding in bindings:
        if binding and binding.get("value"):
            return binding["value"]
    return ""


def _label_rank(*bindings):
    for index, binding in enumerate(bindings):
        if binding and binding.get("value"):
            return len(bindings) - index
    return 0


def _qid(value):
    match = re.search(r"Q\d+$", str(value or ""))
    return match.group(0) if match else ""


def _integer(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _fallback_prompt(vault, award, year, category):
    try:
        return write_search_prompt(vault, award, year, category)
    except OSError as error:
        return f"提示词没有写成文件：{error}"
