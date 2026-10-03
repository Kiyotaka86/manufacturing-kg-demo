"""タブ3「グラフを見る」: 投入状態の確認。"""

import streamlit as st

import fuseki
import llm


def render() -> None:
    """タブ3: 投入状態の確認（グラフ別・クラス別の件数）。個体の周辺探索は未実装。"""
    cols = st.columns(2)
    with cols[0]:
        st.markdown("#### グラフ別トリプル数")
        graphs = fuseki.select(fuseki.load_query("app_graph_counts.rq"))
        graphs["triples"] = graphs["triples"].astype(int)
        st.dataframe(graphs, width="stretch", hide_index=True)
        st.caption(f"{len(graphs)} グラフ・{graphs.triples.sum():,} トリプル（スキーマ urn:schema:kg を含む全名前付きグラフ）")
    with cols[1]:
        st.markdown("#### クラス別インスタンス数")
        classes = fuseki.select(fuseki.load_query("app_class_counts.rq"))
        st.dataframe(classes.map(llm.compact), width="stretch", hide_index=True)
