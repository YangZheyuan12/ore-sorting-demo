from __future__ import annotations

import argparse

from sorting_demo.classifier_adapter import demo_batch
from sorting_demo.domain import SortingMachine


def run_headless() -> int:
    machine = SortingMachine()
    machine.load_batch(demo_batch())
    machine.start()
    now = 0.0
    for _ in range(machine.expected_batch):
        machine.trigger_entry(now)
        machine.trigger_exit(now + 1.0)
        machine.return_servo_default()
        now += 2.5
    for event in machine.events:
        print(f"[{event.sequence:03d}] {event.kind:<6} {event.message}")
    snapshot = machine.snapshot()
    print("RESULT", snapshot)
    return 0 if snapshot["state"] == "COMPLETE" else 1


def check_model_bundle() -> int:
    """Load the deployed ONNX model; used to validate a packaged desktop app."""
    from sorting_demo.gui import SortingDemoApp
    from sorting_demo.ml.onnx_classifier import OnnxOreClassifier

    classifier = OnnxOreClassifier(SortingDemoApp.MODEL_PATH)
    if "CPUExecutionProvider" not in classifier.session.get_providers():
        raise RuntimeError("ONNX Runtime CPUExecutionProvider 不可用")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="微波智选｜微波加热智能选矿系统")
    parser.add_argument("--headless", action="store_true", help="不打开窗口，运行标准批次自检")
    parser.add_argument("--check-model", action="store_true", help="加载部署模型并检查 ONNX Runtime")
    args = parser.parse_args()
    if args.check_model:
        return check_model_bundle()
    if args.headless:
        return run_headless()
    from sorting_demo.gui import launch

    launch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
