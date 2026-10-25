"""Small credential-store adapter that never falls back to plaintext files."""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SERVICE_NAME = "BrainCellGPT"
MAX_SECRET_BYTES = 64_000


def _account_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 200:
        raise ValueError("Credential-store key is invalid.")
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _windows_credential_path(account: str) -> Path:
    return Path.home() / "AppData" / "Local" / SERVICE_NAME / "credentials" / (account + ".bin")


def _dpapi_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable.")
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source = ctypes.create_string_buffer(value)
    source_blob = DataBlob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
                                         wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    if not crypt32.CryptProtectData(ctypes.byref(source_blob), "BrainCellGPT credential", None, None, None, 0x1, ctypes.byref(target_blob)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(target_blob.pbData, ctypes.c_void_p))


def _dpapi_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable.")
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source = ctypes.create_string_buffer(value)
    source_blob = DataBlob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    target_blob = DataBlob()
    description = wintypes.LPWSTR()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR),
                                           ctypes.POINTER(DataBlob), wintypes.LPVOID, wintypes.LPVOID,
                                           wintypes.DWORD, ctypes.POINTER(DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    if not crypt32.CryptUnprotectData(ctypes.byref(source_blob), ctypes.byref(description), None, None, None, 0x1, ctypes.byref(target_blob)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(target_blob.pbData, ctypes.c_void_p))
        if description:
            kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))


def _windows_get(name: str) -> str | None:
    path = _windows_credential_path(_account_name(name))
    try:
        encrypted = path.read_bytes()
    except FileNotFoundError:
        return None
    if not encrypted or len(encrypted) > MAX_SECRET_BYTES * 2:
        return None
    try:
        raw = _dpapi_unprotect(encrypted)
        if len(raw) > MAX_SECRET_BYTES:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _windows_set(name: str, value: str) -> bool:
    raw = value.encode("utf-8")
    if len(raw) > MAX_SECRET_BYTES:
        return False
    path = _windows_credential_path(_account_name(name))
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = _dpapi_protect(raw)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".credential-", delete=False) as temporary:
            temporary.write(encrypted)
            temp_path = Path(temporary.name)
        os.replace(temp_path, path)
        return True
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _keyring_backend():
    try:
        import keyring  # type: ignore[import-not-found]
        backend = keyring.get_keyring()
    except Exception:
        return None
    module = type(backend).__module__.lower()
    if sys.platform == "darwin" and "macos" in module:
        return keyring
    if sys.platform.startswith("linux") and ("secretservice" in module or "kwallet" in module):
        return keyring
    return None


def get_secret(name: str) -> str | None:
    """Read a secret from DPAPI or an available native OS keyring."""
    try:
        if sys.platform == "win32":
            return _windows_get(name)
        account = _account_name(name)
        keyring = _keyring_backend()
        if keyring:
            value = keyring.get_password(SERVICE_NAME, account)
            return value if isinstance(value, str) and len(value.encode("utf-8")) <= MAX_SECRET_BYTES else None
        if sys.platform.startswith("linux"):
            tool = shutil.which("secret-tool")
            if tool:
                result = subprocess.run(
                    [tool, "lookup", "service", SERVICE_NAME, "key", account],
                    capture_output=True, timeout=5, check=False,
                )
                if result.returncode == 0 and len(result.stdout) <= MAX_SECRET_BYTES + 1:
                    return result.stdout.decode("utf-8").removesuffix("\n")
    except Exception:
        return None
    return None


def set_secret(name: str, value: str) -> bool:
    """Write to a native OS store; return false rather than write plaintext."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_SECRET_BYTES:
        return False
    try:
        if sys.platform == "win32":
            return _windows_set(name, value)
        account = _account_name(name)
        keyring = _keyring_backend()
        if keyring:
            keyring.set_password(SERVICE_NAME, account, value)
            return True
        if sys.platform.startswith("linux"):
            tool = shutil.which("secret-tool")
            if tool:
                result = subprocess.run(
                    [tool, "store", "--label=BrainCellGPT credential", "service", SERVICE_NAME, "key", account],
                    input=value.encode("utf-8"), capture_output=True, timeout=5, check=False,
                )
                return result.returncode == 0
    except Exception:
        return False
    return False


def delete_secret(name: str) -> bool:
    """Delete a value from a native store when the backend supports deletion."""
    try:
        if sys.platform == "win32":
            path = _windows_credential_path(_account_name(name))
            path.unlink(missing_ok=True)
            return True
        account = _account_name(name)
        keyring = _keyring_backend()
        if keyring:
            try:
                keyring.delete_password(SERVICE_NAME, account)
            except Exception as exc:
                if "password was not found" not in str(exc).lower():
                    return False
            return True
        if sys.platform.startswith("linux"):
            tool = shutil.which("secret-tool")
            if tool:
                result = subprocess.run(
                    [tool, "clear", "service", SERVICE_NAME, "key", account],
                    capture_output=True, timeout=5, check=False,
                )
                return result.returncode == 0
    except Exception:
        return False
    return False
