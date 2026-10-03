"""タブ2「閾値を変える」: 閾値を変えた what-if 判定と、変更前後の差分。ロットを押すとタブ1 の CQ06 で経路を開く。"""

import pandas as pd
import streamlit as st

import evidence
import fuseki
import llm
from ui import state


def lot_sets(df: pd.DataFrame, include_undetermined: bool) -> dict[str, list[str]]:
    """ロット → 該当したルールID の一覧（判定不能を含めるかは指定）。"""
    if not include_undetermined:
        df = df[df.conclusion != evidence.UNDETERMINED]
    return df.groupby("lot")["ruleId"].apply(lambda s: sorted(set(s))).to_dict()


def render() -> None:
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
