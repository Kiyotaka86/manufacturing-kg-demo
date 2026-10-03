"""タブ1 CQ07: 既存 Evidence の集計による原因候補の順位付け。"""

import streamlit as st

import evidence
import fuseki
import llm
import viz
from ui import state


def render(cq: fuseki.CQ, target: str, graph: str, show_sparql: bool) -> None:
    """CQ07: 製品のロットに付いた Evidence を、根拠事実のサプライヤー・設備ごとに集計して順位付けする。"""
    query = evidence.retarget(fuseki.bind_values(cq.text, cq.target_var, target), graph)
    df = fuseki.select(query)
    if df.empty:
        st.markdown("### 原因候補: なし")
        st.caption("この製品のロットには、サプライヤー・設備を根拠とする Evidence（R01・R02）がありません。")
    else:
        df["lotCount"] = df["lotCount"].astype(int)
        # 該当ロット数の多い順。同数は同順位（SPARQL の ORDER BY と同じ並び）
        df.insert(0, "順位", df["lotCount"].rank(method="min", ascending=False).astype(int))
        st.markdown("### 原因候補（該当ロット数の多い順）")
        for row in df.itertuples():
            lots = row.lots.split()
            label = f"{row.順位}位　{viz.local(row.cause)} {row.causeName or ''}（{row.causeType}）— {row.lotCount} ロット・{row.rules}"
            with st.expander(label):
                st.caption("ロットを押すと CQ06 でそのロットの根拠経路を開きます。")
                for col, lot in zip(st.columns(min(len(lots), 6)), lots):
                    col.button(viz.local(lot), key=f"cq07_{viz.local(row.cause)}_{viz.local(lot)}",
                               on_click=state.open_in_cq06, args=(lot, graph), width="stretch")
    st.caption("既存の Evidence（R01: サプライヤー、R02: 設備）の集計のみで、新たな判定は行っていません。"
               "判定不能は除外。R07（設備起因疑い）は判定クエリが未実装のため含みません。"
               f"参照した判定: {state.graph_label(graph)}")
    if show_sparql:
        with st.expander("実行した SPARQL"):
            st.code(query, language="sparql")
    with st.expander(f"結果テーブル（全列・{len(df)} 件）"):
        st.dataframe(df.map(llm.compact), width="stretch", hide_index=True)
