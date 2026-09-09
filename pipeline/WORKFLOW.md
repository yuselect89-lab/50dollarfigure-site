# $50 FIGURE ショート動画制作ワークフロー

> このドキュメントはClaudeが新しい商品の動画を作るときに参照する手順書です。

---

## ゴール

GoogleドライブのプライズフォルダにYutaさんが素材フォルダを作ったら、
**同じクオリティ・同じ構成でショート動画を生成し、YouTube用タイトル・概要欄も同時出力する。**

---

## 全体フロー

```
① Yutaさんが Drive に素材フォルダを作成
② Claude が写真をローカルにダウンロード
③ new_project.py でコンフィグJSON雛形を生成
④ コンフィグの ★マーク★ 項目を埋める
⑤ short_pipeline.py でレンダリング
⑥ 動画 + _post.txt（YouTube情報）を確認
⑦ Yutaさんに送る
```

---

## ① Driveフォルダ構成（Yutaさんの作業）

```
📁 [作品名_キャラ名] (例: uzaki_swimsuit / luffy_gear5)
    IMG_0001.HEIC
    IMG_0002.HEIC
    ...
```

- ファイル名は変更不要（HEIC/JPG/PNG どれでもOK）
- **最低7枚**あるとフラッシュカット（冒頭の連続カット）がフルに使える
- 10〜15枚が最適。それ以上あってもショーケースは全部使う

---

## ② 写真をローカルにダウンロード

```python
# Drive MCP ツールを使う
# 1. フォルダID を確認（URLの /folders/XXXXXX 部分）
# 2. mcp__Google_Drive__list_folder_items でファイル一覧取得
# 3. 各ファイルを mcp__Google_Drive__download_file_content でダウンロード
#    → /home/claude/pipeline/photos/<プロジェクト名>/ に保存
```

**注意：** Drive MCPは大きいファイル（10MB超）が途中で切れることがある。
HEIC写真は1枚あたり3〜8MB程度なので通常は問題なし。
万が一切れた場合は再試行する。

---

## ③ コンフィグJSON雛形を生成

```bash
python3 /home/claude/pipeline/new_project.py \
    /home/claude/pipeline/photos/<プロジェクト名> \
    <output_name>
```

例：
```bash
python3 /home/claude/pipeline/new_project.py \
    /home/claude/pipeline/photos/rem_re_zero \
    rem_re_zero_short
```

→ `/mnt/user-data/outputs/rem_re_zero_short.json` が生成される

---

## ④ コンフィグの ★マーク★ 項目を埋める

生成されたJSONの以下の項目を確認・編集する：

| 項目 | 内容 | 例 |
|------|------|----|
| `franchise` | 作品名（英語） | `"Re:ZERO"` |
| `character` | キャラクター名（英語） | `"Rem"` |
| `line` | 商品ライン名 | `"Trio-Try-iT"` |
| `maker` | メーカー名 | `"TAITO"` |
| `condition` | コンディション | `"Used (Unopened / Like New Condition)"` |
| `banner_text` | 動画内のバナーテキスト（大文字） | `"RE:ZERO — REM"` |
| `pattern` | 動画パターン（通常は `"C"`） | `"C"` |

**showcase_order / flash_order の調整（任意）：**
- `showcase_order`: ショーケースで見せる順番。ハイライト写真を前に持ってくる
- `flash_order`: 冒頭フラッシュカットの7枚。最もインパクトのある写真を選ぶ
- `crop_offsets`: 0.5=中央 / 0.0=上端 / 1.0=下端。縦構図で顔が切れる場合は調整

**出品後に ebay_url を追記すると概要欄に自動で入る。**

---

## ⑤ 動画生成

```bash
python3 /home/claude/pipeline/short_pipeline.py \
    /mnt/user-data/outputs/<output_name>.json
```

出力物（`/mnt/user-data/outputs/` 内）：
- `<output_name>.mp4`      — メイン動画
- `<output_name>.srt`      — 字幕ファイル
- `<output_name>_post.txt` — YouTube/Instagram/TikTok 投稿テキスト
- `QA_<output_name>_seal.png` — QAフレーム（シール確認用）
- `QA_<output_name>_spec.png` — QAフレーム（スペックカード確認用）

---

## ⑥ 生成内容の確認チェックリスト

```
□ mp4 再生して冒頭フラッシュカット（7枚）確認
□ ショーケース写真が全枚表示されている
□ スペックカード（franchise / character / line / maker / condition）が正しい
□ バナーテキスト（動画上部）が正しい
□ BGM が最後まで鳴っている（無音になっていない）
□ アウトロが見切れていない
□ QA_seal.png: 縦向き・バナーあり・シール跡が見える
□ QA_spec.png: CREAM背景・テキスト切れなし
□ _post.txt: YouTubeタイトル・概要欄が正しく生成されている
```

---

## ⑦ Yutaさんへの送付

確認が取れたら `SendUserFile` で以下を送る：
1. `<output_name>.mp4`
2. `<output_name>_post.txt`

---

## よくあるトラブル

### ロゴが壊れている（broken data stream）
→ `logo_raw.png` が壊れている。`icon_raw.png` をコピーして代用する：
```bash
cp /home/claude/pipeline/assets/icon_raw.png \
   /home/claude/pipeline/assets/logo_raw.png
```

### BGMが途中で無音になる
→ `Eight_Point_Stance.mp3` は20秒以降が無音。
`EIGHT_MUSIC_END=20.0` で対処済み。20秒を超える場合は `Copper_Keys.mp3` にクロスフェード。

### 写真が縦に間延びする / 顔が切れる
→ 該当写真の `crop_offsets` を 0.5 から 0.3〜0.4 に調整（上方向にシフト）。

### Drive MCPでダウンロードが途切れる
→ ファイルサイズが大きい（10MB超）場合に発生。再試行する。
→ 解消しない場合はYutaさんにファイルを直接送ってもらうか、JPEGで再エクスポートを依頼。

---

## ファイル構成

```
/home/claude/pipeline/
├── short_pipeline.py    — メインパイプライン
├── new_project.py       — 新プロジェクト雛形生成ヘルパー
├── config_TEMPLATE.json — コンフィグ雛形（参照用）
├── WORKFLOW.md          — このファイル
├── assets/
│   ├── logo_raw.png     — ロゴ（Drive: 19N07PCdjNhYvAI_HBRYmzlBVu6JzH-j3）
│   ├── icon_raw.png     — アイコン（Drive: 1egL_8msFd9HkqEPkK4_t-_suviR13ETq）
│   ├── logo_final.png   — 処理済みロゴ（自動生成）
│   ├── icon_final.png   — 処理済みアイコン（自動生成）
│   ├── HOOK_01_real.png〜HOOK_05_protection.png  — フックカード
│   ├── Eight_Point_Stance.mp3  — BGM1（0〜20秒）
│   └── Copper_Keys.mp3         — BGM2（クロスフェード後）
├── photos/
│   ├── <プロジェクト名>/  — Driveからダウンロードした写真
│   └── ...
└── build/               — フォント・中間ファイル（自動生成）
```

---

## セキュリティルール（変更禁止）

> **仕入れ先（駿河屋等）は動画内に一切表示しない。**
> showcase オーバーレイには常に `"Bought in person in Japan"` を使用。
> `short_pipeline.py` 内でハードコードされており、コンフィグで変更不可。
