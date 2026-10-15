import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.douban_people import import_people

HELP = """用法
  python 工具/import_douban_people.py
  python 工具/import_douban_people.py --apply

默认只写预览。--apply 才写入人物笔记，并把影片里的导演、编剧、主演改成人物链接。
只补影片的空字段。已有笔记如果没有变化，不改文件。
同名人物的豆瓣编号不同时不覆盖。
"""


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    options = {"apply": False, "images": True, "timeout_ms": 45000, "help": False}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--help", "-h"):
            options["help"] = True
        elif arg == "--apply":
            options["apply"] = True
        elif arg == "--no-images":
            options["images"] = False
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
    print(
        f"补全影片 {counts['补全影片']}，未改影片 {counts['未改影片']}，"
        f"待抓演职员 {counts['待抓演职员']}，缺少详情 {counts['缺少详情']}"
    )
    if not options["apply"]:
        print("未写入人物和影片。加上 --apply 后，只落库新建和补全；未改的笔记不会重写。")
    if result["missingLog"]:
        print(f"缺少人物日志：{result['missingLog']}")
    print(f"报告：{result['reportPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
