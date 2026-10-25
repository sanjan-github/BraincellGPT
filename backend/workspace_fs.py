"""Workspace file operations anchored to directory handles where the OS supports it."""
from __future__ import annotations

import os
import secrets
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO


class WorkspacePathError(ValueError):
    pass


def _parts(relative: str) -> tuple[tuple[str, ...], str]:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise WorkspacePathError("Invalid workspace-relative path.")
    path = PurePosixPath(relative)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        raise WorkspacePathError("Workspace path must be relative and stay inside the workspace.")
    return parts[:-1], parts[-1]


def _supports_dirfd() -> bool:
    return os.name != "nt" and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd and os.unlink in os.supports_dir_fd


def _remove_created_directories(root: Path, created: list[tuple[str, ...]]) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    for components in reversed(created):
        parent_fd = None
        try:
            parent_fd = os.open(root.resolve(strict=True), flags)
            for component in components[:-1]:
                next_fd = os.open(component, flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            os.rmdir(components[-1], dir_fd=parent_fd)
        except OSError:
            pass
        finally:
            if parent_fd is not None:
                os.close(parent_fd)


def _open_parent(root: Path, relative: str, create: bool = False) -> tuple[int, str, list[tuple[str, ...]]]:
    parents, leaf = _parts(relative)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    if not hasattr(os, "O_NOFOLLOW"):
        raise WorkspacePathError("This operating system cannot safely open workspace directories.")
    flags |= getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(root.resolve(strict=True), flags)
    created: list[tuple[str, ...]] = []
    try:
        for index, component in enumerate(parents):
            try:
                child_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=current_fd)
                created.append(tuple(parents[:index + 1]))
                child_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = child_fd
        return current_fd, leaf, created
    except Exception:
        os.close(current_fd)
        raise


def read_bytes(root: Path, relative: str, limit: int) -> bytes:
    if _supports_dirfd():
        parent_fd, leaf, _ = _open_parent(root, relative)
        file_fd = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(leaf, flags, dir_fd=parent_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkspacePathError("Workspace reads require a regular file without hard links.")
            with os.fdopen(file_fd, "rb", closefd=True) as source:
                file_fd = None
                data = source.read(limit + 1)
            if len(data) > limit:
                raise WorkspacePathError("Workspace file exceeds the read limit.")
            return data
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(parent_fd)

    path = root / Path(relative)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WorkspacePathError("Workspace reads require a regular file without hard links.")
        _verify_windows_handle_inside(root, fd)
        with os.fdopen(fd, "rb", closefd=True) as source:
            fd = -1
            data = source.read(limit + 1)
        if len(data) > limit:
            raise WorkspacePathError("Workspace file exceeds the read limit.")
        return data
    finally:
        if fd >= 0:
            os.close(fd)


def atomic_write(root: Path, relative: str, content: bytes, existing_mode: int | None = None) -> None:
    if _supports_dirfd():
        parent_fd, leaf, created = _open_parent(root, relative, create=True)
        temp_name = ".braincell-write-" + secrets.token_hex(12) + ".tmp"
        temp_fd = None
        succeeded = False
        try:
            try:
                info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise WorkspacePathError("Workspace writes require a regular file without hard links.")
                existing_mode = stat.S_IMODE(info.st_mode)
            mode = existing_mode if existing_mode is not None else 0o600
            temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=parent_fd)
            with os.fdopen(temp_fd, "wb", closefd=True) as target:
                temp_fd = None
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            succeeded = True
            try:
                os.fsync(parent_fd)
            except OSError:
                pass
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)
            if not succeeded:
                # Remove only directories this call created, and only if they remain empty.
                _remove_created_directories(root, created)
        return

    path = root / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".braincell-write-", suffix=".tmp", delete=False) as target:
            temp_path = Path(target.name)
            _verify_windows_handle_inside(root, target.fileno())
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def unlink_file(root: Path, relative: str) -> None:
    if _supports_dirfd():
        parent_fd, leaf, _ = _open_parent(root, relative)
        try:
            info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkspacePathError("Workspace deletes require a regular file without hard links.")
            os.unlink(leaf, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        return
    path = root / Path(relative)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise WorkspacePathError("Workspace deletes require a regular file without hard links.")
    _verify_windows_path_inside(root, path)
    path.unlink()


def _windows_final_path(handle: int) -> str:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_path = kernel32.GetFinalPathNameByHandleW
    get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_path(wintypes.HANDLE(handle), buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    value = buffer.value
    return value[4:] if value.startswith("\\\\?\\") else value


def _windows_parent_handle(root: Path, parent: Path) -> tuple[int, str]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(parent), 0xC0000000, 0x1 | 0x2 | 0x4, None, 3,
        0x02000000 | 0x00200000, None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        class AttributeTagInfo(ctypes.Structure):
            _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]
        info = AttributeTagInfo()
        kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        if not kernel32.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not info.FileAttributes & 0x10 or info.FileAttributes & 0x400:
            raise WorkspacePathError("Workspace parent is not a regular directory.")
        final_parent = str(Path(_windows_final_path(handle)))
        _verify_windows_path_inside(root, Path(final_parent))
        return handle, final_parent
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def _windows_delete_by_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    class DispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]
    info = DispositionInfo(True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    if not kernel32.SetFileInformationByHandle(handle, 4, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_replace_from_handle(root: Path, parent: Path, leaf: str, file_descriptor: int) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    stream_handle = msvcrt.get_osfhandle(file_descriptor)
    file_handle = None
    parent_handle = None
    try:
        parent_handle, final_parent = _windows_parent_handle(root, parent)
        final_temp = Path(_windows_final_path(stream_handle))
        _verify_windows_path_inside(root, final_temp)
        if os.path.normcase(str(final_temp.parent)) != os.path.normcase(final_parent):
            raise WorkspacePathError("Temporary workspace file and directory handles do not match.")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        file_handle = kernel32.CreateFileW(
            str(final_temp), 0x00010000 | 0x80000000, 0x1 | 0x2 | 0x4,
            None, 3, 0x00200000, None,
        )
        if not file_handle or file_handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        if os.path.normcase(_windows_final_path(file_handle)) != os.path.normcase(str(final_temp)):
            raise WorkspacePathError("Temporary workspace file changed while opening its rename handle.")
        encoded_name = leaf.encode("utf-16-le")

        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", wintypes.WCHAR * (len(leaf) + 1)),
            ]

        rename = RenameInfo()
        rename.Flags = 1
        rename.RootDirectory = parent_handle
        rename.FileNameLength = len(encoded_name)
        rename.FileName = leaf
        buffer_size = RenameInfo.FileName.offset + len(encoded_name)
        kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        if not kernel32.SetFileInformationByHandle(file_handle, 3, ctypes.byref(rename), buffer_size):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception:
        try:
            _windows_delete_by_handle(stream_handle)
        except OSError:
            pass
        raise
    finally:
        if parent_handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(parent_handle)
        if file_handle is not None and file_handle != ctypes.c_void_p(-1).value:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(file_handle)


def _verify_windows_path_inside(root: Path, path: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise WorkspacePathError("Workspace file handle resolved outside its authorized root.") from exc


def _verify_windows_handle_inside(root: Path, fd: int) -> None:
    if os.name != "nt":
        return
    import msvcrt
    final_path = Path(_windows_final_path(msvcrt.get_osfhandle(fd)))
    _verify_windows_path_inside(root, final_path)
