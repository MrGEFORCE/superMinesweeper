#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于内存的自动扫雷（XP 扫雷 winmine.exe / 补丁版 winmine_bigbrd.exe）。

原理来自本项目反汇编结论：
  - 宽 0x1005334，高 0x1005338，雷总数 0x10056A4
  - 原始版棋盘基址 0x1005340，行步长 32
  - 补丁版棋盘基址 0x1020000，行步长 64
  - 格子字节：0x0F 未开非雷，0x8F 雷，0x40|n 已开，0x10 边界，
             0x0E 旗，0x0D 问号
  - 雷位在开局时就写入内存，因此可以在点击前直接知道所有雷的位置

策略：只点击非雷格，直到所有非雷格被翻开即胜利。不会踩雷，也不需要图像识别。

用法：
    python memory_autosweeper.py                     # 自动附着已打开的扫雷
    python memory_autosweeper.py --launch ..\\winmine_patch\\winmine_bigbrd.exe
    python memory_autosweeper.py --dry-run           # 只读内存并打印棋盘，不点击
    python memory_autosweeper.py --pid 1234          # 指定进程

依赖：无，仅 Python 3 标准库 ctypes。
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

# ---- 反汇编得到的内存布局 -------------------------------------------------
ADDR_W = 0x1005334
ADDR_H = 0x1005338
ADDR_MINES = 0x10056A4

# (棋盘基址, 行步长, 标签)
BOARD_CANDIDATES = [
    (0x1020000, 64, "bigbrd"),     # 方案 B 补丁版
    (0x1005340, 32, "original"),   # 原版
]

CELL_SIZE = 16                 # 格子像素尺寸
ORIGIN_X, ORIGIN_Y = 12, 55    # 客户区第一格左上角
TARGET_NAMES = {"winmine.exe", "winmine_bigbrd.exe"}
TARGET_TITLES = {"扫雷", "minesweeper", "挖地雷"}

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM


# ---- 进程与窗口 -----------------------------------------------------------
class Proc:
    def __init__(self, pid):
        self.pid = pid
        access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION
        self.h = kernel32.OpenProcess(access, False, pid)
        if not self.h:
            raise RuntimeError(f"OpenProcess 失败（pid={pid}），请以相同权限运行")

    def read(self, addr, size):
        buf = (ctypes.c_ubyte * size)()
        n = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
        if not ok:
            raise RuntimeError(f"ReadProcessMemory 失败：{addr:#x} size={size}")
        return bytes(buf)

    def u32(self, addr):
        return int.from_bytes(self.read(addr, 4), "little")

    def close(self):
        if self.h:
            kernel32.CloseHandle(self.h)
            self.h = None


_image_cache = {}


def process_image_name(pid):
    """通过 QueryFullProcessImageNameW 取进程文件名，失败返回空串。"""
    if pid in _image_cache:
        return _image_cache[pid]
    name = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value).lower()
        kernel32.CloseHandle(h)
    _image_cache[pid] = name
    return name


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def find_window(pid=None):
    """枚举顶层可见窗口，返回 (hwnd, pid, client_w, client_h)。"""
    candidates = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if pid is not None and p.value != pid:
            return True
        title = window_title(hwnd)
        image = process_image_name(p.value)
        if pid is None and image not in TARGET_NAMES and title.lower() not in TARGET_TITLES:
            return True
        r = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        if r.right > 50 and r.bottom > 50:
            candidates.append((hwnd, p.value, r.right, r.bottom))
        return True

    user32.EnumWindows(cb, 0)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[2] * c[3])


def wait_window(pid, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        w = find_window(pid)
        if w:
            return w
        time.sleep(0.2)
    return None


# ---- 棋盘读取与识别 -------------------------------------------------------
class Board:
    def __init__(self, base, stride, width, height, mines, tag):
        self.base = base
        self.stride = stride
        self.w = width
        self.h = height
        self.mines = mines
        self.tag = tag
        self.raw = b""

    def load(self, proc):
        self.raw = proc.read(self.base, self.stride * (self.h + 2))

    def cell(self, x, y):
        return self.raw[y * self.stride + x]

    @staticmethod
    def is_mine(v):
        return bool(v & 0x80)

    @staticmethod
    def is_opened(v):
        return bool(v & 0x40)

    @staticmethod
    def mark(v):
        return v & 0x1F

    def iter_cells(self):
        for y in range(1, self.h + 1):
            for x in range(1, self.w + 1):
                yield x, y, self.cell(x, y)

    def counts(self):
        opened = 0
        safe = 0
        flags = 0
        for x, y, v in self.iter_cells():
            if not self.is_mine(v):
                safe += 1
                if self.is_opened(v):
                    opened += 1
            if self.mark(v) == 0x0E:
                flags += 1
        return opened, safe, flags


def validate_board(raw, stride, w, h):
    """校验边界和内部字节是否符合扫雷编码。"""
    def cell(x, y):
        return raw[y * stride + x]

    if len(raw) < stride * (h + 2):
        return False
    for x in range(w + 2):
        if cell(x, 0) != 0x10 or cell(x, h + 1) != 0x10:
            return False
    for y in range(h + 1):
        if cell(0, y) != 0x10 or cell(w + 1, y) != 0x10:
            return False
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            v = cell(x, y)
            if v & 0x20:          # 已知编码都不使用 0x20 位
                return False
            low = v & 0x1F
            if not (low <= 0x08 or low in (0x0D, 0x0E, 0x0F)):
                return False
    return True


def detect_board(proc):
    """按反汇编结论尝试两种布局，返回 Board。"""
    w = proc.u32(ADDR_W)
    h = proc.u32(ADDR_H)
    mines = proc.u32(ADDR_MINES)
    if not (1 <= w <= 62 and 1 <= h <= 62 and 0 <= mines <= 999):
        raise RuntimeError(f"宽高/雷数异常：{w}x{h}, mines={mines}")

    best = None
    for base, stride, tag in BOARD_CANDIDATES:
        try:
            raw = proc.read(base, stride * (h + 2))
        except RuntimeError:
            continue
        if not validate_board(raw, stride, w, h):
            continue
        mine_bits = sum(1 for y in range(1, h + 1) for x in range(1, w + 1) if raw[y * stride + x] & 0x80)
        # 雷位数量与配置雷数一致时优先
        score = (2 if mine_bits == mines else 0) + (1 if tag == "bigbrd" else 0)
        if best is None or score > best[0]:
            best = (score, base, stride, tag, mine_bits)

    if best is None:
        raise RuntimeError("无法识别棋盘布局：边界校验失败（请确认游戏未暂停在菜单/对话框）")
    _, base, stride, tag, mine_bits = best
    b = Board(base, stride, w, h, mines, tag)
    b.load(proc)
    print(f"[识别] {tag} 布局：{w}x{h}，配置雷数 {mines}，内存雷位 {mine_bits}，"
          f"基址 {base:#x}，步长 {stride}")
    return b


# ---- 点击 -----------------------------------------------------------------
def screen_point(x, y):
    px = ORIGIN_X + (x - 1) * CELL_SIZE + CELL_SIZE // 2
    py = ORIGIN_Y + (y - 1) * CELL_SIZE + CELL_SIZE // 2
    return px, py


def send_click(hwnd, x, y, right=False):
    px, py = screen_point(x, y)
    lp = (py << 16) | px
    user32.SendMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    if right:
        user32.SendMessageW(hwnd, WM_RBUTTONDOWN, 2, lp)
        user32.SendMessageW(hwnd, WM_RBUTTONUP, 0, lp)
    else:
        user32.SendMessageW(hwnd, WM_LBUTTONDOWN, 1, lp)
        user32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def clear_mark(proc, board, hwnd, x, y):
    """把旗子/问号清回未标记，避免左键点不动。"""
    for _ in range(3):
        v = proc.read(board.base + y * board.stride + x, 1)[0]
        if Board.mark(v) not in (0x0D, 0x0E):
            return
        send_click(hwnd, x, y, right=True)
        time.sleep(0.02)


# ---- 打印 -----------------------------------------------------------------
def print_board(board):
    """按游戏实际的棋盘行列输出：每行正好 w 个格子，不带边界列。"""
    for y in range(1, board.h + 1):
        line = []
        for x in range(1, board.w + 1):
            v = board.cell(x, y)
            low = Board.mark(v)
            if Board.is_mine(v):
                line.append("*")
            elif Board.is_opened(v):
                line.append("." if low == 0 else str(low))
            elif low == 0x0E:
                line.append("f")
            elif low == 0x0D:
                line.append("q")
            else:
                line.append(" ")
        print("".join(line))


# ---- 主流程 ---------------------------------------------------------------
def solve(proc, board, hwnd, delay, max_clicks):
    opened, safe, _ = board.counts()
    print(f"[开始] 已开 {opened}/{safe} 个非雷格，剩余 {safe - opened}")
    clicks = 0
    failures = 0
    started = time.time()

    while True:
        board.load(proc)
        opened, safe, _ = board.counts()
        if opened >= safe:
            print(f"[胜利] 所有 {safe} 个非雷格已翻开，用时 {time.time() - started:.1f}s，点击 {clicks} 次")
            return True

        target = None
        for x, y, v in board.iter_cells():
            if not Board.is_mine(v) and not Board.is_opened(v):
                target = (x, y, v)
                break
        if target is None:
            print("[结束] 没有可点击的非雷格，但仍有未开格？")
            return False

        x, y, v = target
        if Board.mark(v) in (0x0D, 0x0E):
            clear_mark(proc, board, hwnd, x, y)
        send_click(hwnd, x, y)
        clicks += 1
        if delay:
            time.sleep(delay)

        after = proc.read(board.base + y * board.stride + x, 1)[0]
        if not Board.is_opened(after) and Board.mark(after) == Board.mark(v):
            # 偶发情况下窗口处理消息会有短暂延迟，先等待再复读一次
            time.sleep(0.05)
            after = proc.read(board.base + y * board.stride + x, 1)[0]
        if not Board.is_opened(after) and Board.mark(after) == Board.mark(v):
            failures += 1
            if failures >= 8:
                px, py = screen_point(x, y)
                raise RuntimeError(
                    f"连续 8 次点击无变化 @ ({x},{y}) 原值=0x{v:02X} 现值=0x{after:02X} "
                    f"已开={opened}/{safe} 点击坐标=({px},{py})"
                )
        else:
            failures = 0

        if clicks % 100 == 0:
            opened, safe, _ = board.counts()
            print(f"[进度] 点击 {clicks} 次，已开 {opened}/{safe}")

        if max_clicks and clicks >= max_clicks:
            print(f"[停止] 达到 --max-clicks={max_clicks}，已开 {opened}/{safe}")
            return False


def main():
    ap = argparse.ArgumentParser(description="基于内存的扫雷自动求解器")
    ap.add_argument("--launch", metavar="EXE", help="启动指定扫雷程序后再求解")
    ap.add_argument("--pid", type=int, help="指定目标进程 PID")
    ap.add_argument("--dry-run", action="store_true", help="只读内存并打印棋盘，不点击")
    ap.add_argument("--delay", type=float, default=0.005, help="每次点击后的间隔秒数（默认 0.005）")
    ap.add_argument("--max-clicks", type=int, default=0, help="最多点击次数（0 表示不限）")
    ap.add_argument("--close", action="store_true", help="结束后关闭由 --launch 启动的游戏")
    args = ap.parse_args()

    child = None
    proc = None
    try:
        if args.launch:
            exe = os.path.abspath(args.launch)
            if not os.path.isfile(exe):
                raise SystemExit(f"找不到 {exe}")
            child = subprocess.Popen([exe], cwd=os.path.dirname(exe))
            win = wait_window(child.pid)
            if not win:
                raise SystemExit("启动后未找到游戏窗口")
        else:
            win = find_window(args.pid)
            if not win:
                raise SystemExit("未找到扫雷窗口，请先打开游戏，或用 --launch 指定 exe")

        hwnd, pid, cw, ch = win
        print(f"[窗口] hwnd={hwnd} pid={pid} 客户区 {cw}x{ch}")
        proc = Proc(pid)
        board = detect_board(proc)
        if args.dry_run:
            print_board(board)
            opened, safe, flags = board.counts()
            print(f"[只读] 已开 {opened}/{safe}，旗子 {flags} 个")
            return 0
        ok = solve(proc, board, hwnd, args.delay, args.max_clicks)
        return 0 if ok else 1
    finally:
        if proc:
            proc.close()
        if args.close and child:
            child.terminate()


if __name__ == "__main__":
    sys.exit(main())
