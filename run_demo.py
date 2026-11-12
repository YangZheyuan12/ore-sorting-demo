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


def main() -> int:
    parser = argparse.ArgumentParser(description="矿石分选样机演示程序")
    parser.add_argument("--headless", action="store_true", help="不打开窗口，运行标准批次自检")
    args = parser.parse_args()
    if args.headless:
        return run_headless()
    from sorting_demo.gui import launch

    launch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
