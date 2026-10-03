# CLAUDE.md — ナレッジグラフデモ（製造・品質トレーサビリティ）

## 目的
エンタープライズ向けナレッジグラフPOCの予行練習。RDF（Apache Jena Fuseki）前提。
価値仮説：「結論に至った理由をオントロジーの繋がり（トリプル経路）で説明できること」。

## 技術スタック
- グラフDB: Apache Jena Fuseki（TDB2、名前付きグラフでソース別管理）
- Python 3.12 / uv（`uv run` 経由で実行。`pip` 直接使用禁止）
- RDF: rdflib, SPARQLWrapper, pyshacl
- LLM: anthropic SDK（自然文→SPARQL生成、結果→説明文生成に限定。数値計算はSPARQL/Python側）
- UI: Streamlit + pyvis

## ディレクトリ
```
ontology/   OWL/RDFS スキーマ（v0, v1 と版管理）
shapes/     SHACL シェイプ
data/raw/   元データ（CSV/Excel/JSON 混在、名寄せは対象外）
data/rdf/   生成した Turtle
queries/    コンピテンシークエスチョン別 SPARQL（cq01.rq …）
src/        ETL・投入・検証スクリプト
app/        Streamlit アプリ
scripts/    環境チェック・起動スクリプト
```

## よく使うコマンド
- 環境構築: `SETUP.md` を参照（初回のみ）
- Fuseki 起動: `fuseki-server --config=config/fuseki.ttl`
- 接続確認: `uv run scripts/check_fuseki.py`
- データ投入: `uv run src/load.py`
- SHACL検証: `uv run src/validate.py`
- アプリ: `uv run streamlit run app/main.py`
- テスト: `uv run pytest`

## 規約
- 名前空間・URI設計は `ontology/README.md` に従う
- SPARQL は `queries/` に保存し、Python に埋め込まない
- 秘密情報は `.env` のみ（コミット禁止）
- 対象外: 名寄せ、セキュリティ考慮、成果物テンプレート整備
