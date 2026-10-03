"""タブ1 CQ06・CQ08: 判定 → 根拠経路グラフ → 説明文。"""

import json

import anthropic
import streamlit as st

import evidence
import fuseki
import llm
import viz
from ui import cache, state

EMPTY_JUDGEMENT = {
    "cq06": ("該当なし", "R01〜R03 のいずれの条件も満たしていません（判定不能も含めて Evidence がありません）。"),
    "cq08": ("代替候補なし", "この部品の現行サプライヤーが R04 に該当しないか、R05 を満たす代替がありません。"),
}


def render(cq: fuseki.CQ, target: str, graph: str, show_sparql: bool, explain_on: bool) -> None:
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
