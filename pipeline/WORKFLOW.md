# $50 FIGURE ショート動画制作ワークフロー

> このドキュメントはClaudeが新しい商品の動画を作るときに参照する手順書です。

---

## 新チャット開始時に最初にやること（必須）

```bash
# リポジトリをcloneして（まだなら）
git clone https://github.com/yuselect89-lab/50dollarfigure-site /home/user/50dollarfigure-site

# セットアップ実行（アセット展開・フォントDL）
python3 /home/user/50dollarfigure-site/pipeline/setup.py
```

これだけで全素材が `/home/claude/pipeline/` に揃い、すぐ動画制作に入れる。

---

## ゴール

YutaさんがDriveに写真フォルダを作ったと伝えたら、
**Notionの在庫トラッカーから商品情報を自動取得 → 動画生成 → YouTube投稿テキスト出力** まで完結させる。

---

## 全体フロー

```
① Yutaさんが Drive に素材フォルダを作成し、Claudeに伝える
② Claude が Notion 在庫トラッカーから商品情報を取得
③ Claude が Drive から写真をローカルにダウンロード
④ new_project.py で写真スキャン + Notionデータ込みのコンフィグJSON生成
⑤ short_pipeline.py でレンダリング
⑥ 動画 + _post.txt（YouTube情報）を確認してYutaさんに送る
```

> **注意:** ①のフォルダ作成は自動トリガーにはならない。
> Yutaさんがチャットで「〇〇の動画作って」と言うことで始まる。

---

## ② Notion から商品情報を取得

データソース: `collection://d7b55c79-19e2-45ba-bf41-81e72df196bf`

Claudeが以下のプロパティを読み取り、Notionデータとして取り出す：

| Notion プロパティ | CONFIGのキー | 備考 |
|------------------|-------------|------|
| `✍️ シリーズ・作品名` | `franchise` | 例: `One Piece` |
| `✍️ 商品名` | `character` | キャラ名＋バリエーション |
| `✍️ メーカー` | `maker` | 例: `TAITO` |
| `✍️ コンディション` | `condition` | 例: `Used (Unopened / Like New Condition)` |

`line`（商品ライン名）と `banner_text` は商品名から推定するか、Yutaさんに確認する。

**Notion取得後、以下のJSONを `/tmp/<output_name>_notion.json` に保存する：**

```json
{
  "franchise":   "One Piece",
  "character":   "Monkey D. Luffy Gear 5",
  "line":        "Grandista",
  "maker":       "Bandai Spirits",
  "condition":   "Used (Unopened / Like New Condition)",
  "banner_text": "ONE PIECE — LUFFY GEAR 5"
}
```

---

## ③ Drive から写真をダウンロード

```
Drive フォルダID → mcp__Google_Drive__list_folder_items → ファイル一覧
各ファイル → mcp__Google_Drive__download_file_content → ローカル保存
保存先: /home/claude/pipeline/photos/<プロジェクト名>/
```

**注意:**
- HEIC/JPG/PNG どれでも処理できる（pillow-heif で自動変換）
- 1枚あたり3〜8MBなので通常は問題なし
- 稀に大きいファイルが切れる → 再試行する

---

## ④ コンフィグJSON生成

```bash
python3 /home/claude/pipeline/new_project.py \
    /home/claude/pipeline/photos/<プロジェクト名> \
    <output_name> \
    /tmp/<output_name>_notion.json
```

→ `/mnt/user-data/outputs/<output_name>.json` が生成される（Notionデータ込み）

**写真順の調整（任意）:**
- `showcase_order`: 最初がハイライット写真になるよう並び替える
- `flash_order`: 冒頭フラッシュの7枚、インパクト重視で選ぶ
- `crop_offsets`: 0.5=中央 / 0.3=上よりに / 0.7=下よりに（顔が切れる場合に調整）

---

## ⑤ 動画生成

```bash
python3 /home/claude/pipeline/short_pipeline.py \
    /mnt/user-data/outputs/<output_name>.json
```

出力物（`/mnt/user-data/outputs/` 内）：
- `<output_name>.mp4`         — メイン動画（1080×1920、30fps）
- `<output_name>.srt`         — 字幕ファイル
- `<output_name>_post.txt`    — YouTube/Instagram/TikTok 投稿テキスト
- `QA_<output_name>_seal.png` — QAフレーム（シール確認用）
- `QA_<output_name>_spec.png` — QAフレーム（スペックカード確認用）

---

## ⑥ 確認チェックリスト

```
□ mp4 再生して冒頭フラッシュカット（7枚）確認
□ ショーケース写真が全枚表示されている
□ スペックカード（franchise / character / line / maker / condition）がNotionと一致
□ バナーテキストが正しい
□ BGM が最後まで鳴っている
□ アウトロが見切れていない
□ QA_seal.png: 縦向き・バナーあり
□ QA_spec.png: CREAM背景・テキスト切れなし
□ _post.txt: YouTubeタイトル・概要欄が正しく生成されている
```

確認OK → `SendUserFile` で mp4 + _post.txt をYutaさんへ送る

---

## YouTube 投稿テキストの構成（_post.txt）

```
【YouTube タイトル】
$50 for This {franchise} {character} Figure? | $50 FIGURE #Shorts

【YouTube 概要欄】
{franchise} × {character} ({line}) by {maker}.
Condition: {condition}. Sourced from Japan.

$50 flat — shipping & import duties included.
👉 50dollarfigure.com          ← eBayへはここから飛ぶ

─────────────────
⚠️  AI-generated promo images used for visual effect.
─────────────────

#{franchise} #{character} ... #AnimeFigure #PrizeFigure ...

【Instagram / TikTok キャプション】（〜180字）
...
```

---

## よくあるトラブル

### ロゴが壊れている
```bash
cp /home/claude/pipeline/assets/icon_raw.png \
   /home/claude/pipeline/assets/logo_raw.png
```

### BGMが途中で無音になる
`Eight_Point_Stance.mp3` は20秒以降が無音。`EIGHT_MUSIC_END=20.0` で対処済み。

### 写真の顔が切れる
該当写真の `crop_offsets` を 0.5 → 0.3〜0.4 に調整。

---

## セキュリティルール（変更禁止）

> **仕入れ先（駿河屋等）は動画内に一切表示しない。**
> showcase オーバーレイには常に `"Bought in person in Japan"` を使用。
> `short_pipeline.py` 内でハードコードされており、コンフィグで変更不可。

---

## ファイル構成

```
/home/claude/pipeline/
├── short_pipeline.py       — メインパイプライン
├── new_project.py          — 写真スキャン + Notionデータ込みコンフィグ生成
├── config_TEMPLATE.json    — 参照用テンプレート
├── WORKFLOW.md             — このファイル
├── assets/
│   ├── logo_raw.png / icon_raw.png
│   ├── HOOK_01〜HOOK_05.png
│   ├── Eight_Point_Stance.mp3
│   └── Copper_Keys.mp3
└── photos/
    └── <プロジェクト名>/   — Driveからダウンロードした写真
```
