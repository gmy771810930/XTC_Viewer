#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XTC/XTCH 格式预览器（增强版）
支持：XTC (1-bit) 和 XTCH (2-bit) 文件，提供缩放预览、快速跳转、关于菜单、背景色设置、鼠标滚轮缩放/翻页、滚动条、完美居中、批量导出图片序列、全屏模式、单/双页显示
新增：支持 XTG/XTH 单页文件，自动扫描目录内所有 .xtg/.xth 文件并按文件名排序作为多页漫画，支持循环翻页。
修改：翻页控件移至菜单“跳转”中，右下角显示页码状态。
新增：上一本/下一本功能，支持切换同目录下的多本 XTC/XTCH 文件或上层目录下的多本 XTG/XTH 文件夹。
新增：可选的日志文件输出（菜单设置中开启），默认不输出日志文件。
"""

import os
import sys
import logging
import struct
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional, List

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
try:
    from natsort import natsorted
except ImportError:
    MISSING_MODULES.append("natsort")

if MISSING_MODULES:
    print("正在自动安装缺失的依赖...")
    for pkg in MISSING_MODULES:
        install_package(pkg)
    print("依赖安装完成，请重新运行程序。")
    sys.exit(0)

# ---------- 日志配置 ----------
def setup_logger():
    """创建 logger，只添加控制台处理器，文件处理器稍后按需添加"""
    logger = logging.getLogger("XTCViewer")
    logger.setLevel(logging.DEBUG)

    # 移除可能已有的处理器（避免重复）
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # 控制台处理器（始终存在）
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(fmt)
    logger.addHandler(console)

    return logger

logger = setup_logger()
# 文件处理器引用，用于动态添加/移除
_file_handler = None

def enable_file_log():
    """启用文件日志：创建 log 目录并添加文件处理器"""
    global _file_handler
    if _file_handler is not None:
        return  # 已启用
    log_dir = Path.cwd() / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log"
    log_path = log_dir / log_filename

    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    _file_handler = file_handler
    logger.info(f"文件日志已启用: {log_path}")

def disable_file_log():
    """禁用文件日志：移除文件处理器并关闭"""
    global _file_handler
    if _file_handler is not None:
        logger.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None
        logger.info("文件日志已禁用")

# ---------- XTC/XTCH 解析器（扩展支持单页 XTG/XTH） ----------
class XTCReader:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.container_mode = False   # 是否为容器文件（XTC/XTCH）
        self.page_files = []          # 单页模式下的文件列表
        self.pages = []               # 容器模式下的 (offset, size) 列表
        self.page_count = 0
        self.title = ""
        self.author = ""
        self.chapters = []
        self.is_hq = False             # 容器模式专用
        self.f = None                  # 容器模式文件句柄

        ext = os.path.splitext(filepath)[1].lower()
        if ext in ('.xtc', '.xtch'):
            # 容器文件模式
            self.container_mode = True
            self._parse_container()
        elif ext in ('.xtg', '.xth'):
            # 单页文件模式：扫描同目录下所有 .xtg/.xth 文件
            self.container_mode = False
            self._load_single_page_files(filepath)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    def _parse_container(self):
        """解析 XTC/XTCH 容器文件"""
        self.f = open(self.filepath, 'rb')
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

    def _load_single_page_files(self, filepath):
        """单页模式：收集目录下所有 .xtg/.xth 文件并按自然顺序排序"""
        dir_path = os.path.dirname(filepath)
        files = []
        for ext in ('.xtg', '.xth'):
            files.extend(Path(dir_path).glob(f'*{ext}'))
        # 使用自然排序确保 1,2,3,...,10 正确排序
        files = natsorted(files, key=lambda p: p.name)
        if not files:
            raise ValueError(f"目录 {dir_path} 中没有找到 .xtg 或 .xth 文件")

        self.page_files = [str(f) for f in files]
        self.page_count = len(self.page_files)
        # 元数据：以目录名作为书名
        self.title = os.path.basename(dir_path)
        self.author = ""
        self.chapters = []
        logger.info(f"单页模式加载完成: {self.page_count} 个文件，书名: {self.title}")

    def _parse_metadata(self, offset: int):
        self.f.seek(offset)
        title_bytes = self.f.read(128)
        self.title = title_bytes.split(b'\x00')[0].decode('utf-8', errors='ignore')
        author_bytes = self.f.read(64)
        self.author = author_bytes.split(b'\x00')[0].decode('utf-8', errors='ignore')
        logger.info(f"书名: {self.title}, 作者: {self.author}")

    def _parse_index(self, offset: int):
        """解析索引表（容器模式）"""
        self.f.seek(offset)
        for i in range(self.page_count):
            page_offset = struct.unpack('<Q', self.f.read(8))[0]
            page_size = struct.unpack('<I', self.f.read(4))[0]
            # 读取宽高（虽然未使用，但指针需要前进）
            width = struct.unpack('<H', self.f.read(2))[0]
            height = struct.unpack('<H', self.f.read(2))[0]
            self.pages.append((page_offset, page_size))
            logger.debug(f"页 {i}: 偏移={page_offset}, 大小={page_size}, 尺寸={width}x{height}")

    def _parse_chapters(self, offset: int):
        """解析章节表（容器模式）"""
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
        """获取指定页的图像"""
        if page_index < 0 or page_index >= self.page_count:
            raise IndexError(f"页码超出范围: {page_index}")

        if self.container_mode:
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
        else:
            # 单页模式：读取对应文件
            file_path = self.page_files[page_index]
            with open(file_path, 'rb') as f:
                file_data = f.read()
            # 根据扩展名或文件头判断格式
            ext = os.path.splitext(file_path)[1].lower()
            if ext == '.xtg' or file_data[:4] == b'XTG\0':
                img = self._decode_xtg(file_data)
            elif ext == '.xth' or file_data[:4] == b'XTH\0':
                img = self._decode_xth(file_data)
            else:
                raise ValueError(f"未知格式: {file_path}")
            if save_debug:
                debug_path = Path.home() / f"debug_page_{page_index}.png"
                img.save(debug_path)
                logger.info(f"保存调试图像: {debug_path}")
            return img

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
        if self.container_mode and self.f:
            self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ---------- GUI 应用 ----------
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
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

        # 书籍列表相关（用于上一本/下一本）
        self.book_list: List[str] = []    # 容器模式：文件路径列表；单页模式：目录路径列表
        self.book_index: int = -1

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

        # ---------- 跳转菜单 ----------
        jump_menu = tk.Menu(self.menubar, tearoff=0)
        jump_menu.add_command(label="上一页", command=self.prev_page, accelerator="Left")
        jump_menu.add_command(label="下一页", command=self.next_page, accelerator="Right")
        jump_menu.add_separator()
        jump_menu.add_command(label="上一本", command=self.prev_book, accelerator="PageUp")
        jump_menu.add_command(label="下一本", command=self.next_book, accelerator="PageDown")
        jump_menu.add_separator()
        jump_menu.add_command(label="跳转到页码...", command=self.show_jump_dialog, accelerator="Ctrl+G")
        self.menubar.add_cascade(label="跳转", menu=jump_menu)

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

        # ---------- 新增：输出日志(log文件) 子菜单 ----------
        settings_menu.add_separator()
        self.log_file_var = tk.BooleanVar(value=False)
        settings_menu.add_checkbutton(label="输出日志(log文件)", variable=self.log_file_var,
                                      command=self.toggle_log_file)

        # 全屏选项
        settings_menu.add_separator()
        settings_menu.add_command(label="全屏 (F11)", command=self.toggle_fullscreen)

        self.menubar.add_cascade(label="设置", menu=settings_menu)

        # 帮助菜单（关于）
        help_menu = tk.Menu(self.menubar, tearoff=0)
        help_menu.add_command(label="关于", command=self.show_about)
        self.menubar.add_cascade(label="帮助", menu=help_menu)

        self.root.config(menu=self.menubar)

        # 主框架（仅包含画布区域，控制栏已移除）
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

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

        # 状态栏（分为左侧状态信息和右侧页码显示）
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.status = ttk.Label(self.status_frame, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 右下角显示页码状态（替换原来的文件名显示）
        self.page_status_label = ttk.Label(self.status_frame, text="", relief=tk.SUNKEN, anchor=tk.E)
        self.page_status_label.pack(side=tk.RIGHT, padx=5)

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
        self.root.bind('<Prior>', lambda e: self.prev_book())   # PageUp
        self.root.bind('<Next>', lambda e: self.next_book())   # PageDown
        # 全屏快捷键
        self.root.bind('<F11>', lambda e: self.toggle_fullscreen())
        self.root.bind('<Escape>', lambda e: self.exit_fullscreen())
        # 跳转对话框快捷键
        self.root.bind('<Control-g>', lambda e: self.show_jump_dialog())
        self.root.bind('<Control-G>', lambda e: self.show_jump_dialog())

    # ---------- 日志文件切换 ----------
    def toggle_log_file(self):
        """根据菜单勾选状态启用/禁用文件日志"""
        if self.log_file_var.get():
            enable_file_log()
        else:
            disable_file_log()

    # ---------- 书籍列表扫描 ----------
    def _scan_books(self, filepath: str) -> List[str]:
        """根据当前文件，扫描同类型书籍列表，返回路径列表"""
        ext = os.path.splitext(filepath)[1].lower()
        if ext in ('.xtc', '.xtch'):
            # 容器模式：同目录下所有 .xtc/.xtch 文件
            dir_path = os.path.dirname(filepath)
            files = []
            for e in ('.xtc', '.xtch'):
                files.extend(Path(dir_path).glob(f'*{e}'))
            # 按文件名排序
            files = sorted(files, key=lambda p: p.name)
            return [str(f) for f in files]
        elif ext in ('.xtg', '.xth'):
            # 单页模式：上一层目录下所有包含 .xtg/.xth 文件的子目录
            current_dir = os.path.dirname(filepath)
            parent_dir = os.path.dirname(current_dir)
            # 扫描 parent_dir 下的所有子目录
            subdirs = [d for d in Path(parent_dir).iterdir() if d.is_dir()]
            valid_dirs = []
            for d in subdirs:
                # 检查目录下是否有 .xtg 或 .xth 文件
                has_files = False
                for e in ('.xtg', '.xth'):
                    if list(d.glob(f'*{e}')):
                        has_files = True
                        break
                if has_files:
                    valid_dirs.append(str(d))
            # 按文件夹名排序
            valid_dirs.sort(key=lambda p: os.path.basename(p))
            return valid_dirs
        else:
            return []

    def _update_book_list(self):
        """根据当前文件更新书籍列表和索引"""
        if not self.current_file:
            self.book_list = []
            self.book_index = -1
            return
        self.book_list = self._scan_books(self.current_file)
        # 确定当前索引
        current_path = self.current_file
        if self.reader and not self.reader.container_mode:
            # 单页模式：当前路径为文件，但书籍列表为目录，需找所在目录
            current_dir = os.path.dirname(current_path)
            try:
                self.book_index = self.book_list.index(current_dir)
            except ValueError:
                self.book_index = -1
        else:
            # 容器模式：直接匹配文件
            try:
                self.book_index = self.book_list.index(current_path)
            except ValueError:
                self.book_index = -1
        logger.info(f"书籍列表更新: 共 {len(self.book_list)} 本，当前索引 {self.book_index}")

    def _load_book(self, path: str):
        """加载一本书（可能是文件或目录）"""
        try:
            # 如果是目录，需要找到目录下第一个 .xtg/.xth 文件
            if os.path.isdir(path):
                # 找第一个支持的格式文件
                for ext in ('.xtg', '.xth'):
                    files = list(Path(path).glob(f'*{ext}'))
                    if files:
                        file_to_open = str(files[0])
                        break
                else:
                    raise ValueError(f"目录 {path} 中没有找到 .xtg 或 .xth 文件")
            else:
                file_to_open = path

            # 关闭当前 reader
            if self.reader:
                self.reader.close()

            # 打开新文件
            self.reader = XTCReader(file_to_open)
            self.current_file = file_to_open
            self.current_page = 0
            self.zoom_factor = 1.0
            self.scale_mode.set("原始")
            self.double_page = False
            self.double_page_var.set(False)

            self.original_image = self.reader.get_page_image(self.current_page)
            self.update_preview()

            # 更新书籍列表（可能需要重新扫描）
            self._update_book_list()

            logger.info(f"切换到书籍: {path}")
            self.status.config(text=f"已打开: {path}")
        except Exception as e:
            logger.exception(f"加载书籍失败: {path}")
            messagebox.showerror("错误", f"加载失败:\n{e}")

    def prev_book(self):
        """切换到上一本书"""
        if not self.book_list or self.book_index <= 0:
            messagebox.showinfo("提示", "已经是第一本书了")
            return
        self.book_index -= 1
        self._load_book(self.book_list[self.book_index])

    def next_book(self):
        """切换到下一本书"""
        if not self.book_list or self.book_index >= len(self.book_list) - 1:
            messagebox.showinfo("提示", "已经是最后一本书了")
            return
        self.book_index += 1
        self._load_book(self.book_list[self.book_index])

    # ---------- 其他功能方法 ----------
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
        self.root.config(menu='')
        self.status_frame.pack_forget()
        self.fullscreen = True
        logger.info("进入全屏模式")

    def exit_fullscreen(self):
        """退出全屏模式"""
        if self.fullscreen:
            self.root.attributes('-fullscreen', False)
            self.root.config(menu=self.menubar)
            self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)
            if self.original_geometry:
                self.root.geometry(self.original_geometry)
            self.fullscreen = False
            logger.info("退出全屏模式")
            self.update_preview()

    def on_window_resize(self, event):
        if self.display_image:
            self._center_view()

    def _center_view(self):
        """根据图像和画布大小，精确居中图像"""
        self.canvas.update_idletasks()
        if not self.display_image:
            return

        img_w = self.display_image.width
        img_h = self.display_image.height
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        if canvas_w <= 1 or canvas_h <= 1:
            return

        if img_w <= canvas_w and img_h <= canvas_h:
            self.canvas.delete("all")
            x = (canvas_w - img_w) // 2
            y = (canvas_h - img_h) // 2
            self.canvas_image_id = self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo_image)
            self.canvas.config(scrollregion=(0, 0, canvas_w, canvas_h))
            return

        self.canvas.delete("all")
        if img_w <= canvas_w:
            x = (canvas_w - img_w) // 2
            need_h_scroll = False
        else:
            x = 0
            need_h_scroll = True

        if img_h <= canvas_h:
            y = (canvas_h - img_h) // 2
            need_v_scroll = False
        else:
            y = 0
            need_v_scroll = True

        self.canvas_image_id = self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo_image)
        self.canvas.config(scrollregion=(0, 0, img_w, img_h))

        if need_h_scroll:
            x_center = (img_w / 2 - canvas_w / 2) / img_w
            x_center = max(0.0, min(1.0 - canvas_w / img_w, x_center))
            self.canvas.xview_moveto(x_center)
        if need_v_scroll:
            y_center = (img_h / 2 - canvas_h / 2) / img_h
            y_center = max(0.0, min(1.0 - canvas_h / img_h, y_center))
            self.canvas.yview_moveto(y_center)

    def on_scale_mode_changed(self):
        self.zoom_factor = 1.0
        self.update_preview()

    def set_background_color(self, color):
        self.background_color = color
        self.canvas.config(bg=color)

    def custom_background_color(self):
        color_win = tk.Toplevel(self.root)
        color_win.title("自定义背景颜色")
        color_win.geometry("400x350")
        color_win.resizable(False, False)
        color_win.transient(self.root)
        color_win.grab_set()

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

        about_win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - about_win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - about_win.winfo_height()) // 2
        about_win.geometry(f"+{x}+{y}")

        frame = ttk.Frame(about_win, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        title_label = ttk.Label(frame, text="XTC/XTCH 预览器", font=("微软雅黑", 14, "bold"))
        title_label.pack(pady=(0, 10))

        version_label = ttk.Label(frame, text="版本 2.2")
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
            filetypes=[("XTC/XTCH 文件", "*.xtc *.xtch *.xtg *.xth"), ("所有文件", "*.*")]
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

            self.original_image = self.reader.get_page_image(self.current_page)
            self.update_preview()

            self._update_book_list()

            logger.info(f"打开文件: {file_path}")
        except Exception as e:
            logger.exception("打开文件失败")
            messagebox.showerror("错误", f"打开文件失败:\n{e}")

    def show_jump_dialog(self):
        if not self.reader:
            messagebox.showwarning("警告", "请先打开一个文件")
            return
        current = self.current_page + 1
        total = self.reader.page_count
        result = simpledialog.askinteger(
            "跳转到页码",
            f"请输入页码 (1-{total}):",
            parent=self.root,
            initialvalue=current,
            minvalue=1,
            maxvalue=total
        )
        if result is not None:
            self.jump_to_page(result - 1)

    def jump_to_page(self, page_index):
        if self.reader and 0 <= page_index < self.reader.page_count:
            self.current_page = page_index
            self.update_preview()

    def save_current_page(self):
        if not self.reader:
            messagebox.showwarning("警告", "请先打开一个 XTC/XTCH 文件")
            return
        try:
            if self.double_page:
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
        if not self.reader:
            messagebox.showwarning("警告", "请先打开一个 XTC/XTCH 文件")
            return

        opt_win = tk.Toplevel(self.root)
        opt_win.title("另存为")
        opt_win.geometry("500x400")
        opt_win.resizable(False, False)
        opt_win.transient(self.root)
        opt_win.grab_set()

        opt_win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - opt_win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - opt_win.winfo_height()) // 2
        opt_win.geometry(f"+{x}+{y}")

        frame = ttk.Frame(opt_win, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="图片格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        format_var = tk.StringVar(value="png")
        ttk.Radiobutton(frame, text="PNG", variable=format_var, value="png").grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(frame, text="JPEG", variable=format_var, value="jpg").grid(row=0, column=2, sticky=tk.W)

        ttk.Label(frame, text="命名方式:").grid(row=1, column=0, sticky=tk.W, pady=5)
        naming_var = tk.StringVar(value="原文件名-编号")
        base_name = Path(self.current_file).stem
        ttk.Radiobutton(frame, text=f"原文件名-编号（例如: {base_name}-001）", variable=naming_var, value="原文件名-编号").grid(row=1, column=1, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(frame, text=f"编号（例如: 001）", variable=naming_var, value="编号").grid(row=2, column=1, columnspan=2, sticky=tk.W)

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

    def toggle_double_page(self):
        self.double_page = self.double_page_var.get()
        self.update_preview()

    def update_preview(self):
        if not self.reader:
            return

        if not self.double_page:
            self._update_preview_single()
        else:
            self._update_preview_double()

        if self.double_page:
            has_next = self.current_page + 1 < self.reader.page_count
            if has_next:
                page_text = f"第 {self.current_page+1}-{self.current_page+2} / {self.reader.page_count} 页"
            else:
                page_text = f"第 {self.current_page+1} / {self.reader.page_count} 页"
        else:
            page_text = f"第 {self.current_page+1} / {self.reader.page_count} 页"
        self.page_status_label.config(text=page_text)

    def _update_preview_single(self):
        self.original_image = self.reader.get_page_image(self.current_page)

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

        mode_name = mode if mode != "原始" else f"自定义缩放 {self.zoom_factor:.2f}x"
        self.status.config(text=f"第 {self.current_page+1} 页 | 显示尺寸 {self.display_image.width}x{self.display_image.height} | 模式 {mode_name}")
        logger.info(f"显示第 {self.current_page+1} 页，显示尺寸 {self.display_image.width}x{self.display_image.height}")

        self._center_view()

    def _update_preview_double(self):
        img_current = self.reader.get_page_image(self.current_page)

        has_next = self.current_page + 1 < self.reader.page_count
        if has_next:
            img_next = self.reader.get_page_image(self.current_page + 1)
        else:
            img_next = None

        mode = self.scale_mode.get()
        if mode != "原始":
            target = self.scale_factors[mode]
            if target is not None:
                w, h = target
                img_current = img_current.resize((w, h), Image.Resampling.LANCZOS)
                if has_next:
                    img_next = img_next.resize((w, h), Image.Resampling.LANCZOS)
                self.zoom_factor = 1.0
        else:
            w = int(img_current.width * self.zoom_factor)
            h = int(img_current.height * self.zoom_factor)
            img_current = img_current.resize((w, h), Image.Resampling.LANCZOS)
            if has_next:
                w2 = int(img_next.width * self.zoom_factor)
                h2 = int(img_next.height * self.zoom_factor)
                img_next = img_next.resize((w2, h2), Image.Resampling.LANCZOS)

        if has_next:
            max_h = max(img_current.height, img_next.height)
            combined_w = img_current.width + img_next.width
            combined_h = max_h
            bg_value = 255
            combined_img = Image.new('L', (combined_w, combined_h), bg_value)
            combined_img.paste(img_current, (0, 0))
            combined_img.paste(img_next, (img_current.width, 0))
            self.display_image = combined_img
        else:
            self.display_image = img_current

        self.photo_image = ImageTk.PhotoImage(self.display_image)

        mode_name = mode if mode != "原始" else f"自定义缩放 {self.zoom_factor:.2f}x"
        if has_next:
            page_range = f"{self.current_page+1} - {self.current_page+2}"
        else:
            page_range = f"{self.current_page+1}"
        self.status.config(text=f"双页模式 {page_range} | 显示尺寸 {self.display_image.width}x{self.display_image.height} | 模式 {mode_name}")
        logger.info(f"双页显示第 {self.current_page+1} 页及之后，显示尺寸 {self.display_image.width}x{self.display_image.height}")

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
        self.zoom_factor = max(0.1, min(5.0, self.zoom_factor))
        self.scale_mode.set("原始")
        self.update_preview()

    def prev_page(self):
        if not self.reader:
            return
        if self.double_page:
            new_page = self.current_page - 2
            if new_page < 0:
                new_page = 0
            if new_page != self.current_page:
                self.current_page = new_page
                self.update_preview()
        else:
            if self.current_page == 0:
                self.current_page = self.reader.page_count - 1
            else:
                self.current_page -= 1
            self.update_preview()

    def next_page(self):
        if not self.reader:
            return
        if self.double_page:
            new_page = self.current_page + 2
            if new_page >= self.reader.page_count:
                if self.current_page < self.reader.page_count - 1:
                    new_page = self.reader.page_count - 1
                else:
                    return
            self.current_page = new_page
            self.update_preview()
        else:
            if self.current_page == self.reader.page_count - 1:
                self.current_page = 0
            else:
                self.current_page += 1
            self.update_preview()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = XTCViewerApp()
    app.run()