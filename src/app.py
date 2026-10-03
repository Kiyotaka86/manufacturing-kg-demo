"""ナレッジグラフ予行練習アプリ。起動: uv run streamlit run src/app.py

現時点の実装範囲（APP_SPEC.md 第7節 1〜3）:
- サイドバー: Fuseki 接続状態
- タブ1: CQ01〜05 の実行とテーブル表示（対象は Fuseki から動的取得）
判定系 CQ（06〜10）の根拠経路表示、タブ2・3 は後続段階で実装する。
"""

import httpx
import streamlit as st

import fuseki

# 根拠経路の描画（viz.py）ができるまでは、テーブル表示で完結する CQ だけを選べるようにする
TABLE_ONLY_CQS = {"cq01", "cq02", "cq03", "cq04", "cq05"}

PREFIXES = {
    "http://example.org/kg/data/": "data:",
    "http://example.org/kg/ontology#": "ex:",
}


def compact(value):
    """表示用に IRI を接頭辞付きに短縮する。リテラルはそのまま返す。"""
    if isinstance(value, str):
        for ns, prefix in PREFIXES.items():
            if value.startswith(ns):
                return prefix + value[len(ns):]
    return value


@st.cache_data(ttl=30, show_spinner=False)
def cached_status() -> fuseki.Status:
    return fuseki.status()


@st.cache_data(show_spinner=False)
def cached_targets(sample_iri: str) -> list[tuple[str, str]]:
    df = fuseki.target_options(sample_iri)
    return [(row.s, f"{row.s.rsplit('/', 1)[-1]} {row.name or ''}".strip()) for row in df.itertuples()]


def sidebar() -> tuple[fuseki.Status, bool]:
    with st.sidebar:
        st.subheader("Fuseki 接続状態")
        st.caption(fuseki.FUSEKI_URL)
        status = cached_status()
        if status.ok:
            st.markdown(":green[●] 接続中")
            st.markdown(f"{status.graphs} graphs  \n{status.triples:,} triples")
        else:
            st.markdown(":red[●] 未接続")
            st.caption(status.error)
        if st.button("再読込"):
            st.cache_data.clear()
            st.rerun()

        st.divider()
        st.subheader("表示オプション")
        show_sparql = st.checkbox("SPARQL を表示", value=True)
    return status, show_sparql


def tab_ask(show_sparql: bool) -> None:
    cqs = {cq.id: cq for cq in fuseki.list_cqs() if cq.id in TABLE_ONLY_CQS}
    cq = cqs[st.selectbox("CQ を選ぶ", list(cqs), format_func=lambda i: cqs[i].title)]

    target = None
    if cq.target_var:
        options = cached_targets(cq.default_target)
        labels = dict(options)
        iris = [iri for iri, _ in options]
        default = iris.index(cq.default_target) if cq.default_target in iris else 0
        target = st.selectbox(
            f"対象を選ぶ（?{cq.target_var}）", iris, index=default, format_func=labels.get
        )

    if not st.button("実行", type="primary"):
        return

    try:
        query, df = fuseki.run_cq(cq, target)
    except httpx.HTTPError as e:
        st.error(f"クエリ実行に失敗しました: {e}")
        return

    st.markdown(f"**{len(df)} 件**")
    if show_sparql:
        with st.expander("実行した SPARQL"):
            st.code(query, language="sparql")
    with st.expander("結果テーブル（全列）", expanded=True):
        st.dataframe(df.map(compact), width="stretch", hide_index=True)


def main() -> None:
    st.set_page_config(page_title="KG 予行練習", layout="wide")
    status, show_sparql = sidebar()

    tab1, tab2, tab3 = st.tabs(["質問する", "閾値を変える", "グラフを見る"])
    with tab1:
        if status.ok:
            tab_ask(show_sparql)
        else:
            st.warning("Fuseki に接続できません。サイドバーの接続先を確認してください。")
    with tab2:
        st.info("未実装（APP_SPEC.md 第7節 6）")
    with tab3:
        st.info("未実装（APP_SPEC.md 第7節 6）")


main()
