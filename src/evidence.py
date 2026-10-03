"""判定クエリの実行と Evidence の書き戻し。

方式A（APP_SPEC.md 第6節）: 判定のたびに urn:src:evidence を CLEAR し、全ルールを INSERT し直す。
CLEAR と INSERT は1つの update リクエストにまとめ、Fuseki 上で1トランザクションとして実行する。
ex:evaluatedAt は各判定クエリ内で NOW() から記録する。

方式B（世代を積む）へ切り替えるときは build_request() だけを差し替える。

使い方:
    uv run src/evidence.py              # 再判定して件数を表示
    uv run src/evidence.py L0037        # 再判定し、そのロットの根拠ノードを表示
"""

import sys

import pandas as pd

import fuseki

EVIDENCE_GRAPH = "urn:src:evidence"
LOT_NS = "http://example.org/kg/data/lot/"

# ルールID → 判定クエリ（queries/ 配下）。他ルールも同じ形で追加する
RULE_QUERIES = {
    "R01": "evidence_r01_supplier_defect.rq",
}


def build_request(rule_ids: list[str]) -> str:
    """方式A: CLEAR → 各ルールの INSERT。各 .rq は PREFIX 付きの独立した Update として連結する。"""
    ops = [f"CLEAR SILENT GRAPH <{EVIDENCE_GRAPH}>"]
    ops += [fuseki.load_query(RULE_QUERIES[r]) for r in rule_ids]
    # .rq 末尾がコメント行でも区切りが潰れないよう、';' は独立した行に置く
    return "\n;\n".join(ops)


def evaluate(rule_ids: list[str] | None = None) -> pd.DataFrame:
    """判定を実行して urn:src:evidence に書き戻し、ルール別の件数を返す。"""
    fuseki.update(build_request(rule_ids or list(RULE_QUERIES)))
    return counts()


def counts() -> pd.DataFrame:
    return fuseki.select(fuseki.load_query("evidence_count.rq"))


def evidence_for(lot_iri: str) -> pd.DataFrame:
    """1 ロットの Evidence と根拠ノード（subject / rule / fact）の一覧。"""
    query = fuseki.bind_values(fuseki.load_query("evidence_detail.rq"), "lot", lot_iri)
    return fuseki.select(query)


def edges_for(evidence_iris: list[str]) -> pd.DataFrame:
    """Evidence と根拠ノード間に実在するトリプル（列: s, p, o）。"""
    template = fuseki.load_query("evidence_edges.rq")
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
