# ResNet50 重训结果（fold 0）

本目录只保存可审计的训练摘要、数据划分和预测结果，不把大体积权重直接写入 Git 历史。

- `training_summary.json`：训练、验证和测试指标摘要。
- `model.metadata.json`：ONNX 输入、标准化、类别和阈值约定。
- `split.csv`：按实体分组的训练/验证/测试划分。
- `validation_predictions.csv` 与 `test_predictions.csv`：逐图概率输出。

完整 ONNX 推理模型在对应 GitHub Release 中单独提供。原始图片和 PyTorch 检查点不随仓库发布。

注意：时间戳命名的新增图片在当前清单中按“每图一个实体”处理。如果同一块样品拍摄了多个角度，应重新按实体编号后划分，否则指标可能过于乐观。
