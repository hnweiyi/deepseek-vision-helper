# -*- coding: utf-8 -*-
"""Windows DPAPI 加解密（仅 Windows）。用 ctypes 调 Crypt32，无第三方依赖。

api_key 支持两种形态：
  - 明文：直接填写。
  - 加密：以 "dpapi:" 开头的 base64 串（用 encrypt_key.py 生成），运行时自动解密。
加密后的数据仅能被【当前 Windows 用户】解密，换机器/换用户无效。
"""
import base64
import ctypes
from ctypes import wintypes

PREFIX = "dpapi:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = None
_kernel32 = None


def _setup():
    global _crypt32, _kernel32
    if _crypt32 is not None:
        return
    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _input_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def encrypt_bytes(data):
    _setup()
    if not isinstance(data, bytes):
        data = bytes(data)
    inb, _keep = _input_blob(data)
    outb = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(ctypes.byref(inb), "shijuefenxi", None, None, None, 0, ctypes.byref(outb))
    if not ok:
        raise OSError("CryptProtectData 失败，错误码 %d" % ctypes.get_last_error())
    try:
        return ctypes.string_at(outb.pbData, outb.cbData)
    finally:
        if outb.pbData:
            _kernel32.LocalFree(ctypes.cast(outb.pbData, ctypes.c_void_p))


def decrypt_bytes(data):
    _setup()
    if not isinstance(data, bytes):
        data = bytes(data)
    inb, _keep = _input_blob(data)
    outb = _DATA_BLOB()
    desc = wintypes.LPWSTR()
    ok = _crypt32.CryptUnprotectData(ctypes.byref(inb), ctypes.byref(desc), None, None, None, 0, ctypes.byref(outb))
    if not ok:
        raise OSError("CryptUnprotectData 失败，错误码 %d（可能不是当前 Windows 用户加密的）" % ctypes.get_last_error())
    try:
        return ctypes.string_at(outb.pbData, outb.cbData)
    finally:
        if outb.pbData:
            _kernel32.LocalFree(ctypes.cast(outb.pbData, ctypes.c_void_p))


def encrypt_text(plain):
    return PREFIX + base64.b64encode(encrypt_bytes(plain.encode("utf-8"))).decode("ascii")


def decrypt_text(value):
    if isinstance(value, str) and value.startswith(PREFIX):
        return decrypt_bytes(base64.b64decode(value[len(PREFIX):])).decode("utf-8")
    return value


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)