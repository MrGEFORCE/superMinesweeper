# -*- coding: utf-8 -*-
"""运行时验证 winmine_bigbrd.exe：尺寸、雷数、边界、点击与洪水展开完整性。

用法：python verify_runtime.py
依赖：pip install pywin32 不是必需；本脚本只用 ctypes。
"""
import ctypes
import subprocess
import sys
import time
import winreg
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXE = HERE / "winmine_bigbrd.exe"

# 补丁版的关键内存地址（与 patch_winmine_bigboard.py 必须保持一致）
ADDR_W, ADDR_H, ADDR_MINES = 0x1005334, 0x1005338, 0x10056A4
BOARD_BASE, STRIDE = 0x1020000, 64
CELL, ORIGIN_X, ORIGIN_Y = 16, 12, 55   # 客户区坐标：第一格中心 (12+8, 55+8)
KEY = r"Software\Microsoft\winmine"    # HKCU

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202


def snapshot():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        vals, i = {}, 0
        while True:
            try:
                n, v, t = winreg.EnumValue(k, i)
            except OSError:
                break
            vals[n] = (v, t)
            i += 1
    return vals


def restore(vals):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE) as k:
        for n, (v, t) in vals.items():
            winreg.SetValueEx(k, n, 0, t, v)


def set_board(w, h, m):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "Height", 0, winreg.REG_DWORD, h)
        winreg.SetValueEx(k, "Width", 0, winreg.REG_DWORD, w)
        winreg.SetValueEx(k, "Mines", 0, winreg.REG_DWORD, m)
        winreg.SetValueEx(k, "Difficulty", 0, winreg.REG_DWORD, 3)


def find_window(pid, timeout=6.0):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            if r.right > 50 and r.bottom > 50:
                found.append((hwnd, r.right, r.bottom))
        return True

    end = time.time() + timeout
    while time.time() < end:
        found.clear()
        user32.EnumWindows(cb, 0)
        if found:
            return max(found, key=lambda x: x[1] * x[2])
        time.sleep(0.15)
    return None


class Proc:
    def __init__(self, pid):
        self.h = kernel32.OpenProcess(0x10 | 0x400, False, pid)
        if not self.h:
            raise RuntimeError("OpenProcess 失败")

    def read(self, addr, size):
        buf = (ctypes.c_ubyte * size)()
        n = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
            raise RuntimeError(f"ReadProcessMemory {addr:#x} 失败")
        return bytes(buf)

    def u32(self, addr):
        return int.from_bytes(self.read(addr, 4), "little")

    def close(self):
        kernel32.CloseHandle(self.h)


def board(p, w, h):
    raw = p.read(BOARD_BASE, STRIDE * (h + 2))
    return [[raw[y * STRIDE + x] for x in range(w + 2)] for y in range(h + 2)]


def click(hwnd, x, y):
    px = ORIGIN_X + (x - 1) * CELL + CELL // 2
    py = ORIGIN_Y + (y - 1) * CELL + CELL // 2
    lp = (py << 16) | px
    user32.SendMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    user32.SendMessageW(hwnd, WM_LBUTTONDOWN, 1, lp)
    user32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def is_mine(v):    return v & 0x80
def is_opened(v):  return v & 0x40
def is_flag(v):    return (v & 0x1F) == 0x0E
def is_blank(v):   return (not is_mine(v)) and (not is_opened(v))


def check_closure(bd, w, h):
    """所有已打开的 0 格，其 8 邻域必须已打开或已插旗，否则说明洪水展开漏格。"""
    missed = []
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            v = bd[y][x]
            if not is_opened(v) or (v & 0x1F) != 0:
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if not (1 <= nx <= w and 1 <= ny <= h) or (dx == 0 and dy == 0):
                        continue
                    nv = bd[ny][nx]
                    if not is_opened(nv) and not is_flag(nv):
                        missed.append((x, y, nx, ny, nv))
    return missed


def run_case(w, h, m):
    set_board(w, h, m)
    proc = subprocess.Popen([str(EXE)], cwd=str(HERE))
    try:
        win = find_window(proc.pid)
        if not win:
            print(f"[{w}x{h}] 失败：未找到窗口，进程状态 {proc.poll()}")
            return False
        hwnd, cw, ch = win
        p = Proc(proc.pid)
        got_w, got_h = p.u32(ADDR_W), p.u32(ADDR_H)
        got_m = p.u32(ADDR_MINES)
        bd = board(p, got_w, got_h)
        border_ok = all(
            bd[y][x] == 0x10
            for x, y in [(0, 0), (got_w + 1, 0), (0, got_h + 1), (got_w + 1, got_h + 1)]
        )
        mines = sum(1 for y in range(1, got_h + 1) for x in range(1, got_w + 1) if is_mine(bd[y][x]))
        size_ok = (got_w == w and got_h == h and border_ok and got_m == m and mines == m)
        print(f"[{w}x{h}] 窗口 {cw}x{ch} 全局 {got_w}x{got_h} 配置雷数 {got_m} 实际雷数 {mines} 边界 {border_ok}")

        # 首点：从中心附近找非雷未开格（本版本雷在开局即布置，无首点安全保证）
        first = None
        for radius in range(0, max(w, h)):
            for (x, y) in [(w // 2 + radius, h // 2), (w // 2 - radius, h // 2),
                           (w // 2, h // 2 + radius), (w // 2, h // 2 - radius)]:
                if 1 <= x <= w and 1 <= y <= h and is_blank(bd[y][x]):
                    first = (x, y)
                    break
            if first:
                break
        if not first:
            print(f"[{w}x{h}] 找不到非雷格")
            p.close()
            return False
        click(hwnd, *first)
        time.sleep(0.05)
        bd = board(p, got_w, got_h)

        total_safe = got_w * got_h - mines
        clicks = 1
        while clicks < 300:
            opened = sum(1 for y in range(1, got_h + 1) for x in range(1, got_w + 1) if is_opened(bd[y][x]))
            if opened >= total_safe:
                break
            nxt = None
            for y in range(1, got_h + 1):
                for x in range(1, got_w + 1):
                    if is_blank(bd[y][x]):
                        nxt = (x, y)
                        break
                if nxt:
                    break
            if not nxt:
                break
            click(hwnd, *nxt)
            clicks += 1
            bd = board(p, got_w, got_h)

        opened = sum(1 for y in range(1, got_h + 1) for x in range(1, got_w + 1) if is_opened(bd[y][x]))
        miss = check_closure(bd, got_w, got_h)
        alive = proc.poll() is None
        ok = size_ok and alive and opened > 1 and not miss
        print(f"[{w}x{h}] 首点{first} 点击数 {clicks} 已开 {opened}/{total_safe} 洪水漏格 {len(miss)} 存活 {alive}")
        if miss[:3]:
            print("   示例漏格:", miss[:3])
        p.close()
        return ok
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass


def main():
    snap = snapshot()
    # (宽, 高, 雷数)。注意扫雷自身把雷数下限钳到 10，写 1 会变成 10。
    cases = [(9, 9, 10), (30, 24, 120), (62, 62, 10), (60, 60, 999), (62, 62, 999)]
    try:
        results = [run_case(*c) for c in cases]
        print("结果:", list(zip([f"{w}x{h}/{m}" for w, h, m in cases], results)))
        sys.exit(0 if all(results) else 1)
    finally:
        restore(snap)
        print("注册表已恢复")


if __name__ == "__main__":
    main()
