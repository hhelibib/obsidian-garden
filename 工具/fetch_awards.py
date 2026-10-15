import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.fetching import HELP, OSCAR, fetch_awards


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    options = {"award": "", "year": None, "category": "", "out": "", "timeout_ms": 45000, "help": False}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--award":
            index += 1
            options["award"] = argv[index]
        elif arg == "--year":
            index += 1
            options["year"] = int(argv[index])
        elif arg == "--category":
            index += 1
            options["category"] = argv[index]
        elif arg == "--out":
            index += 1
            options["out"] = argv[index]
        elif arg == "--timeout":
            index += 1
            options["timeout_ms"] = int(argv[index])
        elif arg in ("--help", "-h"):
            options["help"] = True
        index += 1
    if not options["award"]:
        options["award"] = OSCAR
    if options["help"] or options["year"] is None:
        print(HELP)
        return 0 if options["help"] else 1
    try:
        result = fetch_awards(
            options["award"],
            options["year"],
            category=options["category"],
            out=options["out"],
            timeout_ms=options["timeout_ms"],
        )
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    print(f"已写入 {result['count']} 条到 {result['filename']}")
    print("下一步：工具\\预览导入.bat 该文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
