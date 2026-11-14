# Jev Ultrafast マルチクライアント連携ガイド（Claude / Codex / Antigravity）

`jev-ultrafast` は、ローカル常駐の **SemIf**（意味決定モデル）と Chrome ブラウザ操作を組み合わせた超高速自律ブラウザエージェントです。
Model Context Protocol (MCP) を介して、主要な AI エージェント環境から直接ツールとして呼び出して活用できます。

---

## 1. 提供される MCP ツール

| ツール名 | 説明 | 主要引数 |
|---|---|---|
| `open_browse` (エイリアス: `jev_browse`) | 指定した URL をブラウザで開き、自然言語のゴールを自律的に達成するまで操作を実行 | `url` (必須), `goal` (必須), `max_steps` (任意, デフォルト 10) |
| `open_decide` (エイリアス: `jev_decide`) | 観測したページ状態とゴールから、SemIf を用いて次に実行すべきアクション（クリック/入力等）を判定 | `page` (必須), `goal` (必須), `history` (任意) |

> **意思決定来歴（Decision Provenance）の分離**:
> レスポンス結果には、ブラウザ実行結果（`action`, `elapsed_ms`, `final_page`）とは明確に分離された意思決定来歴（`backend: "semif"`, `calibration_surface: "semif_local"`, `model`, `question_spec_hash`, `decision_source`）が含まれます。単一候補要素の自動バイパス時は `decision_source="deterministic"`、推論時は `decision_source="model"` として記録され、Jev クラウドモデルと SemIf ローカルモデルのキャリブレーション特性の混同を防ぎます。

---

## 2. 各クライアントへの設定状況

### ① Claude Desktop
設定ファイルの登録例：

- **macOS** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "jev-ultrafast": {
      "command": "<path-to-jev-ultrafast>/.venv/bin/python",
      "args": [
        "<path-to-jev-ultrafast>/scripts/jev_mcp.py"
      ]
    }
  }
}
```

- **Windows** (`%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "jev-ultrafast": {
      "command": "C:\\AI\\jev-ultrafast\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\AI\\jev-ultrafast\\scripts\\jev_mcp.py"
      ]
    }
  }
}
```
※ Windows の場合は Python 実行ファイルが `.venv\Scripts\python.exe` となり、JSON 内のパスは `\\` でエスケープしてください。詳細は [WINDOWS_CLAUDE_CODE_GUIDE.md](WINDOWS_CLAUDE_CODE_GUIDE.md) も参照してください。

**利用方法**: Claude Desktop を再起動すると、チャット内で `open_browse`（または `jev_browse`）ツールが有効になります。
> 例: 「Google Flights（https://www.google.com/travel/flights?hl=en）でロンドンからチューリッヒへの9月20日の片道航空券を検索して」

---

### ② Claude Code
プロジェクト直下の `.mcp.json` または CLI コマンドで追加できます：
```bash
claude mcp add jev-ultrafast <path-to-jev-ultrafast>/.venv/bin/python <path-to-jev-ultrafast>/scripts/jev_mcp.py
```

---

### ③ Codex CLI
グローバル MCP サーバーとして登録する例（`codex mcp list` で確認可能）：
```bash
# 登録コマンド
codex mcp add jev-ultrafast -- <path-to-jev-ultrafast>/.venv/bin/python <path-to-jev-ultrafast>/scripts/jev_mcp.py
```
**利用方法**: `codex` コマンドでタスクを実行する際、Codex が必要に応じて自動的に `jev_browse` ツールを呼び出します。

---

### ④ Antigravity (Google DeepMind Antigravity IDE / CLI)
- **MCP 設定**: `~/.gemini/config/mcp_config.json` に `jev-ultrafast` サーバー設定を追加して利用します。
- **エージェントスキル**: `~/.gemini/config/skills/jev-ultrafast/SKILL.md` にスキル定義を配備して利用します。
Antigravity はブラウザ自動化タスクや航空券・Web フォーム操作のリクエストを受けた際、このスキルおよび MCP ツールを自律的に認識して活用します。

---

## 3. 前提条件（SemIf サーバー）
ブラウザ操作の判断には、ローカル常駐の SemIf サーバー（`http://127.0.0.1:8765`）が起動している必要があります：
```bash
# ヘルスチェック
curl -s http://127.0.0.1:8765/health
```
