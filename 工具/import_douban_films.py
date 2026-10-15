import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.douban_import import import_details, load_details

HELP = """用法
  python 工具/import_douban_films.py
  python 工具/import_douban_films.py --list 收件箱/豆瓣/列表-1927-美国-全部.json
  python 工具/import_douban_films.py --list 收件箱/豆瓣/列表-1927-美国-全部.json --apply

默认只写预览。--apply 才写入影片笔记和海报。
有详情的才落库，没有详情的写入 收件箱/豆瓣/缺少详情-*.log。
已有笔记若没有空字段可填，不改文件。
不覆盖我的评分、观影状态、观影日期、观影次数、电影分析和观影笔记。
同名笔记若豆瓣编号不同，只记冲突，不覆盖。
"""


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    options = {"list": "", "apply": False, "images": True, "timeout_ms": 45000, "help": False}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--help", "-h"):
            options["help"] = True
        elif arg == "--apply":
            options["apply"] = True
        elif arg == "--no-images":
            options["images"] = False
        elif arg == "--list":
            index += 1
            options["list"] = argv[index]
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
    list_path = Path(options["list"]) if options["list"] else None
    if list_path and not list_path.is_file():
        print(f"找不到清单：{list_path}", file=sys.stderr)
        return 1
    try:
        details, missing, label = load_details(vault, list_path)
        result = import_details(
            vault,
            details,
            apply=options["apply"],
            images=options["images"],
            timeout_ms=options["timeout_ms"],
            label=label,
            missing=missing,
        )
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    counts = result["counts"]
    print(
        f"新建 {counts['新建']}，补全 {counts['补全']}，未改 {counts['未改']}，"
        f"冲突 {counts['冲突']}，跳过 {counts['跳过']}，缺少详情 {counts['缺少详情']}"
    )
    if not options["apply"]:
        print("未写入影片。加上 --apply 后，只落库新建和补全；未改的笔记不会重写。")
    if result["missingLog"]:
        print(f"缺少详情日志：{result['missingLog']}")
    print(f"报告：{result['reportPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
