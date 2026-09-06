# -*- coding: utf-8 -*-
"""shijuefenxi 网络安全与图片真实性校验（Python 3.8 标准库，无第三方依赖）。

职责：
  1. URL 安全校验：仅 http/https；默认拦截本机/私网/元数据地址，防 SSRF。
  2. 受限下载：大小与超时限制；手动跟随重定向并逐跳重新校验；自动解压 gzip/deflate。
  3. 图片真实性/安全性校验：magic bytes 嗅探 + PIL 结构校验 + 像素数上限，
     拦截伪装成图片的文件、损坏图片与解压炸弹。
"""
import io
import socket
import zlib
import ipaddress
from urllib.parse import urlsplit, urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import URLError, HTTPError

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

MAX_REDIRECTS = 5
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) shijuefenxi-skill/1.0"


class SafeNetError(Exception):
    pass


def _is_private_ip(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            or str(ip) == "169.254.169.254")


def _host_is_private(host):
    h = host.lower().rstrip(".")
    if h in ("localhost", "ip6-localhost", "ip6-loopback"):
        return True
    if h.endswith(".localhost") or h.endswith(".local") or h.endswith(".internal") or h.endswith(".lan"):
        return True
    try:
        return _is_private_ip(ipaddress.ip_address(h))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError as e:
        raise SafeNetError("无法解析主机名 %s：%s" % (host, e))
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_private_ip(ip):
            return True
    return False


def validate_url(url, allow_private=False):
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SafeNetError("仅支持 http/https 链接（不支持 %s）：%s" % (parts.scheme or "无", url))
    if not parts.hostname:
        raise SafeNetError("链接缺少主机名：%s" % url)
    if not allow_private and _host_is_private(parts.hostname):
        raise SafeNetError("已拦截本机/私网地址（防 SSRF）：%s。如确需访问，请在 config.json 设置 allow_local_url=true。" % parts.hostname)
    return parts


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _decompress(data, enc, max_bytes):
    enc = (enc or "").strip().lower()
    if not enc and data[:2] == b"\x1f\x8b":
        enc = "gzip"
    if enc in ("", "identity", "none"):
        return data
    if enc == "br":
        raise SafeNetError("暂不支持 br 压缩响应，请改用支持 gzip/deflate 的地址")
    attempts = []
    if enc in ("gzip", "x-gzip"):
        attempts.append(16 + zlib.MAX_WBITS)
    elif enc == "deflate":
        attempts.append(zlib.MAX_WBITS)
        attempts.append(-zlib.MAX_WBITS)
    else:
        raise SafeNetError("不支持的 Content-Encoding：%s" % enc)
    last_err = None
    for wbits in attempts:
        try:
            dobj = zlib.decompressobj(wbits)
            out = dobj.decompress(data, max_bytes + 1)
            if len(out) > max_bytes or not dobj.eof:
                raise SafeNetError("解压后数据超过大小上限 %s 字节" % max_bytes)
            return out
        except SafeNetError:
            raise
        except zlib.error as e:
            last_err = e
    raise SafeNetError("解压失败：%s" % last_err)


def safe_download(url, allow_private=False, max_bytes=8000000, timeout=20, max_redirects=5):
    opener = build_opener(_NoRedirect())
    current = url
    for _hop in range(int(max_redirects) + 1):
        validate_url(current, allow_private)
        req = Request(current, headers={"User-Agent": DEFAULT_UA, "Accept": "*/*"})
        try:
            resp = opener.open(req, timeout=timeout)
        except HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if loc:
                    current = urljoin(current, loc)
                    continue
            raise SafeNetError("下载失败 HTTP %s：%s" % (e.code, current))
        except URLError as e:
            raise SafeNetError("网络错误：%s（%s）" % (current, getattr(e, "reason", e)))
        except Exception as e:
            raise SafeNetError("下载失败：%s（%s）" % (current, e))
        clen = resp.headers.get("Content-Length")
        if clen:
            try:
                clen = int(clen)
                if clen > max_bytes:
                    raise SafeNetError("文件过大（%s 字节，上限 %s）：%s" % (clen, max_bytes, current))
            except ValueError:
                pass
        data = b""
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > max_bytes:
                raise SafeNetError("下载超过大小上限 %s 字节：%s" % (max_bytes, current))
        data = _decompress(data, resp.headers.get("Content-Encoding"), max_bytes)
        return data, (resp.headers.get("Content-Type") or "")
    raise SafeNetError("重定向次数过多：%s" % url)


def sniff_image(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def verify_image(data, max_pixels=40000000):
    fmt = sniff_image(data)
    if not fmt:
        return {"ok": False, "reason": "不是可识别的图片格式（magic bytes 不符），可能是伪装成图片的文件"}
    if not HAS_PIL:
        return {"ok": True, "format": fmt, "width": None, "height": None,
                "warning": "未安装 Pillow，仅完成 magic bytes 校验"}
    old = getattr(Image, "MAX_IMAGE_PIXELS", None)
    try:
        Image.MAX_IMAGE_PIXELS = max_pixels
        im = Image.open(io.BytesIO(data))
        im.verify()
    except Exception as e:
        return {"ok": False, "reason": "图片结构校验失败（可能损坏/伪造/解压炸弹）：%s" % e}
    finally:
        if old is not None:
            Image.MAX_IMAGE_PIXELS = old
    try:
        Image.MAX_IMAGE_PIXELS = max_pixels
        im2 = Image.open(io.BytesIO(data))
        w, h = im2.size
    except Exception as e:
        return {"ok": False, "reason": "无法读取图片尺寸：%s" % e}
    finally:
        if old is not None:
            Image.MAX_IMAGE_PIXELS = old
    if w <= 0 or h <= 0 or w * h > max_pixels:
        return {"ok": False, "reason": "图片尺寸异常（%dx%d，像素数超过上限）" % (w, h)}
    return {"ok": True, "format": fmt, "width": w, "height": h}
