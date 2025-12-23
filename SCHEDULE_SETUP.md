# 日次スケジュール実行セットアップガイド

このガイドでは、GitHub Actionsを使用して毎日日本時間午前9時にスクレイピングスクリプトを自動実行する方法を説明します。

## 📋 前提条件

- GitHubアカウント
- このリポジトリがGitHubにプッシュされていること
- Google API認証情報（スプレッドシート用）

## ⚙️ セットアップ手順

### 1. GitHub Secretsの設定

GitHubリポジトリに機密情報を安全に保存します。

1. GitHubでリポジトリページを開く
2. **Settings** タブをクリック
3. 左サイドバーの **Secrets and variables** → **Actions** をクリック
4. **New repository secret** をクリック
5. 以下の3つのシークレットを追加:

#### `GOOGLE_CREDENTIALS_JSON`
- Name: `GOOGLE_CREDENTIALS_JSON`
- Secret: Google Cloud ConsoleからダウンロードしたサービスアカウントのJSON認証情報の**全内容**を貼り付け

#### `GOOGLE_API_KEY`
- Name: `GOOGLE_API_KEY`
- Secret: Google Custom Search APIキー

#### `GOOGLE_CSE_ID`
- Name: `GOOGLE_CSE_ID`
- Secret: Google Custom Search Engine ID

### 2. ワークフローファイルの確認

`.github/workflows/daily_scrape.yml` ファイルが存在することを確認してください。このファイルには以下の設定が含まれています:

- **実行時刻**: 毎日 UTC 0:00（日本時間 9:00）
- **手動実行**: GitHub UIから手動でも実行可能

### 3. リポジトリにプッシュ

ワークフローファイルをGitHubにプッシュします:

```bash
git add .github/workflows/daily_scrape.yml
git commit -m "feat: GitHub Actionsによる日次スクレイピングを追加"
git push origin main
```

### 4. GitHub Actionsの有効化確認

1. GitHubリポジトリページの **Actions** タブを開く
2. 「日次スクレイピング実行」というワークフローが表示されることを確認

## 🧪 動作確認

### 手動実行でテスト

初回は手動実行で動作確認することをおすすめします:

1. GitHubリポジトリの **Actions** タブを開く
2. 左サイドバーから **日次スクレイピング実行** を選択
3. 右側の **Run workflow** ボタンをクリック
4. **Run workflow** を再度クリックして実行開始
5. 実行状況を確認し、成功することを確認

### 実行履歴の確認

- **Actions** タブで過去の実行結果を確認できます
- 各実行のログを開いて詳細を確認できます
- 失敗した場合はエラーログを確認してください

## 📅 スケジュール設定の変更

実行時刻を変更したい場合は、`.github/workflows/daily_scrape.yml` の `cron` 設定を編集します:

```yaml
on:
  schedule:
    # 例: 毎日 UTC 1:00 = 日本時間 10:00
    - cron: '0 1 * * *'
```

### Cron構文リファレンス

```
*    *    *    *    *
│    │    │    │    │
│    │    │    │    └─ 曜日 (0-6, 0=日曜日)
│    │    │    └────── 月 (1-12)
│    │    └─────────── 日 (1-31)
│    └──────────────── 時 (0-23, UTC)
└───────────────────── 分 (0-59)
```

**よく使う設定例:**
- `0 0 * * *` - 毎日 UTC 0:00（日本時間 9:00）
- `0 1 * * *` - 毎日 UTC 1:00（日本時間 10:00）
- `0 0 * * 1` - 毎週月曜日 UTC 0:00
- `0 0 1 * *` - 毎月1日 UTC 0:00

## ⚠️ 注意事項

1. **実行時間の誤差**: GitHub Actionsのスケジュール実行は、混雑状況により±15分程度ずれる可能性があります
2. **無料枠**: GitHubの無料プランでは月2,000分まで実行可能です（このスクリプトは1回あたり数分程度）
3. **タイムゾーン**: cronはUTC基準なので、日本時間（JST = UTC+9）を考慮して設定してください

## 🔧 トラブルシューティング

### ワークフローが実行されない
- GitHub Actionsが有効になっているか確認
- リポジトリの **Settings** → **Actions** → **General** で「Allow all actions and reusable workflows」が選択されているか確認

### 認証エラーが発生する
- GitHub Secretsが正しく設定されているか確認
- シークレット名のスペルミスがないか確認
- JSON認証情報が完全にコピーされているか確認

### ChromeDriverのエラー
- GitHub Actionsでは自動的にChromeとChromeDriverがインストールされます
- エラーが続く場合はワークフローのバージョンを確認

## 📞 サポート

問題が解決しない場合は、GitHubリポジトリのIssuesで報告してください。
