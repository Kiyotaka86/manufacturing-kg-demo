"""画面間で共有する session_state のキーと、それを読み書きする小さな処理。

キーの文字列はウィジェットの key と同じもの（変えると選択状態が引き継がれなくなる）。
"""

import streamlit as st

import evidence

TAB_ASK, TAB_WHATIF, TAB_GRAPH = "質問する", "閾値を変える", "グラフを見る"

MAIN_TABS = "main_tabs"  # st.tabs の key（開いているタブ）
CQ_SELECT = "cq_select"  # タブ1 の CQ 選択
EVIDENCE_GRAPH_CHOICE = "evidence_graph"  # タブ1 の「参照する判定」（確定 / what-if）
LAST_RUN = "last_run"  # 実行済みの (CQ ID, 対象)。選択が変わるまで結果を表示し続ける
WHATIF = "whatif"  # タブ2 で最後に適用した閾値の表示文字列
NL = "nl"  # 自然文→SPARQL の進行状態
NL_QUESTION = "nl_question"  # 自然文の質問欄


def target_key(cq_id: str) -> str:
    """タブ1 の対象選択の key（CQ ごとに別）。"""
    return f"target_{cq_id}"


def graph_label(graph: str) -> str:
    if graph == evidence.EVIDENCE_GRAPH:
        return "確定（rules の閾値）"
    applied = st.session_state.get(WHATIF)
    return f"what-if（{applied}）" if applied else "what-if（前回の設定）"


def open_in_cq06(lot: str, graph: str) -> None:
    """タブ2 のロットボタン: タブ1 の CQ06 に切り替え、そのロットと参照グラフを選んで実行済みにする。"""
    st.session_state[CQ_SELECT] = "cq06"
    st.session_state[target_key("cq06")] = lot
    st.session_state[EVIDENCE_GRAPH_CHOICE] = graph
    st.session_state[LAST_RUN] = ("cq06", lot)
    st.session_state[MAIN_TABS] = TAB_ASK
