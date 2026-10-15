import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.douban import PASTE_HINT, DoubanBlocked
from mkimport.douban_fetch import run_people_pipeline
from mkimport.douban_people import import_people

HELP = """用法
  python 工具/run_douban_pipeline.py
  python 工具/run_douban_pipeline.py --apply
  python 工具/run_douban_pipeline.py --threads 2 --delay 3

一条流水线：影片条目返回后立刻抓这些人物，全进程共用一个请求间隔。
人物页优先于还没开始的影片。被拒绝时整条管道停止，已保存的详情和人物页保留。
网络阶段结束后落库。默认只写预览，--apply 才写人物笔记和影片链接。
每次请求会重写 Cookie 文件里的最新值，并打印这次结果和剩余未执行的影片、人物数量。
"""


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    options = {
        "threads": 2,
        "delay": 3,
        "timeout_ms": 45000,
        "refresh": False,
        "apply": False,
        "images": True,
        "help": False,
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--help", "-h"):
            options["help"] = True
        elif arg == "--apply":
            options["apply"] = True
        elif arg == "--no-images":
            options["images"] = False
        elif arg == "--refresh":
            options["refresh"] = True
        elif arg == "--threads":
            index += 1
            options["threads"] = int(argv[index])
        elif arg == "--delay":
            index += 1
            options["delay"] = float(argv[index])
        elif arg == "--timeout":
            index += 1
            options["timeout_ms"] = int(argv[index])
        else:
            print(HELP)
            return 1
        index += 1
    if options["help"]:
        print(HELP)
        return 0
    vault = Path(__file__).resolve().parents[1]
    try:
        fetched = run_people_pipeline(
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
    print(
        f"影片新保存 {len(fetched['filmsSaved'])}，影片缓存 {len(fetched['filmsSkipped'])}，"
        f"人物新保存 {len(fetched['peopleSaved'])}，人物缓存 {len(fetched['peopleSkipped'])}"
    )
    if fetched["blocked"]:
        print(f"已停止：{fetched['blocked']}")
    print(f"流水线报告：{fetched['reportPath']}")
    try:
        result = import_people(
            vault,
            apply=options["apply"],
            images=options["images"],
            timeout_ms=options["timeout_ms"],
        )
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    counts = result["counts"]
    print(
        f"新建人物 {counts['新建人物']}，补全人物 {counts['补全人物']}，未改人物 {counts['未改人物']}，"
        f"冲突人物 {counts['冲突人物']}，缺少人物 {counts['缺少人物']}"
    )
    print(f"补全影片 {counts['补全影片']}，未改影片 {counts['未改影片']}")
    if not options["apply"]:
        print("未写入人物和影片。加上 --apply 后，只落库新建和补全；未改的笔记不会重写。")
    if result["missingLog"]:
        print(f"缺少人物日志：{result['missingLog']}")
    print(f"报告：{result['reportPath']}")
    return 2 if fetched["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
