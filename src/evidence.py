"""判定クエリの実行と Evidence の書き戻し。

方式A（APP_SPEC.md 第6節）: 判定のたびに urn:src:evidence を CLEAR し、全ルールを INSERT し直す。
CLEAR と INSERT は1つの update リクエストにまとめ、Fuseki 上で1トランザクションとして実行する。
ex:evaluatedAt は各判定クエリ内で NOW() から記録する。

方式B（世代を積む）へ切り替えるときは build_request() だけを差し替える。

what-if（タブ2）: 同じ判定クエリを、閾値の VALUES を差し替えて urn:whatif:evidence に書き込む。
rules グラフと urn:src:evidence は書き換えない。Evidence の IRI も evidence/whatif/ 配下に分け、
既定グラフ（全グラフの和）で確定版と what-if 版が混ざらないようにする。
読み出し系の関数は graph 引数でどちらの Evidence を読むかを選ぶ。

使い方:
    uv run src/evidence.py              # 再判定して件数を表示
    uv run src/evidence.py L0037        # 再判定し、そのロットの根拠ノードを表示
"""

import sys

import pandas as pd

import fuseki

EVIDENCE_GRAPH = "urn:src:evidence"
WHATIF_GRAPH = "urn:whatif:evidence"
EVIDENCE_NS = "http://example.org/kg/data/evidence/"
# Evidence グラフ → Evidence IRI の名前空間
GRAPH_NS = {EVIDENCE_GRAPH: EVIDENCE_NS, WHATIF_GRAPH: EVIDENCE_NS + "whatif/"}
LOT_NS = "http://example.org/kg/data/lot/"

# ルールID → 判定クエリ（queries/ 配下）。他ルールも同じ形で追加する
RULE_QUERIES = {
    "R01": "evidence_r01_supplier_defect.rq",
    "R02": "evidence_r02_maint_overdue.rq",
    "R03": "evidence_r03_inspection_ng.rq",
    "R05": "evidence_r05_alternative.rq",
}


# 結論の重さ（小さいほど重い）。画面・説明文ともこの順に並べる。未知の結論は最後
CONCLUSION_ORDER = {"出荷保留": 0, "要注意": 1, "代替候補": 2, "判定不能": 3}
UNDETERMINED = "判定不能"

# 判定の前提になるルール（R05 は R04 該当サプライヤーの部品だけを対象にする）。説明文に前提として渡す
RULE_PRECONDITIONS = {"R05": ["R04"]}

# what-if で差し替えられる VALUES 変数（ルール別）。判定クエリ内の VALUES 行と対応する
OVERRIDE_VARS = {"R01": "thresholdOverride", "R02": "intervalFactor", "R03": "thresholdOverride",
                 "R05": "thresholdOverride"}


def order_by_conclusion(detail: pd.DataFrame) -> list[str]:
    """Evidence IRI を結論の重い順（同じ重さならルールID順）に並べる。"""
    heads = detail.drop_duplicates("evidence")
    key = heads.apply(lambda r: (CONCLUSION_ORDER.get(r.conclusion, len(CONCLUSION_ORDER)), r.ruleId), axis=1)
    return heads.assign(_k=key).sort_values("_k")["evidence"].tolist()


def retarget(text: str, graph: str) -> str:
    """クエリ中の <urn:src:evidence> と Evidence IRI の名前空間を、指定グラフ用に差し替える。"""
    if graph == EVIDENCE_GRAPH:
        return text
    return text.replace(f"<{EVIDENCE_GRAPH}>", f"<{graph}>").replace(EVIDENCE_NS, GRAPH_NS[graph])


def load(name: str, graph: str = EVIDENCE_GRAPH) -> str:
    return retarget(fuseki.load_query(name), graph)


def build_request(rule_ids: list[str], graph: str = EVIDENCE_GRAPH,
                  overrides: dict[str, str] | None = None) -> str:
    """方式A: CLEAR → 各ルールの INSERT。各 .rq は PREFIX 付きの独立した Update として連結する。

    overrides: ルールID → VALUES に入れる値（R01/R03 は閾値、R02 は保全間隔に対する倍率）。
    """
    overrides = overrides or {}
    ops = [f"CLEAR SILENT GRAPH <{graph}>"]
    for r in rule_ids:
        text = load(RULE_QUERIES[r], graph)
        if r in overrides:
            text = fuseki.set_values(text, OVERRIDE_VARS[r], overrides[r])
        ops.append(text)
    # .rq 末尾がコメント行でも区切りが潰れないよう、';' は独立した行に置く
    return "\n;\n".join(ops)


def evaluate(rule_ids: list[str] | None = None, graph: str = EVIDENCE_GRAPH,
             overrides: dict[str, str] | None = None) -> pd.DataFrame:
    """判定を実行して graph に書き戻し、ルール別・結論別の件数を返す。"""
    fuseki.update(build_request(rule_ids or list(RULE_QUERIES), graph, overrides))
    return counts(graph)


def clear(graph: str) -> None:
    fuseki.update(f"CLEAR SILENT GRAPH <{graph}>")


def counts(graph: str = EVIDENCE_GRAPH) -> pd.DataFrame:
    return fuseki.select(load("evidence_count.rq", graph))


def judged_lots(graph: str = EVIDENCE_GRAPH) -> pd.DataFrame:
    """Evidence が付いたロットとルール・結論の一覧（列: lot, ruleId, conclusion）。"""
    return fuseki.select(load("evidence_lots.rq", graph))


def evidence_for(lot_iri: str, graph: str = EVIDENCE_GRAPH) -> pd.DataFrame:
    """1 ロットの Evidence と根拠ノード（subject / rule / fact）の一覧。"""
    return fuseki.select(fuseki.bind_values(load("evidence_detail.rq", graph), "lot", lot_iri))


def node_props_for(lot_iri: str, graph: str = EVIDENCE_GRAPH) -> pd.DataFrame:
    """根拠ノード（subject / fact）のリテラル属性（列: node, p, o）。測定値・規格上下限・日付など。"""
    return fuseki.select(fuseki.bind_values(load("evidence_node_props.rq", graph), "lot", lot_iri))


def edges_for(evidence_iris: list[str], graph: str = EVIDENCE_GRAPH) -> pd.DataFrame:
    """Evidence と根拠ノード間に実在するトリプル（列: s, p, o）。"""
    template = load("evidence_edges.rq", graph)
    frames = [fuseki.select(fuseki.bind_values(template, "evidence", iri)) for iri in evidence_iris]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["s", "p", "o"])


def main() -> None:
    print(evaluate().to_string(index=False))
    if len(sys.argv) > 1:
        df = evidence_for(LOT_NS + sys.argv[1])
        with pd.option_context("display.max_rows", None, "display.width", 250):
            print(df.to_string(index=False) if len(df) else f"{sys.argv[1]}: Evidence なし")


if __name__ == "__main__":
    main()
