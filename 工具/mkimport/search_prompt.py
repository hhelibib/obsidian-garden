from pathlib import Path

INSTRUCTIONS = """你是电影奖项检索员。只输出一个 JSON 对象，不要 Markdown，不要解释。

任务：检索我指定的「某一个奖的某一届」，整理成 mk-import/1 数据包。颁奖年是典礼年份，不是影片上映年。

必须遵守：
- 先搜索再填写。每条 award_records 都给出 sources，至少一条可打开的 http(s) URL。优先使用该奖官网、中文维基或英文维基。
- 未知用 null。没有来源的奖项不要写。
- 两个来源冲突时，不要替我选定，写进 uncertainties。
- 奖项只能是：奥斯卡金像奖、金球奖、英国电影学院奖、戛纳电影节、威尼斯电影节、柏林电影节。
- 结果只能是：获奖、提名。
- 影片检索名必须是「中文名 (四位上映年)」，例如「泰坦尼克号 (1997)」。不知道中文名或上映年时，不要编造，把该片放进 uncertainties。
- 奖项名写具体奖，例如「最佳影片」「金棕榈奖」「金狮奖」「金熊奖」。不要把整个典礼名再写进奖项名。
- 颁给影片的奖，人物检索名用 null。颁给个人的奖，人物检索名写人物笔记的检索名；还没有人物笔记时写中文名。终身成就、人道主义奖等没有对应影片的，放进 uncertainties。
- 不要填写评分、观影状态、剧情分析、人物之间的现实关系。
- 图片可省略。如果给出，url 只能来自 upload.wikimedia.org、commons.wikimedia.org、image.tmdb.org。奖项标识的 role 是「标识」，ownerType 是「奖项」。

输出形状：
{
  "schema": "mk-import/1",
  "query": "",
  "generated_at": "",
  "producer": "search-ai",
  "award_records": [
    {
      "奖项": "奥斯卡金像奖",
      "颁奖年": 1998,
      "届次": 70,
      "奖项名": "最佳影片",
      "结果": "获奖",
      "影片检索名": "泰坦尼克号 (1997)",
      "人物检索名": null,
      "sources": [{ "title": "", "url": "" }]
    }
  ],
  "images": [],
  "uncertainties": [{ "主题": "", "说法": [], "sources": [{ "title": "", "url": "" }] }]
}

一次只处理我下面这一条：
"""


def write_search_prompt(vault, award, year, category=""):
    task = f"{award} {year} {category}".strip() if category else f"{award} {year}"
    safe = "".join(" " if char in '\\/:*?"<>|' else char for char in task)
    relative = Path("收件箱") / "待检索" / f"{safe}.md"
    absolute = Path(vault) / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# 待检索\n\n"
        f"把下面整段交给能搜索的 AI。把它返回的 JSON 存成 `收件箱/{safe}.json`，再执行 `工具\\执行导入.bat`。\n\n"
        f"```text\n{INSTRUCTIONS}{task}\n```\n"
    )
    absolute.write_text(body, encoding="utf-8")
    return absolute
