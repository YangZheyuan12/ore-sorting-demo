# 微波智选

**微波加热智能选矿系统**

这是一套与机械演示机结构对应、但不依赖真实选矿机构和训练数据也能运行的演示程序。它是独立项目，不读取同级目录中的其他科研代码或数据。

## 现在能演示什么

- 离带拍照/识别后，按识别顺序人工上料。
- 入口传感器只做计数，不改变分类队列。
- 出口传感器触发时，从 FIFO 队首取出分类结果。
- 目标矿保持滑槽默认位，废石驱动滑槽偏转，随后自动回默认位。
- 实时展示入口数、出口数、待分选 FIFO、两类料箱计数和事件日志。
- 一键演示 `EXTRA_OBJECT`、`QUEUE_EMPTY` 和急停保护。
- 可选把同一批次命令镜像发送给 Arduino；不接硬件时全部功能仍可用。
- 上传后的图片、模型结果和人工纠错会自动暂存，复位或重启软件后可恢复最后一批。

界面不再预装固定的 5 块演示数据。每次选择 1–20 张图片，所选图片数量就是本批分选数量。

## 运行

要求 Windows 上有 Python 3.10 或更新版本，Tkinter 随常规 Python 安装提供。

```powershell
cd "D:\科研\采矿赛\ore-sorting-demo"
.\.venv\Scripts\python.exe run_demo.py
```

## Windows App 打包

桌面应用名称为“微波智选”，完整名称为“微波加热智能选矿系统”。安装 App 打包依赖后执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt
powershell -ExecutionPolicy Bypass -File scripts\build_windows_app.ps1
```

完成后双击 `dist\微波智选\微波智选.exe`。目录版会把 ONNX 模型、外部权重、元数据和应用图标一起带上，目标电脑不需要另外安装 Python；分发时需要复制整个 `dist\微波智选` 文件夹，不能只复制 EXE。

EXE 的文件版本信息中已写入作者 `Astoria1224`。这只用于文件“属性 → 详细信息”，不等于 Windows 的“已验证发布者”。正式发布需先取得受信任的 Authenticode 代码签名证书并安装 Windows SDK Signing Tools，再把证书指纹交给构建脚本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows_app.ps1 `
  -CertificateThumbprint "代码签名证书指纹"
```

脚本会使用 SHA-256 和 RFC 3161 时间戳签署并验证最终 EXE。没有提供证书时仍能正常构建，但 Windows 会继续把它视为未知发布者。

无需安装任何第三方包即可运行纯软件仿真，也可以直接双击 `启动演示.bat`。命令行自检：

```powershell
.\.venv\Scripts\python.exe run_demo.py --headless
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 导入照片并使用 ONNX 模型识别

将完整模型文件放在 `artifacts/resnet50_baseline_fold0/`。当前重新训练的演示模型名为 `model_retrained.onnx`，并同时需要同目录下的 `model_retrained.onnx.data` 与 `model_retrained.metadata.json`：

```text
model_retrained.onnx
model_retrained.onnx.data
model_retrained.metadata.json
```

启动 GUI 后点击“选择图片并识别”，可一次选择 1–20 张 JPG、JPEG、PNG、BMP、TIF 或 TIFF 图片。程序按文件名排序后逐张执行 ResNet50 ONNX CPU 推理；选择几张图片，本批就分选几块，不再要求固定为 5。界面的“待分选图片”表格会显示批次顺序、原始文件名、识别结果和置信度，上料时按表格从上到下操作。识别结果会按相同顺序装入 FIFO，连接实物时再发送给单片机；点击“开始”后，仍由 D2/D3 真实传感器驱动画面和分选过程。

选择图片后，软件会先把图片副本写入 `%LOCALAPPDATA%\MicrowaveSmartSort\staging`，再开始模型分析；识别结果和人工纠错状态也会同步写入。若 Windows 阻止该目录写入，软件会自动改用 EXE 旁边的 `data\app_cache\staging`，不会再弹出错误窗口，也不会中断识别。即使分析中断，软件重启后仍可从暂存图片继续；也可点击“恢复暂存批次”手动恢复。点击“复位”只重新装载当前暂存批次，不需要再次选择图片。

主动画区与下方“待分选图片/事件日志”之间有一条可上下拖动的分隔线。向上拖动即可扩大图片分析列表并一次展示更多记录；小屏幕下仍保留滚动条作为备用。

当前重训模型合并了原有 144 张 256×256 热成像图与新增 115 张 240×320 热像仪照片，仍只适用于同类热成像采集条件。普通可见光照片与训练分布不同，分类结果不能视为可靠；要识别普通照片，需要用相同相机、光照和背景采集有标签数据后重新训练。

### 识别反馈、人工纠错和模型迭代

导入照片后点击“识别反馈/纠错”，选中一张图片，可标记“预测正确”“实际为目标矿”或“实际为废石”。人工标记会立即覆盖本批次对应的 FIFO 分类，避免错误识别直接控制实物分流；批次运行期间不能修改，需先复位。

点击“保存反馈样本”后，软件会把真实类别、原预测、置信度、废石概率和模型阈值追加到 `data/feedback/feedback.csv`，并把图片复制到 `data/feedback/images/ore/` 或 `data/feedback/images/waste/`。这些目录默认不提交到 Git。

反馈不会自动修改当前 ONNX 模型。ONNX 是已经导出的推理模型；要让纠错在以后的图片上生效，必须用训练代码重新训练并重新导出 ONNX。建议流程如下：

1. 保留原训练集，把反馈图片作为新增且已核验的独立样本；不要只拿 5 张图片单独微调，否则非常容易过拟合并遗忘原有类别。
2. 同一块矿石的多个角度必须使用同一个实体编号，并按实体分组划分训练、验证和测试集，不能把同一实体同时放进训练集和测试集。
3. 先收集更多错误样本、低置信度样本和不同光照/背景样本，再重新执行数据准备、训练及 ONNX 导出命令。
4. 用从未参与训练的独立矿石作为最终测试集。训练图片只能检查拟合情况，不能代表真实泛化准确率。

如果测试的是普通相机照片，而原模型训练数据是热成像伪彩色图片，应优先重新采集同类型普通照片训练新模型，而不是仅调整分类阈值。

需要连接 Arduino/STM32 时再安装串口依赖：

```powershell
python -m pip install -r requirements.txt
```

## 推荐现场流程

1. 点击“选择图片并识别”，按本批需要选择 1–20 张图片。
2. 核对识别结果和 FIFO 数量；需要时在“识别反馈/纠错”中人工复核。
3. 点击“开始”，按图片文件名排序后的顺序人工上料。
4. 指出入口只增加计数；矿石经过出口后，FIFO 才减少一项。
5. 观察废石经过出口后滑槽转向废石箱。
6. 需要时点击异常演示按钮，检查 `EXTRA_OBJECT` 和 `QUEUE_EMPTY` 保护。

界面的“演示速度”只改变动画时间倍率，不改变机械标定参数。

## 与机械文档的对应

| 文档要求 | 程序实现 |
|---|---|
| 60 cm × 20 cm 传送带，0.08–0.10 m/s | 仿真按 0.6 m / 0.1 m/s，即 6 秒通过主输送段 |
| 两组 5V NPN 对射传感器 | 状态机区分 `ENTRY` 与 `EXIT`，Arduino 默认使用 D2/D3 |
| 传感器下降沿与防抖 | 当前实物固件使用 D2/D3 中断下降沿，并以 200 ms 间隔抑制重复触发 |
| 入口只计数 | `trigger_entry()` 不弹出 FIFO |
| 出口按 FIFO 分选 | `trigger_exit()` 弹出队首并生成滑槽动作 |
| 舵机位置 | 当前实物固件为目标矿 45°、废石 135°、批次准备/结束 90° |
| 舵机保持与批次尾料 | 分类后保持当前位置，最后一件完成后等待 300 ms，再让传送带运行 3 秒并回 90° |
| 电机驱动 | 当前实物使用 D5/D6 PWM 与 D12 低电平使能，不使用 D7 继电器协议 |
| 队列异常 | 空队列出口触发会累计 `errorCount` 并通过串口上报，实体急停仍须独立完成 |

注意：固件中的引脚、电机继电器有效电平、舵机角度和延时是安全的演示初值，不是免标定参数。首次接实物应卸下矿石、断开电机负载，仅验证传感器电平和舵机方向，再进行低速联调。急停仍应直接串入 12V 电机主回路，软件急停只作为辅助。

## 实物端串口协议

上位机已适配实物固件 `sorter_minimal_tail3s_servo_home`。波特率为 9600，ASCII 文本，一行一条命令。连接串口后等待约 2 秒，让 Arduino 完成自动复位，再装载批次。

PC → 单片机：

```text
BATCH 5
ORE
WASTE
ORE
WASTE
ORE
END
START
```

单片机 → PC 的主要消息：

```text
READY
OK BATCH 5
QUEUED 1/5 ORE
ARMED 5
RUNNING
IN 1
OUT 1 ORE
DONE 5 3 2 0
ERROR: NOT ARMED
```

GUI 的“实物传感器反馈”区域会根据 `IN`、`OUT` 和 `DONE` 更新入口、出口及分类计数。实物模式采用传感器事件驱动动画：点击开始后不会自动连续生成矿石；收到 `IN n` 才显示第 n 块矿石并让它移动到出口附近，收到 `OUT n ORE/WASTE` 后才播放进入对应料箱的分流过程。纯软件仿真模式仍使用自动动画。

实物固件对入口与出口事件进行一一配对：第 N 次出口仅在第 N 次入口已经计数后才允许 FIFO 出队和改变舵机。如果出口触发时 `outCount >= inCount`，说明当前出口脉冲没有可配对的入口记录。固件会保持舵机不动、不出队并继续运行传送带，同时上报 `WARN: EXIT IGNORED WITHOUT ENTRY`；真正的下一次入口到来后，后续出口仍可正常分选，避免光电门重复触发造成 FIFO 整体错位。

实物固件没有 `PAUSE/RESUME/STOP/ESTOP` 指令，因此上位机停止实物时发送 `BATCH 0`，使当前固件执行 `motorStop()` 并清空批次；继续运行前必须重新加载批次。实体急停仍须独立切断电机主回路。

仓库中的 [firmware/ore_sorter_demo/ore_sorter_demo.ino](firmware/ore_sorter_demo/ore_sorter_demo.ino) 是另一套通用参考固件，引脚和协议与当前实物不同，不能在不核对接线的情况下直接覆盖烧录。

## 当前小样本基线与后续扩展

当前数据清单包含 146 张矿石图和 113 张废石图，共 259 张。原数据按 `实体编号-视角编号.jpg` 解析；新增时间戳照片按每图一个独立实体处理。训练代码严格按实体分组，禁止同一实体的不同视角同时进入训练集和测试集。

建立数据清单：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_dataset.py
```

追加“每张照片对应一个独立样品”的手机/热像仪采集目录：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_dataset.py `
  --extra-ore-dir "模拟待测样本\矿石" `
  --extra-waste-dir "模拟待测样本\废石"
```

额外目录使用相机生成的文件名时，程序会把每张图作为一个独立实体，仍按实体划分训练、验证和测试集。如果同一块实体拍摄了多个视角，应先改名为 `实体编号-视角编号.jpg` 并使用原始分组目录格式，避免数据泄漏。

训练第 0 折最小基线。该模型使用 ImageNet 预训练 ResNet50 作为冻结特征骨干，只训练线性分类头；这适合当前只有 24 个独立实体的情况：

```powershell
.\.venv\Scripts\python.exe scripts\train_resnet50_baseline.py --test-fold 0
```

数据量增加后可用 `--validation-specimens-per-class 10` 扩大每类验证集，令阈值选择更稳定。
离线重训时可加 `--backbone-checkpoint artifacts\resnet50_baseline_fold0\model.pt`，直接复用已有检查点中的 ImageNet 骨干参数。

导出 ONNX 并执行 PyTorch/ONNX Runtime 数值一致性检查：

```powershell
.\.venv\Scripts\python.exe scripts\export_onnx.py
.\.venv\Scripts\python.exe scripts\predict_onnx.py "data\raw\ore\矿石256\1-1.jpg"
```

数据清单 `data/manifest.csv` 和审计摘要 `data/dataset_summary.json` 可以纳入版本管理；原始图片、训练权重和 ONNX 文件默认被 `.gitignore` 排除。

后续有数据后，保持控制层不动，只新增一个实现如下接口的模型适配器：

```python
def classify(image_path: str) -> tuple[str, float]:
    """返回 ("ORE" 或 "WASTE", 置信度)。"""
```

建议按数据量分三步升级：

1. 少量数据：热强度规则与冻结 ResNet50 特征作基线，按矿石实体进行分组交叉验证；低置信度样本仍走目标矿通道并保存复核。
2. 数据增加：采用迁移学习，按矿批、采区或采样日期分组划分训练/验证集，避免同批样本泄漏。
3. 稳定运行：做概率校准、分布外检测和主动学习，优先标注低置信度、模型冲突和新矿源样本。

无论模型如何变化，发送给控制层的最终记录仍是 `SortItem(item_id, label, confidence, source)`，因此机械演示和后续真实算法可以并行迭代。

## 目录

```text
矿石分选样机/
├─ run_demo.py                         # GUI 入口与命令行自检
├─ scripts/
│  ├─ prepare_dataset.py              # 解压、校验和实体分组清单
│  ├─ evaluate_brightness_baseline.py  # 四折热强度规则对照
│  ├─ train_resnet50_baseline.py      # 冻结 ResNet50 特征基线
│  ├─ export_onnx.py                  # ONNX 导出与一致性检查
│  └─ predict_onnx.py                 # 单图 ONNX Runtime 推理
├─ sorting_demo/
│  ├─ domain.py                       # FIFO、安全保护、计数状态机
│  ├─ classifier_adapter.py           # 当前演示分类与未来模型接口
│  ├─ gui.py                          # 机械流程动画、控制台、串口面板
│  ├─ protocol.py                     # 串口消息生成与解析
│  ├─ transport.py                    # 可选 pyserial 连接
│  └─ ml/                             # 清单、指标和 ONNX 适配器
├─ firmware/ore_sorter_demo/
│  └─ ore_sorter_demo.ino             # Arduino 非阻塞固件
└─ tests/                              # 状态机与协议测试
```
