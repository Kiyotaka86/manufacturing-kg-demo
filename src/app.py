"""ナレッジグラフ予行練習アプリ。起動: uv run streamlit run src/app.py

現時点の実装範囲（APP_SPEC.md 第7節 1〜5、CQ06 は R01〜R03）:
- サイドバー: Fuseki 接続状態、判定の再生成、表示オプション
- タブ1: CQ01〜05 はテーブル表示。CQ06 は「判定 → 根拠経路グラフ → 説明文」
タブ2・3、CQ07〜10、自然文→SPARQL は後続段階で実装する。
"""

import json

import anthropic
import httpx
import streamlit as st

import evidence
import fuseki
import llm
import viz

TABLE_ONLY_CQS = {"cq01", "cq02", "cq03", "cq04", "cq05"}
# 判定系 CQ。CQ07〜10 は R01 以外の判定クエリ（queries/evidence_*.rq）が揃ってから追加する
JUDGEMENT_CQS = {"cq06"}


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
        if status.ok and st.button("判定を再生成", help="urn:src:evidence を CLEAR し、全ルールで再判定する"):
            counts = evidence.evaluate()
            st.cache_data.clear()
            st.toast("再判定: " + ", ".join(f"{r.rsplit('/', 1)[-1]} {n}件" for r, n in zip(counts.rule, counts.evidences)))

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


def render_judgement(cq: fuseki.CQ, target: str, show_sparql: bool, explain_on: bool) -> None:
    query, df = fuseki.run_cq(cq, target)
    detail = evidence.evidence_for(target)

    # 1. 判定（値は Evidence / Rule のトリプルをそのまま表示）
    if detail.empty:
        st.markdown("### 判定: 該当なし")
        st.caption("urn:src:evidence にこの対象の Evidence がありません（どのルールにも非該当）。")
    else:
        # 結論の重い順（出荷保留 > 要注意）。最も重い結論を見出しにする
        order = evidence.order_by_conclusion(detail)
        heads = detail.drop_duplicates("evidence").set_index("evidence").loc[order].reset_index()
        st.markdown(f"### 判定: {heads.conclusion.iloc[0]}")
        for ev in heads.itertuples():
            st.markdown(f"- **{ev.conclusion}** — {ev.ruleId} {ev.ruleName} {viz.observed_text(ev)}")

        # 2. 根拠経路
        st.markdown("#### 根拠経路")
        props = evidence.node_props_for(target)
        edges = evidence.edges_for(order)
        html, n_nodes, n_edges = viz.render(detail, edges, props)
        st.iframe(html, height=580)
        st.caption(f"{n_nodes} ノード・{n_edges} エッジ。濃青=判定対象 / 水色=根拠事実 / 橙=適用ルール / 紫=結論。"
                   "エッジはすべて実在トリプル。ノードにカーソルを合わせると属性値を表示。")

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
                    st.caption("自己点検: 根拠外の ID・名前・数値、推測・提案表現は検出されませんでした。")

    st.caption("判定・経路・説明は urn:src:evidence の Evidence（R01〜R03）に基づきます。下の結果テーブルは cq06.rq の"
               "簡易判定で、R02 は日数比較を省略しているため Evidence と一致しない行があります。")
    if show_sparql:
        with st.expander("実行した SPARQL"):
            st.code(query, language="sparql")
    with st.expander(f"結果テーブル（全列・{len(df)} 件）"):
        st.dataframe(df.map(llm.compact), width="stretch", hide_index=True)
    with st.expander("Evidence の根拠ノード"):
        st.dataframe(detail.map(llm.compact), width="stretch", hide_index=True)


def tab_ask(show_sparql: bool, explain_on: bool) -> None:
    cqs = {cq.id: cq for cq in fuseki.list_cqs() if cq.id in TABLE_ONLY_CQS | JUDGEMENT_CQS}
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

    # 実行結果は選択が変わるまで保持する（チェックボックス操作などの再描画で消さない）
    if st.button("実行", type="primary"):
        st.session_state["last_run"] = (cq.id, target)
    if st.session_state.get("last_run") != (cq.id, target):
        return

    st.divider()
    try:
        if cq.id in JUDGEMENT_CQS:
            render_judgement(cq, target, show_sparql, explain_on)
        else:
            render_table(cq, target, show_sparql)
    except httpx.HTTPError as e:
        st.error(f"クエリ実行に失敗しました: {e}")


def main() -> None:
    st.set_page_config(page_title="KG 予行練習", layout="wide")
    status, show_sparql, explain_on = sidebar()

    tab1, tab2, tab3 = st.tabs(["質問する", "閾値を変える", "グラフを見る"])
    with tab1:
        if status.ok:
            tab_ask(show_sparql, explain_on)
        else:
            st.warning("Fuseki に接続できません。サイドバーの接続先を確認してください。")
    with tab2:
        st.info("未実装（APP_SPEC.md 第7節 6）")
    with tab3:
        st.info("未実装（APP_SPEC.md 第7節 6）")


main()
