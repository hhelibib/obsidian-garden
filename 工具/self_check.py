import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.award_notes import merge_award_markdown
from mkimport.fetching import build_packet
from mkimport.importing import import_packet
from mkimport.names import film_search_name, person_search_name, short_category_name
from mkimport.douban import (
    DoubanBlocked,
    DoubanRateLimited,
    choose_release,
    cookie_header_from_values,
    ensure_allowed,
    merge_list_items,
    parse_personage_html,
    parse_subject_html,
    poster_allowed,
    read_cookie,
    require_login,
    save_latest_cookie,
    split_title,
    store_anonymous_cookie,
)
from mkimport.http import split_response_headers
from mkimport.douban_fetch import fetch_details, run_people_pipeline
from mkimport.douban_import import import_details
from mkimport.douban_people import assign_search_names, import_people, sync_appearances
from mkimport.packet import assert_image_url, validate_packet

ROOT = Path(__file__).resolve().parents[1]
FAILED = 0


def check(condition, message):
    global FAILED
    if not condition:
        FAILED += 1
        print(f"失败：{message}", file=sys.stderr)


def main():
    global FAILED
    picture = short_category_name("奥斯卡最佳影片奖", "Academy Award for Best Picture")
    check(picture["name"] == "最佳影片", "奥斯卡奖项中文名应缩短为最佳影片")
    check(short_category_name("最佳影片", "")["name"] == "最佳影片", "已是短名时保持不变")
    check(film_search_name("泰坦尼克号", "Titanic", 1997)["检索名"] == "泰坦尼克号 (1997)", "中文检索名")
    check(film_search_name("", "Titanic (1997 film)", 1997)["检索名"] == "Titanic (1997)", "英文电影消歧义后缀应去掉")
    check(film_search_name("泰坦尼克号（1997年电影）", "Titanic", 1997)["检索名"] == "泰坦尼克号 (1997)", "中文消歧义后缀应去掉")
    rejected = False
    try:
        assert_image_url("https://evil.example/a.jpg")
    except RuntimeError:
        rejected = True
    check(rejected, "非允许主机的图片地址应被拒绝")

    original = (ROOT / "奖项" / "奥斯卡金像奖.md").read_text(encoding="utf-8")
    record = {
        "奖项": "奥斯卡金像奖",
        "颁奖年": 1998,
        "届次": 70,
        "奖项名": "最佳影片",
        "结果": "获奖",
        "影片检索名": "泰坦尼克号 (1997)",
        "sources": [{"title": "Oscars", "url": "https://www.oscars.org/oscars/ceremonies/1998"}],
    }
    first = merge_award_markdown(original, [record])
    check("| 1998 | 70 | 最佳影片 | 获奖 | [[泰坦尼克号 (1997)]] |  |" in first["markdown"], "表格写入获奖行")
    check("| | | | | [[]] |" not in first["markdown"], "空模板行应被替换")
    check("- [1998] #yellow {最佳影片} [[泰坦尼克号 (1997)]] | 获奖" in first["markdown"], "时间线写入获奖")
    check("# - [1998] #yellow {最佳影片} [[泰坦尼克号 (1997)]] | 获奖" in first["markdown"], "原有注释示例保留")
    check("= [1929-05-16] 首届颁奖" in first["markdown"], "首届标记保留")
    check((ROOT / "奖项" / "奥斯卡金像奖.md").read_text(encoding="utf-8") == original, "自检不改动真实笔记")

    second = merge_award_markdown(first["markdown"], [record])
    check(all(action["type"].startswith("skip") for action in second["actions"]), "重复导入应跳过")
    check(sum(1 for line in second["markdown"].split("\n") if line.startswith("- [1998]")) == 1, "时间线不重复")

    nominated = {**record, "结果": "提名", "影片检索名": "心灵捕手 (1997)"}
    both = merge_award_markdown(first["markdown"], [nominated])
    check("#blue {最佳影片} [[心灵捕手 (1997)]] | 提名" in both["markdown"], "提名使用蓝色")
    person_note = """---
类型: 奖项
---

## 时间线

```chronos
> ORDERBY start

```

## 影片

| 颁奖年 | 届次 | 奖项 | 结果 | 影片 | 人物 |
| --- | --- | --- | --- | --- | --- |
| | | | | [[]] | |
"""
    actor = {**record, "奖项名": "最佳男主角", "人物检索名": "莱昂纳多·迪卡普里奥", "人物可链接": True}
    plain = {**record, "奖项名": "最佳女主角", "人物检索名": "凯特·温斯莱特", "人物可链接": False}
    people_awards = merge_award_markdown(person_note, [actor, plain])
    check(
        "| 1998 | 70 | 最佳男主角 | 获奖 | [[泰坦尼克号 (1997)]] | [[莱昂纳多·迪卡普里奥]] |" in people_awards["markdown"],
        "已有人物笔记写成链接",
    )
    check(
        "| 1998 | 70 | 最佳女主角 | 获奖 | [[泰坦尼克号 (1997)]] | 凯特·温斯莱特 |" in people_awards["markdown"],
        "没有人物笔记保持裸人名",
    )
    check(
        "- [1998] #yellow {最佳男主角} [[泰坦尼克号 (1997)]] [[莱昂纳多·迪卡普里奥]] | 获奖" in people_awards["markdown"],
        "时间线写入人物链接",
    )
    upgraded = merge_award_markdown(people_awards["markdown"], [{**plain, "人物可链接": True}])
    check("[[凯特·温斯莱特]]" in upgraded["markdown"], "补上人物笔记后升级为链接")
    check(
        "- [1998] #yellow {最佳女主角} [[泰坦尼克号 (1997)]] [[凯特·温斯莱特]] | 获奖" in upgraded["markdown"],
        "时间线里的裸人名升级为链接",
    )
    check("| 凯特·温斯莱特 |" not in upgraded["markdown"], "裸人名不再留在表格")
    refused = validate_packet(
        {
            "schema": "mk-import/1",
            "award_records": [{**record, "人物检索名": "[[坏链接]]"}],
            "images": [],
            "uncertainties": [],
        }
    )
    check(any("人物检索名" in error for error in refused["errors"]), "人物检索名不能自带方括号")
    check(person_search_name("莱昂纳多·迪卡普里奥", "Leonardo DiCaprio")["检索名"] == "莱昂纳多·迪卡普里奥", "人物检索名优先中文")

    packet = build_packet(
        1998,
        "",
        {"id": "Q536749", "edition": 70},
        [
            {
                "award": {"value": "http://www.wikidata.org/entity/Q102427"},
                "awardZh": {"value": "奥斯卡最佳影片奖"},
                "film": {"value": "http://www.wikidata.org/entity/Q44578"},
                "filmZh": {"value": "泰坦尼克号"},
                "filmEn": {"value": "Titanic"},
                "pubYear": {"value": "1997"},
                "result": {"value": "获奖"},
            },
            {
                "award": {"value": "http://www.wikidata.org/entity/Q102427"},
                "film": {"value": "http://www.wikidata.org/entity/Q44579"},
                "filmEn": {"value": "No Chinese"},
                "result": {"value": "提名"},
            },
        ],
        [],
    )
    check(len(packet["award_records"]) == 1, "缺少上映年的影片不写入")
    check(packet["award_records"][0]["影片检索名"] == "泰坦尼克号 (1997)", "Wikidata 行转成检索名")
    check(packet["award_records"][0]["奖项名"] == "最佳影片", "Wikidata 奖项名缩短")
    check(any(item["主题"] == "影片检索名" for item in packet["uncertainties"]), "缺字段进入待核")

    with tempfile.TemporaryDirectory(prefix="mk-award-") as vault_name:
        vault = Path(vault_name)
        (vault / "奖项").mkdir()
        (vault / "收件箱").mkdir()
        (vault / "奖项" / "奥斯卡金像奖.md").write_text(original, encoding="utf-8")
        packet_path = vault / "收件箱" / "sample.json"
        packet_path.write_text(
            json.dumps(
                {
                    "schema": "mk-import/1",
                    "query": "第70届奥斯卡金像奖 最佳影片",
                    "award_records": [record, nominated],
                    "images": [],
                    "uncertainties": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        preview = import_packet(packet_path, apply=False, images=False, vault=vault)
        check((vault / "奖项" / "奥斯卡金像奖.md").read_text(encoding="utf-8") == original, "预览不写笔记")
        check(not (vault / "影片" / "泰坦尼克号 (1997).md").exists(), "预览不建影片")
        check("将新建" in preview["preview"], "预览列出影片骨架")
        (vault / "泰坦尼克号 (1997).md").write_text("", encoding="utf-8")
        import_packet(packet_path, apply=True, images=False, vault=vault)
        applied = (vault / "奖项" / "奥斯卡金像奖.md").read_text(encoding="utf-8")
        check("[[心灵捕手 (1997)]]" in applied, "执行后写入笔记")
        film_note = (vault / "影片" / "泰坦尼克号 (1997).md").read_text(encoding="utf-8")
        check("中文名: 泰坦尼克号" in film_note, "影片骨架写入中文名")
        check("上映年: 1997" in film_note, "影片骨架写入上映年")
        check(not (vault / "泰坦尼克号 (1997).md").exists(), "空的库根笔记应移走")
        import_packet(packet_path, apply=True, images=False, vault=vault)
        again = (vault / "奖项" / "奥斯卡金像奖.md").read_text(encoding="utf-8")
        check(again == applied, "再次执行不改内容")
        check((vault / "影片" / "泰坦尼克号 (1997).md").read_text(encoding="utf-8") == film_note, "再次执行不改影片骨架")
        held_path = vault / "收件箱" / "held.json"
        held_path.write_text(
            json.dumps(
                {
                    "schema": "mk-import/1",
                    "award_records": [{**record, "影片检索名": "冲突片 (1999)"}],
                    "uncertainties": [{"主题": "影片检索名", "说法": ["两个中文名冲突"]}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        held = import_packet(held_path, apply=True, images=False, vault=vault)
        check(held["held"] is True, "检索名待核应停下")
        check(not (vault / "影片" / "冲突片 (1999).md").exists(), "拦住时不建影片")

    status, headers = split_response_headers(
        "HTTP/1.1 200 OK\r\nSet-Cookie: bid=abc; path=/\r\nSet-Cookie: ll=108288; path=/\r\nContent-Type: text/html\r\n"
    )
    check(status == 200 and headers["content-type"] == "text/html", "普通响应头仍是单值")
    check(headers["set-cookie"] == ["bid=abc; path=/", "ll=108288; path=/"], "保留全部 Set-Cookie")
    check(cookie_header_from_values(headers["set-cookie"]) == "bid=abc; ll=108288", "Cookie 收成一行")
    check(
        cookie_header_from_values(['ll="108297"; path=/; domain=.douban.com', "bid=s4YHG6weT0Q"])
        == "ll=108297; bid=s4YHG6weT0Q",
        "去掉 ll 两侧引号",
    )
    missing_login = False
    try:
        require_login("bid=abc; ll=1")
    except DoubanBlocked as error:
        missing_login = "dbcl2" in str(error)
    check(missing_login, "没有 dbcl2 时要求登录")
    require_login("dbcl2=1; bid=abc")
    login_json = False
    try:
        ensure_allowed(200, '{"msg":"检测到有异常请求从您的IP发出，请登录再试!","r":1}')
    except DoubanBlocked as error:
        login_json = "dbcl2" in str(error)
    check(login_json, "请登录的 JSON 应提示登录")
    limited = False
    try:
        ensure_allowed(200, '{"msg":"检测到有异常请求从您的IP发出，请求暂时被拒绝。","r":1}')
    except DoubanRateLimited:
        limited = True
    check(limited, "暂时拒绝应单独停页")
    forbidden = False
    try:
        ensure_allowed(200, "<title>禁止访问</title><script src='https://turing.captcha.qcloud.com/TCaptcha.js'></script>")
    except DoubanBlocked as error:
        forbidden = "禁止访问" in str(error) and "封号" in str(error)
    check(forbidden, "禁止访问页面立即停止")
    sorry = False
    try:
        ensure_allowed(200, "<html>正常</html>", "https://www.douban.com/misc/sorry?original-url=1")
    except DoubanBlocked as error:
        sorry = "禁止访问" in str(error)
    check(sorry, "sorry 跳转立即停止")
    with tempfile.TemporaryDirectory(prefix="mk-cookie-refresh-") as cookie_name:
        cookie_vault = Path(cookie_name)
        (cookie_vault / "工具").mkdir()
        (cookie_vault / "工具" / "douban_cookie.txt").write_text("dbcl2=keep; bid=old\n", encoding="utf-8")
        updated, changed = save_latest_cookie(cookie_vault, ["bid=new; Path=/; Domain=.douban.com"])
        check(changed and updated == "dbcl2=keep; bid=new", "响应里的新 Cookie 写回文件")
        refreshed, changed = save_latest_cookie(cookie_vault, ["dbcl2=newer; Path=/"])
        check(changed and refreshed == "dbcl2=newer; bid=new", "登录 Cookie 更新后整行重写")
        check(read_cookie(cookie_vault) == "dbcl2=newer; bid=new", "下次读取到重写后的 Cookie")
    with tempfile.TemporaryDirectory(prefix="mk-cookie-") as cookie_vault:
        vault = Path(cookie_vault)
        saved = store_anonymous_cookie(vault, 200, {"set-cookie": ["bid=abc; Path=/", "ll=1; Domain=.douban.com"]}, "ok")
        check(saved == "bid=abc; ll=1", "匿名 Cookie 写入文件")
        check((vault / "工具" / "douban_cookie.txt").read_text(encoding="utf-8").strip() == "bid=abc; ll=1", "Cookie 文件只有一行")
        blocked = False
        try:
            store_anonymous_cookie(vault, 403, {"set-cookie": ["bid=nope; Path=/"]}, "forbidden")
        except Exception as error:
            blocked = "领取匿名 Cookie 时被拦截" in str(error)
        check(blocked, "首页被拦截时不继续")
        check("nope" not in (vault / "工具" / "douban_cookie.txt").read_text(encoding="utf-8"), "拦截时不覆盖已有 Cookie")
    check(split_title("翼 Wings") == ("翼", "Wings"), "中文名与原名分开")
    check(choose_release(["1927-08-12(美国)", "1928-01-01(法国)"]) == ("", ["1927-08-12", "1928-01-01"]), "上映日冲突留空")
    merged, added = merge_list_items([{"id": "1"}], [{"id": "1"}, {"id": "2"}])
    check([item["id"] for item in merged] == ["1", "2"] and len(added) == 1, "列表按编号去重")
    check(poster_allowed("https://img9.doubanio.com/view/photo/l/public/p1.jpg"), "豆瓣图床允许海报")
    douban_rejected = False
    try:
        assert_image_url("https://img9.doubanio.com/view/photo/l/public/p1.jpg")
    except RuntimeError:
        douban_rejected = True
    check(douban_rejected, "奖项导入仍拒绝豆瓣图床")
    sample = """
    <h1><span property="v:itemreviewed">翼 Wings</span> <span class="year">(1927)</span></h1>
    <div id="info">
    <a rel="v:directedBy">威廉·A·韦尔曼</a>
    <a rel="v:starring">克拉拉·鲍</a>
    <span class="pl">制片国家/地区:</span> 美国<br/>
    <span class="pl">又名:</span> 飞翼<br/>
    <span property="v:initialReleaseDate" content="1927-08-12(美国)"></span>
    <span class="pl">IMDb:</span> tt0018578<br/>
    </div>
    <span property="v:summary">一战飞行员。</span>
    <img src="https://img9.doubanio.com/view/photo/s_ratio_poster/public/p1.jpg" title="点击看更多海报"/>
    """
    parsed = parse_subject_html(sample, "100")
    check(parsed["中文名"] == "翼" and parsed["原名"] == "Wings", "详情页解析片名")
    check(parsed["上映年"] == "1927" and parsed["上映日期"] == "1927-08-12", "详情页解析日期")
    check(parsed["imdb"] == "tt0018578" and parsed["导演"] == ["威廉·A·韦尔曼"], "详情页解析职员和 IMDb")
    try:
        fetch_details(ROOT, ROOT / "不存在.json", threads=9)
        check(False, "超过 5 个线程应失败")
    except RuntimeError as error:
        check("1 到 5" in str(error), "超过 5 个线程应失败")
    with tempfile.TemporaryDirectory(prefix="mk-douban-") as vault_name:
        vault = Path(vault_name)
        detail = {
            "id": "100",
            "中文名": "翼",
            "原名": "Wings",
            "上映年": "1927",
            "上映日期": "1927-08-12",
            "主要国家": "美国",
            "制作国家": ["美国"],
            "导演": ["威廉·A·韦尔曼"],
            "主演": ["克拉拉·鲍"],
            "简介": "一战飞行员。",
            "又名": ["飞翼"],
            "imdb": "tt0018578",
            "豆瓣": "https://movie.douban.com/subject/100/",
            "海报": "",
            "不确定": [],
        }
        preview = import_details(vault, [detail], apply=False, images=False, label="test")
        check(not (vault / "影片" / "翼 (1927).md").exists(), "豆瓣预览不建影片")
        check("新建" in preview["preview"], "豆瓣预览列出新建")
        import_details(vault, [detail], apply=True, images=False, label="test")
        note_path = vault / "影片" / "翼 (1927).md"
        note = note_path.read_text(encoding="utf-8")
        check("原名: Wings" in note and "主要国家: 美国" in note, "豆瓣写入空字段")
        check("[[威廉·A·韦尔曼]]" not in note and "威廉·A·韦尔曼" in note, "演职员只写人名")
        score_line = next(line for line in note.splitlines() if line.startswith("我的评分:"))
        check(score_line == "我的评分:", "不写豆瓣评分")
        note_path.write_text(note.replace("## 电影分析\n", "## 电影分析\n\n保留这段\n").replace("我的评分:", "我的评分: 9"), encoding="utf-8")
        edited = note_path.read_bytes()
        again = import_details(
            vault,
            [detail],
            apply=True,
            images=False,
            label="test",
            missing=[{"id": "999", "title": "没有详情"}],
        )
        kept = note_path.read_text(encoding="utf-8")
        check("保留这段" in kept and "我的评分: 9" in kept, "个人内容和电影分析保留")
        check(note_path.read_bytes() == edited, "没有空字段时不改影片文件")
        check(again["counts"]["未改"] == 1, "没有空字段时记为未改")
        missing_log = vault / "收件箱" / "豆瓣" / "缺少详情-test.log"
        check(missing_log.read_text(encoding="utf-8").splitlines() == ["999\t没有详情"], "缺少详情写入日志")
        other = {**detail, "id": "200", "豆瓣": "https://movie.douban.com/subject/200/", "原名": "Other"}
        conflict = import_details(vault, [other], apply=True, images=False, label="test")
        check(any(item["type"] == "conflict" for item in conflict["actions"]), "豆瓣编号不同不覆盖")
        check("原名: Wings" in note_path.read_text(encoding="utf-8"), "冲突后原名不变")
    credit_page = """
    <h1><span property="v:itemreviewed">我爱你！ Love Never Ends</span> <span class="year">(2023)</span></h1>
    <div id="info">
    <span><span class='pl'>导演</span>: <span class='attrs'><a href="https://www.douban.com/personage/27504054/" rel="v:directedBy">韩延</a></span></span>
    <span><span class='pl'>编剧</span>: <span class='attrs'><a href="https://www.douban.com/personage/35317987/">韩今谅</a></span></span>
    <span><span class='pl'>主演</span>: <span class='attrs'><a href="/subject_search?search_text=x" rel="v:starring">张小钰</a></span></span>
    </div>
    """
    credited = parse_subject_html(credit_page, "35818074")
    check(credited["导演"] == ["韩延"] and credited["编剧"] == ["韩今谅"], "条目页解析导演和编剧")
    check(credited["人物"][0]["id"] == "27504054" and credited["人物"][2]["id"] == "", "人物链接和搜索链接分开")
    person_page = """
    <h1>韩延 Yan Han</h1>
    <ul class="subject-property">
    <li><span class="label">出生日期:</span><span class="value">1983年11月16日</span></li>
    <li><span class="label">出生地:</span><span class="value">中国,山东,济南</span></li>
    <li><span class="label">IMDb编号:</span><span class="value">nm5181204</span></li>
    <li><span class="label">职业:</span><span class="value">导演 / 编剧</span></li>
    </ul>
    <section class="subject-mod subject-intro"><div class="content"><p>中国内地导演。</p></div></section>
    """
    person = parse_personage_html(person_page, "27504054")
    check(person["中文名"] == "韩延" and person["原名"] == "Yan Han", "人物页解析姓名")
    check(person["出生"] == "1983-11-16" and person["国籍"] == ["中国"], "人物页解析出生和国籍")
    check(person["imdb"] == "nm5181204" and person["身份"] == ["导演", "编剧"], "人物页解析职业和 IMDb")
    same_name = [
        {"id": "1", "中文名": "韩延", "出生": "1983-11-16"},
        {"id": "2", "中文名": "韩延", "出生": "1990-01-01"},
    ]
    with tempfile.TemporaryDirectory(prefix="mk-names-") as name_vault:
        assigned, name_conflicts = assign_search_names(same_name, name_vault)
    check(assigned == {"1": "韩延 (1983)", "2": "韩延 (1990)"} and not name_conflicts, "同名人物用出生年区分")
    with tempfile.TemporaryDirectory(prefix="mk-people-") as people_vault_name:
        people_vault = Path(people_vault_name)
        detail = {
            "id": "35818074",
            "中文名": "我爱你！",
            "原名": "Love Never Ends",
            "上映年": "2023",
            "上映日期": "",
            "主要国家": "中国大陆",
            "制作国家": ["中国大陆"],
            "导演": ["韩延"],
            "编剧": ["韩今谅"],
            "主演": ["张小钰"],
            "人物": [
                {"id": "27504054", "姓名": "韩延", "职位": "导演"},
                {"id": "35317987", "姓名": "韩今谅", "职位": "编剧"},
                {"id": "", "姓名": "张小钰", "职位": "主演"},
            ],
            "简介": "老年爱情。",
            "又名": [],
            "imdb": "tt21067242",
            "豆瓣": "https://movie.douban.com/subject/35818074/",
            "海报": "",
            "不确定": [{"主题": "上映日期", "说法": ["2023-06-21", "2023-06-09"]}],
        }
        import_details(people_vault, [detail], apply=True, images=False, label="people")
        film = people_vault / "影片" / "我爱你！ (2023).md"
        film.write_text(film.read_text(encoding="utf-8").replace("我的评分:", "我的评分: 8"), encoding="utf-8")
        person_dir = people_vault / "收件箱" / "豆瓣" / "人物"
        person_dir.mkdir(parents=True)
        (person_dir / "27504054.json").write_text(
            json.dumps(
                {
                    "id": "27504054",
                    "中文名": "韩延",
                    "原名": "Yan Han",
                    "出生": "1983-11-16",
                    "国籍": ["中国"],
                    "身份": ["导演", "编剧"],
                    "imdb": "nm5181204",
                    "豆瓣": "https://www.douban.com/personage/27504054/",
                    "简介": "中国内地导演。",
                    "照片": "",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        detail_dir = people_vault / "收件箱" / "豆瓣" / "详情"
        detail_dir.mkdir(parents=True, exist_ok=True)
        (detail_dir / "35818074.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        imported = import_people(people_vault, apply=True, images=False)
        person_note = (people_vault / "人物" / "韩延.md").read_text(encoding="utf-8")
        linked = film.read_text(encoding="utf-8")
        check("原名: Yan Han" in person_note and "出生: 1983-11-16" in person_note, "人物笔记写入空字段")
        check("中国内地导演。" in person_note, "人物生平写入介绍")
        check("[[韩延]]" in linked and "韩今谅" in linked and "[[韩今谅]]" not in linked, "有人物页才改成链接")
        works = person_note.split("## 作品", 1)[-1]
        check("### 出演" in works and "[[我爱你！ (2023)]]" not in works, "导演不写入出演")
        check("| 导演 | [[韩延]] |  |" in linked, "演职员表链到已有人物页")
        check("| 编剧 | 韩今谅 |  |" in linked, "没有人物页的演职员保持裸人名")
        check("我的评分: 8" in linked, "补链接时保留我的评分")
        check(imported["counts"]["缺少人物"] == 1, "没有人物页缓存的编号记入缺少人物")
        edited = film.read_bytes()
        person_bytes = (people_vault / "人物" / "韩延.md").read_bytes()
        import_people(people_vault, apply=True, images=False)
        check(film.read_bytes() == edited and (people_vault / "人物" / "韩延.md").read_bytes() == person_bytes, "没有变化时不改人物和影片")
    with tempfile.TemporaryDirectory(prefix="mk-cast-") as cast_name:
        cast_vault = Path(cast_name)
        (cast_vault / "影片").mkdir()
        (cast_vault / "人物").mkdir()
        (cast_vault / "影片" / "翼 (1927).md").write_text(
            "---\n主演: [克拉拉·鲍]\n---\n\n## 演职员\n\n| 职位 | 人物 | 角色 |\n| --- | --- | --- |\n| 主演 | 克拉拉·鲍 | 飞行员 |\n",
            encoding="utf-8",
        )
        (cast_vault / "人物" / "克拉拉·鲍.md").write_text("---\n中文名: 克拉拉·鲍\n---\n\n## 作品\n\n旧说明\n", encoding="utf-8")
        sync_appearances(cast_vault)
        cast_film = (cast_vault / "影片" / "翼 (1927).md").read_text(encoding="utf-8")
        cast_person = (cast_vault / "人物" / "克拉拉·鲍.md").read_text(encoding="utf-8")
        check("[[克拉拉·鲍]]" in cast_film and "| 主演 | [[克拉拉·鲍]] | 飞行员 |" in cast_film, "主演双向链接写回影片")
        check("[[翼 (1927)]]" in cast_person and "飞行员" in cast_person, "出演写回人物并链回影片")
    with tempfile.TemporaryDirectory(prefix="mk-pipeline-") as pipeline_name:
        pipeline_vault = Path(pipeline_name)
        film_folder = pipeline_vault / "影片"
        film_folder.mkdir()
        (film_folder / "甲.md").write_text("---\n豆瓣: https://movie.douban.com/subject/1/\n---\n", encoding="utf-8")
        (film_folder / "乙.md").write_text("---\n豆瓣: https://movie.douban.com/subject/2/\n---\n", encoding="utf-8")
        film_one = (
            "<h1><span property=\"v:itemreviewed\">甲片</span> <span class=\"year\">(2024)</span></h1>"
            "<div id=\"info\"><span><span class='pl'>导演</span>: <span class='attrs'>"
            "<a href=\"https://www.douban.com/personage/10/\" rel=\"v:directedBy\">甲</a>"
            "</span></span></div>"
        )
        film_two = (
            "<h1><span property=\"v:itemreviewed\">乙片</span> <span class=\"year\">(2024)</span></h1>"
            "<div id=\"info\"><span><span class='pl'>主演</span>: <span class='attrs'>"
            "<a href=\"https://www.douban.com/personage/10/\" rel=\"v:starring\">甲</a> / "
            "<a href=\"https://www.douban.com/personage/11/\" rel=\"v:starring\">乙</a>"
            "</span></span></div>"
        )
        pages = {
            "https://movie.douban.com/subject/1/": film_one,
            "https://movie.douban.com/subject/2/": film_two,
            "https://www.douban.com/personage/10/": "<h1>甲</h1>",
            "https://www.douban.com/personage/11/": "<h1>乙</h1>",
        }
        calls = []

        def transport(url):
            calls.append(url)
            return pages[url]

        streamed = run_people_pipeline(pipeline_vault, threads=1, delay=1, transport=transport)
        check(calls[1] == "https://www.douban.com/personage/10/", "人物页排在剩余影片之前")
        check(calls.count("https://www.douban.com/personage/10/") == 1, "同一人物只抓一次")
        check(not streamed["blocked"] and (pipeline_vault / "收件箱" / "豆瓣" / "人物" / "11.json").is_file(), "流水线写完两部影片的人物")
        blocked_calls = []

        def blocked_transport(url):
            blocked_calls.append(url)
            if "personage" in url:
                raise DoubanRateLimited("停止")
            return film_one

        with tempfile.TemporaryDirectory(prefix="mk-pipeline-stop-") as stopped_name:
            stopped_vault = Path(stopped_name)
            stopped_films = stopped_vault / "影片"
            stopped_films.mkdir()
            (stopped_films / "甲.md").write_text("---\n豆瓣: https://movie.douban.com/subject/1/\n---\n", encoding="utf-8")
            (stopped_films / "乙.md").write_text("---\n豆瓣: https://movie.douban.com/subject/2/\n---\n", encoding="utf-8")
            blocked = run_people_pipeline(stopped_vault, threads=1, delay=1, transport=blocked_transport)
        check(blocked["blocked"] == "停止", "人物页被拒绝时停止整条流水线")
        check(blocked_calls[0].endswith("/subject/1/"), "停止前先完成当前影片条目")
        check("https://movie.douban.com/subject/2/" not in blocked_calls, "停止后不再抓剩余影片")

    bad = subprocess.run(
        [sys.executable, str(ROOT / "工具" / "import_packet.py"), "不存在.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    check(bad.returncode == 1, "无效路径应失败")
    if FAILED:
        print(f"自检失败 {FAILED} 项", file=sys.stderr)
        return 1
    print("自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
