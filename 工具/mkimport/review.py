import re

BLOCKING_FIELD = re.compile(r"影片检索名|检索名|奖项名|结果|中文名")


def partition_uncertainties(items):
    blocking = []
    noted = []
    for item in items or []:
        says = item.get("说法") if isinstance(item, dict) and isinstance(item.get("说法"), list) else []
        text = f"{item.get('主题', '') if isinstance(item, dict) else ''}\n" + "\n".join(str(part) for part in says)
        if BLOCKING_FIELD.search(text):
            blocking.append(item)
        else:
            noted.append(item)
    return {"blocking": blocking, "noted": noted}
