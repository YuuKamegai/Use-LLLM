"""Windows user-scoped protection for persisted application secrets."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

from use_lllm.core.config import ConfigurationError

PROTECTED_SECRET_PREFIX = "dpapi-current-user-v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte)))


def _windows_cryptography():
    if os.name != "nt":
        raise ConfigurationError(
            "API keyの安全な保存にはWindows Data Protection API (DPAPI)が必要です。"
        )
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _input_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value, len(value))
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def _last_error(operation: str) -> ConfigurationError:
    code = ctypes.get_last_error()
    message = ctypes.FormatError(code).strip() if code else "unknown Windows error"
    return ConfigurationError(f"{operation}に失敗しました: {message} (WinError {code})")


def protect_secret(value: str) -> str:
    """Protect a non-empty value for the current Windows user on this machine."""

    if not value:
        raise ConfigurationError("空のsecretは保護できません。")
    crypt32, kernel32 = _windows_cryptography()
    source, source_buffer = _input_blob(value.encode("utf-8"))
    protected = _DataBlob()
    ctypes.set_last_error(0)
    success = crypt32.CryptProtectData(
        ctypes.byref(source),
        "Use-LLLM Azure OpenAI API key",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    )
    del source_buffer
    if not success:
        raise _last_error("API keyの暗号化")
    try:
        ciphertext = ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)
    return PROTECTED_SECRET_PREFIX + base64.b64encode(ciphertext).decode("ascii")


def unprotect_secret(value: str) -> str:
    """Unprotect a value previously produced by :func:`protect_secret`."""

    if not value.startswith(PROTECTED_SECRET_PREFIX):
        raise ConfigurationError("保存されたAPI keyの保護形式が不明です。")
    encoded = value.removeprefix(PROTECTED_SECRET_PREFIX)
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ConfigurationError("保存されたAPI keyの暗号データが不正です。") from exc
    if not ciphertext:
        raise ConfigurationError("保存されたAPI keyの暗号データが空です。")

    crypt32, kernel32 = _windows_cryptography()
    source, source_buffer = _input_blob(ciphertext)
    plaintext = _DataBlob()
    description = wintypes.LPWSTR()
    ctypes.set_last_error(0)
    success = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(plaintext),
    )
    del source_buffer
    if not success:
        raise _last_error("API keyの復号")
    try:
        raw = ctypes.string_at(plaintext.pbData, plaintext.cbData)
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("保存されたAPI keyをUTF-8として復号できません。") from exc
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(plaintext.pbData)
