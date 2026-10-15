import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.douban import PARTIAL_NOTE, DoubanBlocked
from mkimport.douban_fetch import fetch_list

HELP = """用法
  python 工具/fetch_douban_list.py --year 1927
  python 工具/fetch_douban_list.py --year 1927 --region 美国 --genre 剧情
  python 工具/fetch_douban_list.py --year 2025 --limit 3000

按年份、地区、类型拉取豆瓣筛选列表，写入 收件箱/豆瓣/列表-年-地区-类型.json。
默认最多 2000 部。已有清单会从 next_start 继续，--limit 是累计上限。
地区和类型用豆瓣自己的词，可以省略。结果不是该年全部电影。
没有 工具/douban_cookie.txt 时，会先访问豆瓣首页并保存它返回的匿名 Cookie。首页被拦截时停止。
"""


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    options = {"year": "", "region": "", "genre": "", "delay": 3, "timeout_ms": 45000, "limit": None, "help": False}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--help", "-h"):
            options["help"] = True
        elif arg == "--year":
            index += 1
            options["year"] = argv[index]
        elif arg == "--region":
            index += 1
            options["region"] = argv[index]
        elif arg == "--genre":
            index += 1
            options["genre"] = argv[index]
        elif arg == "--delay":
            index += 1
            options["delay"] = float(argv[index])
        elif arg == "--timeout":
            index += 1
            options["timeout_ms"] = int(argv[index])
        elif arg == "--limit":
            index += 1
            options["limit"] = int(argv[index])
        else:
            print(HELP)
            return 1
        index += 1
    if options["help"] or not options["year"].isdigit():
        print(HELP)
        return 0 if options["help"] else 1
    vault = Path(__file__).resolve().parents[1]
    try:
        result = fetch_list(
            vault,
            int(options["year"]),
            options["region"],
            options["genre"],
            delay=options["delay"],
            timeout_ms=options["timeout_ms"],
            limit=options["limit"],
        )
    except DoubanBlocked as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    print(PARTIAL_NOTE)
    print(f"共 {result['count']} 部，停止原因：{result['stopped']}")
    if result["stopped"] == "rate-limit":
        print("豆瓣暂时拒绝继续请求。清单里已有的影片已保存。请过一段时间再运行同一条命令，会从上次的页继续。")
    print(f"清单：{result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
    # argv =["--year", "2025"]
    # main(argv)
