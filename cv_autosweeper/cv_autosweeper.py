#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基于 CV 的自动扫雷（Windows XP 扫雷 winmine.exe / 补丁大棋盘版）。

与最外层参考脚本 python308_autominesweeper.py 的差别：
  - 截图：用 PrintWindow(PW_CLIENTONLY|PW_RENDERFULLCONTENT) 抓客户区，
    窗口被遮挡、不在前台都能截；不依赖屏幕抓取，也不需要移动真实鼠标。
  - 点击：SendMessage(WM_MOUSEMOVE/LBUTTONDOWN/UP、RBUTTONDOWN/UP)，
    和 memory_autosweeper 完全一致，后台操作。
  - 重开：SendMessage(WM_COMMAND, 510)（菜单“开局(F2)”），不依赖真实键盘。
  - 识别：16×16 格子的颜色直方图规则分类，覆盖 0-8、旗、问号、未开、踩雷；
    顶部三位 LED 数字单独识别剩余雷数。
  - 算法：单格规则 + 子集规则 + 全局剩余雷数约束 + 前沿连通分量枚举求概率，
    卡住时按概率挑最安全的格子，而不是随机点。

用法：
    python cv_autosweeper.py                         # 附着已打开的扫雷
    python cv_autosweeper.py --launch ..\\winmine_patch\\winmine.exe --games 3
    python cv_autosweeper.py --dry-run               # 只识别并打印棋盘
    python cv_autosweeper.py --no-guess              # 没有确定解就停
    python cv_autosweeper.py --pid 1234 --delay 0.01

依赖：numpy（pip install numpy）。
"""

import argparse
import base64
import ctypes
import math
import os
import subprocess
import sys
import time
from collections import defaultdict
from ctypes import wintypes

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.exit("需要 numpy：pip install numpy")

# ---------------------------------------------------------------- Win32 基础
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_COMMAND = 0x0111
IDM_NEW_GAME = 510          # 菜单“游戏 -> 开局(N) F2”

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
SMTO_ABORTIFHUNG = 0x2

CELL = 16                       # 格子像素
ORIGIN_X, ORIGIN_Y = 12, 55     # 客户区中第一格的左上角
CLIENT_W_EXTRA = 20             # client_w = w*16 + 20
CLIENT_H_EXTRA = 63             # client_h = h*16 + 63


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

E = wintypes.RECT
TARGET_TITLES = {"扫雷", "minesweeper", "挖地雷"}


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def enum_windows():
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = E()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        out.append((hwnd, pid.value, r.right, r.bottom, window_class(hwnd),
                    window_title(hwnd), bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_minesweeper(pid=None):
    """返回 (hwnd, pid, client_w, client_h)；找不到返回 None。"""
    windows = enum_windows()
    if pid is not None:
        cand = [w for w in windows if w[1] == pid]
    else:
        cand = [w for w in windows
                if w[4] in ("扫雷",) or w[5].lower() in TARGET_TITLES]
    if not cand:
        return None
    good = [w for w in cand if w[2] > 60 and w[3] > 90]
    if not good:
        raise RuntimeError("找到扫雷窗口，但客户区为 0×0：窗口被最小化，请先还原窗口"
                           "（最小化时 PrintWindow 无法截到内容）")
    w = max(good, key=lambda c: c[2] * c[3])
    return w[0], w[1], w[2], w[3]


def wait_window(pid, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            w = find_minesweeper(pid)
        except RuntimeError:
            w = None
        if w:
            return w
        time.sleep(0.2)
    return None


def capture(hwnd):
    """后台截取客户区，返回 RGB ndarray (h, w, 3)。"""
    rect = E()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    cw, ch = rect.right, rect.bottom
    if cw <= 0 or ch <= 0:
        raise RuntimeError("客户区尺寸为 0×0：窗口最小化后无法后台截图，请还原窗口")
    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, cw, ch)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        user32.PrintWindow(hwnd, mdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = cw
        bi.bmiHeader.biHeight = -ch              # 负数：top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        buf = (ctypes.c_ubyte * (cw * ch * 4))()
        got = gdi32.GetDIBits(mdc, bmp, 0, ch, buf, ctypes.byref(bi), 0)
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
    if not got:
        raise RuntimeError(f"PrintWindow/GetDIBits 失败：客户区 {cw}x{ch}")
    img = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(ch, cw, 4)[:, :, :3]
    return img[:, :, ::-1].copy()                # BGR -> RGB


def send(hwnd, msg, wp=0, lp=0, timeout=2000):
    res = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(hwnd, msg, wp, lp, SMTO_ABORTIFHUNG, timeout,
                                    ctypes.byref(res))
    return bool(ok)


def click_cell(hwnd, x, y, right=False, timeout=2000):
    """后台点击第 x,y 格（0 基）。"""
    px = ORIGIN_X + x * CELL + CELL // 2
    py = ORIGIN_Y + y * CELL + CELL // 2
    lp = (py << 16) | px
    if not send(hwnd, WM_MOUSEMOVE, 0, lp, timeout):
        raise RuntimeError("窗口无响应（WM_MOUSEMOVE 超时）")
    if right:
        send(hwnd, WM_RBUTTONDOWN, 2, lp, timeout)
        send(hwnd, WM_RBUTTONUP, 0, lp, timeout)
    else:
        send(hwnd, WM_LBUTTONDOWN, 1, lp, timeout)
        send(hwnd, WM_LBUTTONUP, 0, lp, timeout)


def new_game(hwnd):
    return send(hwnd, WM_COMMAND, IDM_NEW_GAME, 0, 3000)


# ------------------------------------------------------------ 棋盘识别
COLORS = {
    "white": (255, 255, 255),
    "g192": (192, 192, 192),
    "g128": (128, 128, 128),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "maroon": (128, 0, 0),
    "blue": (0, 0, 255),
    "navy": (0, 0, 128),
    "green": (0, 128, 0),
    "teal": (0, 128, 128),
}

UNKNOWN = -1
FLAG = -2
QUESTION = -3
HIT = -4


def grid_counts(img, w, h):
    region = img[ORIGIN_Y:ORIGIN_Y + h * CELL, ORIGIN_X:ORIGIN_X + w * CELL]
    if region.shape[0] != h * CELL or region.shape[1] != w * CELL:
        raise RuntimeError(f"截图尺寸不足：{img.shape}，期望至少 "
                           f"{ORIGIN_Y + h * CELL}x{ORIGIN_X + w * CELL}")
    region = region.reshape(h, CELL, w, CELL, 3)
    out = {}
    for name, rgb in COLORS.items():
        out[name] = (region == np.array(rgb, dtype=np.uint8)).all(axis=-1).sum(axis=(1, 3))
    return out


def classify(white, g192, g128, black, red, maroon, blue, navy, green, teal):
    """按颜色计数分类。返回 0-8 / FLAG / QUESTION / UNKNOWN / HIT。"""
    if white >= 20:                       # 未开、旗、问号都有白色高光
        if red >= 8 and black >= 8:
            return FLAG
        if black >= 8:
            return QUESTION
        return UNKNOWN
    if red >= 60 and black >= 30:         # 踩中的雷：大片红底
        return HIT
    if blue >= 15:
        return 1
    if green >= 20:
        return 2
    if red >= 20:
        return 3
    if navy >= 20:
        return 4
    if maroon >= 25:
        return 5
    if teal >= 25:
        return 6
    if black >= 20:
        return 7
    if g128 >= 70:
        return 8
    return 0


def read_board(img, w, h):
    cnt = grid_counts(img, w, h)
    cells = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            cells[y][x] = classify(
                int(cnt["white"][y][x]), int(cnt["g192"][y][x]), int(cnt["g128"][y][x]),
                int(cnt["black"][y][x]), int(cnt["red"][y][x]), int(cnt["maroon"][y][x]),
                int(cnt["blue"][y][x]), int(cnt["navy"][y][x]), int(cnt["green"][y][x]),
                int(cnt["teal"][y][x]))
    return cells


# ------------------------------------------------------- 顶部 LED 数字识别
LED_X, LED_Y, LED_W, LED_H = 17, 14, 13, 26

# 0-9 的 LED 红色笔画掩码（13×26 位图，np.packbits + base64）
DIGIT_B64 = {
    0: "AAAAAAB/xf032cHODnBzg5wcwGQBAAEATAZwc4OcHODnBzfZf0f8AAAAAA==",
    1: "AAAAAAAAAAEAGAHADgBwA4AcAGABAAAAQAYAcAOAHADgBwAYAEAAAAAAAA==",
    2: "AAAAAAB/wf0H2AHADgBwA4AcAGH9H/F/DABwA4AcAOAHADfBfwf8AAAAAA==",
    3: "AAAAAAB/wf0H2AHADgBwA4AcAGH9H/B/QAYAcAOAHADgBwfYf0f8AAAAAA==",
    4: "AAAAAAAABAEwGcHODnBzg5wcwGX9H/B/QAYAcAOAHADgBwAYAEAAAAAAAA==",
    5: "AAAAAAB/xfw3wcAOAHADgBwAwAX8H/B/QAYAcAOAHADgBwfYf0f8AAAAAA==",
    6: "AAAAAAB/xfw3wcAOAHADgBwAwAX8H/F/TAZwc4OcHODnBzfZf0f8AAAAAA==",
    7: "AAAAAAB/wf0H2AHADgBwA4AcAGABAAAAQAYAcAOAHADgBwAYAEAAAAAAAA==",
    8: "AAAAAAB/xf032cHODnBzg5wcwGX9H/F/TAZwc4OcHODnBzfZf0f8AAAAAA==",
    9: "AAAAAAB/xf032cHODnBzg5wcwGX9H/B/QAYAcAOAHADgBwfYf0f8AAAAAA==",
}
_DIGITS = None


def digit_templates():
    global _DIGITS
    if _DIGITS is None:
        _DIGITS = {}
        for d, s in DIGIT_B64.items():
            bits = np.unpackbits(np.frombuffer(base64.b64decode(s), dtype=np.uint8))
            _DIGITS[d] = bits[:LED_W * LED_H].reshape(LED_H, LED_W).astype(bool)
    return _DIGITS


def read_remaining(img):
    """识别顶部“剩余雷数”三位 LED；失败返回 None。"""
    digits = digit_templates()
    vals = []
    for i in range(3):
        x0 = LED_X + i * LED_W
        sub = img[LED_Y:LED_Y + LED_H, x0:x0 + LED_W]
        if sub.shape[:2] != (LED_H, LED_W):
            return None
        mask = (sub[:, :, 0] > 200) & (sub[:, :, 1] < 70) & (sub[:, :, 2] < 70)
        best, best_diff = None, 1 << 30
        for d, t in digits.items():
            diff = int(np.logical_xor(mask, t).sum())
            if diff < best_diff:
                best, best_diff = d, diff
        if best_diff > 14:
            return None
        vals.append(best)
    return vals[0] * 100 + vals[1] * 10 + vals[2]


# ------------------------------------------------------------- 求解器
def neighbors(x, y, w, h):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                yield nx, ny


def solve_deduce(cells, w, h, remaining):
    """确定解推导。返回 (safe, mine, cons)；矛盾返回 None。"""
    safe, mine = set(), set()
    cons = []
    for _ in range(40):
        unknown = set()
        for y in range(h):
            for x in range(w):
                v = cells[y][x]
                if (v == UNKNOWN or v == QUESTION) and (x, y) not in safe and (x, y) not in mine:
                    unknown.add((x, y))

        cons = []
        for y in range(h):
            for x in range(w):
                n = cells[y][x]
                if n < 0:
                    continue
                U = set()
                flags = known_mine = 0
                for nx, ny in neighbors(x, y, w, h):
                    v = cells[ny][nx]
                    if v == FLAG:
                        flags += 1
                    elif v == UNKNOWN or v == QUESTION:
                        if (nx, ny) in mine:
                            known_mine += 1
                        elif (nx, ny) not in safe:
                            U.add((nx, ny))
                r = n - flags - known_mine
                if r < 0 or r > len(U):
                    return None
                cons.append((x, y, U, r))

        changed = False
        if remaining is not None:
            # remaining 是“还没插旗的雷数”；mine 是本轮新推导出、尚未真正右键的雷
            eff = remaining - len(mine)
            if eff < 0:
                return None
            if eff == 0 and unknown:
                safe |= unknown
                changed = True
            elif eff == len(unknown) and unknown:
                mine |= unknown
                changed = True

        for _x, _y, U, r in cons:
            if r == 0 and U:
                add = U - safe - mine
                if add:
                    safe |= add
                    changed = True
            elif r == len(U) and U:
                add = U - mine - safe
                if add:
                    mine |= add
                    changed = True

        if not changed:
            # 子集规则：U1 ⊆ U2 时，差集的雷数是 r2-r1
            for i in range(len(cons)):
                xi, yi, Ui, ri = cons[i]
                if not Ui:
                    continue
                for j in range(i + 1, len(cons)):
                    xj, yj, Uj, rj = cons[j]
                    if not Uj or abs(xi - xj) > 2 or abs(yi - yj) > 2:
                        continue
                    if Ui <= Uj:
                        diff, rd = Uj - Ui, rj - ri
                    elif Uj <= Ui:
                        diff, rd = Ui - Uj, ri - rj
                    else:
                        continue
                    if not diff:
                        continue
                    if rd == 0:
                        add = diff - safe - mine
                        if add:
                            safe |= add
                            changed = True
                    elif rd == len(diff):
                        add = diff - mine - safe
                        if add:
                            mine |= add
                            changed = True
            if not changed:
                break

    if safe & mine:
        return None
    return safe, mine, cons


def _components(cons):
    cons = [c for c in cons if c[2]]        # 空约束（U 为空）不参与分组
    cell_cons = defaultdict(list)
    for i, (_x, _y, U, _r) in enumerate(cons):
        for c in U:
            cell_cons[c].append(i)
    parent = list(range(len(cons)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for _c, ids in cell_cons.items():
        for k in ids[1:]:
            union(ids[0], k)

    groups = defaultdict(list)
    for i in range(len(cons)):
        groups[find(i)].append(i)
    out = []
    for _root, idxs in groups.items():
        cells = set()
        for i in idxs:
            cells |= cons[i][2]
        out.append((sorted(cells), [cons[i] for i in idxs]))
    return out


MAX_COMP = 32           # 单个前沿分量最多枚举的格子数，超过则退化为粗估
ENUM_BUDGET = 1500000   # DFS 节点预算，防止大分量枚举耗时失控


def enum_component(cells_list, comp_cons, budget=None):
    """穷举一个小连通分量。返回 (dist, per_cell) 或 None。

    dist[k]        = 该分量中恰好 k 颗雷的合法解数
    per_cell[c][k] = 其中格子 c 为雷的解数
    """
    if budget is None:
        budget = ENUM_BUDGET
    n = len(cells_list)
    order = sorted(range(n), key=lambda i: -sum(
        1 for (_x, _y, U, _r) in comp_cons if cells_list[i] in U))
    pos = {cells_list[i]: k for k, i in enumerate(order)}
    ids = [cells_list[i] for i in order]
    con = [([pos[c] for c in U], r) for (_x, _y, U, r) in comp_cons]

    memb = [[] for _ in range(n)]
    for ci, (lst, _r) in enumerate(con):
        for i in lst:
            memb[i].append(ci)

    used = [0] * len(con)
    left_cells = [len(lst) for lst, _r in con]
    assign = [0] * n
    dist = defaultdict(int)
    per_cell = {cell: defaultdict(int) for cell in cells_list}
    nodes = [0]

    def dfs(i):
        nodes[0] += 1
        if nodes[0] > budget:
            raise TimeoutError
        if i == n:
            k = sum(assign)
            dist[k] += 1
            for j in range(n):
                if assign[j]:
                    per_cell[ids[j]][k] += 1
            return
        for val in (0, 1):
            assign[i] = val
            ok = True
            for ci in memb[i]:
                if val:
                    used[ci] += 1
                left_cells[ci] -= 1
                left = con[ci][1] - used[ci]
                if left < 0 or left > left_cells[ci]:
                    ok = False
            if ok:
                dfs(i + 1)
            for ci in memb[i]:
                if val:
                    used[ci] -= 1
                left_cells[ci] += 1

    try:
        dfs(0)
    except TimeoutError:
        return None
    if not dist:
        return None
    return dist, per_cell


def _convolve(a, b):
    out = defaultdict(int)
    for ka, va in a.items():
        for kb, vb in b.items():
            out[ka + kb] += va * vb
    return out


def estimate(cells, w, h, remaining, cons, unknown):
    """无确定解时估算每格雷概率，返回 (probs, p_nonfrontier)。

    前沿按连通分量枚举，再用 DP 把各分量的雷数分布卷积起来，
    最后用全局剩余雷数对“分量外格子”做组合加权。这样各分量的
    概率不是各自独立算的，而是满足全局雷数的一致性。
    """
    frontier = set()
    for _x, _y, U, _r in cons:
        frontier |= U
    nf = unknown - frontier
    n_nf = len(nf)
    comps = _components(cons)

    def comb_weight(k):
        if remaining is None:
            return 1
        need = remaining - k
        if need < 0 or need > n_nf:
            return 0
        return math.comb(n_nf, need)

    data = []
    ok = True
    for cells_list, comp_cons in comps:
        if len(cells_list) > MAX_COMP:
            ok = False
            break
        res = enum_component(cells_list, comp_cons)
        if res is None:
            ok = False
            break
        data.append((cells_list, res[0], res[1]))

    if not ok:
        # 分量太大：用“约束密度均值”粗估
        s = defaultdict(float)
        c = defaultdict(int)
        for _x, _y, U, r in cons:
            if not U:
                continue
            p = r / len(U)
            for cell in U:
                s[cell] += p
                c[cell] += 1
        probs = {cell: s[cell] / c[cell] for cell in s}
        p_nf = None
        if remaining is not None and n_nf:
            p_nf = min(1.0, max(0.0, (remaining - sum(probs.values())) / n_nf))
        return probs, p_nf

    # 全局分布
    g_all = {0: 1}
    for _cells, dist, _per in data:
        g_all = _convolve(g_all, dist)
    z = 0
    for k, cnt in g_all.items():
        z += cnt * comb_weight(k)
    if z <= 0:
        z = 1
    exp_frontier = sum(k * cnt * comb_weight(k) for k, cnt in g_all.items()) / z

    probs = {}
    for idx, (cells_list, dist, per_cell) in enumerate(data):
        g_other = {0: 1}
        for j, (_c2, d2, _p2) in enumerate(data):
            if j != idx:
                g_other = _convolve(g_other, d2)
        weights = {}
        for k in dist:
            wk = 0
            for t, cnt in g_other.items():
                wk += cnt * comb_weight(k + t)
            weights[k] = wk
        for cell in cells_list:
            num = sum(cnt * weights[k] for k, cnt in per_cell[cell].items())
            probs[cell] = num / z

    p_nf = None
    if remaining is not None and n_nf:
        p_nf = min(1.0, max(0.0, (remaining - exp_frontier) / n_nf))
    return probs, p_nf


def choose_guess(unknown, probs, p_nf):
    best = None
    for cell in unknown:
        if cell in probs:
            p, kind = probs[cell], 0
        elif p_nf is not None:
            p, kind = p_nf, 1
        else:
            continue
        key = (round(p, 6), kind, cell[1], cell[0])
        if best is None or key < best[0]:
            best = (key, cell, p)
    if best is None:
        cell = min(unknown, key=lambda c: (c[1], c[0]))
        return cell, None
    return best[1], best[2]


def state_summary(cells, w, h):
    unknown, flags, opened, hit = set(), 0, 0, False
    for y in range(h):
        for x in range(w):
            v = cells[y][x]
            if v == UNKNOWN or v == QUESTION:
                unknown.add((x, y))
            elif v == FLAG:
                flags += 1
            elif v == HIT:
                hit = True
            elif v >= 0:
                opened += 1
    return unknown, flags, opened, hit


def print_board(cells, w, h, p_nf=None):
    for y in range(h):
        line = []
        for x in range(w):
            v = cells[y][x]
            if v == UNKNOWN:
                line.append(" ")
            elif v == FLAG:
                line.append("f")
            elif v == QUESTION:
                line.append("?")
            elif v == HIT:
                line.append("X")
            else:
                line.append("." if v == 0 else str(v))
        print("".join(line))


# ---------------------------------------------------------------- 主流程
def play_one(hwnd, w, h, delay, allow_guess, verbose):
    clicks = 0
    last_state = None
    clean_marks = set()

    while True:
        img = capture(hwnd)
        cells = read_board(img, w, h)
        remaining = read_remaining(img)
        unknown, flags, opened, hit = state_summary(cells, w, h)

        if hit:
            print(f"[踩雷] 已开 {opened}，旗 {flags}，剩余显示={remaining}")
            return False
        if not unknown:
            if remaining is not None and remaining != 0:
                print(f"[异常] 没有未知格但剩余雷数显示 {remaining}，疑似识别错误，停止")
                return None
            print(f"[胜利] 已开 {opened}，旗 {flags}，用时点击 {clicks} 次")
            return True

        if verbose and remaining is not None:
            print(f"[状态] 已开 {opened}，旗 {flags}，剩余雷 {remaining}")

        # XP 扫雷保证第一次点击一定不是雷（布雷发生在首点之后），
        # 直接从中心开局，能开出最大的空白区域，比按 12% 概率猜角落更划算。
        if opened == 0 and flags == 0:
            cx, cy = (w - 1) // 2, (h - 1) // 2
            print(f"[开局] 首点中心 ({cx + 1},{cy + 1})，首点必安全")
            click_cell(hwnd, cx, cy)
            clicks += 1
            sleep_a_bit(delay)
            continue

        ded = solve_deduce(cells, w, h, remaining)
        if ded is None:
            print("[错误] 棋盘读数矛盾（可能有格子识别错误）")
            return False
        safe, mine, cons = ded
        safe -= mine

        if safe or mine:
            for (x, y) in sorted(mine):
                if cells[y][x] == UNKNOWN:
                    click_cell(hwnd, x, y, right=True)
                    clicks += 1
                    sleep_a_bit(delay)
            for (x, y) in sorted(safe):
                if cells[y][x] == QUESTION and (x, y) not in clean_marks:
                    click_cell(hwnd, x, y, right=True)
                    click_cell(hwnd, x, y, right=True)
                    clean_marks.add((x, y))
                if cells[y][x] != FLAG:
                    click_cell(hwnd, x, y)
                    clicks += 1
                    sleep_a_bit(delay)
            continue

        # 无确定解
        probs, p_nf = estimate(cells, w, h, remaining, cons, unknown)
        guess, prob = choose_guess(unknown, probs, p_nf)
        if not allow_guess:
            print(f"[停止] 卡住，未知 {len(unknown)} 格，最小雷概率 "
                  f"{'?' if prob is None else f'{prob:.1%}'}")
            return None
        pstr = "?" if prob is None else f"{prob:.1%}"
        print(f"[猜测] 点 ({guess[0] + 1},{guess[1] + 1}) 雷概率 {pstr}，"
              f"未知 {len(unknown)}，剩余雷 {remaining}")
        if cells[guess[1]][guess[0]] == QUESTION:
            click_cell(hwnd, guess[0], guess[1], right=True)
            click_cell(hwnd, guess[0], guess[1], right=True)
        click_cell(hwnd, guess[0], guess[1])
        clicks += 1
        sleep_a_bit(delay)
        time.sleep(max(delay, 0.02))

        # 保护：状态长时间不变则报错退出
        state = (opened, flags, len(unknown))
        if state == last_state:
            if verbose:
                print("[提示] 状态未变化，重截一次")
            time.sleep(0.2)
        last_state = state


def sleep_a_bit(delay):
    if delay > 0:
        time.sleep(delay)


def geometry(client_w, client_h):
    w = (client_w - CLIENT_W_EXTRA) // CELL
    h = (client_h - CLIENT_H_EXTRA) // CELL
    if w <= 0 or h <= 0:
        raise RuntimeError(f"客户区 {client_w}x{client_h} 不像扫雷棋盘")
    return w, h


def main():
    ap = argparse.ArgumentParser(description="基于 CV 的自动扫雷（后台运行）")
    ap.add_argument("--launch", help="启动指定扫雷 exe 后再运行")
    ap.add_argument("--pid", type=int, help="指定进程 pid")
    ap.add_argument("--games", type=int, default=1, help="连打局数（默认 1）")
    ap.add_argument("--delay", type=float, default=0.01, help="每次点击后的等待秒数")
    ap.add_argument("--no-guess", action="store_true", help="无确定解时停止，不猜")
    ap.add_argument("--dry-run", action="store_true", help="只识别并打印棋盘，不点击")
    ap.add_argument("--verbose", action="store_true", help="输出每步状态")
    args = ap.parse_args()

    game = None
    if args.launch:
        exe = os.path.abspath(args.launch)
        if not os.path.isfile(exe):
            sys.exit(f"找不到 exe：{exe}")
        game = subprocess.Popen([exe], cwd=os.path.dirname(exe))
        win = wait_window(game.pid)
        if not win:
            sys.exit("启动后 15 秒内未找到扫雷窗口")
    else:
        win = find_minesweeper(args.pid)
        if not win:
            sys.exit("未找到扫雷窗口：请先打开扫雷，或用 --launch 指定 exe")

    hwnd, pid, cw, ch = win
    w, h = geometry(cw, ch)
    print(f"[窗口] hwnd={hwnd} pid={pid} 客户区 {cw}x{ch} -> {w}x{h} 格")

    try:
        if args.dry_run:
            img = capture(hwnd)
            cells = read_board(img, w, h)
            remain = read_remaining(img)
            print(f"[识别] 剩余雷数={remain}")
            print_board(cells, w, h)
            unknown, flags, opened, hit = state_summary(cells, w, h)
            print(f"[统计] 已开 {opened}，旗 {flags}，未知 {len(unknown)}，踩雷 {hit}")
            return

        wins = losses = stuck = 0
        t0 = time.time()
        for i in range(args.games):
            if i:
                if not new_game(hwnd):
                    print("[警告] 重开命令超时，停止")
                    break
                time.sleep(0.5)
                r = E()
                user32.GetClientRect(hwnd, ctypes.byref(r))
                nw, nh = geometry(r.right, r.bottom)
                if (nw, nh) != (w, h):
                    w, h = nw, nh
                    print(f"[注意] 棋盘尺寸变化 -> {w}x{h}")
            result = play_one(hwnd, w, h, args.delay, not args.no_guess, args.verbose)
            if result is True:
                wins += 1
            elif result is False:
                losses += 1
            else:
                stuck += 1
            print(f"[第 {i + 1} 局] {'胜利' if result else ('踩雷' if result is False else '卡住')}")
        total = wins + losses + stuck
        dur = time.time() - t0
        if total:
            print(f"[汇总] {total} 局：胜 {wins}，负 {losses}，卡住 {stuck}，"
                  f"胜率 {wins / total:.1%}，耗时 {dur:.1f}s")
    finally:
        if game:
            game.terminate()
            try:
                game.wait(timeout=3)
            except Exception:
                pass


if __name__ == "__main__":
    main()
