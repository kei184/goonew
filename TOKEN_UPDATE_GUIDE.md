# GitHub認証トークンの更新ガイド

## 問題
現在の認証トークンに`workflow`スコープがないため、`.github/workflows/`ディレクトリのファイルをプッシュできません。

## 解決手順

### 1. GitHubで新しいPersonal Access Tokenを生成

1. GitHubにログイン
2. 右上のアイコン → **Settings** をクリック
3. 左サイドバーの一番下 → **Developer settings** をクリック
4. **Personal access tokens** → **Tokens (classic)** をクリック
5. **Generate new token** → **Generate new token (classic)** をクリック

### 2. トークンの設定

**Note（名前）**: `goonew-workflow-access` など分かりやすい名前

**Expiration（有効期限）**: お好みで設定（90日など）

**Select scopes（スコープ）**: 以下にチェックを入れる
- ✅ `repo` （すべてのサブ項目も自動でチェックされます）
- ✅ `workflow` ← **これが重要！**

### 3. トークンを生成してコピー

1. ページ下部の **Generate token** をクリック
2. 表示されたトークン（`ghp_`で始まる文字列）を**必ずコピー**
   - ⚠️ このページを離れると二度と表示されません！

### 4. Git認証情報を更新

#### Windows（資格情報マネージャー使用）

1. Windowsの検索で「資格情報マネージャー」を開く
2. **Windows資格情報** タブを選択
3. `git:https://github.com` を探して展開
4. **編集** をクリック
5. パスワード欄に新しいトークンを貼り付け
6. **保存** をクリック

#### または、コマンドラインで強制的に再認証

```powershell
# 現在の認証情報を削除
git credential reject
protocol=https
host=github.com

# 次回のgit操作時に新しいトークンの入力を求められます
git push origin main
# ユーザー名: kei184
# パスワード: [新しいトークンを貼り付け]
```

### 5. プッシュを再試行

```powershell
git push origin main
```

## トラブルシューティング

### 「authentication failed」エラーが出る場合

Gitの認証情報キャッシュをクリアします:

```powershell
git config --global --unset credential.helper
git config --system --unset credential.helper
```

その後、再度プッシュを試みると認証情報の入力を求められます。

### それでもうまくいかない場合

HTTPSの代わりにSSHを使用する方法もありますが、設定が複雑なため、まずはトークンの更新をお試しください。
