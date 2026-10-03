"""根拠経路の pyvis 描画（APP_SPEC.md 第3節）。

ノードは evidence.evidence_for() の行、エッジは evidence.edges_for() が返す実在トリプルだけを使う。
描画側でエッジや数値を作り足さない（表示用の桁揃えのみ行う）。
"""

from decimal import Decimal, InvalidOperation

import pandas as pd
from pyvis.network import Network

MAX_NODES = 15

# 15ノードを超えたときに残す「閾値比較に直接使われた」根拠事実のクラス（ルール別）
THRESHOLD_FACT_TYPES = {
    "R01": {"Supplier"},  # サプライヤー単位の不良率を閾値と比較する
}

STYLE = {
    "subject": {"color": "#1f3b73", "shape": "dot", "size": 32, "font": {"color": "#1f3b73"}},
    "fact": {"color": "#8fd3f4", "shape": "dot", "size": 18},
    "rule": {"color": "#f28c28", "shape": "box", "font": {"color": "#ffffff"}},
    "evidence": {"color": "#7b3fa0", "shape": "diamond", "size": 24},
}


def local(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def fmt2(value: str) -> str:
    """表示用に小数2桁へ揃える（0.8 → 0.80）。値そのものは変えない。"""
    try:
        return str(Decimal(value).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError):
        return str(value)


def observed_text(row) -> str:
    """「16 / 20 = 0.80（閾値 0.05）」。分子・分母が無い Evidence は観測値のみ。"""
    value = fmt2(row.observedValue)
    if pd.notna(row.numerator) and pd.notna(row.denominator):
        value = f"{row.numerator} / {row.denominator} = {value}"
    return f"{value}（閾値 {row.threshold}）"


def node_label(row) -> str:
    ident = local(row.node)
    return f"{ident} {row.name}" if pd.notna(row.name) else ident


def select_nodes(detail: pd.DataFrame) -> pd.DataFrame:
    """ノード数が MAX_NODES を超える場合、閾値比較に直接使われたノードだけに絞る。"""
    n_nodes = detail["node"].nunique() + detail["evidence"].nunique()
    if n_nodes <= MAX_NODES:
        return detail
    keep_fact = detail.apply(
        lambda r: local(r.type or "") in THRESHOLD_FACT_TYPES.get(r.ruleId, set()), axis=1
    )
    return detail[(detail["role"] != "fact") | keep_fact]


def build(detail: pd.DataFrame, edges: pd.DataFrame) -> Network:
    net = Network(height="460px", width="100%", directed=True, cdn_resources="remote")
    net.barnes_hut(spring_length=140)
    detail = select_nodes(detail)

    for ev in detail.drop_duplicates("evidence").itertuples():
        net.add_node(
            ev.evidence,
            label=f"{ev.conclusion}\n{ev.ruleId}",
            title=f"{ev.evidence}\n{observed_text(ev)}\n評価時刻 {ev.evaluatedAt}",
            **STYLE["evidence"],
        )
    for row in detail.drop_duplicates("node").itertuples():
        pos = {"x": 0, "y": 0, "fixed": True} if row.role == "subject" else {}
        net.add_node(row.node, label=node_label(row), title=row.node, **STYLE[row.role], **pos)

    drawn = set(net.get_nodes())
    for e in edges.itertuples():
        if e.s in drawn and e.o in drawn:
            net.add_edge(e.s, e.o, label=local(e.p), title=e.p, font={"size": 11})
    return net


def render(detail: pd.DataFrame, edges: pd.DataFrame) -> tuple[str, int]:
    """HTML と描画ノード数を返す。"""
    net = build(detail, edges)
    return net.generate_html(), len(net.get_nodes())
