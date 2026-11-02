import numpy
import sys
import win32api
import win32con
import win32gui
import time
from PIL import ImageGrab

rgba_0 = [(225, (192, 192, 192)), (31, (128, 128, 128))]
rgba_hongqi = [(54, (255, 255, 255)), (17, (255, 0, 0)), (109, (192, 192, 192)), (54, (128, 128, 128)), (22, (0, 0, 0))]
rgba_un = [(54, (255, 255, 255)), (148, (192, 192, 192)), (54, (128, 128, 128))]
rgba_1 = [(185, (192, 192, 192)), (31, (128, 128, 128)), (40, (0, 0, 255))]
rgba_2 = [(160, (192, 192, 192)), (31, (128, 128, 128)), (65, (0, 128, 0))]
rgba_3 = [(62, (255, 0, 0)), (163, (192, 192, 192)), (31, (128, 128, 128))]
rgba_4 = [(169, (192, 192, 192)), (31, (128, 128, 128)), (56, (0, 0, 128))]
rgba_5 = [(70, (128, 0, 0)), (155, (192, 192, 192)), (31, (128, 128, 128))]
rgba_6 = [(153, (192, 192, 192)), (31, (128, 128, 128)), (72, (0, 128, 128))]
rgba_8 = [(149, (192, 192, 192)), (107, (128, 128, 128))]
rgba_boom = [(4, (255, 255, 255)), (144, (192, 192, 192)), (31, (128, 128, 128)), (77, (0, 0, 0))]
rgba_boom_red = [(4, (255, 255, 255)), (144, (255, 0, 0)), (31, (128, 128, 128)), (77, (0, 0, 0))]
block_height = 16
block_width = 16
all_flag = 0
finished = 0
win = 0
wasted = 0
wasted_first = 0
wasted_last = 0
count = 0


def right_click(x_pos, y_pos):
    win32api.SetCursorPos([left_c + x_pos * block_width, top_c + y_pos * block_height])
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
    show_map()
    global all_flag
    all_flag = 1


def left_click(x_pos, y_pos):
    win32api.SetCursorPos([left_c + x_pos * block_width, top_c + y_pos * block_height])
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    show_map()
    global all_flag
    all_flag = 1


def find_window():
    global left_org, top_org, right_org, bottom_org
    title_name = "扫雷"
    hwnd = win32gui.FindWindow(None, title_name)
    if hwnd:
        print("找到窗口")
        left_org, top_org, right_org, bottom_org = win32gui.GetWindowRect(hwnd)
        return 1
    else:
        print("未找到窗口")
        return 0


def scr_cap(left_, top_, right_, bottom_):
    rect = (left_, top_, right_, bottom_)
    img = ImageGrab.grab().crop(rect)
    return img


# 数字0-8 周围雷数
# un 未被打开
# hongqi 红旗
# boom 普通雷
# boom_red 踩中的雷
def show_map():
    flag = 0
    img = scr_cap(left, top, right, bottom)
    global finished, wasted, wasted_first, wasted_last, count
    for y in range(blocks_y):
        for x in range(blocks_x):
            if visited[y][x] == 1:
                continue
            this_image = img.crop((x * block_width, y * block_height, (x + 1) * block_width, (y + 1) * block_height))
            if this_image.getcolors() == rgba_un:
                my_map[y][x] = -9
                visited[y][x] = 0
            elif this_image.getcolors() == rgba_1:
                my_map[y][x] = 1
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_2:
                my_map[y][x] = 2
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_3:
                my_map[y][x] = 3
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_4:
                my_map[y][x] = 4
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_5:
                my_map[y][x] = 5
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_6:
                my_map[y][x] = 6
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_8:
                my_map[y][x] = 8
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_0:
                my_map[y][x] = 0
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_hongqi:
                my_map[y][x] = -1
                visited[y][x] = 1
            elif this_image.getcolors() == rgba_boom or this_image.getcolors() == rgba_boom_red:
                wasted += 1
                win_and_wasted.append(0)
                print("W  A  S  T  E  D")
                finished = 1
                if count < 6:
                    wasted_first += 1
                    print("WASTED类型：开幕雷击")
                else:
                    wasted_last += 1
                    print("WASTED类型：闭幕雷击")
                return 0
            else:
                print("无法识别图像")
                print("坐标：")
                print(y, x)
                print("颜色：")
                print(this_image.getcolors())
                print("未能识别个别图像，直接跳过本轮")
                finished = 1
                return 0
            flag = 1
    if flag == 0:  # no block was operated, game has finished.
        global win
        win += 1
        win_and_wasted.append(1)
        print("win")
        finished = 1


#  1 stand for corner, 2 stand for side, 3 stand for center.
def type_confirm(x_pos, y_pos):
    if (x_pos == 0 and y_pos == 0) or (x_pos == blocks_x - 1 and y_pos == 0) or (
            x_pos == 0 and y_pos == blocks_y - 1) or (x_pos == blocks_x - 1 and y_pos == blocks_y - 1):
        return 1
    elif x_pos == 0 or x_pos == blocks_x - 1 or y_pos == 0 or y_pos == blocks_y - 1:
        return 2
    else:
        return 3


def handle_corner(x_pos, y_pos):
    if my_map[y_pos][x_pos] == -9 or my_map[y_pos][x_pos] == -1:
        return 0
    count_un = 0
    count_boom = 0
    if x_pos == 0 and y_pos == 0:  # 左上角
        if my_map[0][1] == -9:
            count_un += 1
        if my_map[1][0] == -9:
            count_un += 1
        if my_map[1][1] == -9:
            count_un += 1

        if my_map[0][1] == -1:
            count_boom += 1
        if my_map[1][0] == -1:
            count_boom += 1
        if my_map[1][1] == -1:
            count_boom += 1

        if my_map[0][0] == count_un + count_boom:  # 未知是雷
            if my_map[0][1] == -9:
                right_click(1, 0)
            if my_map[1][0] == -9:
                right_click(0, 1)
            if my_map[1][1] == -9:
                right_click(1, 1)

        if my_map[0][0] == count_boom:  # 未知是数
            if my_map[0][1] == -9:
                left_click(1, 0)
            if my_map[1][0] == -9:
                left_click(0, 1)
            if my_map[1][1] == -9:
                left_click(1, 1)

    elif x_pos == blocks_x - 1 and y_pos == 0:  # 右上角
        if my_map[0][x_pos - 1] == -9:
            count_un += 1
        if my_map[1][x_pos - 1] == -9:
            count_un += 1
        if my_map[1][x_pos] == -9:
            count_un += 1

        if my_map[0][x_pos - 1] == -1:
            count_boom += 1
        if my_map[1][x_pos - 1] == -1:
            count_boom += 1
        if my_map[1][x_pos] == -1:
            count_boom += 1

        if my_map[0][x_pos] == count_un + count_boom:  # 未知是雷
            if my_map[0][x_pos - 1] == -9:
                right_click(x_pos - 1, 0)
            if my_map[1][x_pos - 1] == -9:
                right_click(x_pos - 1, 1)
            if my_map[1][x_pos] == -9:
                right_click(x_pos, 1)

        if my_map[0][x_pos] == count_boom:  # 未知是数
            if my_map[0][x_pos - 1] == -9:
                left_click(x_pos - 1, 0)
            if my_map[1][x_pos - 1] == -9:
                left_click(x_pos - 1, 1)
            if my_map[1][x_pos] == -9:
                left_click(x_pos, 1)

    elif x_pos == 0 and y_pos == blocks_y - 1:  # 左下角
        if my_map[y_pos - 1][0] == -9:
            count_un += 1
        if my_map[y_pos - 1][1] == -9:
            count_un += 1
        if my_map[y_pos][1] == -9:
            count_un += 1

        if my_map[y_pos - 1][0] == -1:
            count_boom += 1
        if my_map[y_pos - 1][1] == -1:
            count_boom += 1
        if my_map[y_pos][1] == -1:
            count_boom += 1

        if my_map[y_pos][0] == count_un + count_boom:  # 未知是雷
            if my_map[y_pos - 1][0] == -9:
                right_click(0, y_pos - 1)
            if my_map[y_pos - 1][1] == -9:
                right_click(1, y_pos - 1)
            if my_map[y_pos][1] == -9:
                right_click(1, y_pos)

        if my_map[y_pos][0] == count_boom:  # 未知是数
            if my_map[y_pos - 1][0] == -9:
                left_click(0, y_pos - 1)
            if my_map[y_pos - 1][1] == -9:
                left_click(1, y_pos - 1)
            if my_map[y_pos][1] == -9:
                left_click(1, y_pos)

    else:  # 右下角
        if my_map[y_pos - 1][x_pos - 1] == -9:
            count_un += 1
        if my_map[y_pos][x_pos - 1] == -9:
            count_un += 1
        if my_map[y_pos - 1][x_pos] == -9:
            count_un += 1

        if my_map[y_pos - 1][x_pos - 1] == -1:
            count_boom += 1
        if my_map[y_pos][x_pos - 1] == -1:
            count_boom += 1
        if my_map[y_pos - 1][x_pos] == -1:
            count_boom += 1

        if my_map[y_pos][x_pos] == count_un + count_boom:  # 未知是雷
            if my_map[y_pos - 1][x_pos - 1] == -9:
                right_click(x_pos - 1, y_pos - 1)
            if my_map[y_pos][x_pos - 1] == -9:
                right_click(x_pos - 1, y_pos)
            if my_map[y_pos - 1][x_pos] == -9:
                right_click(x_pos, y_pos - 1)

        if my_map[y_pos][x_pos] == count_boom:  # 未知是数
            if my_map[y_pos - 1][x_pos - 1] == -9:
                left_click(x_pos - 1, y_pos - 1)
            if my_map[y_pos][x_pos - 1] == -9:
                left_click(x_pos - 1, y_pos)
            if my_map[y_pos - 1][x_pos] == -9:
                left_click(x_pos, y_pos - 1)


def handle_side(x_pos, y_pos):
    if my_map[y_pos][x_pos] == -9 or my_map[y_pos][x_pos] == -1:
        return 0
    count_un = 0
    count_boom = 0
    if x_pos == 0:  # 左边
        for i in range(2):
            for j in range(3):
                if i == 0 and j == 1:
                    continue
                if my_map[y_pos + j - 1][i] == -9:
                    count_un += 1
                if my_map[y_pos + j - 1][i] == -1:
                    count_boom += 1
        if my_map[y_pos][x_pos] == count_un + count_boom:
            for i in range(2):
                for j in range(3):
                    if i == 0 and j == 1:
                        continue
                    if my_map[y_pos + j - 1][i] == -9:
                        right_click(i, y_pos + j - 1)
        if my_map[y_pos][x_pos] == count_boom:
            for i in range(2):
                for j in range(3):
                    if i == 0 and j == 1:
                        continue
                    if my_map[y_pos + j - 1][i] == -9:
                        left_click(i, y_pos + j - 1)

    elif y_pos == 0:  # 上边
        for i in range(3):
            for j in range(2):
                if i == 1 and j == 0:
                    continue
                if my_map[j][x_pos + i - 1] == -9:
                    count_un += 1
                if my_map[j][x_pos + i - 1] == -1:
                    count_boom += 1
        if my_map[y_pos][x_pos] == count_un + count_boom:
            for i in range(3):
                for j in range(2):
                    if i == 1 and j == 0:
                        continue
                    if my_map[j][x_pos + i - 1] == -9:
                        right_click(x_pos + i - 1, j)
        if my_map[y_pos][x_pos] == count_boom:
            for i in range(3):
                for j in range(2):
                    if i == 1 and j == 0:
                        continue
                    if my_map[j][x_pos + i - 1] == -9:
                        left_click(x_pos + i - 1, j)

    elif y_pos == blocks_y - 1:  # 下边
        for i in range(3):
            for j in range(2):
                if i == 1 and j == 0:
                    continue
                if my_map[y_pos - j][x_pos + i - 1] == -9:
                    count_un += 1
                if my_map[y_pos - j][x_pos + i - 1] == -1:
                    count_boom += 1
        if my_map[y_pos][x_pos] == count_un + count_boom:
            for i in range(3):
                for j in range(2):
                    if i == 1 and j == 0:
                        continue
                    if my_map[y_pos - j][x_pos + i - 1] == -9:
                        right_click(x_pos + i - 1, y_pos - j)
        if my_map[y_pos][x_pos] == count_boom:
            for i in range(3):
                for j in range(2):
                    if i == 1 and j == 0:
                        continue
                    if my_map[y_pos - j][x_pos + i - 1] == -9:
                        left_click(x_pos + i - 1, y_pos - j)

    else:  # 右边
        for i in range(2):
            for j in range(3):
                if i == 1 and j == 1:
                    continue
                if my_map[y_pos - j + 1][x_pos + i - 1] == -9:
                    count_un += 1
                if my_map[y_pos - j + 1][x_pos + i - 1] == -1:
                    count_boom += 1
        if my_map[y_pos][x_pos] == count_un + count_boom:
            for i in range(2):
                for j in range(3):
                    if i == 1 and j == 1:
                        continue
                    if my_map[y_pos - j + 1][x_pos + i - 1] == -9:
                        right_click(x_pos + i - 1, y_pos - j + 1)
        if my_map[y_pos][x_pos] == count_boom:
            for i in range(2):
                for j in range(3):
                    if i == 1 and j == 1:
                        continue
                    if my_map[y_pos - j + 1][x_pos + i - 1] == -9:
                        left_click(x_pos + i - 1, y_pos - j + 1)


def handle_center(x_pos, y_pos):
    if my_map[y_pos][x_pos] == -9 or my_map[y_pos][x_pos] == -1:
        return 0
    count_un = 0
    count_boom = 0
    for i in range(3):
        for j in range(3):
            if i == 1 and j == 1:
                continue
            if my_map[y_pos + j - 1][x_pos + i - 1] == -9:
                count_un += 1
            if my_map[y_pos + j - 1][x_pos + i - 1] == -1:
                count_boom += 1
    if my_map[y_pos][x_pos] == count_un + count_boom:
        for i in range(3):
            for j in range(3):
                if i == 1 and j == 1:
                    continue
                if my_map[y_pos + j - 1][x_pos + i - 1] == -9:
                    right_click(x_pos + i - 1, y_pos + j - 1)
    if my_map[y_pos][x_pos] == count_boom:
        for i in range(3):
            for j in range(3):
                if i == 1 and j == 1:
                    continue
                if my_map[y_pos + j - 1][x_pos + i - 1] == -9:
                    left_click(x_pos + i - 1, y_pos + j - 1)


def rand_click():
    random_x = numpy.random.randint(0, blocks_x - 1)
    random_y = numpy.random.randint(0, blocks_y - 1)
    count_rnd = 0
    while 1:
        count_rnd += 1
        if visited[random_y][random_x] == 1:
            random_x = numpy.random.randint(0, blocks_x - 1)
            random_y = numpy.random.randint(0, blocks_y - 1)
        else:
            break
        if count_rnd > 1000:
            for y in range(blocks_y):
                for x in range(blocks_x):
                    if visited[y][x] == 1:
                        continue
                    else:
                        left_click(x, y)
                        return 0
    left_click(random_x, random_y)


def play_once():
    global finished, all_flag, count
    show_map()
    if finished == 1:
        return 0
    count = 0
    rand_click()  # 开图
    while 1:
        count += 1
        if finished == 1:
            print("本次总共循环{}次".format(count))
            return 0
        show_map()
        all_flag = 0
        for x_it in range(blocks_x):
            for y_it in range(blocks_y):
                block_type = type_confirm(x_it, y_it)
                if block_type == 1:
                    handle_corner(x_it, y_it)
                elif block_type == 2:
                    handle_side(x_it, y_it)
                else:
                    handle_center(x_it, y_it)
        if finished == 1:
            print("本次总共循环{}次".format(count))
            return 0
        if all_flag == 0:
            rand_click()


def sum_data():
    global times, win, wasted, wasted_first, wasted_last, total_time
    print("\n总共运行了{}次".format(times))
    print("其中成功了{}次".format(win))
    print("    白给了{}次".format(wasted))
    print("        其中开幕白给了{}次".format(wasted_first))
    print("            闭幕白给了{}次".format(wasted_last))
    print("总成功率是{:.2f}%".format((win / times)*100))
    if times - wasted_first == 0:
        print("除去开幕雷击的成功率是：0.00%")
    else:
        print("除去开幕雷击的成功率是：{:.2f}%".format((win / (times - wasted_first))*100))
    total_t = 0
    for i in range(times):
        total_t += total_time[i] * win_and_wasted[i]
    if win == 0:
        print("成功情况的平均耗时是：完   全   失   败")
    else:
        print("成功情况的平均耗时是：{:.4f}秒".format(total_t / win))


if __name__ == '__main__':
    print("注意事项：")
    print("①扫雷窗口不可以被任何东西挡住，包括通知，否测会出现无法识别的图像")
    print("②系统焦点需要始终保持在扫雷窗口上，否则会出现点击无效的情况")
    print("③自动扫雷过程中不能乱动鼠标，否则可能造成错误区块甚至其他页面的点击")
    print("④扫雷起始状态必须是待进行状态，不能是玩了一半或是玩好了（成功或踩雷）状态")

    times = int(input('你想让电脑自己玩几次：'))
    if times == 0:
        sys.exit(0)
    if find_window() == 0:
        sys.exit(0)

    left = left_org + 15
    top = top_org + 101
    right = right_org - 11
    bottom = bottom_org - 11
    left_c = left + 8
    top_c = top + 8

    scr_cap(left, top, right, bottom)
    blocks_x = int((right - left) / block_width)
    blocks_y = int((bottom - top) / block_height)

    my_map = []
    visited = []
    for y_i in range(blocks_y):
        my_map.append([])
        for x_i in range(blocks_x):
            my_map[y_i].append(0)
    for y_i in range(blocks_y):
        visited.append([])
        for x_i in range(blocks_x):
            visited[y_i].append(0)

    win_and_wasted = []
    total_time = []
    for t in range(times):
        print("第{}次：".format(t + 1))
        finished = 0
        for y_i in range(blocks_y):
            for x_i in range(blocks_x):
                visited[y_i][x_i] = 0
        time.sleep(1.)
        win32api.keybd_event(113, 0, 0, 0)
        time.sleep(1.)
        time_1 = time.time()
        play_once()
        time_2 = time.time()
        total_time.append(time_2 - time_1)
        print("本次总共耗时{:.4f}秒\n".format(time_2 - time_1))
    sum_data()
