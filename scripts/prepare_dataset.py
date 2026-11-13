from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sorting_demo.ml.manifest import (  # noqa: E402
    discover_class_images,
    manifest_summary,
    write_manifest,
)


def extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    existing_images = [path for path in destination.rglob("*") if path.is_file()]
    if existing_images:
        print(f"复用已解压目录：{destination}")
        return
    tar = shutil.which("tar")
    if tar is None:
        raise RuntimeError("未找到系统 tar。Windows 10/11 自带 tar 可直接读取当前 RAR 文件。")
    completed = subprocess.run(
        [tar, "-xf", str(archive), "-C", str(destination)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"解压失败：{archive}\n{completed.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description="解压矿石/废石数据并建立实体分组清单")
    parser.add_argument("--ore-archive", type=Path, default=WORKSPACE_ROOT / "矿石256.rar")
    parser.add_argument("--waste-archive", type=Path, default=WORKSPACE_ROOT / "废石256.rar")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    for archive in (args.ore_archive, args.waste_archive):
        if not archive.is_file():
            raise FileNotFoundError(archive)

    raw_root = args.data_dir / "raw"
    ore_root = raw_root / "ore"
    waste_root = raw_root / "waste"
    extract_archive(args.ore_archive, ore_root)
    extract_archive(args.waste_archive, waste_root)

    rows = discover_class_images(ore_root, "ORE") + discover_class_images(waste_root, "WASTE")
    manifest_path = args.data_dir / "manifest.csv"
    samples = write_manifest(
        rows,
        project_root=PROJECT_ROOT,
        output_path=manifest_path,
        n_folds=args.folds,
        seed=args.seed,
    )
    summary = manifest_summary(samples)
    summary.update(
        {
            "folds": args.folds,
            "seed": args.seed,
            "ore_archive": str(args.ore_archive.resolve()),
            "waste_archive": str(args.waste_archive.resolve()),
        }
    )
    summary_path = args.data_dir / "dataset_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"清单：{manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
