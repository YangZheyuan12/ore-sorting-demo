from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .domain import MachineState, MaterialClass, SortItem, SortingMachine
from .protocol import HARDWARE_BAUDRATE, HARDWARE_STOP_COMMAND, batch_commands, parse_line
from .transport import SerialTransport


APP_NAME = "微波智选"
APP_FULL_NAME = "微波加热智能选矿系统"


def resource_path(*parts: str) -> Path:
    """Return a source-tree or PyInstaller-bundled resource path."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundle_root) if bundle_root else Path(__file__).resolve().parents[1]
    return root.joinpath(*parts)


def app_data_path(*parts: str) -> Path:
    """Return a writable per-user path that also works in a packaged app."""
    override = os.environ.get("MICROWAVE_SMART_SORT_DATA_DIR")
    if override:
        root = Path(override)
    else:
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MicrowaveSmartSort"
    return root.joinpath(*parts)


def portable_app_data_path(*parts: str) -> Path:
    """Return a cache path beside the packaged app (or inside the source tree)."""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[1]
    return root.joinpath("data", "app_cache", *parts)


@dataclass
class VisualRock:
    item: SortItem
    progress: float = 0.0
    entry_fired: bool = False
    exit_fired: bool = False
    route: MaterialClass | None = None
    extra: bool = False


@dataclass
class PhotoReview:
    item_id: str
    image_path: Path
    predicted_label: MaterialClass
    confidence: float
    waste_probability: float
    threshold: float
    actual_label: MaterialClass | None = None
    saved: bool = False
    original_name: str | None = None

    @property
    def display_name(self) -> str:
        return self.original_name or self.image_path.name


class SortingDemoApp:
    TICK_MS = 33
    BELT_TRAVEL_SECONDS = 6.0  # 0.6 m / 0.1 m/s
    RELEASE_INTERVAL_SECONDS = 2.4
    ENTRY_PROGRESS = 0.10
    EXIT_PROGRESS = 0.75
    HARDWARE_EXIT_WAIT_PROGRESS = 0.62
    MODEL_PATH = resource_path("artifacts", "resnet50_baseline_fold0", "model_retrained.onnx")
    ICON_PATH = resource_path("assets", "microwave-smart-sort-icon.png")
    MAX_HARDWARE_BATCH = 20

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME}｜{APP_FULL_NAME}")
        self.root.geometry("1320x850")
        self.root.minsize(1150, 760)
        self.window_icon: tk.PhotoImage | None = None
        self.header_icon: tk.PhotoImage | None = None
        if self.ICON_PATH.is_file():
            try:
                self.window_icon = tk.PhotoImage(file=str(self.ICON_PATH))
                self.root.iconphoto(True, self.window_icon)
                scale = max(1, round(max(self.window_icon.width(), self.window_icon.height()) / 64))
                self.header_icon = self.window_icon.subsample(scale, scale)
            except tk.TclError:
                self.window_icon = None
                self.header_icon = None

        self.machine = SortingMachine()
        self.transport = SerialTransport()
        self.loaded_items: list[SortItem] = []
        self.pending: list[VisualRock] = []
        self.active: list[VisualRock] = []
        self.simulation_running = False
        self.simulation_paused = False
        self.sim_clock = 0.0
        self.last_wall_time = time.monotonic()
        self.last_release_at = -999.0
        self.servo_move_at: float | None = None
        self.servo_move_target: MaterialClass | None = None
        self.servo_return_at: float | None = None
        self.last_event_rendered = 0
        self.image_classifier: Any | None = None
        self.photo_reviews: list[PhotoReview] = []
        self.cache_root, self.cache_available = self._select_cache_root()
        self.cache_manifest = self.cache_root / "last_batch.json"
        self.feedback_window: tk.Toplevel | None = None
        self.feedback_tree: ttk.Treeview | None = None

        self.speed_var = tk.DoubleVar(value=4.0)
        self.mirror_var = tk.BooleanVar(value=True)
        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="待机")
        self.stats_var = tk.StringVar()
        self.serial_status_var = tk.StringVar(value="未连接")
        self.model_status_var = tk.StringVar(value="模型：未加载｜支持 JPG、PNG、BMP、TIF")
        self.hardware_state_var = tk.StringVar(value="实物状态：未连接")
        self.hardware_stats_var = tk.StringVar(value="入口 0｜出口 0｜目标矿 0｜废石 0｜错误 0")
        self.entry_sensor_var = tk.StringVar(value="入口传感器：等待")
        self.exit_sensor_var = tk.StringVar(value="出口传感器：等待")
        self.hardware_batch_loaded = False
        self.hardware_armed = False
        self.hardware_run_active = False
        self.hardware_ready_at = 0.0
        self.hardware_in_count = 0
        self.hardware_out_count = 0
        self.hardware_ore_count = 0
        self.hardware_waste_count = 0
        self.hardware_error_count = 0

        self._build_style()
        self._build_ui()
        self._draw_static_scene()
        if not self._restore_cached_batch(show_message=False):
            self._refresh_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.TICK_MS, self._tick)
        self.root.after(100, self._poll_serial)

    def _select_cache_root(self) -> tuple[Path, bool]:
        candidates = (app_data_path("staging"), portable_app_data_path("staging"))
        for candidate in candidates:
            probe = candidate / ".write_test"
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe.write_bytes(b"")
                probe.unlink()
                return candidate, True
            except OSError:
                try:
                    if probe.is_file():
                        probe.unlink()
                except OSError:
                    pass
        return candidates[-1], False

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Stat.TLabel", font=("Consolas", 11, "bold"), padding=8)
        style.configure("BrandTitle.TLabel", font=("Microsoft YaHei UI", 20, "bold"), foreground="#0b3558")
        style.configure("BrandSub.TLabel", font=("Microsoft YaHei UI", 10), foreground="#0f766e")
        style.configure(
            "Accent.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            foreground="#ffffff",
            background="#0f766e",
            padding=(10, 6),
        )
        style.map("Accent.TButton", background=[("active", "#0d9488"), ("pressed", "#115e59")])

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(16, 12, 16, 8))
        header.pack(fill="x")
        if self.header_icon is not None:
            ttk.Label(header, image=self.header_icon).pack(side="left", padx=(0, 10))
        brand = ttk.Frame(header)
        brand.pack(side="left")
        ttk.Label(brand, text=APP_NAME, style="BrandTitle.TLabel").pack(anchor="w")
        ttk.Label(brand, text=APP_FULL_NAME, style="BrandSub.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=f"批次数量由所选图片数决定（每批 1–{self.MAX_HARDWARE_BATCH} 张）",
            foreground="#a33a14",
            style="Sub.TLabel",
        ).pack(side="right", padx=8)

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        left = ttk.Frame(body)
        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        body.add(left, weight=4)
        body.add(right, weight=1)

        self.left_stack = ttk.Panedwindow(left, orient="vertical")
        self.left_stack.pack(fill="both", expand=True)
        workspace = ttk.Frame(self.left_stack)

        status = ttk.Frame(workspace)
        status.pack(fill="x", pady=(0, 8))
        ttk.Label(status, textvariable=self.status_var, style="Stat.TLabel").pack(side="left")
        ttk.Label(status, textvariable=self.stats_var, style="Stat.TLabel").pack(side="right")

        self.canvas = tk.Canvas(
            workspace,
            height=380,
            background="#f6f8fb",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.canvas.pack(fill="both", expand=True)

        controls = ttk.LabelFrame(workspace, text="演示控制", padding=10)
        controls.pack(fill="x", pady=10)
        row1 = ttk.Frame(controls)
        row1.pack(fill="x")
        ttk.Button(
            row1,
            text="选择图片并识别",
            command=self.import_images,
            style="Accent.TButton",
        ).pack(side="left", padx=3)
        ttk.Button(row1, text="开始", command=self.start, style="Accent.TButton").pack(side="left", padx=(14, 3))
        ttk.Button(row1, text="暂停/继续", command=self.toggle_pause).pack(side="left", padx=3)
        ttk.Button(row1, text="复位", command=self.reset).pack(side="left", padx=3)
        ttk.Button(row1, text="急停", command=self.emergency_stop).pack(side="left", padx=3)

        row2 = ttk.Frame(controls)
        row2.pack(fill="x", pady=(10, 0))
        ttk.Label(row2, text="演示速度").pack(side="left")
        ttk.Scale(row2, from_=1, to=8, variable=self.speed_var, orient="horizontal", length=180).pack(side="left", padx=6)
        ttk.Button(row2, text="异常演示：多放一块", command=self.demo_extra_object).pack(side="left", padx=(18, 3))
        ttk.Button(row2, text="异常演示：空队列出料", command=self.demo_empty_queue).pack(side="left", padx=3)

        row3 = ttk.Frame(controls)
        row3.pack(fill="x", pady=(10, 0))
        ttk.Button(
            row3,
            text="识别反馈/纠错",
            command=self.open_feedback_window,
        ).pack(side="left", padx=3)
        ttk.Button(
            row3,
            text="恢复暂存批次",
            command=self.restore_cached_batch,
        ).pack(side="left", padx=3)
        ttk.Label(
            row3,
            textvariable=self.model_status_var,
            foreground="#475569",
            wraplength=850,
        ).pack(side="left", padx=10)

        lower = ttk.Panedwindow(self.left_stack, orient="horizontal")
        self.left_stack.add(workspace, weight=3)
        self.left_stack.add(lower, weight=2)
        queue_frame = ttk.LabelFrame(lower, text="待分选图片（从上到下为上料顺序）", padding=8)
        log_frame = ttk.LabelFrame(lower, text="事件日志", padding=8)
        lower.add(queue_frame, weight=1)
        lower.add(log_frame, weight=3)
        queue_columns = ("order", "filename", "result", "confidence")
        self.queue_tree = ttk.Treeview(queue_frame, columns=queue_columns, show="headings", height=9)
        self.queue_tree.heading("order", text="顺序")
        self.queue_tree.heading("filename", text="图片文件名")
        self.queue_tree.heading("result", text="识别")
        self.queue_tree.heading("confidence", text="置信度")
        self.queue_tree.column("order", width=48, minwidth=42, anchor="center", stretch=False)
        self.queue_tree.column("filename", width=220, minwidth=140, anchor="w")
        self.queue_tree.column("result", width=68, minwidth=58, anchor="center", stretch=False)
        self.queue_tree.column("confidence", width=68, minwidth=58, anchor="center", stretch=False)
        queue_scroll = ttk.Scrollbar(queue_frame, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        self.queue_tree.pack(side="left", fill="both", expand=True)
        queue_scroll.pack(side="right", fill="y")
        self.log = tk.Text(log_frame, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.root.after(150, self._set_default_vertical_split)

        self._build_serial_panel(right)

    def _set_default_vertical_split(self) -> None:
        try:
            available = self.left_stack.winfo_height()
            if available > 200:
                self.left_stack.sashpos(0, int(available * 0.70))
        except tk.TclError:
            pass

    def _build_serial_panel(self, parent: ttk.Frame) -> None:
        overview = ttk.LabelFrame(parent, text="结构对应关系", padding=10)
        overview.pack(fill="x", pady=(0, 10))
        ttk.Label(
            overview,
            text=(
                "① 离带拍照/模拟识别\n"
                "② 人工按顺序上料\n"
                "③ 入口传感器只计数\n"
                "④ 出口传感器弹出 FIFO\n"
                "⑤ 全宽漏斗 + 旋转滑槽\n"
                "⑥ 目标矿/废石双料箱"
            ),
            justify="left",
        ).pack(anchor="w")

        serial_box = ttk.LabelFrame(parent, text="实物控制（当前固件：9600）", padding=10)
        serial_box.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(
            serial_box,
            text="启用实物控制",
            variable=self.mirror_var,
            command=self._on_mirror_toggle,
        ).pack(anchor="w")
        self.port_combo = ttk.Combobox(serial_box, textvariable=self.port_var, state="readonly")
        self.port_combo.pack(fill="x", pady=6)
        buttons = ttk.Frame(serial_box)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="刷新", command=self.refresh_ports).pack(side="left")
        ttk.Button(buttons, text="连接/断开", command=self.toggle_serial).pack(side="left", padx=5)
        ttk.Label(serial_box, textvariable=self.serial_status_var, foreground="#475569", wraplength=260).pack(anchor="w", pady=(8, 0))
        if not self.transport.dependency_available:
            self.serial_status_var.set("未安装 pyserial；软件仿真可直接运行。接硬件时执行 pip install -r requirements.txt")

        feedback = ttk.LabelFrame(parent, text="实物传感器反馈", padding=10)
        feedback.pack(fill="x", pady=(0, 10))
        ttk.Label(feedback, textvariable=self.hardware_state_var, wraplength=270).pack(anchor="w")
        ttk.Label(feedback, textvariable=self.hardware_stats_var, wraplength=270).pack(anchor="w", pady=(5, 0))
        ttk.Label(feedback, textvariable=self.entry_sensor_var, foreground="#b45309").pack(anchor="w", pady=(7, 0))
        ttk.Label(feedback, textvariable=self.exit_sensor_var, foreground="#b45309").pack(anchor="w")
        ttk.Label(
            feedback,
            text="D2=入口，D3=出口；此处显示实物固件返回的 IN/OUT。",
            foreground="#64748b",
            wraplength=270,
        ).pack(anchor="w", pady=(6, 0))

        acceptance = ttk.LabelFrame(parent, text="建议现场演示脚本", padding=10)
        acceptance.pack(fill="x")
        ttk.Label(
            acceptance,
            text=(
                "1. 任意选择 1–20 张图片\n"
                "2. 核对模型识别与 FIFO 数量\n"
                "3. 点击开始并按图片顺序上料\n"
                "4. 指出入口/出口计数差异\n"
                "5. 展示废石时滑槽转向\n"
                "6. 可选触发异常保护"
            ),
            justify="left",
            wraplength=270,
        ).pack(anchor="w")

    def _draw_static_scene(self) -> None:
        c = self.canvas
        c.delete("all")
        c.create_text(90, 28, text="离带拍照/识别", font=("Microsoft YaHei UI", 11, "bold"), fill="#334155")
        c.create_rectangle(30, 55, 155, 145, fill="#dbeafe", outline="#2563eb", width=2)
        c.create_oval(68, 73, 118, 123, fill="#1e293b", outline="#0f172a")
        c.create_text(92, 166, text="人工按顺序上料 →", fill="#475569")

        c.create_text(480, 28, text="60 cm × 20 cm 传送带（0.08–0.10 m/s）", font=("Microsoft YaHei UI", 11, "bold"), fill="#334155")
        c.create_rectangle(190, 175, 750, 245, fill="#334155", outline="#0f172a", width=2)
        for x in range(205, 746, 35):
            c.create_line(x, 178, x, 242, fill="#64748b")
        c.create_oval(180, 185, 210, 235, fill="#94a3b8", outline="#475569")
        c.create_oval(730, 185, 760, 235, fill="#94a3b8", outline="#475569")

        self.entry_x = 246
        self.exit_x = 665
        for x, name in ((self.entry_x, "入口：只计数"), (self.exit_x, "出口：FIFO + 滑槽")):
            c.create_rectangle(x - 6, 135, x + 6, 175, fill="#f59e0b", outline="#92400e")
            c.create_line(x, 138, x, 245, fill="#ef4444", dash=(4, 3), width=2)
            c.create_text(x, 118, text=name, font=("Microsoft YaHei UI", 9), fill="#7c2d12")

        c.create_polygon(750, 165, 835, 190, 835, 235, 750, 255, fill="#cbd5e1", outline="#475569", width=2)
        c.create_rectangle(832, 195, 855, 230, fill="#94a3b8", outline="#475569")
        c.create_text(800, 145, text="全宽漏斗", fill="#334155")

        self.servo_line = c.create_line(845, 230, 900, 190, fill="#16a34a", width=8, arrow="last")
        c.create_oval(832, 218, 858, 244, fill="#0f172a", outline="#020617")
        c.create_text(850, 267, text="20 kg 舵机滑槽", fill="#334155")

        c.create_rectangle(910, 75, 990, 155, fill="#dcfce7", outline="#16a34a", width=2)
        c.create_text(950, 115, text="目标矿 A\n默认位", font=("Microsoft YaHei UI", 10, "bold"), fill="#166534")
        c.create_rectangle(910, 285, 990, 365, fill="#fee2e2", outline="#dc2626", width=2)
        c.create_text(950, 325, text="废石 B", font=("Microsoft YaHei UI", 10, "bold"), fill="#991b1b")

    def _load_items(self, items: list[SortItem]) -> None:
        self.machine.load_batch(items)
        self.loaded_items = list(items)
        self.pending = [VisualRock(item) for item in items]
        self.active.clear()
        self.simulation_running = False
        self.simulation_paused = False
        self.sim_clock = 0.0
        self.last_release_at = -999.0
        self.servo_move_at = None
        self.servo_move_target = None
        self.servo_return_at = None
        self.hardware_batch_loaded = False
        self.hardware_armed = False
        self.hardware_run_active = False
        self._reset_hardware_counts()
        self._draw_static_scene()
        self._mirror_batch_if_enabled()
        self._refresh_ui()

    def _items_from_photo_reviews(self) -> list[SortItem]:
        items: list[SortItem] = []
        for review in self.photo_reviews:
            manually_reviewed = review.actual_label is not None
            items.append(
                SortItem(
                    item_id=review.item_id,
                    label=review.actual_label or review.predicted_label,
                    confidence=1.0 if manually_reviewed else review.confidence,
                    source=("MANUAL_REVIEW" if manually_reviewed else "RESNET50_ONNX")
                    + f":{review.display_name}",
                )
            )
        return items

    def _stage_image_paths(self, image_paths: list[Path]) -> list[tuple[Path, str]]:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        batch_dir = self.cache_root / "batches" / stamp
        batch_dir.mkdir(parents=True, exist_ok=False)
        staged: list[tuple[Path, str]] = []
        for index, image_path in enumerate(image_paths, start=1):
            original_name = image_path.name
            destination = batch_dir / f"{index:03d}_{original_name}"
            shutil.copy2(image_path, destination)
            staged.append((destination, original_name))
        return staged

    def _stage_photo_reviews(self, reviews: list[PhotoReview]) -> list[PhotoReview]:
        staged_paths = self._stage_image_paths([review.image_path for review in reviews])
        staged: list[PhotoReview] = []
        for review, (destination, _) in zip(reviews, staged_paths):
            original_name = review.display_name
            staged.append(
                PhotoReview(
                    item_id=review.item_id,
                    image_path=destination,
                    predicted_label=review.predicted_label,
                    confidence=review.confidence,
                    waste_probability=review.waste_probability,
                    threshold=review.threshold,
                    actual_label=review.actual_label,
                    saved=review.saved,
                    original_name=original_name,
                )
            )
        return staged

    def _write_cache_payload(self, payload: dict[str, Any], *, show_error: bool) -> bool:
        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_manifest.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.cache_manifest)
        except OSError:
            self.cache_available = False
            return False
        self.cache_available = True
        return True

    def _write_pending_cache(self, staged: list[tuple[Path, str]], *, show_error: bool) -> bool:
        payload = {
            "version": 1,
            "state": "pending_analysis",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "model_path": str(self.MODEL_PATH),
            "photos": [
                {
                    "item_id": f"P{index:02d}",
                    "image_path": str(image_path.resolve()),
                    "original_name": original_name,
                }
                for index, (image_path, original_name) in enumerate(staged, start=1)
            ],
        }
        return self._write_cache_payload(payload, show_error=show_error)

    def _write_cached_batch(self, *, show_error: bool = False) -> bool:
        if not self.photo_reviews:
            return False
        payload = {
            "version": 1,
            "state": "ready",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "model_path": str(self.MODEL_PATH),
            "photos": [
                {
                    "item_id": review.item_id,
                    "image_path": str(review.image_path.resolve()),
                    "original_name": review.display_name,
                    "predicted_label": review.predicted_label.value,
                    "confidence": review.confidence,
                    "waste_probability": review.waste_probability,
                    "threshold": review.threshold,
                    "actual_label": review.actual_label.value if review.actual_label else None,
                    "saved": review.saved,
                }
                for review in self.photo_reviews
            ],
        }
        written = self._write_cache_payload(payload, show_error=show_error)
        if written:
            self._prune_old_staged_batches()
        return written

    def _prune_old_staged_batches(self) -> None:
        """Keep only the current complete batch inside the app-owned staging folder."""
        batches_root = self.cache_root / "batches"
        try:
            resolved_root = batches_root.resolve()
            active_dirs: set[Path] = set()
            for review in self.photo_reviews:
                resolved_image = review.image_path.resolve()
                if resolved_image.parent.parent != resolved_root:
                    return
                active_dirs.add(resolved_image.parent)
            if not active_dirs or not batches_root.is_dir():
                return
            for child in batches_root.iterdir():
                resolved_child = child.resolve()
                if resolved_child in active_dirs or resolved_child.parent != resolved_root or not child.is_dir():
                    continue
                try:
                    datetime.strptime(child.name, "%Y%m%d_%H%M%S_%f")
                except ValueError:
                    continue
                shutil.rmtree(child)
        except (OSError, shutil.Error):
            pass

    def restore_cached_batch(self) -> None:
        self._restore_cached_batch(show_message=True)

    def _restore_cached_batch(self, *, show_message: bool) -> bool:
        if not self.cache_manifest.is_file():
            if show_message:
                messagebox.showinfo("没有暂存批次", "目前没有可恢复的图片批次。")
            return False
        try:
            payload = json.loads(self.cache_manifest.read_text(encoding="utf-8"))
            records = payload["photos"]
            pending_analysis = payload.get("state") == "pending_analysis" or any(
                "predicted_label" not in record for record in records
            )
            classifier = self._get_image_classifier() if pending_analysis else None
            reviews: list[PhotoReview] = []
            for index, record in enumerate(records, start=1):
                image_path = Path(record["image_path"])
                if not image_path.is_file():
                    raise FileNotFoundError(f"暂存图片不存在：{image_path}")
                if pending_analysis:
                    assert classifier is not None
                    prediction = classifier.predict(image_path)
                    record = {
                        **record,
                        "item_id": record.get("item_id", f"P{index:02d}"),
                        "predicted_label": prediction.label.value,
                        "confidence": prediction.confidence,
                        "waste_probability": prediction.waste_probability,
                        "threshold": prediction.threshold,
                    }
                actual_value = record.get("actual_label")
                reviews.append(
                    PhotoReview(
                        item_id=str(record["item_id"]),
                        image_path=image_path,
                        predicted_label=MaterialClass(record["predicted_label"]),
                        confidence=float(record["confidence"]),
                        waste_probability=float(record["waste_probability"]),
                        threshold=float(record["threshold"]),
                        actual_label=MaterialClass(actual_value) if actual_value else None,
                        saved=bool(record.get("saved", False)),
                        original_name=str(record.get("original_name") or image_path.name),
                    )
                )
            if not reviews:
                raise ValueError("暂存批次为空")
        except Exception as exc:
            if not show_message:
                self.model_status_var.set("检测到未完成的暂存批次｜可点击“恢复暂存批次”重试")
            if show_message:
                messagebox.showwarning("无法恢复暂存", f"暂存记录不完整或已损坏：\n{exc}")
            return False

        self.photo_reviews = reviews
        if pending_analysis:
            self._write_cached_batch()
        self._load_items(self._items_from_photo_reviews())
        saved_at = payload.get("saved_at", "未知时间")
        self.model_status_var.set(f"已恢复暂存批次 {len(reviews)} 张｜暂存时间 {saved_at}")
        self._refresh_feedback_tree()
        if show_message:
            messagebox.showinfo("暂存已恢复", f"已恢复 {len(reviews)} 张图片及其识别结果。")
        return True

    def import_images(self) -> None:
        if self.simulation_running or self.simulation_paused:
            messagebox.showinfo("批次尚未结束", "请先复位当前批次，再选择新的图片。")
            return
        selected = filedialog.askopenfilenames(
            title=f"选择本批图片（1–{self.MAX_HARDWARE_BATCH} 张，按文件名排序）",
            filetypes=(
                ("图片文件", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff"),
                ("JPEG", "*.jpg *.jpeg"),
                ("PNG", "*.png"),
                ("所有文件", "*.*"),
            ),
        )
        if not selected:
            return
        selected = tuple(sorted(selected, key=lambda value: Path(value).name.lower()))
        if len(selected) > self.MAX_HARDWARE_BATCH:
            messagebox.showerror("照片过多", f"当前实物固件每批最多支持 {self.MAX_HARDWARE_BATCH} 块。")
            return
        if not self.MODEL_PATH.is_file():
            messagebox.showerror("模型不存在", f"没有找到模型：\n{self.MODEL_PATH}")
            return

        source_paths = [Path(image_name) for image_name in selected]
        cache_problem = False
        try:
            staged_images = self._stage_image_paths(source_paths)
            if not self._write_pending_cache(staged_images, show_error=False):
                cache_problem = True
        except (OSError, shutil.Error):
            staged_images = [(path, path.name) for path in source_paths]
            self.cache_available = False
            cache_problem = True

        try:
            classifier = self._get_image_classifier()
            reviews: list[PhotoReview] = []
            result_lines: list[str] = []
            for batch_index, (image_path, original_name) in enumerate(staged_images, start=1):
                prediction = classifier.predict(image_path)
                item_id = f"P{batch_index:02d}"
                reviews.append(
                    PhotoReview(
                        item_id=item_id,
                        image_path=image_path,
                        predicted_label=prediction.label,
                        confidence=prediction.confidence,
                        waste_probability=prediction.waste_probability,
                        threshold=prediction.threshold,
                        original_name=original_name,
                    )
                )
                result_lines.append(
                    f"[MODEL] {item_id} {original_name} → {prediction.label.chinese} "
                    f"｜置信度 {prediction.confidence:.1%}｜废石概率 {prediction.waste_probability:.1%}"
                )
        except Exception as exc:
            self.model_status_var.set(f"模型识别失败（图片已暂存）：{exc}")
            messagebox.showerror("模型识别失败", f"图片已经暂存，无需重新选择。\n\n{exc}")
            return

        self.photo_reviews = reviews
        items = self._items_from_photo_reviews()
        if not self._write_cached_batch(show_error=False):
            cache_problem = True
        self._load_items(items)
        self.model_status_var.set(
            f"已识别 {len(items)} 张｜目标矿 {sum(item.label is MaterialClass.ORE for item in items)}｜"
            f"废石 {sum(item.label is MaterialClass.WASTE for item in items)}｜请按列表从上到下上料"
        )
        self.log.configure(state="normal")
        for line in result_lines:
            self.log.insert("end", line + "\n")
        if cache_problem:
            self.log.insert("end", "[CACHE] 暂存不可用；本次仍使用原图片完成识别，不影响模型结果。\n")
        self.log.insert("end", "[MODEL] 提醒：当前模型仅用于小样本技术演示，请人工复核低置信度结果。\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self._refresh_feedback_tree()

    def _get_image_classifier(self) -> Any:
        if self.image_classifier is None:
            self.model_status_var.set("模型：正在加载 ONNX，请稍候……")
            self.root.update_idletasks()
            from .ml.onnx_classifier import OnnxOreClassifier

            self.image_classifier = OnnxOreClassifier(self.MODEL_PATH)
            self.model_status_var.set("模型：ResNet50 ONNX 已加载（CPU）")
        return self.image_classifier

    def open_feedback_window(self) -> None:
        if not self.photo_reviews:
            messagebox.showinfo("没有识别记录", "请先点击“导入照片并识别”。")
            return
        if self.feedback_window is not None and self.feedback_window.winfo_exists():
            self.feedback_window.deiconify()
            self.feedback_window.lift()
            self._refresh_feedback_tree()
            return

        window = tk.Toplevel(self.root)
        window.title("模型识别反馈与人工纠错")
        window.geometry("1040x460")
        window.minsize(900, 380)
        self.feedback_window = window

        ttk.Label(
            window,
            text="选择一行后标记真实类别。纠错会立即更新待分选 FIFO；保存反馈后可用于后续重训。",
            padding=(12, 10),
        ).pack(fill="x")

        columns = ("item", "file", "prediction", "confidence", "waste_probability", "actual")
        tree = ttk.Treeview(window, columns=columns, show="headings", selectmode="browse")
        headings = {
            "item": "编号",
            "file": "图片",
            "prediction": "模型预测",
            "confidence": "置信度",
            "waste_probability": "废石概率",
            "actual": "人工真实类别",
        }
        widths = {"item": 70, "file": 330, "prediction": 100, "confidence": 90, "waste_probability": 100, "actual": 150}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="center" if column != "file" else "w")
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.feedback_tree = tree

        buttons = ttk.Frame(window, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="预测正确", command=self._mark_selected_prediction_correct).pack(side="left", padx=3)
        ttk.Button(
            buttons,
            text="实际为目标矿",
            command=lambda: self._mark_selected_actual(MaterialClass.ORE),
        ).pack(side="left", padx=3)
        ttk.Button(
            buttons,
            text="实际为废石",
            command=lambda: self._mark_selected_actual(MaterialClass.WASTE),
        ).pack(side="left", padx=3)
        ttk.Button(buttons, text="清除该条反馈", command=self._clear_selected_feedback).pack(side="left", padx=3)
        ttk.Button(buttons, text="保存反馈样本", command=self.save_feedback).pack(side="right", padx=3)

        window.protocol("WM_DELETE_WINDOW", self._close_feedback_window)
        self._refresh_feedback_tree()

    def _close_feedback_window(self) -> None:
        if self.feedback_window is not None:
            self.feedback_window.destroy()
        self.feedback_window = None
        self.feedback_tree = None

    def _refresh_feedback_tree(self) -> None:
        tree = self.feedback_tree
        if tree is None or not tree.winfo_exists():
            return
        selected = tree.selection()
        selected_id = selected[0] if selected else None
        children = tree.get_children()
        if children:
            tree.delete(*children)
        for review in self.photo_reviews:
            if review.actual_label is None:
                actual = "未反馈"
            else:
                actual = review.actual_label.chinese + ("（已保存）" if review.saved else "")
            tree.insert(
                "",
                "end",
                iid=review.item_id,
                values=(
                    review.item_id,
                    review.display_name,
                    review.predicted_label.chinese,
                    f"{review.confidence:.1%}",
                    f"{review.waste_probability:.1%}",
                    actual,
                ),
            )
        if selected_id and tree.exists(selected_id):
            tree.selection_set(selected_id)

    def _selected_photo_review(self) -> PhotoReview | None:
        tree = self.feedback_tree
        if tree is None:
            return None
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("请选择图片", "请先在反馈表格中选择一条识别记录。")
            return None
        item_id = selected[0]
        return next((review for review in self.photo_reviews if review.item_id == item_id), None)

    def _mark_selected_prediction_correct(self) -> None:
        review = self._selected_photo_review()
        if review is not None:
            self._set_review_actual_label(review, review.predicted_label)

    def _mark_selected_actual(self, label: MaterialClass) -> None:
        review = self._selected_photo_review()
        if review is not None:
            self._set_review_actual_label(review, label)

    def _clear_selected_feedback(self) -> None:
        review = self._selected_photo_review()
        if review is None:
            return
        if self.simulation_running:
            messagebox.showinfo("批次正在运行", "请先复位，再修改识别反馈。")
            return
        review.actual_label = None
        review.saved = False
        self._apply_photo_reviews_to_batch()

    def _set_review_actual_label(self, review: PhotoReview, label: MaterialClass) -> None:
        if self.simulation_running:
            messagebox.showinfo("批次正在运行", "请先复位，再修改识别反馈。")
            return
        review.actual_label = label
        review.saved = False
        self._apply_photo_reviews_to_batch()

    def _apply_photo_reviews_to_batch(self) -> None:
        items = self._items_from_photo_reviews()
        self._load_items(items)
        reviewed = sum(review.actual_label is not None for review in self.photo_reviews)
        corrected = sum(
            review.actual_label is not None and review.actual_label is not review.predicted_label
            for review in self.photo_reviews
        )
        self.model_status_var.set(f"人工反馈 {reviewed}/{len(self.photo_reviews)}｜纠错 {corrected}｜FIFO 已更新")
        self._write_cached_batch()
        self._refresh_feedback_tree()

    def save_feedback(self) -> None:
        pending = [review for review in self.photo_reviews if review.actual_label is not None and not review.saved]
        if not pending:
            messagebox.showinfo("没有待保存反馈", "请先标记真实类别，或当前反馈已经保存。")
            return

        project_root = Path(__file__).resolve().parents[1]
        feedback_root = project_root / "data" / "feedback"
        image_root = feedback_root / "images"
        csv_path = feedback_root / "feedback.csv"
        fieldnames = [
            "saved_at",
            "item_id",
            "source_image",
            "saved_image",
            "predicted_label",
            "actual_label",
            "is_correct",
            "confidence",
            "waste_probability",
            "waste_threshold",
            "model_path",
        ]

        saved_count = 0
        try:
            feedback_root.mkdir(parents=True, exist_ok=True)
            write_header = not csv_path.exists()
            with csv_path.open("a", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                if write_header:
                    writer.writeheader()
                for review in pending:
                    assert review.actual_label is not None
                    label_dir = image_root / review.actual_label.value.lower()
                    label_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    destination = label_dir / f"{stamp}_{review.item_id}_{review.display_name}"
                    shutil.copy2(review.image_path, destination)
                    writer.writerow(
                        {
                            "saved_at": datetime.now().isoformat(timespec="seconds"),
                            "item_id": review.item_id,
                            "source_image": str(review.image_path.resolve()),
                            "saved_image": destination.resolve().relative_to(project_root.resolve()).as_posix(),
                            "predicted_label": review.predicted_label.value,
                            "actual_label": review.actual_label.value,
                            "is_correct": review.predicted_label is review.actual_label,
                            "confidence": f"{review.confidence:.8f}",
                            "waste_probability": f"{review.waste_probability:.8f}",
                            "waste_threshold": f"{review.threshold:.8f}",
                            "model_path": str(self.MODEL_PATH.resolve()),
                        }
                    )
                    review.saved = True
                    saved_count += 1
        except (OSError, shutil.Error) as exc:
            messagebox.showerror("保存反馈失败", f"无法保存反馈样本：\n{exc}")
            return

        self._refresh_feedback_tree()
        self._write_cached_batch()
        self.log.configure(state="normal")
        self.log.insert("end", f"[FEEDBACK] 已保存 {saved_count} 条反馈：{csv_path}\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        messagebox.showinfo("反馈已保存", f"已保存 {saved_count} 条反馈。\n\n清单：{csv_path}\n图片：{image_root}")

    def start(self) -> None:
        if self.simulation_paused:
            if self._hardware_sync_active():
                messagebox.showinfo("需要重新加载", "实物固件不支持继续命令；请重新加载批次后再开始。")
                return
            self.simulation_paused = False
            self.simulation_running = True
            self.last_wall_time = time.monotonic()
            return
        if self.machine.state is not MachineState.READY:
            messagebox.showinfo("无法启动", "请先选择本批图片并完成模型识别。")
            return
        if self._hardware_sync_active():
            if time.monotonic() < self.hardware_ready_at:
                messagebox.showinfo("单片机启动中", "串口刚连接，请等待约 2 秒后再点击开始。")
                return
            if not self.hardware_batch_loaded:
                if not self._mirror_batch_if_enabled():
                    messagebox.showerror("实物批次未就绪", "未能向实物端装载批次，请检查串口连接后重新加载。")
                    return
        try:
            self.machine.start()
        except RuntimeError as exc:
            messagebox.showerror("无法启动", str(exc))
            return
        self.simulation_running = True
        self.hardware_run_active = self._hardware_sync_active()
        self.last_wall_time = time.monotonic()
        self._send_if_mirroring("START")
        self._refresh_ui()

    def toggle_pause(self) -> None:
        if not self.simulation_running and not self.simulation_paused:
            return
        if self._hardware_sync_active():
            if not messagebox.askyesno(
                "实物固件不支持暂停",
                "当前实物固件没有 PAUSE/RESUME。为安全起见将停止电机并清空实物批次；是否继续？",
            ):
                return
            self.reset()
            self.serial_status_var.set("实物已停机；请重新加载批次")
            return
        self.simulation_paused = not self.simulation_paused
        self.simulation_running = not self.simulation_paused
        self.last_wall_time = time.monotonic()

    def reset(self, preserve_batch: bool = True) -> None:
        self._stop_hardware()
        self.machine.reset()
        self.loaded_items.clear()
        self.pending.clear()
        self.active.clear()
        self.simulation_running = False
        self.simulation_paused = False
        self.sim_clock = 0.0
        self.servo_move_at = None
        self.servo_move_target = None
        self.servo_return_at = None
        self.hardware_batch_loaded = False
        self.hardware_armed = False
        self.hardware_run_active = False
        self._reset_hardware_counts()
        self._draw_static_scene()
        if preserve_batch and self.photo_reviews:
            self._load_items(self._items_from_photo_reviews())
            self.model_status_var.set(f"已从暂存重新装载本批 {len(self.photo_reviews)} 张图片")
        else:
            self._refresh_ui()

    def emergency_stop(self) -> None:
        self._stop_hardware()
        self.machine.emergency_stop()
        self.simulation_running = False
        self.simulation_paused = False
        self.hardware_batch_loaded = False
        self.hardware_armed = False
        self.hardware_run_active = False
        if self._hardware_sync_active():
            self.serial_status_var.set("已发送串口停机；实体急停仍应独立断电")
        self._refresh_ui()

    def demo_extra_object(self) -> None:
        if not self.loaded_items:
            messagebox.showinfo("没有图片批次", "请先选择图片并完成模型识别，再演示多放一块保护。")
            return
        if self.simulation_running or self.simulation_paused:
            messagebox.showinfo("批次尚未结束", "请先复位，再演示多放一块保护。")
            return
        self._load_items(list(self.loaded_items))
        extra = SortItem("EXTRA", MaterialClass.WASTE, 1.0, "UNPLANNED_OBJECT")
        self.pending.append(VisualRock(extra, extra=True))
        self.start()

    def demo_empty_queue(self) -> None:
        self.reset(preserve_batch=False)
        self.machine.start_diagnostic_run()
        self.machine.trigger_exit(0.0)
        self._refresh_ui()

    def _tick(self) -> None:
        now = time.monotonic()
        wall_dt = min(now - self.last_wall_time, 0.1)
        self.last_wall_time = now
        if self.simulation_running and not self.simulation_paused:
            sim_dt = wall_dt * self.speed_var.get()
            self.sim_clock += sim_dt
            if self.hardware_run_active:
                self._advance_hardware_rocks(sim_dt)
            else:
                self._release_rock_if_due()
                self._advance_rocks(sim_dt)
                if self.servo_move_at is not None and self.sim_clock >= self.servo_move_at:
                    if self.servo_move_target is not None:
                        self.machine.move_servo(self.servo_move_target)
                        self.servo_return_at = self.sim_clock + 0.5
                    self.servo_move_at = None
                    self.servo_move_target = None
                if self.servo_return_at is not None and self.sim_clock >= self.servo_return_at:
                    self.machine.return_servo_default()
                    self.servo_return_at = None
            if self.machine.state in {MachineState.ERROR, MachineState.ESTOP}:
                self.servo_move_at = None
                self.servo_move_target = None
                self.simulation_running = False
            elif not self.pending and not self.active and self.machine.state is MachineState.COMPLETE:
                self.simulation_running = False
                self.hardware_run_active = False
        self._render_rocks()
        self._refresh_ui()
        self.root.after(self.TICK_MS, self._tick)

    def _release_rock_if_due(self) -> None:
        if not self.pending:
            return
        if self.sim_clock - self.last_release_at >= self.RELEASE_INTERVAL_SECONDS:
            rock = self.pending.pop(0)
            self.active.append(rock)
            self.last_release_at = self.sim_clock

    def _advance_rocks(self, sim_dt: float) -> None:
        finished: list[VisualRock] = []
        for rock in self.active:
            previous = rock.progress
            rock.progress += sim_dt / self.BELT_TRAVEL_SECONDS
            if previous < self.ENTRY_PROGRESS <= rock.progress and not rock.entry_fired:
                rock.entry_fired = True
                self.machine.trigger_entry(self.sim_clock)
            if previous < self.EXIT_PROGRESS <= rock.progress and not rock.exit_fired:
                rock.exit_fired = True
                action = self.machine.trigger_exit(self.sim_clock)
                if action is not None:
                    rock.route = action.servo_target
                    if action.servo_target is MaterialClass.WASTE:
                        self.servo_move_at = self.sim_clock + 0.55
                        self.servo_move_target = MaterialClass.WASTE
            if rock.progress >= 1.08:
                finished.append(rock)
        for rock in finished:
            self.active.remove(rock)

    def _advance_hardware_rocks(self, sim_dt: float) -> None:
        """实物模式只移动已被入口传感器发现的矿石，并在出口事件前等待。"""
        finished: list[VisualRock] = []
        for rock in self.active:
            if rock.exit_fired:
                rock.progress += sim_dt / self.BELT_TRAVEL_SECONDS
            else:
                rock.progress = min(
                    rock.progress + sim_dt / self.BELT_TRAVEL_SECONDS,
                    self.HARDWARE_EXIT_WAIT_PROGRESS,
                )
            if rock.progress >= 1.08:
                finished.append(rock)
        for rock in finished:
            self.active.remove(rock)

    def _on_hardware_entry(self, count: int) -> None:
        if not self.hardware_run_active or self.machine.state is not MachineState.RUNNING:
            return
        if self.pending:
            rock = self.pending.pop(0)
        else:
            extra = SortItem(f"HW{count:02d}", MaterialClass.ORE, 0.0, "UNPLANNED_HARDWARE_OBJECT")
            rock = VisualRock(extra, extra=True)
        rock.progress = self.ENTRY_PROGRESS
        rock.entry_fired = True
        self.active.append(rock)
        self.machine.trigger_entry(time.monotonic())

    def _on_hardware_exit(self, count: int, label_text: str) -> None:
        if not self.hardware_run_active or self.machine.state is not MachineState.RUNNING:
            return
        rock = next((item for item in self.active if item.entry_fired and not item.exit_fired), None)
        if rock is None:
            return
        rock.exit_fired = True
        rock.progress = max(rock.progress, self.HARDWARE_EXIT_WAIT_PROGRESS)
        action = self.machine.trigger_exit(time.monotonic())
        try:
            actual_label = MaterialClass(label_text)
        except ValueError:
            actual_label = action.servo_target if action is not None else rock.item.label
        rock.route = actual_label
        self.machine.move_servo(actual_label)

    def _render_rocks(self) -> None:
        self.canvas.delete("rock")
        for rock in self.active:
            progress = max(0.0, min(rock.progress, 1.08))
            if progress <= 0.78:
                x = 190 + progress / 0.78 * 600
                y = 210
            elif progress <= 0.90:
                ratio = (progress - 0.78) / 0.12
                x = 790 + ratio * 58
                y = 210 + ratio * 28
            else:
                ratio = min((progress - 0.90) / 0.18, 1.0)
                x = 848 + ratio * 100
                target_y = 115 if rock.route is MaterialClass.ORE else 325
                y = 238 + ratio * (target_y - 238)
            color = "#16a34a" if rock.item.label is MaterialClass.ORE else "#dc2626"
            if rock.extra:
                color = "#7c3aed"
            self.canvas.create_oval(x - 13, y - 11, x + 13, y + 11, fill=color, outline="#0f172a", width=2, tags="rock")
            self.canvas.create_text(x, y - 20, text=rock.item.item_id, font=("Consolas", 8, "bold"), tags="rock")

        if self.machine.servo_target is MaterialClass.WASTE:
            self.canvas.coords(self.servo_line, 845, 230, 900, 310)
            self.canvas.itemconfigure(self.servo_line, fill="#dc2626")
        else:
            self.canvas.coords(self.servo_line, 845, 230, 900, 190)
            self.canvas.itemconfigure(self.servo_line, fill="#16a34a")

    def _refresh_ui(self) -> None:
        snap = self.machine.snapshot()
        state_text = {
            "IDLE": "待机",
            "READY": "批次就绪",
            "RUNNING": "运行中",
            "COMPLETE": "批次完成",
            "ERROR": "故障停机",
            "ESTOP": "急停",
        }[snap["state"]]
        if self.simulation_paused:
            state_text += "（已暂停）"
        if snap["error"]:
            state_text += f"｜{snap['error']}"
        self.status_var.set(f"状态：{state_text}｜滑槽：{'目标矿 A' if snap['servo_target'] == 'ORE' else '废石 B'}")
        self.stats_var.set(
            f"入口 {snap['in_count']}/{snap['expected']}  出口 {snap['out_count']}/{snap['expected']}  "
            f"目标矿 {snap['ore_count']}  废石 {snap['waste_count']}"
        )
        filename_by_item = {review.item_id: review.display_name for review in self.photo_reviews}
        for row_id in self.queue_tree.get_children():
            self.queue_tree.delete(row_id)
        if self.machine.queue:
            for item in self.machine.queue:
                filename = filename_by_item.get(item.item_id)
                if filename is None and ":" in item.source:
                    filename = item.source.rsplit(":", 1)[-1]
                self.queue_tree.insert(
                    "",
                    "end",
                    values=(
                        item.item_id.removeprefix("P"),
                        filename or "（未知图片）",
                        item.label.chinese,
                        f"{item.confidence:.0%}",
                    ),
                )
        else:
            self.queue_tree.insert("", "end", values=("—", "（空）", "—", "—"))
        self._render_new_events()

    def _render_new_events(self) -> None:
        new_events = [event for event in self.machine.events if event.sequence > self.last_event_rendered]
        if not new_events:
            return
        self.log.configure(state="normal")
        for event in new_events:
            self.log.insert("end", f"[{event.sequence:03d}] {event.kind:<6} {event.message}\n")
            self.last_event_rendered = event.sequence
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh_ports(self) -> None:
        ports = self.transport.ports()
        self.port_combo["values"] = ports
        if ports:
            self.port_var.set(ports[0])
            self.serial_status_var.set(f"发现 {len(ports)} 个串口")
        elif self.transport.dependency_available:
            self.serial_status_var.set("未发现串口设备")

    def _hardware_sync_active(self) -> bool:
        return self.mirror_var.get() and self.transport.connected

    def _on_mirror_toggle(self) -> None:
        if not self.mirror_var.get():
            self.serial_status_var.set("实物控制已关闭；仅运行软件仿真")
            return
        if not self.transport.connected:
            self.serial_status_var.set("实物控制已启用；请连接串口")
            return
        if self.machine.state is MachineState.READY:
            self.hardware_batch_loaded = False
            self._mirror_batch_if_enabled()
        else:
            self.serial_status_var.set("实物控制已启用；请重新加载批次")

    def toggle_serial(self) -> None:
        if self.transport.connected:
            self._stop_hardware()
            self.transport.close()
            self.serial_status_var.set("已断开")
            self.hardware_state_var.set("实物状态：未连接")
            self.hardware_batch_loaded = False
            self.hardware_armed = False
            self.hardware_run_active = False
            self.simulation_running = False
            return
        if not self.port_var.get():
            self.refresh_ports()
        if not self.port_var.get():
            messagebox.showinfo("无串口", self.serial_status_var.get())
            return
        try:
            self.transport.connect(self.port_var.get())
            self.hardware_ready_at = time.monotonic() + 1.8
            self.hardware_batch_loaded = False
            self.hardware_armed = False
            self.hardware_run_active = False
            self._reset_hardware_counts()
            self.hardware_state_var.set("实物状态：串口已连接，等待单片机启动")
            self.serial_status_var.set(f"已连接 {self.port_var.get()} @ {HARDWARE_BAUDRATE}，等待约 2 秒")
            self.root.after(1800, self._finish_hardware_connect)
        except Exception as exc:  # 串口异常需要直接呈现给演示人员
            messagebox.showerror("连接失败", str(exc))

    def _finish_hardware_connect(self) -> None:
        if not self.transport.connected:
            return
        self.hardware_state_var.set("实物状态：已连接")
        self.serial_status_var.set(f"已连接 {self.port_var.get()} @ {HARDWARE_BAUDRATE}")
        if self.machine.state is MachineState.READY:
            self._mirror_batch_if_enabled()

    def _send_if_mirroring(self, command: str) -> bool:
        if not self.mirror_var.get() or not self.transport.connected:
            return False
        try:
            self.transport.send(command)
            return True
        except Exception as exc:
            self.serial_status_var.set(f"发送失败：{exc}")
            return False

    def _stop_hardware(self) -> None:
        if not self._hardware_sync_active():
            return
        self._send_if_mirroring(HARDWARE_STOP_COMMAND)
        self.hardware_batch_loaded = False
        self.hardware_armed = False
        self.hardware_state_var.set("实物状态：已发送停机/清空批次")

    def _mirror_batch_if_enabled(self) -> bool:
        if not self.mirror_var.get() or not self.transport.connected:
            return False
        if time.monotonic() < self.hardware_ready_at:
            self.serial_status_var.set("单片机启动中；稍后将自动装载批次")
            return False
        if self.machine.state is not MachineState.READY or not self.loaded_items:
            return False
        if self.hardware_batch_loaded:
            return True
        self.hardware_batch_loaded = True
        self.hardware_armed = False
        self.hardware_state_var.set("实物状态：正在装载批次")
        for command in batch_commands(self.loaded_items):
            if not self._send_if_mirroring(command):
                self.hardware_batch_loaded = False
                return False
        return True

    def _reset_hardware_counts(self) -> None:
        self.hardware_in_count = 0
        self.hardware_out_count = 0
        self.hardware_ore_count = 0
        self.hardware_waste_count = 0
        self.hardware_error_count = 0
        self.entry_sensor_var.set("入口传感器：等待")
        self.exit_sensor_var.set("出口传感器：等待")
        self._refresh_hardware_stats()

    def _refresh_hardware_stats(self) -> None:
        self.hardware_stats_var.set(
            f"入口 {self.hardware_in_count}｜出口 {self.hardware_out_count}｜"
            f"目标矿 {self.hardware_ore_count}｜废石 {self.hardware_waste_count}｜"
            f"错误 {self.hardware_error_count}"
        )

    def _clear_entry_sensor_flash(self) -> None:
        self.entry_sensor_var.set(f"入口传感器：等待（累计 {self.hardware_in_count}）")

    def _clear_exit_sensor_flash(self) -> None:
        self.exit_sensor_var.set(f"出口传感器：等待（累计 {self.hardware_out_count}）")

    def _handle_hardware_line(self, line: str) -> None:
        try:
            message = parse_line(line)
        except ValueError:
            return

        fields = message.fields
        if message.kind == "READY":
            self.hardware_state_var.set("实物状态：READY")
        elif message.kind == "OK" and len(fields) >= 2 and fields[0].upper() == "BATCH":
            self.hardware_state_var.set(f"实物状态：批次已初始化（{fields[1]} 块）")
        elif message.kind == "QUEUED" and fields:
            self.hardware_state_var.set(f"实物状态：装载队列 {fields[0]}")
        elif message.kind == "ARMED":
            self.hardware_armed = True
            self.hardware_state_var.set("实物状态：批次就绪，等待 START")
        elif message.kind == "RUNNING":
            self.hardware_state_var.set("实物状态：传送带运行中")
        elif message.kind == "IN" and fields:
            try:
                self.hardware_in_count = int(fields[0])
            except ValueError:
                return
            self._on_hardware_entry(self.hardware_in_count)
            self.entry_sensor_var.set(f"入口传感器：已触发（累计 {self.hardware_in_count}）")
            self.root.after(500, self._clear_entry_sensor_flash)
            self._refresh_hardware_stats()
        elif message.kind == "OUT" and fields:
            try:
                self.hardware_out_count = int(fields[0])
            except ValueError:
                return
            label = fields[1].upper() if len(fields) >= 2 else ""
            if label == "ORE":
                self.hardware_ore_count += 1
            elif label == "WASTE":
                self.hardware_waste_count += 1
            self._on_hardware_exit(self.hardware_out_count, label)
            self.exit_sensor_var.set(f"出口传感器：已触发（{label or '未知'}）")
            self.root.after(500, self._clear_exit_sensor_flash)
            self._refresh_hardware_stats()
        elif message.kind == "DONE" and len(fields) >= 4:
            try:
                self.hardware_in_count = int(fields[0])
                self.hardware_ore_count = int(fields[1])
                self.hardware_waste_count = int(fields[2])
                self.hardware_error_count = int(fields[3])
                self.hardware_out_count = self.hardware_ore_count + self.hardware_waste_count
            except ValueError:
                return
            self.hardware_state_var.set("实物状态：批次完成")
            self.hardware_armed = False
            self.machine.return_servo_default()
            self._refresh_hardware_stats()
        elif message.kind == "WARN":
            self.hardware_error_count += 1
            self.hardware_state_var.set(f"实物状态：警告（传送带继续）｜{' '.join(fields)}")
            self.exit_sensor_var.set("出口传感器：无入口配对，已忽略本次脉冲")
            self.root.after(1200, self._clear_exit_sensor_flash)
            self._refresh_hardware_stats()
        elif message.kind == "ERROR":
            self.hardware_error_count += 1
            self.hardware_state_var.set(f"实物状态：故障｜{' '.join(fields)}")
            self.simulation_running = False
            self.hardware_run_active = False
            self._refresh_hardware_stats()

    def _poll_serial(self) -> None:
        try:
            lines = self.transport.poll()
            if lines:
                self.log.configure(state="normal")
                for line in lines:
                    self.log.insert("end", f"[MCU] {line}\n")
                    self._handle_hardware_line(line)
                self.log.see("end")
                self.log.configure(state="disabled")
        except Exception as exc:
            self.serial_status_var.set(f"串口读取失败：{exc}")
            self.transport.close()
            self.simulation_running = False
            self.hardware_run_active = False
        self.root.after(100, self._poll_serial)

    def _on_close(self) -> None:
        self._stop_hardware()
        self.transport.close()
        self.root.destroy()


def launch() -> None:
    root = tk.Tk()
    SortingDemoApp(root)
    root.mainloop()
