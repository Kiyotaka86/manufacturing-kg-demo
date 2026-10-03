# SETUP.md — macOS 環境構築手順（Claude Code 実行用）

このファイルを Claude Code に読ませて実行させる。**Phase 0 の結果を報告して停止し、ユーザー承認後に Phase 1 以降へ進むこと。**

---

## Phase 0: 前提チェック（インストールは行わない）

以下を実行し、結果を表にまとめて報告せよ。不足があれば Phase 1 の該当項目を提示し、**承認を待つ**。

```bash
sw_vers                                  # macOS バージョン（13以上を推奨）
uname -m                                 # arm64 / x86_64
xcode-select -p 2>/dev/null || echo "NO_CLT"   # Command Line Tools
command -v brew && brew --version || echo "NO_BREW"
command -v java && java -version 2>&1 || echo "NO_JAVA"
command -v uv && uv --version || echo "NO_UV"
command -v python3 && python3 --version
command -v docker && docker --version || echo "NO_DOCKER (任意)"
command -v fuseki-server && echo "FUSEKI_OK" || echo "NO_FUSEKI"
command -v riot && echo "JENA_CLI_OK" || echo "NO_JENA_CLI"
lsof -iTCP:3030 -sTCP:LISTEN || echo "PORT_3030_FREE"
lsof -iTCP:8501 -sTCP:LISTEN || echo "PORT_8501_FREE"
df -h ~ | tail -1                        # 空き容量（5GB以上あれば十分）
[ -n "$ANTHROPIC_API_KEY" ] && echo "API_KEY_SET" || echo "API_KEY_NOT_SET"
```

判定基準:
- Homebrew と Java（17以上）が無ければ必須インストール対象
- Docker は **不要**（Homebrew ネイティブ構成を採用）。ある場合も使わない
- ポート 3030 / 8501 が使用中なら、使用プロセスを報告して停止（勝手に kill しない）

---

## Phase 1: 基盤ツール（不足分のみ）

```bash
# Command Line Tools
xcode-select --install

# Homebrew（未導入時）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# uv
brew install uv
```

---

## Phase 2: Apache Jena / Fuseki

```bash
brew install jena fuseki
```

- Java は依存として自動導入される。`java -version` で 17 以上を確認
- `riot --version` と `fuseki-server --version` で導入確認
- Fuseki のホームは `brew --prefix fuseki` 配下。データは本リポジトリの `run/` に置く

### Fuseki 設定（名前付きグラフ + TDB2）

`config/fuseki.ttl` を作成:

```turtle
@prefix fuseki: <http://jena.apache.org/fuseki#> .
@prefix rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix tdb2:   <http://jena.apache.org/2016/tdb#> .
@prefix ja:     <http://jena.hpl.hp.com/2005/11/Assembler#> .

[] rdf:type fuseki:Server ;
   fuseki:services ( :kg ) .

:kg rdf:type fuseki:Service ;
   fuseki:name "kg" ;
   fuseki:endpoint [ fuseki:operation fuseki:query ] ;
   fuseki:endpoint [ fuseki:operation fuseki:update ] ;
   fuseki:endpoint [ fuseki:operation fuseki:gsp-rw ] ;
   fuseki:dataset :kgDataset .

:kgDataset rdf:type tdb2:DatasetTDB2 ;
   tdb2:location "run/databases/kg" ;
   tdb2:unionDefaultGraph true .
```

起動: `fuseki-server --config=config/fuseki.ttl`
確認: `curl -s "http://localhost:3030/kg/query" --data-urlencode "query=SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }"`

---

## Phase 3: Python 環境

```bash
uv init --python 3.12
uv add rdflib SPARQLWrapper pyshacl owlrl \
       pandas openpyxl Faker pydantic \
       anthropic python-dotenv streamlit pyvis httpx
uv add --dev pytest ruff jupyter
```

`.env` を作成（`.gitignore` に追加）:
```
ANTHROPIC_API_KEY=
FUSEKI_URL=http://localhost:3030/kg
```

ディレクトリ作成:
```bash
mkdir -p ontology shapes data/raw data/rdf queries src app scripts config run
```

---

## Phase 4: 動作検証（すべて通ること）

1. `uv run python -c "import rdflib, SPARQLWrapper, pyshacl, anthropic, streamlit, pyvis; print('OK')"`
2. `scripts/check_fuseki.py` を作成し、以下を実行して成功を確認:
   - サンプル Turtle（3トリプル程度）を名前付きグラフ `<urn:src:test>` へ Graph Store Protocol で PUT
   - SPARQL で件数取得 → 3 が返る
   - 当該グラフを DELETE
3. `shapes/test.ttl` に最小 SHACL を置き、`uv run python -m pyshacl data/rdf/sample.ttl -s shapes/test.ttl` が通る
4. `riot --validate data/rdf/sample.ttl` が通る
5. `uv run streamlit hello` が 8501 で起動する（確認後停止）

検証結果を表で報告して終了。

---

## 補足
- Protégé（オントロジー編集 GUI）は手動インストール。Claude Code の対象外
- Docker Compose 構成は本番 POC で必要になった時点で `docker/` に追加する（任意）
