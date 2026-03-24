#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XTC/XTCH 格式预览器（增强版）
支持：XTC (1-bit) 和 XTCH (2-bit) 文件，提供缩放预览、快速跳转、文件名右下角显示、关于菜单、背景色设置、鼠标滚轮缩放/翻页、滚动条、完美居中、批量导出图片序列、全屏模式、单/双页显示
"""

import os
import sys
import logging
import struct
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------- 依赖自动安装 ----------
def install_package(package: str):
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

MISSING_MODULES = []
try:
    from PIL import Image, ImageTk
except ImportError:
    MISSING_MODULES.append("Pillow")
try:
    import numpy as np
except ImportError:
    MISSING_MODULES.append("numpy")

if MISSING_MODULES:
    print("正在自动安装缺失的依赖...")
    for pkg in MISSING_MODULES:
        install_package(pkg)
    print("依赖安装完成，请重新运行程序。")
    sys.exit(0)

# ---------- 日志配置 ----------
def setup_logger(log_dir="log"):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log"
    log_path = Path(log_dir) / log_filename

    logger = logging.getLogger("XTCViewer")
    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger

logger = setup_logger()

# ---------- XTC/XTCH 解析器 ----------
class XTCReader:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.f = open(filepath, 'rb')
        self.pages = []          # 每页的 (offset, size)
        self.page_count = 0
        self.title = ""
        self.author = ""
        self.chapters = []
        self.is_hq = False       # True: XTCH, False: XTC
        self._parse_header()

    def _parse_header(self):
        self.f.seek(0)
        magic = self.f.read(4)
        if magic == b'XTC\0':
            self.is_hq = False
            logger.info("检测到 XTC 格式 (1-bit)")
        elif magic == b'XTCH':
            self.is_hq = True
            logger.info("检测到 XTCH 格式 (2-bit)")
        else:
            raise ValueError(f"未知文件格式: {magic}")

        # 跳过版本、页数等字段，直接获取偏移量
        self.f.read(2)          # version
        self.page_count = struct.unpack('<H', self.f.read(2))[0]
        self.f.read(1)          # read_dir
        has_metadata = self.f.read(1)[0]
        self.f.read(1)          # has_thumbnails
        has_chapters = self.f.read(1)[0]
        self.f.read(4)          # current_page
        metadata_offset = struct.unpack('<Q', self.f.read(8))[0]
        index_offset = struct.unpack('<Q', self.f.read(8))[0]
        data_offset = struct.unpack('<Q', self.f.read(8))[0]
        self.f.read(8)          # thumb_offset
        chapter_offset = struct.unpack('<Q', self.f.read(8))[0]

        logger.debug(f"页数: {self.page_count}, 索引表偏移: {index_offset}")

        if has_metadata:
            self._parse_metadata(metadata_offset)
        self._parse_index(index_offset)
        if has_chapters:
            self._parse_chapters(chapter_offset)

        logger.info(f"文件解析完成: {self.page_count} 页")

    def _parse_metadata(self, offset: int):
        self.f.seek(offset)
        title_bytes = self.f.read(128)
        self.title = title_bytes.split(b'\x00')[0].decode('utf-8', errors='ignore')
        author_bytes = self.f.read(64)
        self.author = author_bytes.split(b'\x00')[0].decode('utf-8', errors='ignore')
        logger.info(f"书名: {self.title}, 作者: {self.author}")

    def _parse_index(self, offset: int):
        """解析索引表，只保存偏移和大小，忽略宽高（从页面数据中读取）"""
        self.f.seek(offset)
        for i in range(self.page_count):
            page_offset = struct.unpack('<Q', self.f.read(8))[0]
            page_size = struct.unpack('<I', self.f.read(4))[0]
            self.f.read(4)          # 跳过宽高（可能为0，我们不用）
            self.pages.append((page_offset, page_size))
            logger.debug(f"页 {i}: 偏移={page_offset}, 大小={page_size}")

    def _parse_chapters(self, offset: int):
        self.f.seek(offset)
        for i in range(self.page_count):
            name_bytes = self.f.read(80)
            name = name_bytes.split(b'\x00')[0].decode('utf-8', errors='ignore')
            start_page = struct.unpack('<H', self.f.read(2))[0]
            end_page = struct.unpack('<H', self.f.read(2))[0]
            self.f.read(12)
            if name:
                self.chapters.append({'name': name, 'start': start_page-1, 'end': end_page-1})
                logger.debug(f"章节: {name} (页 {start_page}-{end_page})")
            else:
                break

    def get_page_image(self, page_index: int, save_debug=False) -> Image.Image:
        """获取指定页的图像（从页面数据头解析实际尺寸）"""
        if page_index < 0 or page_index >= self.page_count:
            raise IndexError(f"页码超出范围: {page_index}")
        offset, size = self.pages[page_index]
        self.f.seek(offset)
        page_data = self.f.read(size)
        logger.debug(f"读取页 {page_index}: 偏移={offset}, 大小={size}")

        try:
            if self.is_hq:
                img = self._decode_xth(page_data)
            else:
                img = self._decode_xtg(page_data)
            logger.debug(f"解码成功，图像尺寸={img.size}")
            if save_debug:
                debug_path = Path.home() / f"debug_page_{page_index}.png"
                img.save(debug_path)
                logger.info(f"保存调试图像: {debug_path}")
            return img
        except Exception as e:
            logger.exception(f"解码页面 {page_index} 失败")
            raise

    def _decode_xtg(self, data: bytes) -> Image.Image:
        """解码 XTG (1-bit) 数据，从头部读取实际尺寸"""
        if len(data) < 22:
            raise ValueError("数据过短，无法读取XTG头")
        header = data[:22]
        if header[:4] != b'XTG\0':
            logger.warning(f"XTG头不匹配: {header[:4]}")
        # 从头部读取实际宽高（小端）
        actual_w = struct.unpack('<H', header[4:6])[0]
        actual_h = struct.unpack('<H', header[6:8])[0]
        logger.debug(f"XTG头尺寸: {actual_w}x{actual_h}")
        if actual_w == 0 or actual_h == 0:
            raise ValueError("XTG头中宽高为0")
        bitmap = data[22:]
        row_bytes = (actual_w + 7) // 8
        expected_size = row_bytes * actual_h
        if len(bitmap) < expected_size:
            raise ValueError(f"位图数据不足: 需要 {expected_size}, 实际 {len(bitmap)}")
        img = Image.new('L', (actual_w, actual_h), 255)
        pixels = img.load()
        for y in range(actual_h):
            for x in range(actual_w):
                byte_idx = y * row_bytes + (x // 8)
                if byte_idx >= len(bitmap):
                    continue
                bit = 7 - (x % 8)
                pixel = (bitmap[byte_idx] >> bit) & 1
                pixels[x, y] = 0 if pixel == 0 else 255
        return img

    def _decode_xth(self, data: bytes) -> Image.Image:
        """解码 XTH (2-bit) 数据，从头部读取实际尺寸"""
        if len(data) < 22:
            raise ValueError("数据过短，无法读取XTH头")
        header = data[:22]
        if header[:4] != b'XTH\0':
            logger.warning(f"XTH头不匹配: {header[:4]}")
        # 从头部读取实际宽高
        actual_w = struct.unpack('<H', header[4:6])[0]
        actual_h = struct.unpack('<H', header[6:8])[0]
        logger.debug(f"XTH头尺寸: {actual_w}x{actual_h}")
        if actual_w == 0 or actual_h == 0:
            raise ValueError("XTH头中宽高为0")
        planes = data[22:]
        col_bytes = (actual_h + 7) // 8
        plane_size = col_bytes * actual_w
        if len(planes) < plane_size * 2:
            raise ValueError(f"位平面数据不足: 需要 {plane_size*2}, 实际 {len(planes)}")
        plane0 = planes[:plane_size]
        plane1 = planes[plane_size:plane_size*2]

        level_to_gray = {0: 255, 1: 85, 2: 170, 3: 0}  # 白, 深灰, 浅灰, 黑
        img = Image.new('L', (actual_w, actual_h), 255)
        pixels = img.load()

        for x in range(actual_w-1, -1, -1):
            col_idx = actual_w - 1 - x
            for y in range(actual_h):
                byte_idx = col_idx * col_bytes + (y // 8)
                if byte_idx >= len(plane0):
                    continue
                bit_pos = 7 - (y % 8)
                b0 = (plane0[byte_idx] >> bit_pos) & 1
                b1 = (plane1[byte_idx] >> bit_pos) & 1
                level = (b1 << 1) | b0
                pixels[x, y] = level_to_gray.get(level, 255)
        return img

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ---------- GUI 应用 ----------
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk

class ProgressDialog:
    """进度对话框，显示导出进度"""
    def __init__(self, parent, title="转换中", total_pages=0):
        self.parent = parent
        self.total = total_pages
        self.cancel_flag = False
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("400x200")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 窗口居中
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

        # 内容框架
        frame = ttk.Frame(self.dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # 文件名标签
        self.file_label = ttk.Label(frame, text="文件名: ")
        self.file_label.pack(anchor=tk.W, pady=5)

        # 位置标签
        self.path_label = ttk.Label(frame, text="文件位置: ")
        self.path_label.pack(anchor=tk.W, pady=5)

        # 进度条
        self.progress = ttk.Progressbar(frame, orient=tk.HORIZONTAL, length=300, mode='determinate')
        self.progress.pack(pady=10)
        self.progress['maximum'] = total_pages

        # 百分比标签
        self.percent_label = ttk.Label(frame, text="0%")
        self.percent_label.pack()

        # 取消按钮
        self.cancel_btn = ttk.Button(frame, text="取消", command=self.cancel)
        self.cancel_btn.pack(pady=10)

        # 绑定窗口关闭事件
        self.dialog.protocol("WM_DELETE_WINDOW", self.cancel)

    def set_file_info(self, filename, location):
        """设置文件名和位置显示"""
        self.file_label.config(text=f"文件名: {filename}")
        self.path_label.config(text=f"文件位置: {location}")

    def update_progress(self, current):
        """更新进度"""
        self.progress['value'] = current
        percent = int((current / self.total) * 100) if self.total > 0 else 0
        self.percent_label.config(text=f"{percent}%")
        self.dialog.update_idletasks()

    def cancel(self):
        """取消导出"""
        self.cancel_flag = True
        logger.info("用户取消导出操作")

    def is_cancelled(self):
        return self.cancel_flag

    def close(self):
        self.dialog.destroy()


class XTCViewerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("XTC/XTCH 预览器")
        self.root.geometry("900x700")

        self.current_file = None
        self.reader: Optional[XTCReader] = None
        self.current_page = 0
        self.original_image = None       # 原始解码图像（未缩放）
        self.display_image = None        # 当前显示的缩放后图像
        self.zoom_factor = 1.0           # 缩放因子
        self.scale_mode = tk.StringVar(value="原始")
        self.scale_factors = {
            "原始": None,
            "X4 (480x800)": (480, 800),
            "X4 双倍 (960x1600)": (960, 1600),
            "X3 (528x792)": (528, 792),
            "X3 双倍 (1056x1584)": (1056, 1584)
        }
        self.photo_image = None
        self.canvas_image_id = None
        self.background_color = "gray"   # 背景颜色，默认灰色
        self.double_page = False         # 双页显示模式标志

        # 全屏相关
        self.fullscreen = False
        self.menubar = None               # 保存菜单栏
        self.status_frame = None          # 保存状态栏框架
        self.original_geometry = None

        self._create_widgets()
        self._bind_events()

    def _create_widgets(self):
        # 菜单栏
        self.menubar = tk.Menu(self.root)

        # 文件菜单
        file_menu = tk.Menu(self.menubar, tearoff=0)
        file_menu.add_command(label="打开文件", command=self.open_file)
        file_menu.add_command(label="保存当前页为图片", command=self.save_current_page)
        file_menu.add_command(label="另存为", command=self.save_as_sequence)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        self.menubar.add_cascade(label="文件", menu=file_menu)

        # 设置菜单
        settings_menu = tk.Menu(self.menubar, tearoff=0)

        # 缩放模式子菜单
        zoom_menu = tk.Menu(settings_menu, tearoff=0)
        for mode in self.scale_factors.keys():
            zoom_menu.add_radiobutton(label=mode, variable=self.scale_mode, value=mode,
                                      command=self.on_scale_mode_changed)
        settings_menu.add_cascade(label="缩放模式", menu=zoom_menu)

        # 背景颜色子菜单
        bg_menu = tk.Menu(settings_menu, tearoff=0)
        bg_menu.add_command(label="灰色", command=lambda: self.set_background_color("gray"))
        bg_menu.add_command(label="白色", command=lambda: self.set_background_color("white"))
        bg_menu.add_command(label="黑色", command=lambda: self.set_background_color("black"))
        bg_menu.add_command(label="自定义...", command=self.custom_background_color)
        settings_menu.add_cascade(label="背景颜色", menu=bg_menu)

        # 单/双页显示子菜单
        settings_menu.add_separator()
        self.double_page_var = tk.BooleanVar(value=False)
        settings_menu.add_checkbutton(label="双页显示", variable=self.double_page_var,
                                      command=self.toggle_double_page)

        # 全屏选项
        settings_menu.add_separator()
        settings_menu.add_command(label="全屏 (F11)", command=self.toggle_fullscreen)

        self.menubar.add_cascade(label="设置", menu=settings_menu)

        # 帮助菜单（关于）
        help_menu = tk.Menu(self.menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self.show_about)
        self.menubar.add_cascade(label="帮助", menu=help_menu)

        self.root.config(menu=self.menubar)

        # 主框架
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 控制栏
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=5)

        self.btn_prev = ttk.Button(control_frame, text="上一页", command=self.prev_page, state=tk.DISABLED)
        self.btn_prev.pack(side=tk.LEFT, padx=5)

        self.btn_next = ttk.Button(control_frame, text="下一页", command=self.next_page, state=tk.DISABLED)
        self.btn_next.pack(side=tk.LEFT, padx=5)

        # 快速跳转控件
        self.page_spin = ttk.Spinbox(control_frame, from_=1, to=1, width=5, command=self.jump_to_page)
        self.page_spin.pack(side=tk.LEFT, padx=5)

        self.btn_jump = ttk.Button(control_frame, text="跳转", command=self.jump_to_page)
        self.btn_jump.pack(side=tk.LEFT, padx=2)

        self.page_label = ttk.Label(control_frame, text="页数: 0 / 0")
        self.page_label.pack(side=tk.LEFT, padx=10)

        # 画布区域（带滚动条）
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        # 创建水平和垂直滚动条
        h_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        v_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL)

        # 创建 Canvas，设置滚动条
        self.canvas = tk.Canvas(canvas_frame, bg=self.background_color, highlightthickness=0,
                                xscrollcommand=h_scrollbar.set, yscrollcommand=v_scrollbar.set)
        h_scrollbar.config(command=self.canvas.xview)
        v_scrollbar.config(command=self.canvas.yview)

        # 布局：canvas 占满，滚动条放在右侧和底部
        self.canvas.grid(row=0, column=0, sticky="nsew")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")

        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        # 状态栏（分为左侧状态信息和右侧文件名）
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.status = ttk.Label(self.status_frame, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.file_label = ttk.Label(self.status_frame, text="", relief=tk.SUNKEN, anchor=tk.E)
        self.file_label.pack(side=tk.RIGHT, padx=5)

        # 绑定窗口大小变化事件
        self.root.bind('<Configure>', self.on_window_resize)
        # 绑定鼠标滚轮事件（用于翻页或滚动）
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", self.on_mousewheel)   # Linux 滚轮向上
        self.canvas.bind("<Button-5>", self.on_mousewheel)   # Linux 滚轮向下
        # Ctrl+滚轮缩放
        self.canvas.bind("<Control-MouseWheel>", self.on_ctrl_mousewheel)
        self.canvas.bind("<Control-Button-4>", self.on_ctrl_mousewheel)
        self.canvas.bind("<Control-Button-5>", self.on_ctrl_mousewheel)

    def _bind_events(self):
        self.root.bind('<Left>', lambda e: self.prev_page())
        self.root.bind('<Right>', lambda e: self.next_page())
        self.root.bind('<Up>', lambda e: self.prev_page())
        self.root.bind('<Down>', lambda e: self.next_page())
        # 全屏快捷键
        self.root.bind('<F11>', lambda e: self.toggle_fullscreen())
        self.root.bind('<Escape>', lambda e: self.exit_fullscreen())

    def toggle_fullscreen(self):
        """切换全屏模式"""
        if self.fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        """进入全屏模式"""
        self.original_geometry = self.root.geometry()
        self.root.attributes('-fullscreen', True)
        # 隐藏菜单栏
        self.root.config(menu='')
        # 隐藏状态栏
        self.status_frame.pack_forget()
        self.fullscreen = True
        logger.info("进入全屏模式")

    def exit_fullscreen(self):
        """退出全屏模式"""
        if self.fullscreen:
            self.root.attributes('-fullscreen', False)
            # 恢复菜单栏
            self.root.config(menu=self.menubar)
            # 恢复状态栏
            self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)
            # 恢复窗口几何位置（如果保存过）
            if self.original_geometry:
                self.root.geometry(self.original_geometry)
            self.fullscreen = False
            logger.info("退出全屏模式")
            # 刷新预览以确保居中
            self.update_preview()

    def on_window_resize(self, event):
        """窗口大小改变时，重新居中图像"""
        if self.display_image:
            self._center_view()

    # ---------- 修复后的 _center_view 方法 ----------
    def _center_view(self):
        """根据图像和画布大小，精确居中图像（修复了水平方向完整、垂直方向超出时靠左的问题）"""
        self.canvas.update_idletasks()
        if not self.display_image:
            return

        img_w = self.display_image.width
        img_h = self.display_image.height
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        # 处理画布尺寸尚未正确获取的情况
        if canvas_w <= 1 or canvas_h <= 1:
            return

        # 情况1：图像完全可见（宽度和高度均小于等于画布）
        if img_w <= canvas_w and img_h <= canvas_h:
            # 将图像直接绘制在画布中央，清除滚动条影响
            self.canvas.delete("all")
            x = (canvas_w - img_w) // 2
            y = (canvas_h - img_h) // 2
            self.canvas_image_id = self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo_image)
            # 设置滚动区域为整个画布大小，避免出现滚动条
            self.canvas.config(scrollregion=(0, 0, canvas_w, canvas_h))
            return

        # 情况2：图像至少在一个方向上超出画布
        self.canvas.delete("all")

        # 确定图像放置的起始坐标
        # 水平方向：若宽度小于画布则居中，否则从0开始（滚动条控制）
        if img_w <= canvas_w:
            x = (canvas_w - img_w) // 2
            need_h_scroll = False
        else:
            x = 0
            need_h_scroll = True

        # 垂直方向：若高度小于画布则居中，否则从0开始（滚动条控制）
        if img_h <= canvas_h:
            y = (canvas_h - img_h) // 2
            need_v_scroll = False
        else:
            y = 0
            need_v_scroll = True

        self.canvas_image_id = self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo_image)
        # 滚动区域必须包含整个图像
        self.canvas.config(scrollregion=(0, 0, img_w, img_h))

        # 需要滚动条时，将视图移动到中心
        if need_h_scroll:
            x_center = (img_w / 2 - canvas_w / 2) / img_w
            x_center = max(0.0, min(1.0 - canvas_w / img_w, x_center))
            self.canvas.xview_moveto(x_center)
        if need_v_scroll:
            y_center = (img_h / 2 - canvas_h / 2) / img_h
            y_center = max(0.0, min(1.0 - canvas_h / img_h, y_center))
            self.canvas.yview_moveto(y_center)

    def on_scale_mode_changed(self):
        """缩放模式改变时的处理"""
        self.zoom_factor = 1.0
        self.update_preview()

    def set_background_color(self, color):
        """设置背景颜色"""
        self.background_color = color
        self.canvas.config(bg=color)

    def custom_background_color(self):
        """自定义背景颜色（通过RGB滑块）"""
        color_win = tk.Toplevel(self.root)
        color_win.title("自定义背景颜色")
        color_win.geometry("400x350")
        color_win.resizable(False, False)
        color_win.transient(self.root)
        color_win.grab_set()

        # 窗口居中
        color_win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - color_win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - color_win.winfo_height()) // 2
        color_win.geometry(f"+{x}+{y}")

        current_rgb = self.root.winfo_rgb(self.background_color)
        if current_rgb:
            r = current_rgb[0] // 256
            g = current_rgb[1] // 256
            b = current_rgb[2] // 256
        else:
            r, g, b = 128, 128, 128

        r_var = tk.IntVar(value=r)
        g_var = tk.IntVar(value=g)
        b_var = tk.IntVar(value=b)

        def update_preview(*args):
            color = f"#{r_var.get():02x}{g_var.get():02x}{b_var.get():02x}"
            preview_label.config(bg=color)

        def apply_color():
            color = f"#{r_var.get():02x}{g_var.get():02x}{b_var.get():02x}"
            self.set_background_color(color)
            color_win.destroy()

        frame = ttk.Frame(color_win, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="红 (R)").grid(row=0, column=0, sticky="w", pady=5)
        r_scale = ttk.Scale(frame, from_=0, to=255, variable=r_var, orient=tk.HORIZONTAL, command=lambda x: update_preview())
        r_scale.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        r_entry = ttk.Entry(frame, textvariable=r_var, width=5)
        r_entry.grid(row=0, column=2, padx=5, pady=5)
        r_entry.bind("<KeyRelease>", lambda e: update_preview())

        ttk.Label(frame, text="绿 (G)").grid(row=1, column=0, sticky="w", pady=5)
        g_scale = ttk.Scale(frame, from_=0, to=255, variable=g_var, orient=tk.HORIZONTAL, command=lambda x: update_preview())
        g_scale.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        g_entry = ttk.Entry(frame, textvariable=g_var, width=5)
        g_entry.grid(row=1, column=2, padx=5, pady=5)
        g_entry.bind("<KeyRelease>", lambda e: update_preview())

        ttk.Label(frame, text="蓝 (B)").grid(row=2, column=0, sticky="w", pady=5)
        b_scale = ttk.Scale(frame, from_=0, to=255, variable=b_var, orient=tk.HORIZONTAL, command=lambda x: update_preview())
        b_scale.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        b_entry = ttk.Entry(frame, textvariable=b_var, width=5)
        b_entry.grid(row=2, column=2, padx=5, pady=5)
        b_entry.bind("<KeyRelease>", lambda e: update_preview())

        preview_label = tk.Label(frame, text="预览", width=20, height=5, relief=tk.SUNKEN)
        preview_label.grid(row=3, column=0, columnspan=3, pady=10)
        update_preview()

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=10)
        ttk.Button(btn_frame, text="确定", command=apply_color).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=color_win.destroy).pack(side=tk.LEFT, padx=5)

        frame.columnconfigure(1, weight=1)

    def show_about(self):
        about_win = tk.Toplevel(self.root)
        about_win.title("关于")
        about_win.geometry("400x200")
        about_win.resizable(False, False)
        about_win.transient(self.root)
        about_win.grab_set()

        # 窗口居中
        about_win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - about_win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - about_win.winfo_height()) // 2
        about_win.geometry(f"+{x}+{y}")

        frame = ttk.Frame(about_win, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        title_label = ttk.Label(frame, text="XTC/XTCH 预览器", font=("微软雅黑", 14, "bold"))
        title_label.pack(pady=(0, 10))

        # 版本号改为 2.0
        version_label = ttk.Label(frame, text="版本 2.0")
        version_label.pack()

        link_frame = ttk.Frame(frame)
        link_frame.pack(pady=10)

        link_label = tk.Label(link_frame, text="GitHub 仓库", fg="blue", cursor="hand2")
        link_label.pack()
        link_label.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/gmy771810930/XTC_Viewer"))

        close_btn = ttk.Button(frame, text="关闭", command=about_win.destroy)
        close_btn.pack(pady=10)

    def open_file(self):
        file_path = filedialog.askopenfilename(
            title="选择 XTC/XTCH 文件",
            filetypes=[("XTC/XTCH 文件", "*.xtc *.xtch"), ("所有文件", "*.*")]
        )
        if not file_path:
            return
        try:
            if self.reader:
                self.reader.close()
            self.reader = XTCReader(file_path)
            self.current_file = file_path
            self.current_page = 0
            self.zoom_factor = 1.0
            self.scale_mode.set("原始")
            self.double_page = False
            self.double_page_var.set(False)

            self.page_spin.config(from_=1, to=self.reader.page_count)
            self.page_spin.delete(0, tk.END)
            self.page_spin.insert(0, "1")

            self.original_image = self.reader.get_page_image(self.current_page)
            self.update_preview()
            self.btn_prev.config(state=tk.NORMAL)
            self.btn_next.config(state=tk.NORMAL)

            self.file_label.config(text=f"文件名: {Path(file_path).name}")
            self.status.config(text=f"已打开: {file_path}")
            logger.info(f"打开文件: {file_path}")
        except Exception as e:
            logger.exception("打开文件失败")
            messagebox.showerror("错误", f"打开文件失败:\n{e}")

    def save_current_page(self):
        """保存当前页为图片"""
        if not self.reader:
            messagebox.showwarning("警告", "请先打开一个 XTC/XTCH 文件")
            return
        try:
            # 如果双页模式，提示用户保存的是当前第一页（或可以改为保存拼接图，这里保持原意）
            if self.double_page:
                # 可选：提示或保存拼接图，此处保持保存第一页
                msg = "双页模式下将保存当前显示的第一页。是否继续？"
                if not messagebox.askyesno("提示", msg):
                    return
            img = self.reader.get_page_image(self.current_page)
            if img.size[0] == 0 or img.size[1] == 0:
                messagebox.showerror("错误", "图像尺寸为0，无法保存")
                return

            save_path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG 图片", "*.png"), ("JPEG 图片", "*.jpg *.jpeg"), ("所有文件", "*.*")]
            )
            if save_path:
                ext = os.path.splitext(save_path)[1].lower()
                if ext in ('.jpg', '.jpeg'):
                    if img.mode == 'L':
                        img = img.convert('RGB')
                    img.save(save_path, 'JPEG', quality=95)
                else:
                    img.save(save_path, 'PNG')
                logger.info(f"保存页面至: {save_path}")
                self.status.config(text=f"已保存: {save_path}")
        except Exception as e:
            logger.exception("保存页面失败")
            messagebox.showerror("错误", f"保存页面失败:\n{e}")

    def save_as_sequence(self):
        """另存为：弹出选项对话框，确认后显示进度条并导出所有页面"""
        if not self.reader:
            messagebox.showwarning("警告", "请先打开一个 XTC/XTCH 文件")
            return

        # 创建选项对话框
        opt_win = tk.Toplevel(self.root)
        opt_win.title("另存为")
        opt_win.geometry("500x400")
        opt_win.resizable(False, False)
        opt_win.transient(self.root)
        opt_win.grab_set()

        # 窗口居中
        opt_win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - opt_win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - opt_win.winfo_height()) // 2
        opt_win.geometry(f"+{x}+{y}")

        frame = ttk.Frame(opt_win, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        # 格式选择
        ttk.Label(frame, text="图片格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        format_var = tk.StringVar(value="png")
        ttk.Radiobutton(frame, text="PNG", variable=format_var, value="png").grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(frame, text="JPEG", variable=format_var, value="jpg").grid(row=0, column=2, sticky=tk.W)

        # 命名方式（两种）
        ttk.Label(frame, text="命名方式:").grid(row=1, column=0, sticky=tk.W, pady=5)
        naming_var = tk.StringVar(value="原文件名-编号")
        base_name = Path(self.current_file).stem
        ttk.Radiobutton(frame, text=f"原文件名-编号（例如: {base_name}-001）", variable=naming_var, value="原文件名-编号").grid(row=1, column=1, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(frame, text=f"编号（例如: 001）", variable=naming_var, value="编号").grid(row=2, column=1, columnspan=2, sticky=tk.W)

        # 保存位置
        ttk.Label(frame, text="保存位置:").grid(row=3, column=0, sticky=tk.W, pady=5)
        location_var = tk.StringVar(value="源目录")
        ttk.Radiobutton(frame, text="源目录", variable=location_var, value="源目录").grid(row=3, column=1, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(frame, text="自定义", variable=location_var, value="自定义").grid(row=4, column=1, columnspan=2, sticky=tk.W)

        self.custom_path_var = tk.StringVar()
        self.custom_path_entry = ttk.Entry(frame, textvariable=self.custom_path_var, state='disabled')
        self.custom_path_entry.grid(row=5, column=1, sticky=tk.EW, padx=5)
        ttk.Button(frame, text="浏览...", command=lambda: self._browse_custom_path(self.custom_path_var), state='disabled').grid(row=5, column=2, padx=5)

        def on_location_change(*args):
            if location_var.get() == "自定义":
                self.custom_path_entry.config(state='normal')
                for child in frame.grid_slaves(row=5, column=2):
                    if isinstance(child, ttk.Button):
                        child.config(state='normal')
            else:
                self.custom_path_entry.config(state='disabled')
                for child in frame.grid_slaves(row=5, column=2):
                    if isinstance(child, ttk.Button):
                        child.config(state='disabled')
        location_var.trace('w', on_location_change)

        # 确定取消按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=6, column=0, columnspan=3, pady=15)

        def confirm():
            fmt = format_var.get()
            naming = naming_var.get()
            loc = location_var.get()
            if loc == "自定义" and not self.custom_path_var.get().strip():
                messagebox.showwarning("警告", "请选择自定义保存目录")
                return
            opt_win.destroy()
            self._export_sequence(fmt, naming, loc, base_name)

        ttk.Button(btn_frame, text="确认", command=confirm).pack(side=tk.LEFT, padx=10)
        ttk.Button(btn_frame, text="取消", command=opt_win.destroy).pack(side=tk.LEFT, padx=10)

        frame.columnconfigure(1, weight=1)

    def _browse_custom_path(self, var):
        dir_path = filedialog.askdirectory(title="选择保存目录")
        if dir_path:
            var.set(dir_path)

    def _export_sequence(self, fmt, naming, location, base_name):
        """实际执行导出，显示进度对话框"""
        logger.info(f"导出参数：格式={fmt}, 命名方式={naming}, 位置={location}, 基础名={base_name}")

        if location == "源目录":
            source_dir = Path(self.current_file).parent
            save_dir = source_dir / base_name
        else:
            save_dir = Path(self.custom_path_var.get())

        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"创建导出目录: {save_dir}")
        except Exception as e:
            logger.exception("创建目录失败")
            messagebox.showerror("错误", f"无法创建目录 {save_dir}: {e}")
            return

        total_pages = self.reader.page_count
        digits = len(str(total_pages))
        if digits < 1:
            digits = 1

        if naming == "原文件名-编号":
            template = f"{base_name}-{{:0{digits}d}}.{fmt}"
        else:
            template = f"{{:0{digits}d}}.{fmt}"

        logger.info(f"文件名模板: {template}")

        progress = ProgressDialog(self.root, f"转换中 - {fmt.upper()} 导出", total_pages)
        first_filename = template.format(1)
        progress.set_file_info(first_filename, str(save_dir))
        progress.update_progress(0)

        cancelled = False
        try:
            for page_idx in range(total_pages):
                if progress.is_cancelled():
                    cancelled = True
                    break

                img = self.reader.get_page_image(page_idx)
                if img.size[0] == 0 or img.size[1] == 0:
                    logger.warning(f"第 {page_idx+1} 页图像尺寸为0，跳过")
                    continue

                filename = template.format(page_idx+1)
                save_path = save_dir / filename

                if fmt == 'jpg':
                    if img.mode == 'L':
                        img = img.convert('RGB')
                    img.save(save_path, 'JPEG', quality=95)
                else:
                    img.save(save_path, 'PNG')

                logger.info(f"导出页面 {page_idx+1}/{total_pages}: {save_path}")
                progress.update_progress(page_idx + 1)

            if cancelled:
                logger.info("导出被用户取消")
                messagebox.showinfo("提示", "导出已取消")
            else:
                logger.info(f"导出完成，共 {total_pages} 页，保存至 {save_dir}")
                messagebox.showinfo("完成", f"成功导出 {total_pages} 页图片到:\n{save_dir}")
        except Exception as e:
            logger.exception("导出过程中出错")
            messagebox.showerror("错误", f"导出失败: {e}")
        finally:
            progress.close()

    def jump_to_page(self):
        if not self.reader:
            return
        try:
            page_str = self.page_spin.get()
            page = int(page_str)
            if page < 1 or page > self.reader.page_count:
                raise ValueError
            self.current_page = page - 1
            # 如果是双页模式且当前页不是第一页，可能需要调整显示起始页
            # 但用户跳转后直接显示该页，所以保持 current_page 不变，update_preview 会处理双页逻辑
            self.update_preview()
        except ValueError:
            self.page_spin.delete(0, tk.END)
            self.page_spin.insert(0, str(self.current_page + 1))
            messagebox.showwarning("警告", f"页码必须在1到{self.reader.page_count}之间")

    def toggle_double_page(self):
        """切换双页显示模式"""
        self.double_page = self.double_page_var.get()
        # 刷新当前显示
        self.update_preview()

    def update_preview(self):
        """更新显示图像，支持单页和双页模式"""
        if not self.reader:
            return

        # 单页模式：保持原有逻辑
        if not self.double_page:
            self._update_preview_single()
        else:
            self._update_preview_double()

    def _update_preview_single(self):
        """单页模式的预览更新"""
        self.original_image = self.reader.get_page_image(self.current_page)

        # 根据缩放模式和缩放因子计算显示图像
        mode = self.scale_mode.get()
        if mode != "原始":
            target = self.scale_factors[mode]
            if target is not None:
                w, h = target
                self.display_image = self.original_image.resize((w, h), Image.Resampling.LANCZOS)
                self.zoom_factor = 1.0
            else:
                self.display_image = self.original_image
        else:
            w = int(self.original_image.width * self.zoom_factor)
            h = int(self.original_image.height * self.zoom_factor)
            self.display_image = self.original_image.resize((w, h), Image.Resampling.LANCZOS)

        self.photo_image = ImageTk.PhotoImage(self.display_image)

        # 更新页码信息
        self.page_label.config(text=f"页数: {self.current_page+1} / {self.reader.page_count}")
        self.page_spin.delete(0, tk.END)
        self.page_spin.insert(0, str(self.current_page + 1))

        mode_name = mode if mode != "原始" else f"自定义缩放 {self.zoom_factor:.2f}x"
        self.status.config(text=f"第 {self.current_page+1} 页 | 显示尺寸 {self.display_image.width}x{self.display_image.height} | 模式 {mode_name}")
        logger.info(f"显示第 {self.current_page+1} 页，显示尺寸 {self.display_image.width}x{self.display_image.height}")

        # 居中显示
        self._center_view()

    def _update_preview_double(self):
        """双页模式的预览更新：显示当前页和下一页（若存在），否则只显示当前页"""
        # 获取当前页图像
        img_current = self.reader.get_page_image(self.current_page)

        # 判断是否有下一页
        has_next = self.current_page + 1 < self.reader.page_count
        if has_next:
            img_next = self.reader.get_page_image(self.current_page + 1)
        else:
            img_next = None

        # 根据缩放模式分别缩放两页（或单页）
        mode = self.scale_mode.get()
        if mode != "原始":
            target = self.scale_factors[mode]
            if target is not None:
                w, h = target
                img_current = img_current.resize((w, h), Image.Resampling.LANCZOS)
                if has_next:
                    img_next = img_next.resize((w, h), Image.Resampling.LANCZOS)
                self.zoom_factor = 1.0
            # 如果是"原始"但 target 为 None，则不缩放
        else:
            # 自定义缩放因子
            w = int(img_current.width * self.zoom_factor)
            h = int(img_current.height * self.zoom_factor)
            img_current = img_current.resize((w, h), Image.Resampling.LANCZOS)
            if has_next:
                w2 = int(img_next.width * self.zoom_factor)
                h2 = int(img_next.height * self.zoom_factor)
                img_next = img_next.resize((w2, h2), Image.Resampling.LANCZOS)

        # 拼接两页
        if has_next:
            # 两页高度可能不同，以较高者为总高度，较低者顶部对齐，底部留空（用背景色填充）
            max_h = max(img_current.height, img_next.height)
            # 创建空白画布（背景色）
            combined_w = img_current.width + img_next.width
            combined_h = max_h
            # 使用背景色创建图像
            bg_color = self.background_color
            # PIL 中 'L' 模式不支持彩色背景，需要转换为 'RGB' 或保持 'L' 但背景色只能是灰度
            # 因为图像是灰度模式（'L'），我们创建 'L' 模式的背景，灰度值根据背景色计算
            # 由于背景色可能为颜色名（如 'gray'）或十六进制，需要转换为灰度值
            # 简单起见，统一使用白色背景，或者根据当前画布背景颜色转换
            # 更稳妥：直接将拼接图创建为 'L' 模式，背景设为白色（255）或黑色（0），忽略彩色背景
            # 这里我们简单使用白色背景，因为画布背景也是灰度，但用户可能设置彩色背景，但图像是灰度，效果可能不好。
            # 为了简洁，使用白色背景，因为漫画阅读器通常白底黑字。
            # 更好的做法是获取画布背景的灰度值，但比较复杂，此处使用白色。
            # 注意：背景色设置仅影响画布，拼接图内部的背景可设为白色，看起来一致。
            bg_value = 255  # 白色
            combined_img = Image.new('L', (combined_w, combined_h), bg_value)
            # 粘贴第一页（顶部对齐）
            combined_img.paste(img_current, (0, 0))
            # 粘贴第二页（顶部对齐）
            combined_img.paste(img_next, (img_current.width, 0))
            self.display_image = combined_img
        else:
            # 只有一页
            self.display_image = img_current

        self.photo_image = ImageTk.PhotoImage(self.display_image)

        # 更新页码信息
        if has_next:
            page_range = f"{self.current_page+1} - {self.current_page+2}"
            page_spin_value = self.current_page + 1
        else:
            page_range = f"{self.current_page+1}"
            page_spin_value = self.current_page + 1

        self.page_label.config(text=f"双页模式 | 页数: {page_range} / {self.reader.page_count}")
        self.page_spin.delete(0, tk.END)
        self.page_spin.insert(0, str(page_spin_value))

        mode_name = mode if mode != "原始" else f"自定义缩放 {self.zoom_factor:.2f}x"
        self.status.config(text=f"双页模式 {page_range} | 显示尺寸 {self.display_image.width}x{self.display_image.height} | 模式 {mode_name}")
        logger.info(f"双页显示第 {self.current_page+1} 页及之后，显示尺寸 {self.display_image.width}x{self.display_image.height}")

        # 居中显示
        self._center_view()

    def on_mousewheel(self, event):
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        if not self.display_image:
            return
        img_w = self.display_image.width
        img_h = self.display_image.height

        if img_w <= canvas_width and img_h <= canvas_height:
            if event.num == 4 or event.delta > 0:
                self.prev_page()
            else:
                self.next_page()
        else:
            if event.num == 4 or event.delta > 0:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5 or event.delta < 0:
                self.canvas.yview_scroll(1, "units")

    def on_ctrl_mousewheel(self, event):
        if event.num == 4 or event.delta > 0:
            self.zoom_factor *= 1.1
        elif event.num == 5 or event.delta < 0:
            self.zoom_factor *= 0.9
        # 限制缩放范围：最小0.1倍，最大5倍
        self.zoom_factor = max(0.1, min(5.0, self.zoom_factor))
        self.scale_mode.set("原始")
        self.update_preview()

    def prev_page(self):
        if not self.reader:
            return
        if self.double_page:
            # 双页模式下，上一页跳两页，但确保不越界
            new_page = self.current_page - 2
            if new_page < 0:
                new_page = 0
            if new_page != self.current_page:
                self.current_page = new_page
                self.update_preview()
        else:
            if self.current_page > 0:
                self.current_page -= 1
                self.update_preview()

    def next_page(self):
        if not self.reader:
            return
        if self.double_page:
            # 双页模式下，下一页跳两页，但确保最后一页的单独显示
            new_page = self.current_page + 2
            if new_page >= self.reader.page_count:
                # 如果 new_page 超出总页数，则尝试跳到最后一页（可能单独显示）
                if self.current_page < self.reader.page_count - 1:
                    new_page = self.reader.page_count - 1
                else:
                    return
            self.current_page = new_page
            self.update_preview()
        else:
            if self.current_page < self.reader.page_count - 1:
                self.current_page += 1
                self.update_preview()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = XTCViewerApp()
    app.run()
