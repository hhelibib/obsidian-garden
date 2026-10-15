from pathlib import Path

from mkimport.http import request
from mkimport.packet import assert_image_url

EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def relative_image_path(image, extension):
    return f"附件/{image['ownerType']}/{image['owner']}/{file_stem(image)}.{extension}"


def file_stem(image):
    if image["role"] in ("标识", "照片", "海报"):
        return image["role"]
    if image["role"] == "典礼":
        return f"典礼-{image['year']}"
    if image["role"] == "画面":
        return "画面-01"
    raise RuntimeError(f"未知图片角色：{image['role']}")


def embed_for(relative_path):
    return f'<img src="{relative_path}" width="240">'


def download_image(vault_root, image, timeout_ms=45000):
    assert_image_url(image["url"])
    folder = Path(vault_root) / "附件" / image["ownerType"] / image["owner"]
    existing = _find_existing(folder, file_stem(image))
    if existing:
        return {
            "relativePath": existing,
            "embed": embed_for(existing) if image["role"] == "标识" else None,
            "skipped": True,
        }
    response = request(
        image["url"],
        timeout_ms=timeout_ms,
        headers={"Accept": "image/jpeg,image/png,image/webp,image/gif"},
    )
    if response["status"] >= 400:
        raise RuntimeError(f"图片下载失败 HTTP {response['status']}")
    content_type = response["headers"].get("content-type", "").split(";", 1)[0].strip().lower()
    extension = EXTENSIONS.get(content_type)
    if not extension:
        raise RuntimeError(f"不是允许的图片类型：{content_type or '未知'}")
    relative_path = relative_image_path(image, extension)
    absolute = Path(vault_root).joinpath(*relative_path.split("/"))
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_bytes(response["body"])
    return {
        "relativePath": relative_path,
        "embed": embed_for(relative_path) if image["role"] == "标识" else None,
        "skipped": False,
    }


def _find_existing(folder, stem):
    for extension in ("jpg", "jpeg", "png", "webp", "gif"):
        absolute = folder / f"{stem}.{extension}"
        if absolute.is_file():
            owner_type = folder.parent.name
            return f"附件/{owner_type}/{folder.name}/{stem}.{extension}"
    return None
