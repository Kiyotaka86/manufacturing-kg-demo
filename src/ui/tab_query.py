"""タブ1「質問する」: CQ の選択・実行と結果の表示。CQ01〜05 はテーブル、判定系・順位付けは各 ui モジュールへ振り分ける。"""

import httpx
import pandas as pd
import streamlit as st

import evidence
import fuseki
import llm
from ui import cache, cause_ranking, judgment_view, nl_query, state

TABLE_ONLY_CQS = {"cq01", "cq02", "cq03", "cq04", "cq05"}
# 判定系 CQ（Evidence を判定・経路・説明文で示す）と、Evidence の集計で順位を付ける CQ
JUDGEMENT_CQS = {"cq06", "cq08"}
RANKING_CQS = {"cq07"}
EVIDENCE_CQS = JUDGEMENT_CQS | RANKING_CQS


def cq05_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """CQ05 の平均日数を Python で計算する（日付の減算は SPARQL 側で行わない。cq05.rq の注記どおり）。

    返り値: (各行に「日数」を足した明細, 設備別の平均日数)
    """
    detail = df.copy()
    detail["日数"] = (pd.to_datetime(detail["nextDefectDate"]) - pd.to_datetime(detail["maint_date"])).dt.days
    summary = (
        detail.groupby("equipment", sort=True)["日数"]
        .agg(平均日数="mean", 保全回数="size", 最短日数="min", 最長日数="max")
        .reset_index()
    )
    summary["平均日数"] = summary["平均日数"].round(1)
    summary.insert(0, "設備", summary.pop("equipment").str.rsplit("/", n=1).str[-1])
    return detail, summary


def render_table(cq: fuseki.CQ, target: str | None, show_sparql: bool) -> None:
    query, df = fuseki.run_cq(cq, target)
    st.markdown(f"**{len(df)} 件**")
    if show_sparql:
        with st.expander("実行した SPARQL"):
            st.code(query, language="sparql")
    if cq.id == "cq05" and not df.empty:
        df, summary = cq05_summary(df)
        st.markdown("**設備別の平均日数**（保全実施日 → その後最初の不良報告日。Python で計算）")
        st.dataframe(summary, width="stretch", hide_index=True)
    # CQ01〜05 は結果テーブルが唯一の出力なので開いた状態で出す
    with st.expander("結果テーブル（全列）", expanded=True):
        st.dataframe(df.map(llm.compact), width="stretch", hide_index=True)


def render(show_sparql: bool, explain_on: bool) -> None:
    cqs = {cq.id: cq for cq in fuseki.list_cqs() if cq.id in TABLE_ONLY_CQS | EVIDENCE_CQS}
    cq = cqs[st.selectbox("CQ を選ぶ", list(cqs), format_func=lambda i: cqs[i].title, key=state.CQ_SELECT)]

    target = None
    if cq.target_var:
        options = cache.cached_targets(cq.default_target)
        labels = dict(options)
        iris = [iri for iri, _ in options]
        key = state.target_key(cq.id)
        if key not in st.session_state:
            st.session_state[key] = cq.default_target if cq.default_target in iris else iris[0]
        target = st.selectbox(f"対象を選ぶ（?{cq.target_var}）", iris, format_func=labels.get, key=key)

    graph = evidence.EVIDENCE_GRAPH
    if cq.id in EVIDENCE_CQS and not evidence.counts(evidence.WHATIF_GRAPH).empty:
        graphs = [evidence.EVIDENCE_GRAPH, evidence.WHATIF_GRAPH]
        graph = st.radio("参照する判定", graphs, format_func=state.graph_label, horizontal=True, key=state.EVIDENCE_GRAPH_CHOICE)

    # 実行結果は選択が変わるまで保持する（チェックボックス操作などの再描画で消さない）
    if st.button("実行", type="primary"):
        st.session_state[state.LAST_RUN] = (cq.id, target)
    if st.session_state.get(state.LAST_RUN) == (cq.id, target):
        render_selected(cq, target, graph, show_sparql, explain_on)
    st.divider()
    nl_query.render()


def render_selected(cq: fuseki.CQ, target: str | None, graph: str, show_sparql: bool, explain_on: bool) -> None:
    st.divider()
    try:
        if cq.id in JUDGEMENT_CQS:
            judgment_view.render(cq, target, graph, show_sparql, explain_on)
        elif cq.id in RANKING_CQS:
            cause_ranking.render(cq, target, graph, show_sparql)
        else:
            render_table(cq, target, show_sparql)
    except httpx.HTTPError as e:
        st.error(f"クエリ実行に失敗しました: {e}")
