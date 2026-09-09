#!/usr/bin/env python3
"""
new_project.py — 新プロジェクトのコンフィグJSONを生成するセットアップヘルパー

【使い方】
Claudeが事前にNotionの在庫トラッカーから商品情報を取得し、
そのデータをコマンドライン引数で渡してコンフィグJSONを生成する。

    python3 new_project.py <写真フォルダのパス> <output_name> [notion_json_file]

引数:
    写真フォルダのパス  : Driveからダウンロードした写真が入ったフォルダ
    output_name        : 出力ファイル名（.mp4/.json の prefix）
    notion_json_file   : Notionから取得した商品情報JSON（省略時は雛形のみ出力）

Notionデータのフォーマット（notion_json_file の中身）:
    {
        "franchise":   "One Piece",
        "character":   "Monkey D. Luffy Gear 5",
        "line":        "Grandista",
        "maker":       "Bandai Spirits",
        "condition":   "Used (Unopened / Like New Condition)",
        "banner_text": "ONE PIECE — LUFFY GEAR 5"
    }
"""

import sys, json
from pathlib import Path

IMAGE_EXTS = {".heic", ".jpg", ".jpeg", ".png", ".HEIC", ".JPG", ".JPEG", ".PNG"}

def main():
    if len(sys.argv) < 3:
        print("使い方: python3 new_project.py <写真フォルダのパス> <output_name> [notion_json_file]")
        sys.exit(1)

    photos_dir   = Path(sys.argv[1]).resolve()
    output_name  = sys.argv[2]
    notion_file  = Path(sys.argv[3]) if len(sys.argv) >= 4 else None

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

    # Notionデータ読み込み（あれば）
    notion = {}
    if notion_file and notion_file.exists():
        with open(notion_file, encoding="utf-8") as nf:
            notion = json.load(nf)
        print(f"\n✅ Notionデータ読み込み済み: {notion_file}")

    # flash_order: 最初の7枚（少なければ全部）
    flash    = files[:7]
    showcase = files

    cfg = {
        "franchise":   notion.get("franchise",   "★作品名を入力★"),
        "character":   notion.get("character",   "★キャラクター名を入力★"),
        "line":        notion.get("line",        "★商品ライン名を入力★"),
        "maker":       notion.get("maker",       "★メーカー名を入力★"),
        "size":        notion.get("size",        None),
        "condition":   notion.get("condition",   "Used (Unopened / Like New Condition)"),
        "banner_text": notion.get("banner_text", "★バナーテキストを入力★"),
        "pattern":     "C",

        "showcase_duration_per_photo": 3.0,

        "crop_offsets":   {f: 0.5 for f in files},
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

    has_gaps = any("★" in str(v) for v in cfg.values() if v)
    print(f"\n✅ コンフィグ生成完了: {out_path}")

    if has_gaps:
        print("\n⚠️  以下の項目が未入力です（Notionデータが取れなかった可能性）:")
        for k, v in cfg.items():
            if isinstance(v, str) and "★" in v:
                print(f"   {k}: {v}")
        print("\n   JSONを直接編集するか、Notionから情報を取得して再実行してください")
    else:
        print("\n次のステップ — 動画生成:")
        print(f"   python3 /home/claude/pipeline/short_pipeline.py {out_path}")
        print("\n   ※ showcase_order / flash_order の写真順は必要に応じてJSON編集で調整")

if __name__ == "__main__":
    main()
