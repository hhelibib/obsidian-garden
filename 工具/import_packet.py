import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.importing import HELP, import_packet


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    options = {"apply": False, "images": True, "vault": None, "help": False, "timeout_ms": 45000}
    positionals = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--apply":
            options["apply"] = True
        elif arg == "--no-images":
            options["images"] = False
        elif arg in ("--help", "-h"):
            options["help"] = True
        elif arg == "--vault":
            index += 1
            options["vault"] = argv[index]
        elif arg == "--timeout":
            index += 1
            options["timeout_ms"] = int(argv[index])
        else:
            positionals.append(arg)
        index += 1
    if options["help"] or len(positionals) != 1:
        print(HELP)
        return 0 if options["help"] else 1
    try:
        result = import_packet(
            Path(positionals[0]).resolve(),
            apply=options["apply"],
            images=options["images"],
            vault=options["vault"],
            timeout_ms=options["timeout_ms"],
        )
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    print(result["preview"])
    print(f"报告：{result['reportPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
