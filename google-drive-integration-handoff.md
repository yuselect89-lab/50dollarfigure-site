# Google Drive 画像自動取得機能 - 引継ぎドキュメント

## 概要
$50 FIGURE / YUSelect のサイトで、Notion 在庫トラッカーの商品写真を Google Drive から自動取得して貼る機能を実装。
現在、この機能が動作していません。

## 実装内容

### 何をしたいのか
- Google Drive の「プライズフォルダ」（ID: `1_1MnlneD79VFQiYy6-laIMCtVxBwZrc6`）に商品フォルダがある
- 各フォルダ内に商品画像がある（番号順ソート）
- Notion 在庫トラッカーの商品名と Google Drive フォルダ名は一致している
- 在庫トラッカーの「✍️ 商品写真」プロパティが空の場合、Google Drive から最初の 2 枚の画像 URL を自動取得して貼る

### コード変更

**`.github/workflows/sync-inventory.yml`**
- google-api-python-client、google-auth-oauthlib、google-auth-httplib2 をインストール依存関係に追加
- `GOOGLE_DRIVE_CREDENTIALS` 環境変数を `${{ secrets.GOOGLE_DRIVE_CREDENTIALS }}` から読み込む

**`sync_inventory.py`**

追加したインポート：
```python
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
```

追加した定数：
```python
GOOGLE_DRIVE_PRIZE_FOLDER_ID = "1_1MnlneD79VFQiYy6-laIMCtVxBwZrc6"
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
```

追加した関数：
- `build_drive_service(credentials_dict)` - サービスアカウント認証でドライブサービス初期化
- `find_folder_by_name(drive, parent_folder_id, folder_name)` - フォルダ名で検索（Drive API Query）
- `get_images_from_folder(drive, folder_id)` - フォルダ内の JPEG/PNG を取得、名前でソート
- `get_google_drive_image_urls(drive, product_name)` - 商品名でフォルダを探して、最初の 2 枚の画像 URL を返す
- `sync_product_images_from_drive(notion, drive)` - 在庫トラッカーをクエリ、空の商品写真を更新

main() 関数での動作：
1. GOOGLE_DRIVE_CREDENTIALS 環境変数を base64 デコード → JSON パース
2. `build_drive_service()` でドライブサービス初期化
3. `sync_product_images_from_drive(notion, drive)` を実行

### コミット
- `94e0b68` - Google Drive 認証情報を環境変数から読み込む基本実装
- `339ad26` - Google Drive API 統合（フォルダ検索・画像取得）
- `c557885` - Notion 自動更新機能（商品写真プロパティに画像 URL を追加）

ブランチ：`claude/notion-workspace-databases-glbxfm`

## 現在の問題

**症状：** Google Drive 画像同期が実行されていない

手動テスト実行時のログ：
```
Google Translate レート制限エラー（大量）
Found 10 listed (出品中) product(s)
Found 3 outlet product(s)
No changes to index.html
```

**想定される症状のログが出ていない：**
- ❌ 「Google Drive service initialized」
- ❌ 「Updated images for {product_name}」
- ❌ 「Synced images for X product(s) from Google Drive」
- ❌ 「GOOGLE_DRIVE_CREDENTIALS not set; Google Drive image sync disabled」
- ❌ 「Failed to initialize Google Drive: ...」

つまり：
- `if gcp_credentials_b64:` ブロックが実行されていない（環境変数が読み込まれていない）
- または、`sync_product_images_from_drive()` が実行されていない

## 確認・デバッグ項目

### 1. GitHub Secrets 確認
- リポジトリ → Settings → Secrets and variables → Actions
- `GOOGLE_DRIVE_CREDENTIALS` が登録されているか確認
- 登録内容が新しい秘密鍵の base64 エンコード値か確認（古い JSON ではないか）

### 2. Base64 エンコード確認
オンラインツール（https://www.base64encode.org/）で、新しい JSON ファイルを正しくエンコードしたか確認

### 3. コードのデバッグ
`sync_inventory.py` の main() 関数に、デバッグログを追加：
```python
gcp_credentials_b64 = os.environ.get("GOOGLE_DRIVE_CREDENTIALS")
print(f"DEBUG: gcp_credentials_b64 = {gcp_credentials_b64[:50] if gcp_credentials_b64 else None}...")
print(f"DEBUG: gcp_credentials_b64 is None: {gcp_credentials_b64 is None}")
```

### 4. 環境変数が GitHub Actions に渡されているか確認
`.github/workflows/sync-inventory.yml` の env セクション：
```yaml
- name: Sync inventory into index.html
  env:
    NOTION_API_KEY: ${{ secrets.NOTION_API_KEY }}
    GOOGLE_DRIVE_CREDENTIALS: ${{ secrets.GOOGLE_DRIVE_CREDENTIALS }}
  run: python sync_inventory.py
```

### 5. Google API ライブラリのインストール確認
インストール ステップでエラーが出ていないか確認：
```yaml
- name: Install dependencies
  run: pip install notion-client requests Pillow deep-translator google-api-python-client google-auth-oauthlib google-auth-httplib2
```

## 推奨次のステップ

1. **まず確認：** GitHub Secrets に `GOOGLE_DRIVE_CREDENTIALS` が登録されているか確認
   - 登録されていなければ、新しい JSON を base64 エンコードして登録

2. **コードのデバッグログを追加** して手動実行

3. **エラーメッセージを確認** - stderr に出力されている可能性

4. **必要に応じて実装修正** - 秘密鍵が正しく読み込まれても動かなければ、コード側に問題がある

## 参考情報

### プライズフォルダ構造（想定）
```
プライズフォルダ (1_1MnlneD79VFQiYy6-laIMCtVxBwZrc6)
├── Uzaki-chanのスワイムスーツ/
│   ├── 1_front.jpg
│   ├── 2_back.jpg
│   └── ...
├── ワンピース MAXIMATICPLUS .../
│   ├── 1_front.jpg
│   ├── 2_side.jpg
│   └── ...
└── ...
```

### Notion API（Files & media プロパティ更新形式）
```python
{
  "✍️ 商品写真": {
    "files": [
      {
        "type": "external",
        "name": "product_image_1",
        "external": {"url": "https://drive.google.com/uc?export=view&id=..."}
      },
      {
        "type": "external",
        "name": "product_image_2",
        "external": {"url": "https://drive.google.com/uc?export=view&id=..."}
      }
    ]
  }
}
```

## ユーザー要件

- **定期実行：** 毎日 00:00 UTC（日本時間 9:00）に自動実行
- **手動実行：** GitHub Actions から手動トリガーで即実行可能
- **商品写真が空の場合だけ更新：** 既存画像を上書きしない
- **若い番号から 2 枚取得：** ファイル名の辞書順ソート

