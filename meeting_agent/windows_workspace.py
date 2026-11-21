"""Read access to private meeting files for Codex's unelevated Windows token."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import stat
import subprocess

from .agent_materials import AgentError
from .runtime import child_options


def _logon_sid():
    """Read TokenLogonSid from this process. This SID identifies one Windows sign-in."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    ptr = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ptr]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ptr, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(wintypes.HANDLE(-1), 8, ctypes.byref(token)):  # TOKEN_QUERY
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 28, None, 0, ctypes.byref(size))  # TokenLogonSid
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buf = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 28, buf, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        class TokenLogonGroups(ctypes.Structure):
            _fields_ = [("count", wintypes.DWORD), ("sid", ptr), ("attributes", wintypes.DWORD)]
        group = TokenLogonGroups.from_buffer(buf)
        text = wintypes.LPWSTR()
        if group.count != 1 or not security.ConvertSidToStringSidW(group.sid, ctypes.byref(text)):
            raise OSError("无法读取 Windows 登录会话 SID")
        try:
            sid = text.value
        finally:
            kernel.LocalFree(text)
        if not re.fullmatch(r"S-1-5-5-\d+-\d+", sid):
            raise OSError("Windows 登录会话 SID 格式异常")
        return sid
    finally:
        kernel.CloseHandle(token)


class WorkspaceReadAccess:
    def __init__(self, directory):
        self.root = directory
        self.sid = None
        self.granted = set()

    def refresh(self):
        # A restricted token can lose access to owner-only directories created by
        # an elevated desktop. Codex retains the current sign-in SID in that token.
        # No other user, meeting, or sandbox principal receives an ACL here.
        try:
            if not self.sid:
                self.sid = _logon_sid()
            program = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
            pending = [self.root]
            while pending:
                path = pending.pop()
                details = path.lstat()
                if details.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                directory = stat.S_ISDIR(details.st_mode)
                if not directory and (not stat.S_ISREG(details.st_mode) or details.st_nlink > 1):
                    continue
                if path not in self.granted:
                    perm = "(RX)" if directory else "(R)"
                    proc = subprocess.run([program, str(path), "/grant", f"*{self.sid}:{perm}", "/L", "/Q"],
                                          capture_output=True, timeout=15, **child_options())
                    if proc.returncode:
                        raise OSError(f"icacls {proc.returncode} ({path.name})")
                    self.granted.add(path)
                if directory:
                    if path == self.root:
                        files = path / "attachments"
                        if files.exists():
                            pending.append(files)
                    else:
                        pending.extend(path.iterdir())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentError(f"无法为当前 Windows 登录会话设置会议附件只读权限：{exc}") from exc
