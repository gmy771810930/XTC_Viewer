#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XTC/XTCH 格式预览器（修正版）
支持：XTC (1-bit) 和 XTCH (2-bit) 文件，提供缩放预览
"""

import os
import sys
import logging
import struct
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

class XTCViewerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("XTC/XTCH 预览器")
        self.root.geometry("900x700")

        self.current_file = None
        self.reader: Optional[XTCReader] = None
        self.current_page = 0
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

        self._create_widgets()
        self._bind_events()

    def _create_widgets(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开文件", command=self.open_file)
        file_menu.add_command(label="保存当前页为PNG", command=self.save_current_page)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        menubar.add_cascade(label="文件", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        for mode in self.scale_factors.keys():
            view_menu.add_radiobutton(label=mode, variable=self.scale_mode, value=mode,
                                      command=self.update_preview)
        menubar.add_cascade(label="缩放模式", menu=view_menu)
        self.root.config(menu=menubar)

        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=5)

        self.btn_prev = ttk.Button(control_frame, text="上一页", command=self.prev_page, state=tk.DISABLED)
        self.btn_prev.pack(side=tk.LEFT, padx=5)

        self.btn_next = ttk.Button(control_frame, text="下一页", command=self.next_page, state=tk.DISABLED)
        self.btn_next.pack(side=tk.LEFT, padx=5)

        self.page_label = ttk.Label(control_frame, text="页数: 0 / 0")
        self.page_label.pack(side=tk.LEFT, padx=10)

        self.info_label = ttk.Label(control_frame, text="未打开文件")
        self.info_label.pack(side=tk.LEFT, padx=10)

        self.canvas = tk.Canvas(main_frame, bg='gray', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.status = ttk.Label(self.root, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def _bind_events(self):
        self.root.bind('<Left>', lambda e: self.prev_page())
        self.root.bind('<Right>', lambda e: self.next_page())
        self.root.bind('<Up>', lambda e: self.prev_page())
        self.root.bind('<Down>', lambda e: self.next_page())

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
            self.update_preview()
            self.btn_prev.config(state=tk.NORMAL)
            self.btn_next.config(state=tk.NORMAL)
            self.info_label.config(text=f"{Path(file_path).name} | {self.reader.title} | {self.reader.author}")
            self.status.config(text=f"已打开: {file_path}")
            logger.info(f"打开文件: {file_path}")
        except Exception as e:
            logger.exception("打开文件失败")
            messagebox.showerror("错误", f"打开文件失败:\n{e}")

    def save_current_page(self):
        if not self.reader:
            return
        try:
            img = self.reader.get_page_image(self.current_page)
            if img.size[0] == 0 or img.size[1] == 0:
                messagebox.showerror("错误", "图像尺寸为0，无法保存")
                return
            save_path = filedialog.asksaveasfilename(
                defaultextension=".png",
                filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")]
            )
            if save_path:
                img.save(save_path)
                logger.info(f"保存页面至: {save_path}")
                self.status.config(text=f"已保存: {save_path}")
        except Exception as e:
            logger.exception("保存页面失败")
            messagebox.showerror("错误", f"保存页面失败:\n{e}")

    def update_preview(self):
        if not self.reader:
            return

        try:
            img = self.reader.get_page_image(self.current_page)
            if img.size[0] == 0 or img.size[1] == 0:
                raise ValueError("图像尺寸为0，解码可能失败")

            mode = self.scale_mode.get()
            target = self.scale_factors[mode]
            if target is not None:
                w, h = target
                img = img.resize((w, h), Image.Resampling.LANCZOS)
                logger.debug(f"缩放至 {w}x{h}")
            else:
                w, h = img.size

            self.photo_image = ImageTk.PhotoImage(img)

            if self.canvas_image_id:
                self.canvas.delete(self.canvas_image_id)
            self.canvas.config(width=w, height=h)
            self.canvas_image_id = self.canvas.create_image(w//2, h//2, anchor=tk.CENTER, image=self.photo_image)

            self.page_label.config(text=f"页数: {self.current_page+1} / {self.reader.page_count}")
            self.status.config(text=f"第 {self.current_page+1} 页 | 尺寸 {w}x{h} | 模式 {mode}")
            logger.info(f"显示第 {self.current_page+1} 页，图像尺寸 {w}x{h}")

        except Exception as e:
            logger.exception("预览失败")
            self.status.config(text=f"预览失败: {str(e)}")
            self.canvas.delete("all")
            self.canvas.create_text(400, 300, text=f"预览失败\n{str(e)}", fill="red")

    def prev_page(self):
        if self.reader and self.current_page > 0:
            self.current_page -= 1
            self.update_preview()

    def next_page(self):
        if self.reader and self.current_page < self.reader.page_count - 1:
            self.current_page += 1
            self.update_preview()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = XTCViewerApp()
    app.run()