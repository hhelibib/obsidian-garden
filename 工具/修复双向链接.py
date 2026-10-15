import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.douban_people import repair_bidirectional_links


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    vault = Path(__file__).resolve().parents[1]
    result = repair_bidirectional_links(vault)
    paired = sum(1 for films in result["出演"].values() if films)
    print(f"改写影片 {result['影片']}，改写人物 {result['人物']}，有出演的人物 {paired}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
