import socket
import ssl
from urllib.parse import urljoin, urlsplit

USER_AGENT = "mk-vault-award-import/0.1 (personal Obsidian vault)"
MAX_BYTES = 15 * 1024 * 1024


def request(url, timeout_ms=45000, headers=None, max_redirects=5):
    current = url
    timeout = timeout_ms / 1000
    extra = dict(headers or {})
    jar = _cookie_jar(extra.get("Cookie", ""))
    collected = {}
    for _ in range(max_redirects + 1):
        parts = urlsplit(current)
        if parts.scheme != "https":
            raise RuntimeError(f"只允许 https：{current}")
        port = parts.port or 443
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        try:
            sock = _connect(parts.hostname, port, timeout)
        except TimeoutError as error:
            raise RuntimeError(f"连接超时（{timeout_ms}ms）：{parts.hostname}") from error
        except OSError as error:
            code = getattr(error, "winerror", None) or getattr(error, "errno", "")
            if isinstance(error, (ConnectionResetError, ConnectionRefusedError, socket.gaierror, TimeoutError)):
                raise RuntimeError(f"无法连接 {parts.hostname}（{error.__class__.__name__}）") from error
            raise RuntimeError(f"无法连接 {parts.hostname}（{code}）") from error
        try:
            context = ssl.create_default_context()
            tls = context.wrap_socket(sock, server_hostname=parts.hostname)
            outbound = dict(extra)
            if jar:
                outbound["Cookie"] = _format_cookie_jar(jar)
            request_headers = {"Host": parts.hostname, "User-Agent": USER_AGENT, "Connection": "close", **outbound}
            payload = "".join(f"{key}: {value}\r\n" for key, value in request_headers.items())
            tls.sendall(f"GET {path} HTTP/1.1\r\n{payload}\r\n".encode("ascii", "ignore"))
            status, response_headers, body = _read_response(tls)
        finally:
            try:
                sock.close()
            except OSError:
                pass
        received = _absorb_cookies(jar, response_headers.get("set-cookie"))
        collected.update(received)
        if 300 <= status < 400 and response_headers.get("location"):
            current = urljoin(current, response_headers["location"])
            continue
        if collected:
            response_headers["set-cookie"] = [f"{name}={value}" for name, value in collected.items()]
        return {"status": status, "headers": response_headers, "body": body, "url": current}
    raise RuntimeError(f"重定向次数过多：{url}")


def request_text(url, timeout_ms=45000, headers=None):
    response = request(url, timeout_ms=timeout_ms, headers=headers)
    text = response["body"].decode("utf-8", "replace")
    if response["status"] >= 400:
        raise RuntimeError(f"HTTP {response['status']}：{text[:240]}")
    return text


def _connect(hostname, port, timeout):
    infos = socket.getaddrinfo(hostname, port, socket.AF_INET, socket.SOCK_STREAM)
    last = None
    for family, socktype, proto, _, address in infos:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            return sock
        except OSError as error:
            last = error
            sock.close()
    if last:
        raise last
    raise OSError(f"无法连接 {hostname}")


def _read_response(tls):
    chunks = []
    size = 0
    while b"\r\n\r\n" not in b"".join(chunks):
        piece = tls.recv(8192)
        if not piece:
            break
        size += len(piece)
        if size > MAX_BYTES:
            raise RuntimeError("响应超过 15MB")
        chunks.append(piece)
    raw = b"".join(chunks)
    header_blob, _, rest = raw.partition(b"\r\n\r\n")
    status, headers = split_response_headers(header_blob.decode("iso-8859-1", "replace"))
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _read_chunked(rest, tls, size)
    else:
        length = int(headers.get("content-length", "0") or "0")
        body = rest
        while len(body) < length:
            piece = tls.recv(min(65536, length - len(body)))
            if not piece:
                break
            if len(body) + len(piece) > MAX_BYTES:
                raise RuntimeError("响应超过 15MB")
            body += piece
        if not length:
            while True:
                piece = tls.recv(65536)
                if not piece:
                    break
                if len(body) + len(piece) > MAX_BYTES:
                    raise RuntimeError("响应超过 15MB")
                body += piece
    return status, headers, body


def split_response_headers(header_text):
    lines = header_text.split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {}
    cookies = []
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key == "set-cookie":
            cookies.append(value.strip())
        else:
            headers[key] = value.strip()
    if cookies:
        headers["set-cookie"] = cookies
    return status, headers


def _cookie_jar(header):
    jar = {}
    for part in str(header or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            jar[name] = value.strip()
    return jar


def _unwrap_cookie_value(value):
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _format_cookie_jar(jar):
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def _absorb_cookies(jar, set_cookie):
    values = set_cookie if isinstance(set_cookie, list) else ([set_cookie] if set_cookie else [])
    received = {}
    for item in values:
        pair = str(item).split(";", 1)[0].strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        value = _unwrap_cookie_value(value.strip())
        if not name or not value:
            continue
        jar[name] = value
        received[name] = value
    return received


def _read_chunked(initial, tls, already):
    data = initial
    body = bytearray()
    while True:
        while b"\r\n" not in data:
            piece = tls.recv(8192)
            if not piece:
                return bytes(body)
            data += piece
        line, _, data = data.partition(b"\r\n")
        size = int(line.split(b";", 1)[0], 16)
        if size == 0:
            return bytes(body)
        while len(data) < size + 2:
            piece = tls.recv(65536)
            if not piece:
                break
            data += piece
        body += data[:size]
        data = data[size + 2 :]
        if len(body) + already > MAX_BYTES:
            raise RuntimeError("响应超过 15MB")
