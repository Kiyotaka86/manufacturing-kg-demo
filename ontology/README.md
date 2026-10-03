# ontology/README.md — 名前空間・URI設計の規約

`data/kg_toolkit.xlsx` の `README` / `dictionary` シートの内容を要約したもの。
CLAUDE.md の「名前空間・URI設計は ontology/README.md に従う」の参照先。

## 名前空間

| prefix | URI | 用途 |
| --- | --- | --- |
| `ex:`   | `http://example.org/kg/ontology#` | クラス・プロパティ（スキーマ本体） |
| `data:` | `http://example.org/kg/data/`     | インスタンスデータ |

いずれも予行練習用の仮名前空間（`ontology/schema.ttl` 冒頭のコメント参照）。

## 名前付きグラフ

- `urn:src:<sheet名>` … `data/kg_toolkit.xlsx` の各データシートに対応する16グラフ
  （15データシート: products, parts, suppliers, customers, bom, equipment, specs,
  purchases, lots, lot_operations, lot_parts, inspections, maintenance,
  orders_shipments, defects + rules シート）。`scripts/xlsx_to_rdf.py` の `MAP` が
  シート名→グラフ名の対応を保持する。
- `urn:derived:chains` … `owl:propertyChainAxiom`（`ex:lotUsesSupplier`,
  `ex:lotMadeOn`）を Fuseki の Reasoner を使わずに SPARQL INSERT で実体化した結果を
  格納するグラフ（`queries/materialize_*.rq`、`src/load.py` が投入時に実行）。
- スキーマ（`ontology/schema.ttl`）自体は既定グラフに投入する。
  `config/fuseki.ttl` で `tdb2:unionDefaultGraph true` としているため、
  クエリ側は `GRAPH` 句なしで全グラフを横断参照できる。

## URI パターン（インスタンス）

`data:<class>/<id>` の形式。`<class>` はオントロジーのクラス名の先頭を小文字化した
lowerCamelCase（`scripts/xlsx_to_rdf.py` の `cls[0].lower()+cls[1:]` 変換に対応）。

例:
- `data:lot/L0003`（`ex:Lot`）
- `data:supplier/S005`（`ex:Supplier`）
- `data:bomLine/B001`（`ex:BomLine`）
- `data:partConsumption/LP003`（`ex:PartConsumption`）
- `data:rule/R01`（`ex:Rule`。ID列は `rule_id`）

`<id>` は各シートの主キー列（`product_id`, `lot_id`, `rule_id` など）の値をそのまま使う。
名寄せは対象外（CLAUDE.md）のため、シート間でIDが一意である前提。

## データ辞書

各シートの列・型・説明は `data/kg_toolkit.xlsx` の `dictionary` シート（79行、
15データシート分）を参照。列名はそのまま `ex:<列名>` のデータタイププロパティに
マッピングされる（`ontology/schema.ttl` の「データタイププロパティ」節、
`scripts/xlsx_to_rdf.py` の `lit()` 関数）。

## 判断ルール（rules シート）とコンピテンシークエスチョン（cq シート）

- `rules` シート（R01〜R08）は `ex:Rule` インスタンス（`data:rule/R01` 等）として
  他データと同様に named graph `urn:src:rules` に投入される。閾値
  （`ex:threshold`, `ex:threshold_unit`）はハードコードせず、SPARQL からこのリソースを
  参照することで「結論に至った理由をトリプル経路で説明できる」という価値仮説に沿わせる。
- `cq` シート（CQ01〜CQ10、コンピテンシークエスチョン）は `queries/cq01.rq` 〜
  `queries/cq10.rq` に実装済み。各クエリのコメントに元の `path` 列・関連ルール・
  実装上の簡略化を記載している。

## 埋め込みシナリオ（README シートより）

合成データには検証用の意図的なシナリオが埋め込まれている。CQ の期待結果を確認する際の
目安にする:
- `S005`（サプライヤー評価2）の部品を使ったロットに不良が集中（R01 該当）
- `E003`（設備）は保全間隔30日を超過し、不良と進行中ロットが集中（R02, R06, R07 該当）
- 同一 `spec_id` を持つ部品が2つずつあり、代替候補（R04, R05）が見つかる

全て合成データであり、企業名・人名は架空（xlsx README シート「注意」より）。
