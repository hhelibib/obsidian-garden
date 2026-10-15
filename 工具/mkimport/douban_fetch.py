import json
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mkimport.douban import (
    PARTIAL_NOTE,
    DoubanBlocked,
    DoubanRateLimited,
    cookie_path,
    douban_headers,
    ensure_allowed,
    list_filename,
    list_page_url,
    load_cookie,
    merge_list_items,
    parse_list_payload,
    parse_personage_html,
    parse_subject_html,
    require_login,
    save_latest_cookie,
    read_cookie,
    store_anonymous_cookie,
)
from mkimport.films import _read_frontmatter
from mkimport.http import request

MAX_THREADS = 5
MIN_DELAY = 1.0
PAGE_SIZE = 20
PAGE_LIMIT = 2000


def fetch_list(vault, year, region="", genre="", delay=3, timeout_ms=45000, limit=None):
    delay = max(MIN_DELAY, float(delay))
    _log(f"拉列表 year={year} region={region or '全部'} genre={genre or '全部'} 每页 {PAGE_SIZE}")
    cookie = ensure_cookie(vault, timeout_ms)
    _log(f"列表请求使用的 Cookie 名称：{_cookie_names(cookie)}")
    require_login(cookie)
    headers = douban_headers(cookie, "https://movie.douban.com/explore")
    folder = Path(vault) / "收件箱" / "豆瓣"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / list_filename(year, region, genre)
    items, start, finished = _resume_list(path)
    if finished:
        _log(f"清单已到末页，共 {len(items)} 部")
        return {"path": path, "count": len(items), "stopped": "empty", "note": PARTIAL_NOTE}
    if start:
        _log(f"从 start={start} 继续，已有 {len(items)} 部")
    cap = PAGE_LIMIT if limit is None else int(limit)
    if cap < PAGE_SIZE:
        raise RuntimeError(f"上限至少为 {PAGE_SIZE}")
    stopped = "empty"
    try:
        while start < cap:
            if start:
                time.sleep(delay)
            _log(f"分页 start={start}")
            page = _get_json(list_page_url(year, region, genre, start), headers, timeout_ms, vault)
            added_page = parse_list_payload(page)
            items, added = merge_list_items(items, added_page)
            if not added_page:
                stopped = "empty"
                break
            if not added:
                stopped = "repeat"
                break
            start += PAGE_SIZE
            _write_list(path, year, region, genre, items, start, "partial")
        else:
            stopped = "cap"
    except DoubanRateLimited as error:
        stopped = "rate-limit"
        _log(str(error))
    _write_list(path, year, region, genre, items, start, stopped)
    return {"path": path, "count": len(items), "stopped": stopped, "note": PARTIAL_NOTE}


def fetch_details(vault, list_path, threads=2, delay=1.2, timeout_ms=45000, refresh=False):
    threads = int(threads)
    if threads < 1 or threads > MAX_THREADS:
        raise RuntimeError("线程数必须是 1 到 5")
    delay = max(MIN_DELAY, float(delay))
    packet = json.loads(Path(list_path).read_text(encoding="utf-8"))
    items = packet.get("items") or []
    folder = Path(vault) / "收件箱" / "豆瓣" / "详情"
    folder.mkdir(parents=True, exist_ok=True)
    pending = []
    skipped = []
    for item in items:
        target = folder / f"{item['id']}.json"
        if target.is_file() and not refresh and str((_read_json(target).get("中文名") or "")).strip():
            skipped.append(item["id"])
            continue
        pending.append(item)
    cookie = ensure_cookie(vault, timeout_ms) if pending else ""
    if pending:
        require_login(cookie)
    headers = douban_headers(cookie, "https://movie.douban.com/")
    gap = _Gap(delay)
    stop = threading.Event()
    saved = []
    failed = []
    blocked = ""

    def worker(item):
        if stop.is_set():
            return {"id": item["id"], "skipped": True}
        gap.wait()
        if stop.is_set():
            return {"id": item["id"], "skipped": True}
        url = item.get("url") or f"https://movie.douban.com/subject/{item['id']}/"
        try:
            page = _get_text(url, headers, timeout_ms, vault)
            detail = parse_subject_html(page, item["id"])
        except DoubanBlocked:
            stop.set()
            raise
        except Exception as error:
            return {"id": item["id"], "error": str(error)}
        target = folder / f"{item['id']}.json"
        target.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"id": item["id"], "saved": True}

    if pending:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            futures = [pool.submit(worker, item) for item in pending]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except DoubanBlocked as error:
                    blocked = str(error)
                    break
                if result.get("error"):
                    failed.append(f"{result['id']}：{result['error']}")
                elif result.get("saved"):
                    saved.append(result["id"])
            if blocked:
                for future in futures:
                    future.cancel()
    report = _detail_report(packet, saved, skipped, failed, blocked)
    report_path = Path(vault) / "收件箱" / "豆瓣" / f"报告-详情-{Path(list_path).stem}.md"
    report_path.write_text(report, encoding="utf-8")
    return {
        "saved": saved,
        "skipped": skipped,
        "failed": failed,
        "blocked": blocked,
        "reportPath": report_path,
    }


def fetch_film_credits(vault, threads=2, delay=3, timeout_ms=45000, refresh=False):
    threads = int(threads)
    if threads < 1 or threads > MAX_THREADS:
        raise RuntimeError("线程数必须是 1 到 5")
    delay = max(MIN_DELAY, float(delay))
    vault = Path(vault)
    ids = _film_subject_ids(vault)
    folder = vault / "收件箱" / "豆瓣" / "详情"
    folder.mkdir(parents=True, exist_ok=True)
    pending = []
    skipped = []
    for subject_id in ids:
        target = folder / f"{subject_id}.json"
        if target.is_file() and not refresh:
            try:
                cached = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached = {}
            if _detail_cache_ok(target):
                skipped.append(subject_id)
                continue
        pending.append(subject_id)
    return _fetch_named(
        vault,
        pending,
        skipped,
        threads,
        delay,
        timeout_ms,
        folder,
        lambda subject_id: f"https://movie.douban.com/subject/{subject_id}/",
        lambda page, subject_id: parse_subject_html(page, subject_id),
        "演职员",
        "https://movie.douban.com/",
    )


def fetch_personages(vault, threads=2, delay=3, timeout_ms=45000, refresh=False):
    threads = int(threads)
    if threads < 1 or threads > MAX_THREADS:
        raise RuntimeError("线程数必须是 1 到 5")
    delay = max(MIN_DELAY, float(delay))
    vault = Path(vault)
    ids = _person_ids_from_details(vault)
    folder = vault / "收件箱" / "豆瓣" / "人物"
    folder.mkdir(parents=True, exist_ok=True)
    pending = []
    skipped = []
    for person_id in ids:
        target = folder / f"{person_id}.json"
        if target.is_file() and not refresh and _person_cache_ok(target):
            skipped.append(person_id)
            continue
        pending.append(person_id)
    return _fetch_named(
        vault,
        pending,
        skipped,
        threads,
        delay,
        timeout_ms,
        folder,
        lambda person_id: f"https://www.douban.com/personage/{person_id}/",
        lambda page, person_id: parse_personage_html(page, person_id),
        "人物页",
        "https://www.douban.com/",
    )


def run_people_pipeline(vault, threads=2, delay=3, timeout_ms=45000, refresh=False, transport=None):
    threads = int(threads)
    if threads < 1 or threads > MAX_THREADS:
        raise RuntimeError("线程数必须是 1 到 5")
    delay = max(MIN_DELAY, float(delay))
    vault = Path(vault)
    detail_folder = vault / "收件箱" / "豆瓣" / "详情"
    person_folder = vault / "收件箱" / "豆瓣" / "人物"
    detail_folder.mkdir(parents=True, exist_ok=True)
    person_folder.mkdir(parents=True, exist_ok=True)
    film_queue = deque()
    films_skipped = []
    for subject_id in _film_subject_ids(vault):
        target = detail_folder / f"{subject_id}.json"
        if target.is_file() and not refresh and _detail_cache_ok(target):
            films_skipped.append(subject_id)
            continue
        film_queue.append(subject_id)
    person_queue = deque()
    seen_people = set()
    people_skipped = []
    for person_id in _person_ids_from_details(vault):
        _queue_person(person_id, person_folder, person_queue, seen_people, people_skipped, refresh)
    if not film_queue and not person_queue:
        report_path = _pipeline_report(vault, [], films_skipped, [], [], people_skipped, [], "")
        return _pipeline_result([], films_skipped, [], [], people_skipped, [], "", report_path)
    _log(f"流水线开始，剩余未执行 影片 {len(film_queue)}、人物 {len(person_queue)}")
    film_headers, person_headers = _pipeline_headers(vault, timeout_ms, transport)
    gap = _Gap(delay)
    cond = threading.Condition()
    done = threading.Event()
    stop = threading.Event()
    state = {
        "films_left": len(film_queue),
        "inflight": 0,
        "blocked": "",
        "films_saved": [],
        "films_failed": [],
        "people_saved": [],
        "people_failed": [],
    }

    def report(kind, item_id, outcome):
        with cond:
            waiting = (len(film_queue), len(person_queue))
        _log(f"结果 {kind} {item_id} {outcome}，剩余未执行 影片 {waiting[0]}、人物 {waiting[1]}")

    def enqueue_person(person_id):
        with cond:
            added = _queue_person(person_id, person_folder, person_queue, seen_people, people_skipped, refresh)
            if added:
                cond.notify()

    def finish_if_idle():
        if state["films_left"] == 0 and state["inflight"] == 0 and not person_queue and not film_queue:
            done.set()
            cond.notify_all()

    def fail_blocked(error):
        with cond:
            if not state["blocked"]:
                state["blocked"] = str(error)
            stop.set()
            done.set()
            cond.notify_all()

    def worker():
        while not done.is_set():
            with cond:
                job = None
                while not done.is_set():
                    job = _take_pipeline_job(person_queue, film_queue, stop)
                    if job:
                        state["inflight"] += 1
                        break
                    finish_if_idle()
                    if done.is_set():
                        return
                    cond.wait(timeout=0.3)
                else:
                    return
            kind, item_id = job
            try:
                if kind == "film":
                    _pipeline_film(
                        item_id,
                        detail_folder,
                        film_headers,
                        timeout_ms,
                        gap,
                        stop,
                        state,
                        fail_blocked,
                        enqueue_person,
                        transport,
                        vault,
                        report,
                    )
                else:
                    _pipeline_person(
                        item_id,
                        person_folder,
                        person_headers,
                        timeout_ms,
                        gap,
                        stop,
                        state,
                        fail_blocked,
                        transport,
                        vault,
                        report,
                    )
            finally:
                with cond:
                    state["inflight"] -= 1
                    if kind == "film":
                        state["films_left"] -= 1
                    finish_if_idle()

    workers = [threading.Thread(target=worker, name=f"douban-pipeline-{index}") for index in range(threads)]
    for item in workers:
        item.start()
    with cond:
        cond.notify_all()
    for item in workers:
        item.join()
    _log(f"流水线结束，剩余未执行 影片 {len(film_queue)}、人物 {len(person_queue)}")
    report_path = _pipeline_report(
        vault,
        state["films_saved"],
        films_skipped,
        state["films_failed"],
        state["people_saved"],
        people_skipped,
        state["people_failed"],
        state["blocked"],
    )
    return _pipeline_result(
        state["films_saved"],
        films_skipped,
        state["films_failed"],
        state["people_saved"],
        people_skipped,
        state["people_failed"],
        state["blocked"],
        report_path,
    )


def _pipeline_headers(vault, timeout_ms, transport):
    if transport is not None:
        return {}, {}
    cookie = ensure_cookie(vault, timeout_ms)
    require_login(cookie)
    return douban_headers(cookie, "https://movie.douban.com/"), douban_headers(cookie, "https://www.douban.com/")


def _pipeline_film(subject_id, folder, headers, timeout_ms, gap, stop, state, fail_blocked, enqueue_person, transport, vault, report):
    if stop.is_set():
        report("影片", subject_id, "未请求")
        return
    gap.wait()
    if stop.is_set():
        report("影片", subject_id, "未请求")
        return
    url = f"https://movie.douban.com/subject/{subject_id}/"
    try:
        page = _pipeline_fetch(url, headers, timeout_ms, transport, vault)
        detail = parse_subject_html(page, subject_id)
    except DoubanBlocked as error:
        fail_blocked(error)
        report("影片", subject_id, f"已停止：{error}")
        return
    except Exception as error:
        state["films_failed"].append(f"{subject_id}：{error}")
        report("影片", subject_id, f"失败：{error}")
        return
    target = folder / f"{subject_id}.json"
    target.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state["films_saved"].append(subject_id)
    for person in detail.get("人物") or []:
        enqueue_person(person.get("id") or "")
    report("影片", subject_id, "已保存")


def _pipeline_person(person_id, folder, headers, timeout_ms, gap, stop, state, fail_blocked, transport, vault, report):
    if stop.is_set():
        report("人物", person_id, "未请求")
        return
    gap.wait()
    if stop.is_set():
        report("人物", person_id, "未请求")
        return
    url = f"https://www.douban.com/personage/{person_id}/"
    try:
        page = _pipeline_fetch(url, headers, timeout_ms, transport, vault)
        detail = parse_personage_html(page, person_id)
    except DoubanBlocked as error:
        fail_blocked(error)
        report("人物", person_id, f"已停止：{error}")
        return
    except Exception as error:
        state["people_failed"].append(f"{person_id}：{error}")
        report("人物", person_id, f"失败：{error}")
        return
    target = folder / f"{person_id}.json"
    target.write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state["people_saved"].append(person_id)
    report("人物", person_id, "已保存")


def _pipeline_fetch(url, headers, timeout_ms, transport, vault=None):
    if transport is not None:
        return transport(url)
    return _get_text(url, headers, timeout_ms, vault)


def _take_pipeline_job(person_queue, film_queue, stop):
    if stop.is_set():
        return None
    if person_queue:
        return ("person", person_queue.popleft())
    if film_queue:
        return ("film", film_queue.popleft())
    return None


def _queue_person(person_id, folder, person_queue, seen, skipped, refresh):
    person_id = str(person_id or "")
    if not person_id or person_id in seen:
        return False
    seen.add(person_id)
    if not refresh and _person_cache_ok(folder / f"{person_id}.json"):
        skipped.append(person_id)
        return False
    person_queue.append(person_id)
    return True


def _detail_cache_ok(path):
    payload = _read_json(path)
    if not payload or "人物" not in payload:
        return False
    if str(payload.get("中文名") or "").strip():
        return True
    return bool(payload.get("人物"))


def _person_cache_ok(path):
    payload = _read_json(path)
    return bool(payload and str(payload.get("中文名") or "").strip())


def _read_json(path):
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _pipeline_result(films_saved, films_skipped, films_failed, people_saved, people_skipped, people_failed, blocked, report_path):
    return {
        "filmsSaved": films_saved,
        "filmsSkipped": films_skipped,
        "filmsFailed": films_failed,
        "peopleSaved": people_saved,
        "peopleSkipped": people_skipped,
        "peopleFailed": people_failed,
        "blocked": blocked,
        "reportPath": report_path,
    }


def _pipeline_report(vault, films_saved, films_skipped, films_failed, people_saved, people_skipped, people_failed, blocked):
    lines = [
        "# 豆瓣人物流水线",
        "",
        f"- 影片新保存：{len(films_saved)}",
        f"- 影片已有缓存：{len(films_skipped)}",
        f"- 影片失败：{len(films_failed)}",
        f"- 人物新保存：{len(people_saved)}",
        f"- 人物已有缓存：{len(people_skipped)}",
        f"- 人物失败：{len(people_failed)}",
    ]
    if blocked:
        lines.append(f"- 已停止：{blocked}")
    for item in films_failed + people_failed:
        lines.append(f"- 失败说明：{item}")
    lines.append("")
    path = Path(vault) / "收件箱" / "豆瓣" / "报告-流水线.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _film_subject_ids(vault):
    ids = []
    folder = Path(vault) / "影片"
    if not folder.is_dir():
        return ids
    for path in folder.glob("*.md"):
        if path.stem == "索引":
            continue
        fields = _read_frontmatter(path.read_text(encoding="utf-8"))
        found = re_search_subject(fields.get("豆瓣") or "")
        if found and found not in ids:
            ids.append(found)
    ids.sort(key=int)
    return ids


def re_search_subject(value):
    import re

    found = re.search(r"subject/(\d+)", str(value))
    return found.group(1) if found else ""


def _person_ids_from_details(vault):
    folder = Path(vault) / "收件箱" / "豆瓣" / "详情"
    ids = []
    if not folder.is_dir():
        return ids
    for path in sorted(folder.glob("*.json")):
        try:
            detail = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for person in detail.get("人物") or []:
            person_id = str(person.get("id") or "")
            if person_id and person_id not in ids:
                ids.append(person_id)
    return ids


def _fetch_named(vault, pending, skipped, threads, delay, timeout_ms, folder, url_of, parse, label, referer):
    cookie = ensure_cookie(vault, timeout_ms) if pending else ""
    if pending:
        require_login(cookie)
    headers = douban_headers(cookie, referer)
    gap = _Gap(delay)
    stop = threading.Event()
    saved = []
    failed = []
    blocked = ""

    def worker(item_id):
        if stop.is_set():
            return {"id": item_id, "skipped": True}
        gap.wait()
        if stop.is_set():
            return {"id": item_id, "skipped": True}
        try:
            page = _get_text(url_of(item_id), headers, timeout_ms, vault)
            payload = parse(page, item_id)
        except DoubanBlocked:
            stop.set()
            raise
        except Exception as error:
            return {"id": item_id, "error": str(error)}
        target = folder / f"{item_id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"id": item_id, "saved": True}

    if pending:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            futures = [pool.submit(worker, item_id) for item_id in pending]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except DoubanBlocked as error:
                    blocked = str(error)
                    break
                if result.get("error"):
                    failed.append(f"{result['id']}：{result['error']}")
                elif result.get("saved"):
                    saved.append(result["id"])
            if blocked:
                for future in futures:
                    future.cancel()
    report = _named_report(label, saved, skipped, failed, blocked)
    report_path = Path(vault) / "收件箱" / "豆瓣" / f"报告-{label}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return {
        "saved": saved,
        "skipped": skipped,
        "failed": failed,
        "blocked": blocked,
        "reportPath": report_path,
    }


def _named_report(label, saved, skipped, failed, blocked):
    lines = [
        f"# 豆瓣{label}",
        "",
        f"- 新保存：{len(saved)}",
        f"- 已有缓存：{len(skipped)}",
        f"- 失败：{len(failed)}",
    ]
    if blocked:
        lines.append(f"- 已停止：{blocked}")
    for item in failed:
        lines.append(f"- 失败说明：{item}")
    lines.append("")
    return "\n".join(lines)


def _resume_list(path):
    if not path.is_file():
        return [], 0, False
    packet = json.loads(path.read_text(encoding="utf-8"))
    items = packet.get("items") or []
    if packet.get("stopped") == "empty":
        return items, 0, True
    start = packet.get("next_start")
    if start is None:
        start = len(items) // PAGE_SIZE * PAGE_SIZE
    return items, int(start), False


def _write_list(path, year, region, genre, items, next_start, stopped):
    payload = {
        "source": "douban",
        "partial": True,
        "note": PARTIAL_NOTE,
        "year": int(year),
        "region": region or "",
        "genre": genre or "",
        "page_size": PAGE_SIZE,
        "next_start": next_start,
        "stopped": stopped,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_cookie(vault, timeout_ms=45000):
    path = cookie_path(vault)
    existing = load_cookie(vault)
    if existing:
        _log(f"沿用 Cookie 文件 {path} 名称：{_cookie_names(existing)}")
        return existing
    _log(f"Cookie 文件不存在：{path}，开始访问首页")
    response = request(
        "https://movie.douban.com/",
        timeout_ms=timeout_ms,
        headers=douban_headers("", "https://movie.douban.com/"),
    )
    page = _decode(response)
    _log(
        "首页响应 "
        f"status={response['status']} final={response['url']} "
        f"set-cookie={_set_cookie_names(response['headers'].get('set-cookie'))}"
    )
    _log(f"首页正文开头：{_preview(page)}")
    try:
        cookie = store_anonymous_cookie(vault, response["status"], response["headers"], page)
    except DoubanBlocked as error:
        _log(f"判定拦截：{error}")
        raise
    _log(f"已写入 Cookie 文件，名称：{_cookie_names(cookie)}")
    return cookie


def _get_text(url, headers, timeout_ms, vault=None):
    headers = dict(headers or {})
    if vault is not None:
        latest = read_cookie(vault)
        if latest and latest != headers.get("Cookie", ""):
            _log(f"使用 Cookie 文件中的最新值，名称：{_cookie_names(latest)}")
            headers["Cookie"] = latest
    _log(f"请求 {url}")
    response = request(url, timeout_ms=timeout_ms, headers=headers)
    if vault is not None:
        updated, changed = save_latest_cookie(vault, response["headers"].get("set-cookie") or [])
        if changed:
            _log(f"已重写 Cookie，名称：{_cookie_names(updated)}")
    text = _decode(response)
    content_type = response["headers"].get("content-type", "")
    _log(
        f"响应 status={response['status']} type={content_type} "
        f"bytes={len(text)} final={response['url']}"
    )
    _log(f"正文开头：{_preview(text)}")
    try:
        ensure_allowed(response["status"], text, response.get("url") or "")
    except DoubanBlocked as error:
        _log(f"判定拦截：{error}")
        raise
    if response["status"] >= 400:
        raise RuntimeError(f"HTTP {response['status']}：{text[:240]}")
    return text


def _log(message):
    print(f"[douban] {message}", file=sys.stderr)


def _cookie_names(header):
    names = []
    for part in str(header or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        names.append(part.split("=", 1)[0].strip())
    return ",".join(names) or "(无)"


def _set_cookie_names(values):
    if isinstance(values, str):
        values = [values]
    names = []
    for item in values or []:
        pair = str(item).split(";", 1)[0]
        if "=" in pair:
            names.append(pair.split("=", 1)[0].strip())
    return ",".join(names) or "(无)"


def _preview(text):
    return " ".join(str(text)[:180].split())


def _get_json(url, headers, timeout_ms, vault=None):
    text = _get_text(url, headers, timeout_ms, vault)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        if "<html" in text[:500].lower():
            raise DoubanBlocked("豆瓣返回了网页而不是列表数据") from error
        raise RuntimeError("列表不是 JSON") from error


def _decode(response):
    import gzip

    body = response["body"]
    if body.startswith(b"\x1f\x8b"):
        body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def _detail_report(packet, saved, skipped, failed, blocked):
    lines = [
        f"# 豆瓣详情",
        "",
        packet.get("note") or PARTIAL_NOTE,
        "",
        f"- 新保存：{len(saved)}",
        f"- 已有缓存：{len(skipped)}",
        f"- 失败：{len(failed)}",
    ]
    if blocked:
        lines.append(f"- 已停止：{blocked}")
    for item in failed:
        lines.append(f"- 失败说明：{item}")
    lines.append("")
    return "\n".join(lines)


class _Gap:
    def __init__(self, seconds):
        self.seconds = seconds
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now if now < self.next_at else 0
            self.next_at = (self.next_at if now < self.next_at else now) + self.seconds
        if delay:
            time.sleep(delay)
