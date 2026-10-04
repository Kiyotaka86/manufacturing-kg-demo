# アーキテクチャ

このデモの構成要素と、データが合成データ（xlsx）から判定・説明文になるまでの流れ。
図は Mermaid で書いている（GitHub 上ではそのまま図として表示される）。

## 1. 全体構成

```mermaid
flowchart LR
    subgraph SRC["データソース"]
        XLSX["data/kg_toolkit.xlsx<br/>15 データシート + rules"]
    end

    subgraph ONT["ontology/"]
        SCHEMA["schema.ttl<br/>OWL/RDFS・文型"]
        SHAPES["shapes.ttl<br/>SHACL"]
    end

    subgraph ETL["投入（uv run src/load.py）"]
        CONV["scripts/xlsx_to_rdf.py<br/>xlsx → TriG"]
        VAL{"SHACL 検証<br/>pyshacl"}
        LOAD["Graph Store Protocol<br/>で PUT"]
        MAT["queries/materialize_*.rq<br/>導出プロパティの実体化"]
        EVAL["src/evidence.py<br/>queries/evidence_r0*.rq"]
        VAL2{"SHACL 検証<br/>Evidence を含む全グラフ<br/>（根拠の有無・時系列）"}
    end

    subgraph FUSEKI["Apache Jena Fuseki（TDB2, :3030/kg）"]
        KG[("名前付きグラフ<br/>図2 を参照")]
    end

    subgraph APP["Streamlit アプリ（uv run streamlit run src/app.py, :8501）"]
        UI["src/app.py + src/ui/<br/>タブ1 質問する / タブ2 閾値を変える / タブ3 グラフを見る"]
        FQ["src/fuseki.py<br/>queries/*.rq を読んで実行"]
        VIZ["src/viz.py<br/>pyvis で根拠経路グラフ"]
        LLM["src/llm.py<br/>prompts/*.md"]
    end

    CLAUDE["Claude API<br/>説明文生成・自然文→SPARQL"]
    DOCS["docs/ontology_and_cq.html<br/>オントロジーと CQ の解説（静的）"]
    USER(("利用者<br/>ブラウザ"))

    XLSX --> CONV --> VAL
    SCHEMA --> CONV
    SHAPES --> VAL
    VAL -- "準拠" --> LOAD
    VAL -. "違反: 投入せず停止" .-> STOP["exit 1"]
    SCHEMA --> LOAD
    LOAD --> KG
    MAT --> KG
    EVAL --> KG
    KG --> VAL2
    VAL2 -. "違反: exit 1" .-> STOP

    USER --> UI
    USER --> DOCS
    UI --> FQ <--> KG
    UI --> VIZ
    UI --> LLM --> CLAUDE
    UI -- "再判定 / what-if" --> EVAL
```

| 要素 | 役割 | 方針 |
| --- | --- | --- |
| `scripts/xlsx_to_rdf.py` | シートごとに名前付きグラフ `urn:src:<シート名>` の TriG を作り、SHACL で検証する | 違反があれば投入しない（exit 1） |
| `src/load.py` | スキーマと 16 グラフを投入し、実体化と判定まで一括で実行する | Fuseki の Reasoner は使わない |
| `queries/` | CQ・判定・実体化・アプリ用の SPARQL をすべて置く | SPARQL を Python に埋め込まない。対象は `VALUES` 行を差し替える |
| `src/evidence.py` | 判定クエリを実行し、結果を `ex:Evidence` として書き戻す | CLEAR と INSERT を1リクエスト（1トランザクション）で実行する |
| `src/validate.py` | 判定後に Fuseki 上の全グラフを SHACL で検証する（`load.py` の最後でも実行する） | Evidence に根拠が付いていること、根拠事実の日付が評価時点より前であること、ロット判定の根拠の保全・購買が製造開始日以前であることを確かめる |
| `src/llm.py` | 根拠経路の三つ組から説明文を作る。自然文から SPARQL を作る | 数値計算と判定は LLM にさせない。生成した説明文は `audit()` で根拠データと突き合わせる。生成した SPARQL は承認後にだけ実行する |
| Claude API | 上記 2 つの生成だけを担う | API キーがなくても、判定・経路グラフ・what-if は動く |

## 2. 名前付きグラフの層

Fuseki は `tdb2:unionDefaultGraph true` で構成している。そのため、`GRAPH` 句のないクエリは全名前付きグラフの和を参照する。

```mermaid
flowchart TB
    G0["<b>スキーマ層</b><br/>urn:schema:kg<br/>クラス・プロパティ・rdfs:label・ex:sentenceTemplate"]
    G1["<b>事実層</b>（xlsx から投入、15 グラフ）<br/>urn:src:products / parts / suppliers / customers / equipment / specs<br/>urn:src:bom / purchases / lots / lot_operations / lot_parts<br/>urn:src:inspections / maintenance / orders_shipments / defects"]
    G4["<b>ルール</b><br/>urn:src:rules<br/>R01〜R08 の閾値・比較方向・最小標本数"]
    G5["<b>導出層</b><br/>urn:derived:chains<br/>lotUsesSupplier / lotMadeOn / partSuppliedBy"]
    G6["<b>説明層</b>（判定結果 ex:Evidence）<br/>urn:src:evidence … rules の閾値による確定の判定<br/>urn:whatif:evidence … タブ2 の閾値による what-if の判定"]

    G0 -. "語彙・ラベル・文型" .-> G1
    G1 -- "materialize_*.rq" --> G5
    G1 & G5 -- "evidence_r0*.rq" --> G6
    G4 -- "閾値・比較方向<br/>（what-if は VALUES で差し替え）" --> G6
    G6 -. "evidenceRule / evidenceSubject / evidenceFact<br/>根拠事実へのリンク" .-> G1
```

Evidence は、結論・適用ルール・根拠事実へのリンクを1ノードにまとめたものである。この Evidence から事実層へ辿り直せることが「結論に至った理由をトリプル経路で説明できる」ことの実体になる。

## 3. 判定から説明文まで（タブ1 の CQ06）

```mermaid
sequenceDiagram
    actor U as 利用者
    participant UI as Streamlit<br/>ui/judgment_view.py
    participant EV as evidence.py
    participant F as Fuseki
    participant V as viz.py
    participant L as llm.py
    participant C as Claude API

    U->>UI: CQ06 を選び、ロット L0003 を実行
    UI->>EV: evidence_for(L0003, graph)
    EV->>F: cq06 相当の SELECT（urn:src:evidence）
    F-->>EV: R03 出荷保留 / R01 要注意 / R02 判定不能
    UI->>EV: node_props_for() / edges_for()
    EV->>F: evidence_node_props.rq / evidence_edges.rq
    F-->>EV: 根拠ノードの属性と、Evidence から事実への辺
    UI->>V: render(detail, edges, props)
    V-->>UI: 根拠経路グラフ（pyvis の HTML）
    alt ANTHROPIC_API_KEY あり、かつ「説明文を生成」がオン
        UI->>L: evidence_payload() で三つ組と文型を組み立てる
        L->>C: prompts/evidence_to_text.md + 判定 JSON
        C-->>L: 説明文
        L->>L: audit() で根拠にない ID・名前・数値、推測表現を検出
        L-->>UI: 説明文 + 自己点検の結果
    else キーなし
        UI-->>U: 説明文は表示しない（判定と経路グラフは表示する）
    end
    UI-->>U: 判定・根拠経路・説明文
```

タブ2「閾値を変える」で再判定すると、`evidence.evaluate(graph=urn:whatif:evidence, overrides=…)` が同じ判定クエリを実行し、結果を what-if 用のグラフに書く。タブ1 の「参照する判定」で `urn:src:evidence` と `urn:whatif:evidence` を切り替えると、同じ流れで経路を比べられる。
