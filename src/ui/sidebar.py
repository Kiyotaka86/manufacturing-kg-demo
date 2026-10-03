"""サイドバー: Fuseki 接続状態、再読込・判定の再生成、表示オプション。"""

import streamlit as st

import evidence
import fuseki
import llm
from ui import cache


def render() -> tuple[fuseki.Status, bool, bool]:
    with st.sidebar:
        st.subheader("Fuseki 接続状態")
        st.caption(fuseki.FUSEKI_URL)
        status = cache.cached_status()
        if status.ok:
            st.markdown(":green[●] 接続中")
            st.markdown(f"{status.graphs} graphs  \n{status.triples:,} triples")
        else:
            st.markdown(":red[●] 未接続")
            st.caption(status.error)
        if st.button("再読込"):
            st.cache_data.clear()
            st.rerun()
        if status.ok and st.button("判定を再生成", help="urn:src:evidence を CLEAR し、rules の閾値で全ルールを再判定する"):
            counts = evidence.evaluate()
            st.cache_data.clear()
            st.toast("再判定: " + ", ".join(
                f"{r.rule.rsplit('/', 1)[-1]} {r.conclusion} {r.evidences}件" for r in counts.itertuples()))

        st.divider()
        st.subheader("表示オプション")
        show_sparql = st.checkbox("SPARQL を表示", value=True)
        explain_on = st.checkbox("説明文を生成", value=llm.available(), disabled=not llm.available())
        if not llm.available():
            st.caption("ANTHROPIC_API_KEY が未設定のため説明文生成は無効")
    return status, show_sparql, explain_on
