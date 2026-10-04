# kg-demo — 製造・品質トレーサビリティのナレッジグラフ デモ

製造・品質トレーサビリティを題材にした RDF ナレッジグラフのデモ。

価値仮説は **「結論に至った理由を、オントロジーの繋がりを辿って説明できること」**。
「ロット L0003 は出荷保留」という判定に対して、どの検査・規格・部品・サプライヤーを経由してその結論に至ったかを、グラフ上の実在するトリプルの経路として示す。

データはすべて合成データ（`data/kg_toolkit.xlsx`）。

## 構成

| 要素 | 役割 |
| --- | --- |
| Apache Jena Fuseki（TDB2） | RDF ストアと SPARQL エンドポイント。データソースごとに名前付きグラフで管理する |
| SHACL（pyshacl） | 変換後の RDF を `ontology/shapes.ttl` で検証する。違反があれば投入しない |
| 名前付きグラフ | `urn:schema:kg`（スキーマ）、`urn:src:<シート名>`（事実データとルール、16 グラフ）、`urn:derived:chains`（導出プロパティ）、`urn:src:evidence`（判定結果）、`urn:whatif:evidence`（閾値 what-if の判定結果） |
| 導出プロパティ | `ex:lotUsesSupplier` / `ex:lotMadeOn` / `ex:partSuppliedBy`。`owl:propertyChainAxiom` を Reasoner を使わずに SPARQL INSERT で実体化する |
| 判定ルール | R01〜R03・R05 を SPARQL で判定し、結論・閾値・根拠事実を `ex:Evidence` として書き戻す |
| Streamlit + pyvis | CQ の実行、判定の根拠経路グラフ、閾値の what-if |
| Claude API | 根拠経路から説明文を生成する。自然文から SPARQL を生成する。数値の計算と判定は SPARQL / Python で行い、LLM には任せない |

## ディレクトリ構成

```
config/      Fuseki 設定（fuseki.ttl：TDB2、データは run/databases/kg）
data/        合成データ（kg_toolkit.xlsx）と変換結果（kg.trig、Git 管理外）
docs/        デモ手順書（DEMO.md）とオントロジー解説ページ（ontology_and_cq.html）
ontology/    スキーマ（schema.ttl）、SHACL（shapes.ttl）、URI 設計の規約（README.md）
prompts/     LLM 用プロンプト（説明文生成、自然文→SPARQL、関係の監査）
queries/     SPARQL（cq01〜cq10、判定 evidence_*、実体化 materialize_*、アプリ用 app_*）
scripts/     xlsx → TriG 変換 + SHACL 検証（xlsx_to_rdf.py）、Fuseki 接続確認（check_fuseki.py）
src/         投入（load.py）、判定（evidence.py）、検証（validate.py）、CQ 実行（run_cq.py）、LLM（llm.py）
src/ui/      Streamlit の各画面（起動は src/app.py）
tests/       アプリのヘッドレステスト（streamlit AppTest + スナップショット）
run/         Fuseki のデータベースとログ（Git 管理外）
```

## セットアップ（macOS / Homebrew。Docker は使わない）

前提：Homebrew。詳細な確認手順は [SETUP.md](SETUP.md) を参照。

```bash
# 1. Jena / Fuseki と uv（Java 17 以上は依存として入る）
brew install jena fuseki uv

# 2. Python 3.12 の仮想環境と依存パッケージ（pyproject.toml / uv.lock から）
uv sync

# 3. 環境変数
cp .env.example .env
#   ANTHROPIC_API_KEY を記入する（任意。下記「API キー未設定時の挙動」を参照）

# 4. Fuseki を起動（リポジトリ直下で。別ターミナルで起動したままにする）
fuseki-server --config=config/fuseki.ttl

# 5. 接続確認 → データ投入 → アプリ起動
uv run scripts/check_fuseki.py
uv run src/load.py
uv run streamlit run src/app.py
```

アプリは http://localhost:8501 、Fuseki の管理画面は http://localhost:3030 で開く。

### `.env` の設定

`.env.example` をコピーして使う。`.env` は `.gitignore` 済みで、コミットしない。

| キー | 内容 |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API キー。空でもアプリは起動する |
| `FUSEKI_URL` | Fuseki のデータセット URL。既定は `http://localhost:3030/kg` |

### API キー未設定時の挙動

`ANTHROPIC_API_KEY` が空の場合も、次の機能はそのまま動く。

- CQ01〜08 の実行、判定（R01〜R03・R05）と根拠経路グラフ
- タブ2「閾値を変える」の what-if 判定と差分
- タブ3「グラフを見る」

無効になるのは次の2つだけ。どちらも、画面に「ANTHROPIC_API_KEY が未設定のため…」と表示する。

- 判定の説明文の生成（サイドバーの「説明文を生成」がオフで固定される）
- タブ1 下部の自由入力（自然文 → SPARQL）

## よく使うコマンド

| コマンド | 内容 |
| --- | --- |
| `uv run src/load.py` | 変換 → SHACL 検証 → 投入 → 導出プロパティの実体化 → 判定、を一括で実行する |
| `uv run src/validate.py [--fuseki]` | SHACL 検証だけを行う（`--fuseki` なら Fuseki 上の全グラフを検証） |
| `uv run src/evidence.py [L0003]` | 判定だけをやり直す（ロット ID を渡すとその Evidence を表示） |
| `uv run src/run_cq.py cq06 http://example.org/kg/data/lot/L0003` | CQ をコマンドラインで実行する |
| `uv run pytest` | アプリのヘッドレステスト（Fuseki の起動と投入済みが前提） |

## ドキュメント

- [docs/DEMO.md](docs/DEMO.md)：デモ手順書。事前準備、CQ の進行台本と期待される結果、トラブル対処
- [docs/ontology_and_cq.html](docs/ontology_and_cq.html)：オントロジーとコンピテンシークエスチョン（CQ）の読み方。CQ を選ぶとオントロジー概念図上で経路を強調する（ブラウザで開く）
- [ontology/README.md](ontology/README.md)：名前空間・URI・名前付きグラフの設計規約
- [APP_SPEC.md](APP_SPEC.md)：アプリの仕様
