"""ナレッジグラフ予行練習アプリ。起動: uv run streamlit run src/app.py

現時点の実装範囲（APP_SPEC.md 第7節 1〜6 の一部）:
- サイドバー: Fuseki 接続状態、判定の再生成、表示オプション
- タブ1: CQ01〜05 はテーブル表示。CQ06 は「判定 → 根拠経路グラフ → 説明文」（R01〜R03）
- タブ2: 閾値を変えた what-if 判定と、変更前後の差分。ロットを押すとタブ1 の CQ06 で経路を開く
タブ3、CQ07〜10、自然文→SPARQL は後続段階で実装する。
"""

import json

import anthropic
import httpx
import pandas as pd
import streamlit as st

import evidence
import fuseki
import llm
import viz

TABLE_ONLY_CQS = {"cq01", "cq02", "cq03", "cq04", "cq05"}
# 判定系 CQ。CQ07〜10 は R04 以降の判定クエリ（queries/evidence_*.rq）が揃ってから追加する
JUDGEMENT_CQS = {"cq06"}

TAB_ASK, TAB_WHATIF, TAB_GRAPH = "質問する", "閾値を変える", "グラフを見る"


@st.cache_data(ttl=30, show_spinner=False)
def cached_status() -> fuseki.Status:
    return fuseki.status()


@st.cache_data(show_spinner=False)
def cached_targets(sample_iri: str) -> list[tuple[str, str]]:
    df = fuseki.target_options(sample_iri)
    return [(row.s, f"{row.s.rsplit('/', 1)[-1]} {row.name or ''}".strip()) for row in df.itertuples()]


@st.cache_data(show_spinner=False)
def cached_names() -> list[str]:
    return fuseki.select(fuseki.load_query("app_names.rq"))["name"].tolist()


@st.cache_data(show_spinner="説明文を生成中…")
def cached_explanation(payload_json: str) -> str:
    return llm.explain(json.loads(payload_json))


def graph_label(graph: str) -> str:
    if graph == evidence.EVIDENCE_GRAPH:
        return "確定（rules の閾値）"
    applied = st.session_state.get("whatif")
    return f"what-if（{applied}）" if applied else "what-if（前回の設定）"


def sidebar() -> tuple[fuseki.Status, bool, bool]:
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


def render_table(cq: fuseki.CQ, target: str | None, show_sparql: bool) -> None:
    query, df = fuseki.run_cq(cq, target)
    st.markdown(f"**{len(df)} 件**")
    if show_sparql:
        with st.expander("実行した SPARQL"):
            st.code(query, language="sparql")
    # CQ01〜05 は結果テーブルが唯一の出力なので開いた状態で出す
    with st.expander("結果テーブル（全列）", expanded=True):
        st.dataframe(df.map(llm.compact), width="stretch", hide_index=True)


def render_judgement(cq: fuseki.CQ, target: str, graph: str, show_sparql: bool, explain_on: bool) -> None:
    # 結果テーブル・判定・経路・説明文はすべて同じ Evidence グラフ（graph）から読む
    query = evidence.retarget(fuseki.bind_values(cq.text, cq.target_var, target), graph)
    df = fuseki.select(query)
    detail = evidence.evidence_for(target, graph)

    # 1. 判定（cq06 の結果をそのまま表示。結論の重い順: 出荷保留 > 要注意 > 判定不能）
    if df.empty:
        st.markdown("### 判定: 該当なし")
        st.caption("R01〜R03 のいずれの条件も満たしていません（判定不能も含めて Evidence がありません）。")
    else:
        order = evidence.order_by_conclusion(df)
        heads = df.set_index("evidence").loc[order].reset_index()
        st.markdown(f"### 判定: {heads.conclusion.iloc[0]}")
        for ev in heads.itertuples():
            st.markdown(f"- **{ev.conclusion}** — {ev.ruleId} {ev.ruleName} {viz.observed_text(ev)}")

        # 2. 根拠経路
        st.markdown("#### 根拠経路")
        props = evidence.node_props_for(target, graph)
        edges = evidence.edges_for(order, graph)
        html, n_nodes, n_edges = viz.render(detail, edges, props)
        st.iframe(html, height=580)
        st.caption(f"{n_nodes} ノード・{n_edges} エッジ。濃青=判定対象 / 水色=根拠事実 / 橙=適用ルール / 紫=結論 / "
                   "灰色破線=判定不能。エッジはすべて実在トリプル。ノードにカーソルを合わせると属性値を表示。")

        # 3. 説明文
        if explain_on:
            st.markdown("#### 説明")
            payload = llm.evidence_payload(detail, edges, props)
            try:
                text = cached_explanation(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            except (anthropic.APIError, RuntimeError) as e:  # 説明文が無くても判定と経路は成立させる
                st.warning(f"説明文を生成できませんでした: {e}")
            else:
                st.write(text)
                issues = llm.audit(text, payload, cached_names())
                for issue in issues:
                    st.warning(f"自己点検: {issue}")
                if not issues:
                    st.caption("自己点検: 根拠外の ID・名前・数値、推測・提案表現、該当なしへの読み替えは検出されませんでした。")

    st.caption(f"参照した判定: {graph_label(graph)}（{graph}）")
    if show_sparql:
        with st.expander("実行した SPARQL"):
            st.code(query, language="sparql")
    with st.expander(f"結果テーブル（全列・{len(df)} 件）"):
        st.dataframe(df.map(llm.compact), width="stretch", hide_index=True)


def tab_ask(show_sparql: bool, explain_on: bool) -> None:
    cqs = {cq.id: cq for cq in fuseki.list_cqs() if cq.id in TABLE_ONLY_CQS | JUDGEMENT_CQS}
    cq = cqs[st.selectbox("CQ を選ぶ", list(cqs), format_func=lambda i: cqs[i].title, key="cq_select")]

    target = None
    if cq.target_var:
        options = cached_targets(cq.default_target)
        labels = dict(options)
        iris = [iri for iri, _ in options]
        key = f"target_{cq.id}"
        if key not in st.session_state:
            st.session_state[key] = cq.default_target if cq.default_target in iris else iris[0]
        target = st.selectbox(f"対象を選ぶ（?{cq.target_var}）", iris, format_func=labels.get, key=key)

    graph = evidence.EVIDENCE_GRAPH
    if cq.id in JUDGEMENT_CQS and not evidence.counts(evidence.WHATIF_GRAPH).empty:
        graphs = [evidence.EVIDENCE_GRAPH, evidence.WHATIF_GRAPH]
        graph = st.radio("参照する判定", graphs, format_func=graph_label, horizontal=True, key="evidence_graph")

    # 実行結果は選択が変わるまで保持する（チェックボックス操作などの再描画で消さない）
    if st.button("実行", type="primary"):
        st.session_state["last_run"] = (cq.id, target)
    if st.session_state.get("last_run") != (cq.id, target):
        return

    st.divider()
    try:
        if cq.id in JUDGEMENT_CQS:
            render_judgement(cq, target, graph, show_sparql, explain_on)
        else:
            render_table(cq, target, show_sparql)
    except httpx.HTTPError as e:
        st.error(f"クエリ実行に失敗しました: {e}")


def open_in_cq06(lot: str, graph: str) -> None:
    """タブ2 のロットボタン: タブ1 の CQ06 に切り替え、そのロットと参照グラフを選んで実行済みにする。"""
    st.session_state["cq_select"] = "cq06"
    st.session_state["target_cq06"] = lot
    st.session_state["evidence_graph"] = graph
    st.session_state["last_run"] = ("cq06", lot)
    st.session_state["main_tabs"] = TAB_ASK


def lot_sets(df: pd.DataFrame, include_undetermined: bool) -> dict[str, list[str]]:
    """ロット → 該当したルールID の一覧（判定不能を含めるかは指定）。"""
    if not include_undetermined:
        df = df[df.conclusion != evidence.UNDETERMINED]
    return df.groupby("lot")["ruleId"].apply(lambda s: sorted(set(s))).to_dict()


def tab_whatif() -> None:
    st.markdown("ルールの閾値を変えて再判定し、判定が変わるロットを比べます。"
                "rules グラフは書き換えず、判定クエリの VALUES に値を差し込んで `urn:whatif:evidence` に書き込みます。")
    rules = fuseki.select(fuseki.load_query("app_rules.rq"))
    st.dataframe(rules.map(llm.compact), width="stretch", hide_index=True)

    by_id = rules.set_index("ruleId")
    cols = st.columns(3)
    r01 = cols[0].number_input("R01 閾値（不良率）", min_value=0.0, max_value=1.0, step=0.05, format="%.2f",
                               value=float(by_id.loc["R01", "threshold"]))
    r02 = cols[1].number_input("R02 保全間隔に対する倍率", min_value=0.1, max_value=5.0, step=0.1, format="%.1f",
                               value=1.0, help="閾値 = 設備の maint_interval_days × 倍率（設備ごとに閾値が異なるため）")
    r03 = cols[2].number_input("R03 閾値（NG 件数）", min_value=0, step=1, value=int(by_id.loc["R03", "threshold"]))
    st.caption("R04〜R08 は判定クエリが未実装のため対象外です。比較方向（comparison）は rules シートの値を使います。")

    if st.button("再判定", type="primary"):
        overrides = {"R01": f"{r01:.2f}", "R02": f"{r02:.1f}", "R03": str(int(r03))}
        evidence.evaluate(graph=evidence.WHATIF_GRAPH, overrides=overrides)
        st.session_state["whatif"] = f"R01={overrides['R01']}, R02 倍率={overrides['R02']}, R03={overrides['R03']}"
        st.cache_data.clear()

    after_df = evidence.judged_lots(evidence.WHATIF_GRAPH)
    if after_df.empty:
        st.info("「再判定」を押すと、変更前（確定の判定）との差分を表示します。")
        return
    before_df = evidence.judged_lots(evidence.EVIDENCE_GRAPH)

    st.divider()
    st.markdown(f"**変更前**: {graph_label(evidence.EVIDENCE_GRAPH)}　→　**変更後**: {graph_label(evidence.WHATIF_GRAPH)}")
    include_undetermined = st.checkbox("判定不能も「判定あり」に含める", value=False)
    before, after = lot_sets(before_df, include_undetermined), lot_sets(after_df, include_undetermined)

    # ルール別の該当ロット数（判定不能は別に数える）
    summary = pd.DataFrame({
        "変更前": before_df.groupby(["ruleId", "conclusion"]).lot.nunique(),
        "変更後": after_df.groupby(["ruleId", "conclusion"]).lot.nunique(),
    }).fillna(0).astype(int).reset_index()
    m = st.columns(3)
    m[0].metric("判定ありロット（変更前）", len(before))
    m[1].metric("判定ありロット（変更後）", len(after), delta=len(after) - len(before))
    m[2].dataframe(summary, hide_index=True)

    only_before = sorted(set(before) - set(after))
    both = sorted(set(before) & set(after))
    only_after = sorted(set(after) - set(before))
    columns = [
        ("変更前のみ", only_before, before, evidence.EVIDENCE_GRAPH),
        ("両方", both, after, evidence.WHATIF_GRAPH),
        ("変更後のみ", only_after, after, evidence.WHATIF_GRAPH),
    ]
    for col, (title, lots, rules_of, graph) in zip(st.columns(3), columns):
        with col:
            st.markdown(f"**{title}**（{len(lots)} ロット）")
            for lot in lots:
                ident = lot.rsplit("/", 1)[-1]
                changed = "" if title != "両方" or before[lot] == after[lot] else f" ← {'+'.join(before[lot])}"
                st.button(f"{ident}  {'+'.join(rules_of[lot])}{changed}", key=f"open_{title}_{ident}",
                          on_click=open_in_cq06, args=(lot, graph), width="stretch")
    st.caption("ロットを押すとタブ1 の CQ06 で根拠経路を開きます（変更前のみ → 確定の判定、両方・変更後のみ → what-if の判定）。"
               "「両方」で該当ルールが変わったロットは「← 変更前のルール」を併記します。")


def main() -> None:
    st.set_page_config(page_title="KG 予行練習", layout="wide")
    status, show_sparql, explain_on = sidebar()

    tab1, tab2, tab3 = st.tabs([TAB_ASK, TAB_WHATIF, TAB_GRAPH], key="main_tabs", on_change="rerun")
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
            tab_whatif()
    if tab3.open:
        with tab3:
            st.info("未実装（APP_SPEC.md 第7節 6）")


main()
