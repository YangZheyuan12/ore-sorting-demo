# 矿石智能分选演示程序

这是一套与机械演示机结构对应、但不依赖真实选矿机构和训练数据也能运行的演示程序。它是独立项目，不读取同级目录中的其他科研代码或数据。

## 现在能演示什么

- 离带拍照/识别后，按识别顺序人工上料。
- 入口传感器只做计数，不改变分类队列。
- 出口传感器触发时，从 FIFO 队首取出分类结果。
- 目标矿保持滑槽默认位，废石驱动滑槽偏转，随后自动回默认位。
- 实时展示入口数、出口数、待分选 FIFO、两类料箱计数和事件日志。
- 一键演示 `EXTRA_OBJECT`、`QUEUE_EMPTY` 和急停保护。
- 可选把同一批次命令镜像发送给 Arduino；不接硬件时全部功能仍可用。

界面顶部会始终显示“当前分类为固定演示数据，不代表真实选矿准确率”，避免在数据不足阶段制造虚假的模型精度。

## 运行

要求 Windows 上有 Python 3.10 或更新版本，Tkinter 随常规 Python 安装提供。

```powershell
cd "D:\科研\采矿赛\矿石分选样机"
python run_demo.py
```

无需安装任何第三方包即可运行纯软件仿真，也可以直接双击 `启动演示.bat`。命令行自检：

```powershell
python run_demo.py --headless
python -m unittest discover -s tests -v
```

需要连接 Arduino/STM32 时再安装串口依赖：

```powershell
python -m pip install -r requirements.txt
```

## 推荐现场流程

1. 点击“加载标准 5 块”，确认 FIFO 为 `ORE, WASTE, ORE, WASTE, ORE`。
2. 点击“开始”，说明画面左侧是离带拍照，圆点是人工按顺序放上的矿石。
3. 指出入口只增加计数；矿石经过出口后，FIFO 才减少一项。
4. 观察废石经过出口后滑槽转向废石箱，并在 0.5 秒后回到目标矿默认位。
5. 点击“异常演示：多放一块”，展示传送带保护停机和 `EXTRA_OBJECT`。
6. 点击“异常演示：空队列出料”，展示 `QUEUE_EMPTY`。
7. 最后说明：当前识别结果是固定演示输入，未来只替换分类适配器，FIFO、传感器、舵机和安全逻辑不用重写。

界面的“演示速度”只改变动画时间倍率，不改变机械标定参数。

## 与机械文档的对应

| 文档要求 | 程序实现 |
|---|---|
| 60 cm × 20 cm 传送带，0.08–0.10 m/s | 仿真按 0.6 m / 0.1 m/s，即 6 秒通过主输送段 |
| 两组 5V NPN 对射传感器 | 状态机区分 `ENTRY` 与 `EXIT`，Arduino 默认使用 D2/D3 |
| 下降沿、20–30 ms 去抖、恢复 200 ms | 固件采用稳定下降沿 25 ms 去抖，并要求光束恢复 200 ms 后重新武装 |
| 入口只计数 | `trigger_entry()` 不弹出 FIFO |
| 出口按 FIFO 分选 | `trigger_exit()` 弹出队首并生成滑槽动作 |
| 默认目标矿，废石偏转 20–30° | 固件默认 90°，废石 115°，角度可按实物标定 |
| 舵机保持 0.4–0.6 s 后回位 | 默认保持 500 ms，复位/故障/急停也回默认位 |
| `t_delay = d/v - t_servo - t_margin` | 固件默认 `SORT_DELAY_MS = 550`，对应文档示例参数 |
| 多放一块与空队列保护 | `EXTRA_OBJECT`、`QUEUE_EMPTY` 均停机并记录错误 |

注意：固件中的引脚、电机继电器有效电平、舵机角度和延时是安全的演示初值，不是免标定参数。首次接实物应卸下矿石、断开电机负载，仅验证传感器电平和舵机方向，再进行低速联调。急停仍应直接串入 12V 电机主回路，软件急停只作为辅助。

## 串口协议

波特率为 115200，ASCII 文本，一行一条命令。

PC → 单片机：

```text
RESET
BATCH 5
PUSH S01 ORE
PUSH S02 WASTE
PUSH S03 ORE
PUSH S04 WASTE
PUSH S05 ORE
START
PAUSE
RESUME
STOP
ESTOP
PING
STATUS
```

单片机 → PC 的主要消息：

```text
READY
ACK BATCH 5
ACK PUSH S01
EVENT ENTRY 1
EVENT EXIT S01 ORE
STATE SERVO_WASTE
STATE SERVO_ORE
DONE 3 2
ERROR EXTRA_OBJECT
ERROR QUEUE_EMPTY
```

Arduino 固件位于 [firmware/ore_sorter_demo/ore_sorter_demo.ino](firmware/ore_sorter_demo/ore_sorter_demo.ino)。使用 Arduino IDE 打开后即可编译；`Servo` 是 Arduino 内置库。

## 当前数据不足与后续扩展

当前阶段不训练一个看似完整但不可验证的深度模型。`sorting_demo/classifier_adapter.py` 只提供固定概率与保守阈值策略：只有废石概率达到 0.72 才执行抛废，否则回到目标矿通道，以降低误抛有用矿石的风险。

后续有数据后，保持控制层不动，只新增一个实现如下接口的模型适配器：

```python
def classify(image_path: str) -> tuple[str, float]:
    """返回 ("ORE" 或 "WASTE", 置信度)。"""
```

建议按数据量分三步升级：

1. 少量数据：人工规则/传统特征作基线，分层交叉验证；低置信度样本仍走目标矿通道并保存复核。
2. 数据增加：采用迁移学习，按矿批、采区或采样日期分组划分训练/验证集，避免同批样本泄漏。
3. 稳定运行：做概率校准、分布外检测和主动学习，优先标注低置信度、模型冲突和新矿源样本。

无论模型如何变化，发送给控制层的最终记录仍是 `SortItem(item_id, label, confidence, source)`，因此机械演示和后续真实算法可以并行迭代。

## 目录

```text
矿石分选样机/
├─ run_demo.py                         # GUI 入口与命令行自检
├─ sorting_demo/
│  ├─ domain.py                       # FIFO、安全保护、计数状态机
│  ├─ classifier_adapter.py           # 当前演示分类与未来模型接口
│  ├─ gui.py                          # 机械流程动画、控制台、串口面板
│  ├─ protocol.py                     # 串口消息生成与解析
│  └─ transport.py                    # 可选 pyserial 连接
├─ firmware/ore_sorter_demo/
│  └─ ore_sorter_demo.ino             # Arduino 非阻塞固件
└─ tests/                              # 状态机与协议测试
```
