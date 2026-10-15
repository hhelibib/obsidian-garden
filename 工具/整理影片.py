import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mkimport.films import reorganize_film

VAULT = Path(__file__).resolve().parents[1]
HELP = """用法
  python 工具/整理影片.py
  python 工具/整理影片.py "翼 (1927)" "第七天堂 (1927)" "非法图利 (1928)"

按 模板/影片.md 重写影片笔记。已填写的文首属性会保留。
简介、演职员、电影分析、观影笔记里多出来的内容也会保留。
不写参数时，处理 影片/ 里除索引以外的全部笔记。
"""


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    if argv in (["-h"], ["--help"]):
        print(HELP)
        return 0
    folder = VAULT / "影片"
    if argv:
        paths = [folder / (name if name.endswith(".md") else f"{name}.md") for name in argv]
    else:
        paths = sorted(path for path in folder.glob("*.md") if path.stem != "索引")
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        print("找不到影片笔记：" + "、".join(missing), file=sys.stderr)
        return 1
    for path in paths:
        result = reorganize_film(path)
        verb = "已重写" if result == "rewritten" else "无变化"
        print(f"{verb}：影片/{path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
