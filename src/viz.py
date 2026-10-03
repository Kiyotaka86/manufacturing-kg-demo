"""根拠経路の pyvis 描画（APP_SPEC.md 第3節）。

ノードは evidence.evidence_for() の行、エッジは evidence.edges_for() が返す実在トリプルだけを使う。
描画側でエッジや数値を作り足さない（表示用の桁揃えのみ行う）。

配置: 判定対象（ロット）を中心に固定し、該当ルールごとに方向を分けて放射状に並べる。
各方向では、根拠事実をロットから辿れる距離（ホップ数）の順に外側へ置き、
Evidence（結論）と適用ルールはその脇に置く。
"""

import math
from collections import deque
from decimal import Decimal, InvalidOperation

import pandas as pd
from pyvis.network import Network

import evidence

MAX_NODES = 15
HOP_RADIUS = 170  # ロットから1ホップごとの距離（px）
SIDE_ANGLE = 0.8  # Evidence・ルールを経路の脇に置く角度（rad）

# 15ノードを超えたときに残す「閾値比較に直接使われた」根拠事実のクラス（ルール別）
THRESHOLD_FACT_TYPES = {
    "R01": {"Supplier"},  # サプライヤー単位の不良率を閾値と比較する
    "R02": {"Equipment", "Maintenance"},  # 最終保全日と設備の保全間隔を比較する
    "R03": {"Inspection"},  # NG となった検査の件数を閾値と比較する
}

STYLE = {
    "subject": {"color": "#1f3b73", "shape": "dot", "size": 32, "font": {"color": "#1f3b73"}},
    "fact": {"color": "#8fd3f4", "shape": "dot", "size": 18},
    "rule": {"color": "#f28c28", "shape": "box", "font": {"color": "#ffffff"}},
    "evidence": {"color": "#7b3fa0", "shape": "diamond", "size": 24},
    # 判定不能: 灰色・破線。結論が出ていないことを該当（紫）と見分けられるようにする
    "undetermined": {"color": {"background": "#d9d9d9", "border": "#7f7f7f"}, "shape": "diamond", "size": 24,
                     "borderWidth": 2, "shapeProperties": {"borderDashes": [6, 4]}},
}
UNDETERMINED_EDGE = {"dashes": True, "color": "#9e9e9e"}


def local(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def fmt2(value: str) -> str:
    """表示用に小数2桁へ揃える（0.8 → 0.80）。値そのものは変えない。"""
    try:
        return str(Decimal(value).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError):
        return str(value)


def display_value(value: str, unit) -> str:
    """画面と説明文で共通の観測値表記。比率は小数2桁、日数・件数は SPARQL の値そのまま。"""
    return fmt2(value) if isinstance(unit, str) and unit.startswith("ratio") else value


def observed_text(row) -> str:
    """測定値と閾値の表示。単位は Rule の threshold_unit に従う。

    ratio → 「16 / 20 = 0.80（閾値 0.05）」、days → 「75 日（閾値 60 日）」、count → 「1 件（閾値 1 件）」
    判定不能 → 「判定不能（ロット開始日以前の保全記録なし）」
    """
    if row.conclusion == evidence.UNDETERMINED:
        return f"判定不能（{row.undeterminedReason}）"
    unit = row.thresholdUnit if pd.notna(row.thresholdUnit) else ""
    if unit.startswith("ratio"):
        value = display_value(row.observedValue, unit)
        if pd.notna(row.numerator) and pd.notna(row.denominator):
            value = f"{row.numerator} / {row.denominator} = {value}"
        return f"{value}（閾値 {row.threshold}）"
    suffix = " 日" if unit.startswith("days") else " 件" if unit.startswith("count") else ""
    return f"{row.observedValue}{suffix}（閾値 {row.threshold}{suffix}）"


def measures_text(row) -> str:
    """ツールチップ用: 分子・分母をルールのラベルと対にした行。"""
    lines = []
    for label, value in ((row.numeratorLabel, row.numerator), (row.denominatorLabel, row.denominator)):
        if pd.notna(value):
            lines.append(f"{label if pd.notna(label) else '値'} = {value}")
    return "\n".join(lines)


def node_label(row) -> str:
    ident = local(row.node)
    return f"{ident} {row.name}" if pd.notna(row.name) else ident


def node_title(iri: str, props: pd.DataFrame) -> str:
    rows = props[props.node == iri]
    return "\n".join([iri] + [f"{local(r.p)} = {r.o}" for r in rows.itertuples()])


def select_nodes(detail: pd.DataFrame) -> pd.DataFrame:
    """ノード数が MAX_NODES を超える場合、閾値比較に直接使われたノードだけに絞る。"""
    n_nodes = detail["node"].nunique() + detail["evidence"].nunique()
    if n_nodes <= MAX_NODES:
        return detail
    keep_fact = detail.apply(
        lambda r: local(r.type or "") in THRESHOLD_FACT_TYPES.get(r.ruleId, set()), axis=1
    )
    return detail[(detail["role"] != "fact") | keep_fact]


def hops_from(subject: str, nodes: set[str], edges: pd.DataFrame) -> dict[str, int]:
    """subject から根拠事実への距離（Evidence・ルールを経由しない無向グラフ上のホップ数）。"""
    adj: dict[str, set[str]] = {n: set() for n in nodes | {subject}}
    for e in edges.itertuples():
        if e.s in adj and e.o in adj:
            adj[e.s].add(e.o)
            adj[e.o].add(e.s)
    dist = {subject: 0}
    queue = deque([subject])
    while queue:
        cur = queue.popleft()
        for nxt in adj[cur]:
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                queue.append(nxt)
    far = max(dist.values()) + 1
    return {n: dist.get(n, far) for n in nodes}


def polar(radius: float, angle: float) -> dict:
    return {"x": radius * math.cos(angle), "y": radius * math.sin(angle)}


def build(detail: pd.DataFrame, edges: pd.DataFrame, props: pd.DataFrame | None = None) -> Network:
    props = props if props is not None else pd.DataFrame(columns=["node", "p", "o"])
    net = Network(height="560px", width="100%", directed=True, cdn_resources="remote")
    net.toggle_physics(False)
    detail = select_nodes(detail)
    edges = edges.drop_duplicates()

    subject = next(detail[detail.role == "subject"].itertuples())
    net.add_node(subject.node, label=node_label(subject), title=node_title(subject.node, props),
                 x=0, y=0, **STYLE["subject"])

    order = evidence.order_by_conclusion(detail)
    for i, ev_iri in enumerate(order):
        theta = 2 * math.pi * i / len(order) - math.pi / 2 if len(order) > 1 else 0.0
        rows = detail[detail.evidence == ev_iri]
        ev = next(rows.itertuples())

        # 根拠事実: ロットから辿れる順に、ルールの方向へ外側に並べる
        facts = rows[rows.role == "fact"].drop_duplicates("node")
        hops = hops_from(subject.node, set(facts.node), edges)
        by_hop: dict[int, list] = {}
        for f in facts.itertuples():
            by_hop.setdefault(hops[f.node], []).append(f)
        for hop, group in by_hop.items():
            for j, f in enumerate(group):
                if f.node in net.get_nodes():
                    continue
                spread = (j - (len(group) - 1) / 2) * 0.55
                net.add_node(f.node, label=node_label(f), title=node_title(f.node, props),
                             **STYLE["fact"], **polar(HOP_RADIUS * hop, theta + spread))

        # 結論（Evidence）と適用ルールは経路の脇に置く
        title = "\n".join(x for x in (ev_iri, f"{ev.ruleName}: {observed_text(ev)}", measures_text(ev),
                                      f"評価時刻 {ev.evaluatedAt}") if x)
        style = STYLE["undetermined" if ev.conclusion == evidence.UNDETERMINED else "evidence"]
        net.add_node(ev_iri, label=f"{ev.conclusion}\n{ev.ruleId}", title=title,
                     **style, **polar(HOP_RADIUS * 1.5, theta + SIDE_ANGLE))
        rule = next(rows[rows.role == "rule"].itertuples())
        net.add_node(rule.node, label=node_label(rule), title=rule.node,
                     **STYLE["rule"], **polar(HOP_RADIUS * 2.5, theta + SIDE_ANGLE * 0.75))

    drawn = set(net.get_nodes())
    undetermined = set(detail[detail.conclusion == evidence.UNDETERMINED].evidence)
    for e in edges.itertuples():
        if e.s in drawn and e.o in drawn:
            extra = UNDETERMINED_EDGE if e.s in undetermined else {}
            net.add_edge(e.s, e.o, label=local(e.p), title=e.p, font={"size": 11}, **extra)
    return net


def render(detail: pd.DataFrame, edges: pd.DataFrame, props: pd.DataFrame | None = None) -> tuple[str, int, int]:
    """HTML・描画ノード数・描画エッジ数を返す。"""
    net = build(detail, edges, props)
    return net.generate_html(), len(net.get_nodes()), len(net.get_edges())
