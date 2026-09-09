#!/usr/bin/env python3
"""
new_project.py — 新プロジェクトのコンフィグJSONを生成するセットアップヘルパー

使い方（Claudeがコマンド実行）:
    python3 new_project.py <写真フォルダのパス> <output_name>

例:
    python3 new_project.py /home/claude/pipeline/photos/rem_re_zero rem_re_zero_short

何をするか:
    1. 指定フォルダ内の画像ファイル（HEIC/JPG/PNG）を一覧表示
    2. ファイル名一覧をもとに、コンフィグJSONの雛形を <output_name>.json として出力
    3. Claude（またはYutaさん）が franchise / character / line / maker などを
       JSONファイル内で編集してから、以下のコマンドで動画生成:
           python3 /home/claude/pipeline/short_pipeline.py <output_name>.json
"""

import sys, json
from pathlib import Path

IMAGE_EXTS = {".heic", ".jpg", ".jpeg", ".png", ".HEIC", ".JPG", ".JPEG", ".PNG"}

def main():
    if len(sys.argv) < 3:
        print("使い方: python3 new_project.py <写真フォルダのパス> <output_name>")
        print("例:     python3 new_project.py /tmp/rem_photos rem_re_zero_short")
        sys.exit(1)

    photos_dir = Path(sys.argv[1]).resolve()
    output_name = sys.argv[2]

    if not photos_dir.exists():
        print(f"エラー: フォルダが見つかりません → {photos_dir}")
        sys.exit(1)

    # 画像ファイルを取得してソート
    files = sorted(
        [f.stem for f in photos_dir.iterdir()
         if f.suffix in IMAGE_EXTS and not f.name.startswith(".")],
        key=lambda s: s
    )

    if not files:
        print(f"エラー: {photos_dir} に画像ファイルが見つかりません")
        sys.exit(1)

    print(f"\n📁 {photos_dir}")
    print(f"   {len(files)} 枚の画像を検出:\n")
    for i, f in enumerate(files):
        print(f"   [{i+1:02d}] {f}")

    # flash_order: 最初の7枚（少なければ全部）
    flash = files[:7]
    # showcase_order: 全枚数（残りはショーケースに使う）
    showcase = files

    cfg = {
        "franchise":   "★作品名を入力★",
        "character":   "★キャラクター名を入力★",
        "line":        "★商品ライン名を入力★（例: Trio-Try-iT / BiCute Bunnies / Grandista）",
        "maker":       "★メーカー名を入力★（例: TAITO / FuRyu / Bandai Spirits）",
        "size":        None,
        "condition":   "Used (Unopened / Like New Condition)",
        "banner_text": "★バナーテキストを入力★（例: ONE PIECE — LUFFY GEAR 5）",
        "pattern":     "C",

        "showcase_duration_per_photo": 3.0,

        "crop_offsets": {f: 0.5 for f in files},
        "showcase_order": showcase,
        "flash_order":    flash,

        "output_name":        output_name,
        "is_remake":          False,
        "original_video_url": "",
        "ebay_url":           "",
        "photos_dir":         str(photos_dir),
    }

    out_path = Path(f"/mnt/user-data/outputs/{output_name}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ コンフィグ生成完了: {out_path}")
    print("\n次のステップ:")
    print(f"  1. {out_path} を開いて ★マーク★ の項目を埋める")
    print(f"     （franchise / character / line / maker / banner_text）")
    print(f"  2. showcase_order / flash_order の写真順を調整する（任意）")
    print(f"  3. 動画生成:")
    print(f"       python3 /home/claude/pipeline/short_pipeline.py {out_path}")

if __name__ == "__main__":
    main()
