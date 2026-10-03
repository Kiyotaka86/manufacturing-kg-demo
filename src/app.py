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

import json

import anthropic
import httpx
import pandas as pd
import streamlit as st

import evidence
import fuseki
import llm
import viz
from ui import cache, state, tab_graph

TABLE_ONLY_CQS = {"cq01", "cq02", "cq03", "cq04", "cq05"}
# 判定系 CQ（Evidence を判定・経路・説明文で示す）と、Evidence の集計で順位を付ける CQ
JUDGEMENT_CQS = {"cq06", "cq08"}
RANKING_CQS = {"cq07"}
EVIDENCE_CQS = JUDGEMENT_CQS | RANKING_CQS
EMPTY_JUDGEMENT = {
    "cq06": ("該当なし", "R01〜R03 のいずれの条件も満たしていません（判定不能も含めて Evidence がありません）。"),
    "cq08": ("代替候補なし", "この部品の現行サプライヤーが R04 に該当しないか、R05 を満たす代替がありません。"),
}

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


def render_judgement(cq: fuseki.CQ, target: str, graph: str, show_sparql: bool, explain_on: bool) -> None:
    # 結果テーブル・判定・経路・説明文はすべて同じ Evidence グラフ（graph）から読む
    query = evidence.retarget(fuseki.bind_values(cq.text, cq.target_var, target), graph)
    df = fuseki.select(query)
    detail = evidence.evidence_for(target, graph)

    # 1. 判定（CQ の結果をそのまま表示。結論の重い順: 出荷保留 > 要注意 > 代替候補 > 判定不能）
    if df.empty:
        title, note = EMPTY_JUDGEMENT[cq.id]
        st.markdown(f"### 判定: {title}")
        st.caption(note)
    else:
        order = evidence.order_by_conclusion(df)
        heads = df.set_index("evidence").loc[order].reset_index()
        names = detail.drop_duplicates("node").set_index("node")["name"]
        st.markdown(f"### 判定: {heads.conclusion.iloc[0]}")
        for ev in heads.itertuples():
            # CQ08 は代替部品・代替サプライヤーを併記する（推奨の強さは付けない）
            parts = [f"**{ev.conclusion}** — {ev.ruleId} {ev.ruleName}"]
            if "altSupplier" in heads.columns:
                parts.append(" ".join(f"{viz.local(i)} {names.get(i) or ''}".strip() for i in (ev.altPart, ev.altSupplier)))
            parts.append(viz.observed_text(ev))
            st.markdown("- " + "　".join(parts))

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
                text = cache.cached_explanation(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            except (anthropic.APIError, RuntimeError) as e:  # 説明文が無くても判定と経路は成立させる
                st.warning(f"説明文を生成できませんでした: {e}")
            else:
                st.write(text)
                issues = llm.audit(text, payload, cache.cached_names())
                for issue in issues:
                    st.warning(f"自己点検: {issue}")
                if not issues:
                    st.caption("自己点検: 根拠外の ID・名前・数値、推測・提案表現、該当なしへの読み替えは検出されませんでした。")

    st.caption(f"参照した判定: {state.graph_label(graph)}（{graph}）")
    if show_sparql:
        with st.expander("実行した SPARQL"):
            st.code(query, language="sparql")
    with st.expander(f"結果テーブル（全列・{len(df)} 件）"):
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
    nl_section()


def render_selected(cq: fuseki.CQ, target: str | None, graph: str, show_sparql: bool, explain_on: bool) -> None:
    st.divider()
    try:
        if cq.id in JUDGEMENT_CQS:
            render_judgement(cq, target, graph, show_sparql, explain_on)
        elif cq.id in RANKING_CQS:
            render_ranking(cq, target, graph, show_sparql)
        else:
            render_table(cq, target, show_sparql)
    except httpx.HTTPError as e:
        st.error(f"クエリ実行に失敗しました: {e}")


def render_ranking(cq: fuseki.CQ, target: str, graph: str, show_sparql: bool) -> None:
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


def nl_section() -> None:
    """自然文→SPARQL（APP_SPEC 第4節）。生成 SPARQL は必ず表示し、承認されてから実行する。

    構文エラーまたは 0 件のときは、エラー内容を添えて1回だけ再生成する（再生成分も承認後に実行）。
    2回失敗したらそこで止め、CQ からの選択を促す。
    """
    st.markdown("#### 自由入力（自然文 → SPARQL）")
    if not llm.available():
        st.caption("ANTHROPIC_API_KEY が未設定のため使えません。上の CQ から選んでください。")
        return
    question = st.text_input("質問", placeholder="例: 顧客ごとのクレーム件数を多い順に", key=state.NL_QUESTION)
    nl_state = st.session_state.get(state.NL)
    if nl_state and nl_state["q"] != question:
        nl_state = st.session_state[state.NL] = None

    if st.button("SPARQL を生成", disabled=not question):
        try:
            sparql = llm.nl_to_sparql(question)
        except (anthropic.APIError, RuntimeError) as e:
            st.error(f"生成に失敗しました: {e}")
            return
        nl_state = st.session_state[state.NL] = {"q": question, "sparql": sparql, "attempt": 1, "status": "pending",
                                          "errors": [], "df": None}
    if not nl_state:
        return

    if nl_state["errors"]:
        st.warning(f"1回目のクエリは失敗しました（{nl_state['errors'][0]}）。再生成したクエリを確認してください。")
    st.markdown(f"生成された SPARQL（{nl_state['attempt']} 回目）")
    st.code(nl_state["sparql"], language="sparql")

    if nl_state["status"] == "pending" and st.button("承認して実行", type="primary"):
        df, err = fuseki.try_select(nl_state["sparql"])
        if err is None:
            nl_state.update(status="done", df=df)
        elif nl_state["attempt"] == 1:
            try:
                sparql = llm.nl_to_sparql(question, previous=nl_state["sparql"], error=err)
            except (anthropic.APIError, RuntimeError) as e:
                nl_state.update(status="failed", errors=[err, f"再生成に失敗: {e}"])
            else:
                nl_state.update(sparql=sparql, attempt=2, errors=[err])
        else:
            nl_state.update(status="failed", errors=nl_state["errors"] + [err], df=df)
        st.rerun()  # 実行後は承認ボタンを消す

    if nl_state["status"] == "done":
        st.markdown(f"**{len(nl_state['df'])} 件**")
        st.dataframe(nl_state["df"].map(llm.compact), width="stretch", hide_index=True)
    elif nl_state["status"] == "failed":
        st.error(f"2回とも失敗しました（{nl_state['errors'][-1]}）。上の CQ から近い質問を選んでください。")


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
        st.session_state[state.WHATIF] = f"R01={overrides['R01']}, R02 倍率={overrides['R02']}, R03={overrides['R03']}"
        st.cache_data.clear()

    after_df = evidence.judged_lots(evidence.WHATIF_GRAPH)
    if after_df.empty:
        st.info("「再判定」を押すと、変更前（確定の判定）との差分を表示します。")
        return
    before_df = evidence.judged_lots(evidence.EVIDENCE_GRAPH)

    st.divider()
    st.markdown(f"**変更前**: {state.graph_label(evidence.EVIDENCE_GRAPH)}　→　**変更後**: {state.graph_label(evidence.WHATIF_GRAPH)}")
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
                          on_click=state.open_in_cq06, args=(lot, graph), width="stretch")
    st.caption("ロットを押すとタブ1 の CQ06 で根拠経路を開きます（変更前のみ → 確定の判定、両方・変更後のみ → what-if の判定）。"
               "「両方」で該当ルールが変わったロットは「← 変更前のルール」を併記します。")


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
            tab_whatif()
    if tab3.open:
        with tab3:
            tab_graph.render()


main()
