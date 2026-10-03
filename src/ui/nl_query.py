"""タブ1 下部「自由入力（自然文 → SPARQL）」。"""

import anthropic
import streamlit as st

import fuseki
import llm
from ui import state


def render() -> None:
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
