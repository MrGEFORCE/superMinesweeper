#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 XP 扫雷 winmine.exe (5.1.2600.0) 的棋盘上限从 30x24 改为 62x62。
做法：步长 32 -> 64；棋盘数组搬到新 PE 节 0x1020000；
     洪水展开队列扩到 127 格并搬到新节；
     放开自定义对话框/注册表加载的宽高钳制。
所有补丁均为等长替换，不需要代码洞。
"""
import hashlib
import struct
from pathlib import Path

import capstone
import pefile

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "winmine.exe"
DST = ROOT / "winmine_bigbrd.exe"
EXPECT_MD5 = "16A4FD569A3EB5CEBEB3DA99EF1D17E1"

IB = 0x01000000
NEW_SEC_RVA = 0x20000          # 绝对地址 0x1020000
BOARD_BASE = IB + NEW_SEC_RVA  # 0x1020000
QUEUE_X = BOARD_BASE + 0x1000  # 0x1021000
QUEUE_Y = BOARD_BASE + 0x1200  # 0x1021200
SEC_VSIZE = 0x1400
STRIDE_OLD = 0x20
STRIDE_NEW = 0x40
QUEUE_CAP_OLD = 0x64
QUEUE_CAP_NEW = 0x7F

# 22 处行索引左移 5 位（32 字节步长），全部位于棋盘相关函数
SHL_SITES = [
    0x100213A, 0x1002666, 0x1002EB7, 0x1002EFA, 0x1002F1A, 0x1002F58,
    0x1003016, 0x100313A, 0x1003173, 0x10031A8, 0x10032E2, 0x1003321,
    0x10033B1, 0x10033E7, 0x100351F, 0x1003570, 0x10035CB, 0x1003600,
    0x10036E3, 0x10036F0, 0x1003774, 0x1003898,
]
# 10 处跨行加/减步长；0x10031D7 是 sub esp,0x20，不能动
ROWSTEP_SITES = [
    0x100271D, 0x1002F2A, 0x1002F2D, 0x1002F74, 0x1002FCF,
    0x100315B, 0x1003309, 0x1003343, 0x1003563, 0x1003642,
]
# 29 处棋盘基址常量
BOARD_REF_SITES = [
    0x100213D, 0x1002669, 0x10026C4, 0x1002EBA, 0x1002EDB, 0x1002EFD,
    0x1002F04, 0x1002F1D, 0x1002F23, 0x1002F5D, 0x1002F93, 0x100301B,
    0x1003047, 0x100313F, 0x1003176, 0x10031AB, 0x10032E5, 0x1003324,
    0x10033B4, 0x10033EA, 0x1003522, 0x100354B, 0x1003573, 0x10035CE,
    0x1003606, 0x10036E6, 0x10036F3, 0x1003778, 0x100389B,
]
QUEUE_REF_SITES = [0x100305D, 0x1003064, 0x10030AD, 0x10030A6]
QUEUE_WRAP_SITES = [0x100306C, 0x1003103]
CLAMP_SITES = {0x10015ED: 0x18, 0x10015FC: 0x1E, 0x1002BEB: 0x19, 0x1002BF9: 0x1E}
RESET_SITE = 0x1002ED5

OLD_VALS = {0x1005340: 0x1005340, 0x1005341: 0x1005341, 0x1005360: 0x1005360}
NEW_VALS = {0x1005340: BOARD_BASE, 0x1005341: BOARD_BASE + 1, 0x1005360: BOARD_BASE + STRIDE_NEW}


def md5_of(path):
    return hashlib.md5(path.read_bytes()).hexdigest().upper()


def main():
    if not SRC.exists():
        raise SystemExit(f"缺少 {SRC}")
    actual = md5_of(SRC)
    if actual != EXPECT_MD5:
        raise SystemExit(f"版本不匹配: MD5={actual}, 期望 {EXPECT_MD5}")

    pe = pefile.PE(str(SRC))
    text = next(s for s in pe.sections if s.Name.decode().strip("\x00") == ".text")
    text_va = IB + text.VirtualAddress
    text_raw = text.PointerToRawData
    raw = bytearray(SRC.read_bytes())

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = True
    insns = {i.address: i for i in md.disasm(text.get_data(), text_va) if i.id != 0}

    def off(va):
        if not (text_va <= va < text_va + text.Misc_VirtualSize):
            raise ValueError(f"{va:#x} 不在 .text")
        return text_raw + (va - text_va)

    def patch_byte_tail(va, old, new, why):
        ins = insns[va]
        ib = ins.bytes
        if ib[-1] != old:
            raise ValueError(f"{va:#x} {why}: 末字节 {ib[-1]:#x} != {old:#x} ({ins.mnemonic} {ins.op_str})")
        raw[off(va) + len(ib) - 1] = new

    def patch_imm32(va, old, new, why, imm_at):
        o = off(va) + imm_at
        if raw[o:o + 4] != struct.pack("<I", old):
            raise ValueError(f"{va:#x} {why}: 立即数不匹配")
        raw[o:o + 4] = struct.pack("<I", new)

    def patch_operand32(va, old, new, why):
        ib = insns[va].bytes
        pat = struct.pack("<I", old)
        pos = ib.find(pat)
        if pos < 0 or ib.find(pat, pos + 1) >= 0:
            raise ValueError(f"{va:#x} {why}: 找不到唯一 {old:#x}")
        o = off(va) + pos
        raw[o:o + 4] = struct.pack("<I", new)

    # 1. 步长 32 -> 64
    for va in SHL_SITES:
        patch_byte_tail(va, 5, 6, "步长")

    # 2. 跨行加减 0x20 -> 0x40
    for va in ROWSTEP_SITES:
        patch_byte_tail(va, STRIDE_OLD, STRIDE_NEW, "行步长")

    # 3. 棋盘基址整体搬家
    for va in BOARD_REF_SITES:
        ib = insns[va].bytes
        for old, new in NEW_VALS.items():
            if struct.pack("<I", old) in ib:
                patch_operand32(va, old, new, "棋盘基址")
                break
        else:
            raise ValueError(f"{va:#x} 找不到棋盘基址")

    # 4. 清零范围 0x360 -> 0x1000
    if insns[RESET_SITE].bytes[:1] != b"\xb8":
        raise ValueError("RESET 指令不是 mov eax,imm32")
    patch_imm32(RESET_SITE, 0x360, 0x1000, "清零范围", 1)

    # 5. 放开钳制：自定义对话框高度24/宽度30，注册表加载高度25/宽度30 -> 62
    for va, old in CLAMP_SITES.items():
        patch_byte_tail(va, old, 0x3E, "钳制")

    # 6. 洪水队列搬到新节
    for va in QUEUE_REF_SITES:
        ib = insns[va].bytes
        if struct.pack("<I", 0x10051A0) in ib:
            patch_operand32(va, 0x10051A0, QUEUE_X, "队列X")
        elif struct.pack("<I", 0x10057C0) in ib:
            patch_operand32(va, 0x10057C0, QUEUE_Y, "队列Y")
        else:
            raise ValueError(f"{va:#x} 找不到队列基址")

    # 7. 队列容量 100 -> 127
    for va in QUEUE_WRAP_SITES:
        patch_byte_tail(va, QUEUE_CAP_OLD, QUEUE_CAP_NEW, "队列容量")

    # 8. 新增 .bigbrd 节
    e_lfanew = struct.unpack_from("<I", raw, 0x3C)[0]
    nsec_off = e_lfanew + 6
    nsec = struct.unpack_from("<H", raw, nsec_off)[0]
    size_opt = struct.unpack_from("<H", raw, e_lfanew + 20)[0]
    opt_off = e_lfanew + 24
    if struct.unpack_from("<H", raw, opt_off)[0] != 0x10B:
        raise ValueError("不是 PE32")
    sec_table = opt_off + size_opt
    new_hdr = sec_table + nsec * 40
    size_headers = struct.unpack_from("<I", raw, opt_off + 60)[0]
    if new_hdr + 40 > size_headers:
        raise ValueError("PE 头空间不足，无法添加节")

    last = pe.sections[-1]
    align = lambda v, a: (v + a - 1) & ~(a - 1)
    sec_align = pe.OPTIONAL_HEADER.SectionAlignment
    file_align = pe.OPTIONAL_HEADER.FileAlignment
    new_va = align(last.VirtualAddress + last.Misc_VirtualSize, sec_align)
    if new_va != NEW_SEC_RVA:
        raise ValueError(f"新节 RVA 预期 {NEW_SEC_RVA:#x}，实际 {new_va:#x}")
    new_raw = align(len(raw), file_align)
    if new_raw != len(raw):
        raw += b"\x00" * (new_raw - len(raw))
    sec_rsize = align(SEC_VSIZE, file_align)
    raw += b"\x00" * sec_rsize

    hdr = bytearray(40)
    hdr[0:7] = b".bigbrd"
    struct.pack_into("<I", hdr, 8, SEC_VSIZE)
    struct.pack_into("<I", hdr, 12, NEW_SEC_RVA)
    struct.pack_into("<I", hdr, 16, sec_rsize)
    struct.pack_into("<I", hdr, 20, new_raw)
    struct.pack_into("<I", hdr, 36, 0xC0000040)  # initialized + read/write
    raw[new_hdr:new_hdr + 40] = hdr
    struct.pack_into("<H", raw, nsec_off, nsec + 1)
    struct.pack_into("<I", raw, opt_off + 56, align(new_va + SEC_VSIZE, sec_align))
    init_size = struct.unpack_from("<I", raw, opt_off + 8)[0]
    struct.pack_into("<I", raw, opt_off + 8, init_size + sec_rsize)

    DST.write_bytes(raw)

    print(f"已生成 {DST}")
    print(f"  棋盘基址 : {BOARD_BASE:#x}  (步长 {STRIDE_NEW})")
    print(f"  队列 X/Y : {QUEUE_X:#x} / {QUEUE_Y:#x}  (容量 {QUEUE_CAP_NEW})")
    print(f"  最大棋盘 : 62 x 62，雷数仍为 999")
    print(f"  补丁数量 : shl={len(SHL_SITES)}, rowstep={len(ROWSTEP_SITES)}, "
          f"base={len(BOARD_REF_SITES)}, queue={len(QUEUE_REF_SITES) + len(QUEUE_WRAP_SITES)}, "
          f"clamp={len(CLAMP_SITES)}")


if __name__ == "__main__":
    main()
