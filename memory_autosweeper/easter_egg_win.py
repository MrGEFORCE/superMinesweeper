#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
彩蛋：把当前正在运行的扫雷直接写成“胜利”状态。

实测真实胜利时的内存特征（9x9 原始版）：
  0x1005160 = 3            笑脸/结束状态：3=胜利，2=踩雷，0=进行中
  0x1005164 = 0            计时器停止标志
  0x1005000 = 0x10         游戏结束标志
  0x10057A4 = 0x10057A0    已开格数 = 应开格数
  0x1005194 = 0            雷数计数器归零
  棋盘：雷 = 0x8E（雷+旗），非雷 = 0x40 | 周围雷数

用法：
    python easter_egg_win.py
    python easter_egg_win.py --launch ..\\winmine_patch\\winmine_bigbrd.exe
    python easter_egg_win.py --pid 1234

注意：只是把状态改成胜利，不会写入最佳成绩；重新开始请按 F2 或点笑脸。
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_autosweeper as m

ADDR_FACE = 0x1005160          # 笑脸/结束状态
ADDR_TIMER_FLAG = 0x1005164    # 计时器停止标志
ADDR_STATE = 0x1005000         # 游戏状态位，0x10 = 结束
ADDR_SAFE_TOTAL = 0x10057A0    # 应开格数
ADDR_OPENED = 0x10057A4        # 已开格数
ADDR_REMAIN_COUNTER = 0x1005194  # 雷数显示计数器

PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]


def open_writer(pid):
    access = (PROCESS_VM_WRITE | PROCESS_VM_OPERATION | m.PROCESS_VM_READ |
              m.PROCESS_QUERY_INFORMATION | m.PROCESS_QUERY_LIMITED_INFORMATION)
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        raise RuntimeError(f"OpenProcess(写) 失败（pid={pid}），请以管理员权限运行")
    return h


def write_bytes(h, addr, data):
    n = ctypes.c_size_t()
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    ok = kernel32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, len(data), ctypes.byref(n))
    if not ok:
        raise RuntimeError(f"WriteProcessMemory 失败：{addr:#x} size={len(data)}")


def write_u32(h, addr, value):
    write_bytes(h, addr, value.to_bytes(4, "little"))


def neighbor_mines(board, x, y):
    n = 0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            if board.cell(x + dx, y + dy) & 0x80:
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="扫雷彩蛋：直接写成胜利状态")
    ap.add_argument("--launch", metavar="EXE", help="启动指定扫雷程序")
    ap.add_argument("--pid", type=int, help="指定目标进程 PID")
    ap.add_argument("--close", action="store_true", help="结束后关闭由 --launch 启动的游戏")
    args = ap.parse_args()

    child = None
    proc = None
    writer = None
    try:
        if args.launch:
            exe = os.path.abspath(args.launch)
            if not os.path.isfile(exe):
                raise SystemExit(f"找不到 {exe}")
            child = subprocess.Popen([exe], cwd=os.path.dirname(exe))
            win = m.wait_window(child.pid)
            if not win:
                raise SystemExit("启动后未找到游戏窗口")
        else:
            win = m.find_window(args.pid)
            if not win:
                raise SystemExit("未找到扫雷窗口，请先打开游戏，或用 --launch 指定 exe")

        hwnd, pid, _, _ = win
        proc = m.Proc(pid)
        board = m.detect_board(proc)
        state = proc.u32(ADDR_STATE)
        if state & 0x10:
            print("[跳过] 游戏已经结束（胜利或踩雷），请先按 F2 / 点笑脸重开")
            return 1

        writer = open_writer(pid)
        safe_total = proc.u32(ADDR_SAFE_TOTAL)

        # 1. 棋盘：所有非雷格写成已开，所有雷写成“雷+旗”
        for y in range(1, board.h + 1):
            row = bytearray()
            for x in range(1, board.w + 1):
                v = board.cell(x, y)
                if v & 0x80:
                    row.append(0x8E)          # 雷 + 旗
                elif v & 0x40:
                    row.append(v)             # 保持已开格原值
                else:
                    row.append(0x40 | neighbor_mines(board, x, y))
            write_bytes(writer, board.base + y * board.stride + 1, bytes(row))

        # 2. 状态：照抄真实胜利时的值
        write_bytes(writer, ADDR_TIMER_FLAG, (0).to_bytes(4, "little"))
        write_bytes(writer, ADDR_REMAIN_COUNTER, (0).to_bytes(4, "little"))
        write_bytes(writer, ADDR_FACE, (3).to_bytes(4, "little"))
        write_bytes(writer, ADDR_OPENED, safe_total.to_bytes(4, "little"))
        write_bytes(writer, ADDR_STATE, (0x10).to_bytes(4, "little"))

        # 3. 重绘：让笑脸和旗帜立刻显示出来
        user32.InvalidateRect(hwnd, None, True)
        user32.UpdateWindow(hwnd)
        time.sleep(0.2)

        # 4. 读回校验
        board.load(proc)
        opened, safe, flags = board.counts()
        print(f"[彩蛋] 已写入胜利状态：{board.w}x{board.h}")
        print(f"[状态] face={proc.u32(ADDR_FACE)} state={proc.u32(ADDR_STATE):#x} "
              f"opened={proc.u32(ADDR_OPENED)}/{proc.u32(ADDR_SAFE_TOTAL)} "
              f"counter={proc.u32(ADDR_REMAIN_COUNTER)} flags={flags}")
        print(f"[棋盘] 已开非雷格 {opened}/{safe}，旗子 {flags} 个")
        return 0
    finally:
        if writer:
            kernel32.CloseHandle(writer)
        if proc:
            proc.close()
        if args.close and child:
            child.terminate()


if __name__ == "__main__":
    sys.exit(main())
