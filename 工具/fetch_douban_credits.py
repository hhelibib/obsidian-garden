import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.douban import PASTE_HINT, DoubanBlocked
from mkimport.douban_fetch import fetch_film_credits

HELP = """用法
  python 工具/fetch_douban_credits.py
  python 工具/fetch_douban_credits.py --threads 2 --delay 3

从影片笔记的豆瓣条目重新读取导演、编剧、主演，写入 收件箱/豆瓣/详情/编号.json。
已经有「人物」字段的详情会跳过。--threads 默认 2，最大 5。--delay 默认 3 秒。
遇到验证或拒绝访问会停止，已保存的详情保留。
"""


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    options = {"threads": 2, "delay": 3, "timeout_ms": 45000, "refresh": False, "help": False}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--help", "-h"):
            options["help"] = True
        elif arg == "--threads":
            index += 1
            options["threads"] = int(argv[index])
        elif arg == "--delay":
            index += 1
            options["delay"] = float(argv[index])
        elif arg == "--timeout":
            index += 1
            options["timeout_ms"] = int(argv[index])
        elif arg == "--refresh":
            options["refresh"] = True
        else:
            print(HELP)
            return 1
        index += 1
    if options["help"]:
        print(HELP)
        return 0
    vault = Path(__file__).resolve().parents[1]
    try:
        result = fetch_film_credits(
            vault,
            threads=options["threads"],
            delay=options["delay"],
            timeout_ms=options["timeout_ms"],
            refresh=options["refresh"],
        )
    except DoubanBlocked as error:
        print(str(error), file=sys.stderr)
        print(PASTE_HINT, file=sys.stderr)
        return 2
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    print(f"新保存 {len(result['saved'])}，缓存跳过 {len(result['skipped'])}，失败 {len(result['failed'])}")
    if result["blocked"]:
        print(f"已停止：{result['blocked']}")
    print(f"报告：{result['reportPath']}")
    return 2 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
