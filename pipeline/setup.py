#!/usr/bin/env python3
"""
setup.py — 新しいチャットセッションで最初に1回だけ実行するセットアップスクリプト

使い方:
    python3 /home/user/50dollarfigure-site/pipeline/setup.py

何をするか:
    リポジトリ内の pipeline/assets/ を /home/claude/pipeline/assets/ にコピーし、
    動画制作パイプラインがすぐ使える状態にする。
"""

import shutil, subprocess, sys
from pathlib import Path

REPO_PIPELINE  = Path(__file__).parent                  # リポジトリの pipeline/
WORK           = Path("/home/claude/pipeline")          # 作業ディレクトリ
REPO_ASSETS    = REPO_PIPELINE / "assets"


def run(cmd, **kw):
    result = subprocess.run(cmd, **kw, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ⚠️  {' '.join(str(c) for c in cmd)}")
        if result.stderr: print(f"     {result.stderr.strip()}")
    return result


def main():
    print("=" * 50)
    print("$50 FIGURE パイプライン セットアップ")
    print("=" * 50)

    # ── 1. 作業ディレクトリ作成 ──────────────────────────
    for d in [WORK/"assets/hooks", WORK/"assets/bgm", WORK/"photos", WORK/"build"]:
        d.mkdir(parents=True, exist_ok=True)
    print("✅ ディレクトリ作成")

    # ── 2. アセットをリポジトリからコピー ─────────────────
    copied = 0
    for src in REPO_ASSETS.rglob("*"):
        if src.is_file():
            rel  = src.relative_to(REPO_ASSETS)
            dest = WORK / "assets" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
    print(f"✅ アセットコピー完了: {copied} ファイル")

    # ── 3. パイプラインスクリプトをコピー ─────────────────
    for fn in ["short_pipeline.py", "new_project.py", "WORKFLOW.md", "config_TEMPLATE.json"]:
        src = REPO_PIPELINE / fn
        if src.exists():
            shutil.copy2(src, WORK / fn)
    print("✅ パイプラインスクリプトコピー完了")

    # ── 4. フォント取得 ───────────────────────────────────
    # short_pipeline.py の get_fonts() を呼ぶ（初回のみDLが走る）
    build = WORK / "build"
    if not (build / "ArchivoBlack-Regular.ttf").exists():
        print("   フォントをダウンロード中...")
        sys.path.insert(0, str(WORK))
        try:
            import short_pipeline as sp
            sp.get_fonts()
        except Exception as e:
            print(f"  ⚠️  フォント取得失敗: {e}")
            print("     次回パイプライン実行時に自動ダウンロードされます")
    else:
        print("✅ フォント確認済み")

    # ── 5. チェック ───────────────────────────────────────
    print("\n" + "=" * 50)
    required = [
        WORK/"assets/logo_raw.png",
        WORK/"assets/icon_raw.png",
        WORK/"assets/hooks/HOOK_01_real.png",
        WORK/"assets/hooks/HOOK_04_price.png",
        WORK/"assets/bgm/Eight_Point_Stance.mp3",
        WORK/"assets/bgm/Copper_Keys.mp3",
    ]
    all_ok = True
    for p in required:
        ok = p.exists()
        mark = "✅" if ok else "❌"
        print(f"  {mark} {p.relative_to(WORK)}")
        if not ok: all_ok = False

    print("=" * 50)
    if all_ok:
        print("\n🎉 セットアップ完了！")
        print("   次のステップ: Driveから写真をダウンロードして動画制作を開始")
        print("   → WORKFLOW.md を参照")
    else:
        print("\n⚠️  不足ファイルがあります。リポジトリをpullして再実行してください:")
        print("   cd /home/user/50dollarfigure-site && git pull")
        print("   python3 pipeline/setup.py")


if __name__ == "__main__":
    main()
