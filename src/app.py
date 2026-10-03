"""ナレッジグラフ予行練習アプリ。起動: uv run streamlit run src/app.py

現時点の実装範囲（APP_SPEC.md 第7節 1〜7。タブ3 の個体探索を除く）:
- サイドバー: Fuseki 接続状態、判定の再生成、表示オプション
- タブ1: CQ01〜05 はテーブル表示。CQ06（ロット判定 R01〜R03）・CQ08（代替候補 R05）は
  「判定 → 根拠経路グラフ → 説明文」。CQ07 は既存 Evidence の集計による原因候補の順位付け。
  下部に自然文→SPARQL（生成 → 表示 → 承認後に実行、失敗時の再生成は1回まで）
- タブ2: 閾値を変えた what-if 判定と、変更前後の差分。ロットを押すとタブ1 の CQ06 で経路を開く
- タブ3: グラフ別・クラス別の件数
CQ09〜10 は R06 以降の判定クエリが揃ってから追加する。
"""


import httpx
import pandas as pd
import streamlit as st

import evidence
import fuseki
import llm
from ui import (
    cache,
    cause_ranking,
    judgment_view,
    nl_query,
    state,
    tab_graph,
    tab_whatif,
)

TABLE_ONLY_CQS = {"cq01", "cq02", "cq03", "cq04", "cq05"}
# 判定系 CQ（Evidence を判定・経路・説明文で示す）と、Evidence の集計で順位を付ける CQ
JUDGEMENT_CQS = {"cq06", "cq08"}
RANKING_CQS = {"cq07"}
EVIDENCE_CQS = JUDGEMENT_CQS | RANKING_CQS


def sidebar() -> tuple[fuseki.Status, bool, bool]:
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


def tab_ask(show_sparql: bool, explain_on: bool) -> None:
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


def main() -> None:
    st.set_page_config(page_title="KG 予行練習", layout="wide")
    status, show_sparql, explain_on = sidebar()

    tab1, tab2, tab3 = st.tabs([state.TAB_ASK, state.TAB_WHATIF, state.TAB_GRAPH], key=state.MAIN_TABS, on_change="rerun")
    if not status.ok:
        with tab1:
            st.warning("Fuseki に接続できません。サイドバーの接続先を確認してください。")
        return
    # 開いているタブだけを描画する（説明文生成などの重い処理を隠れたタブで走らせない）
    if tab1.open:
        with tab1:
            tab_ask(show_sparql, explain_on)
    if tab2.open:
        with tab2:
            tab_whatif.render()
    if tab3.open:
        with tab3:
            tab_graph.render()


main()
