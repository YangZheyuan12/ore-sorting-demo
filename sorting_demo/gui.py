from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from .classifier_adapter import demo_batch
from .domain import MachineState, MaterialClass, SortItem, SortingMachine
from .protocol import batch_commands
from .transport import SerialTransport


@dataclass
class VisualRock:
    item: SortItem
    progress: float = 0.0
    entry_fired: bool = False
    exit_fired: bool = False
    route: MaterialClass | None = None
    extra: bool = False


class SortingDemoApp:
    TICK_MS = 33
    BELT_TRAVEL_SECONDS = 6.0  # 0.6 m / 0.1 m/s
    RELEASE_INTERVAL_SECONDS = 2.4
    ENTRY_PROGRESS = 0.10
    EXIT_PROGRESS = 0.75

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("矿石智能分选演示台｜软件仿真 + 可选串口")
        self.root.geometry("1320x850")
        self.root.minsize(1150, 760)

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
        self.next_manual_id = 1

        self.speed_var = tk.DoubleVar(value=4.0)
        self.mirror_var = tk.BooleanVar(value=False)
        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="待机")
        self.stats_var = tk.StringVar()
        self.queue_var = tk.StringVar()
        self.serial_status_var = tk.StringVar(value="未连接")

        self._build_style()
        self._build_ui()
        self._draw_static_scene()
        self.load_standard_batch()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.TICK_MS, self._tick)
        self.root.after(100, self._poll_serial)

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Stat.TLabel", font=("Consolas", 11, "bold"), padding=8)
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(16, 12, 16, 8))
        header.pack(fill="x")
        ttk.Label(header, text="矿石智能分选演示台", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="当前分类为固定演示数据，不代表真实选矿准确率",
            foreground="#a33a14",
            style="Sub.TLabel",
        ).pack(side="right", padx=8)

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        left = ttk.Frame(body)
        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        body.add(left, weight=4)
        body.add(right, weight=1)

        status = ttk.Frame(left)
        status.pack(fill="x", pady=(0, 8))
        ttk.Label(status, textvariable=self.status_var, style="Stat.TLabel").pack(side="left")
        ttk.Label(status, textvariable=self.stats_var, style="Stat.TLabel").pack(side="right")

        self.canvas = tk.Canvas(left, height=420, background="#f6f8fb", highlightthickness=1, highlightbackground="#cbd5e1")
        self.canvas.pack(fill="x")

        controls = ttk.LabelFrame(left, text="演示控制", padding=10)
        controls.pack(fill="x", pady=10)
        row1 = ttk.Frame(controls)
        row1.pack(fill="x")
        ttk.Button(row1, text="加载标准 5 块", command=self.load_standard_batch, style="Accent.TButton").pack(side="left", padx=3)
        ttk.Button(row1, text="+ 目标矿", command=lambda: self.add_manual(MaterialClass.ORE)).pack(side="left", padx=3)
        ttk.Button(row1, text="+ 废石", command=lambda: self.add_manual(MaterialClass.WASTE)).pack(side="left", padx=3)
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

        lower = ttk.Panedwindow(left, orient="horizontal")
        lower.pack(fill="both", expand=True)
        queue_frame = ttk.LabelFrame(lower, text="待分选 FIFO", padding=8)
        log_frame = ttk.LabelFrame(lower, text="事件日志", padding=8)
        lower.add(queue_frame, weight=1)
        lower.add(log_frame, weight=3)
        ttk.Label(queue_frame, textvariable=self.queue_var, justify="left", font=("Consolas", 10)).pack(anchor="nw")
        self.log = tk.Text(log_frame, height=10, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

        self._build_serial_panel(right)

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

        serial_box = ttk.LabelFrame(parent, text="可选：串口镜像到 Arduino", padding=10)
        serial_box.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(serial_box, text="同步发送控制命令", variable=self.mirror_var).pack(anchor="w")
        self.port_combo = ttk.Combobox(serial_box, textvariable=self.port_var, state="readonly")
        self.port_combo.pack(fill="x", pady=6)
        buttons = ttk.Frame(serial_box)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="刷新", command=self.refresh_ports).pack(side="left")
        ttk.Button(buttons, text="连接/断开", command=self.toggle_serial).pack(side="left", padx=5)
        ttk.Label(serial_box, textvariable=self.serial_status_var, foreground="#475569", wraplength=260).pack(anchor="w", pady=(8, 0))
        if not self.transport.dependency_available:
            self.serial_status_var.set("未安装 pyserial；软件仿真可直接运行。接硬件时执行 pip install -r requirements.txt")

        acceptance = ttk.LabelFrame(parent, text="建议现场演示脚本", padding=10)
        acceptance.pack(fill="x")
        ttk.Label(
            acceptance,
            text=(
                "1. 标准 5 块序列跑通\n"
                "2. 指出入口/出口计数差异\n"
                "3. 展示废石时滑槽转向\n"
                "4. 展示滑槽自动回默认位\n"
                "5. 触发 EXTRA_OBJECT\n"
                "6. 触发 QUEUE_EMPTY\n"
                "7. 说明未来模型只替换分类接口"
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

    def load_standard_batch(self) -> None:
        if self.simulation_running and not self.simulation_paused:
            messagebox.showinfo("正在运行", "请先复位，再加载新批次。")
            return
        items = demo_batch()
        self._load_items(items)

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
        self._draw_static_scene()
        self._mirror_batch_if_enabled()
        self._refresh_ui()

    def add_manual(self, label: MaterialClass) -> None:
        if self.machine.state not in {MachineState.IDLE, MachineState.READY}:
            messagebox.showinfo("批次已启动", "运行中不能修改 FIFO；请复位后再添加。")
            return
        item = SortItem(f"M{self.next_manual_id:02d}", label, 1.0, "MANUAL_DEMO")
        self.next_manual_id += 1
        self.machine.append_item(item)
        self.loaded_items.append(item)
        self.pending.append(VisualRock(item))
        self._refresh_ui()

    def start(self) -> None:
        if self.simulation_paused:
            self.simulation_paused = False
            self.simulation_running = True
            self.last_wall_time = time.monotonic()
            self._send_if_mirroring("RESUME")
            return
        if self.machine.state is not MachineState.READY:
            messagebox.showinfo("无法启动", "请先加载或添加演示矿石。")
            return
        try:
            self.machine.start()
        except RuntimeError as exc:
            messagebox.showerror("无法启动", str(exc))
            return
        self.simulation_running = True
        self.last_wall_time = time.monotonic()
        self._send_if_mirroring("START")
        self._refresh_ui()

    def toggle_pause(self) -> None:
        if not self.simulation_running and not self.simulation_paused:
            return
        self.simulation_paused = not self.simulation_paused
        self.simulation_running = not self.simulation_paused
        self.last_wall_time = time.monotonic()
        self._send_if_mirroring("PAUSE" if self.simulation_paused else "RESUME")

    def reset(self) -> None:
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
        self._draw_static_scene()
        self._send_if_mirroring("RESET")
        self._refresh_ui()

    def emergency_stop(self) -> None:
        self.machine.emergency_stop()
        self.simulation_running = False
        self.simulation_paused = False
        self._send_if_mirroring("ESTOP")
        self._refresh_ui()

    def demo_extra_object(self) -> None:
        self._load_items(demo_batch())
        extra = SortItem("EXTRA", MaterialClass.WASTE, 1.0, "UNPLANNED_OBJECT")
        self.pending.append(VisualRock(extra, extra=True))
        self.start()

    def demo_empty_queue(self) -> None:
        self.reset()
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
        if self.machine.queue:
            self.queue_var.set("\n".join(
                f"{index:02d}. {item.item_id:<6} {item.label.value:<5}  {item.confidence:.0%}"
                for index, item in enumerate(self.machine.queue, start=1)
            ))
        else:
            self.queue_var.set("（空）")
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

    def toggle_serial(self) -> None:
        if self.transport.connected:
            self.transport.close()
            self.serial_status_var.set("已断开")
            return
        if not self.port_var.get():
            self.refresh_ports()
        if not self.port_var.get():
            messagebox.showinfo("无串口", self.serial_status_var.get())
            return
        try:
            self.transport.connect(self.port_var.get())
            self.serial_status_var.set(f"已连接 {self.port_var.get()} @ 115200")
            self._mirror_batch_if_enabled()
        except Exception as exc:  # 串口异常需要直接呈现给演示人员
            messagebox.showerror("连接失败", str(exc))

    def _send_if_mirroring(self, command: str) -> None:
        if not self.mirror_var.get() or not self.transport.connected:
            return
        try:
            self.transport.send(command)
        except Exception as exc:
            self.serial_status_var.set(f"发送失败：{exc}")

    def _mirror_batch_if_enabled(self) -> None:
        if not self.mirror_var.get() or not self.transport.connected:
            return
        for command in batch_commands(self.loaded_items):
            self._send_if_mirroring(command)

    def _poll_serial(self) -> None:
        try:
            lines = self.transport.poll()
            if lines:
                self.log.configure(state="normal")
                for line in lines:
                    self.log.insert("end", f"[MCU] {line}\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except Exception as exc:
            self.serial_status_var.set(f"串口读取失败：{exc}")
            self.transport.close()
        self.root.after(100, self._poll_serial)

    def _on_close(self) -> None:
        self.transport.close()
        self.root.destroy()


def launch() -> None:
    root = tk.Tk()
    SortingDemoApp(root)
    root.mainloop()
