"""ナレッジグラフ予行練習アプリ。起動: uv run streamlit run src/app.py

ここではページ設定・サイドバー・タブの振り分けだけを行い、各画面は src/ui/ に置く。

現時点の実装範囲（APP_SPEC.md 第7節 1〜7。タブ3 の個体探索を除く）:
- サイドバー（ui/sidebar.py）: Fuseki 接続状態、判定の再生成、表示オプション
- タブ1（ui/tab_query.py → judgment_view / cause_ranking / nl_query）: CQ01〜05 はテーブル表示。
  CQ06（ロット判定 R01〜R03）・CQ08（代替候補 R05）は「判定 → 根拠経路グラフ → 説明文」。CQ07 は既存 Evidence の集計による原因候補の順位付け。
  下部に自然文→SPARQL（生成 → 表示 → 承認後に実行、失敗時の再生成は1回まで）
- タブ2（ui/tab_whatif.py）: 閾値を変えた what-if 判定と、変更前後の差分。ロットを押すとタブ1 の CQ06 で経路を開く
- タブ3（ui/tab_graph.py）: グラフ別・クラス別の件数
CQ09〜10 は R06 以降の判定クエリが揃ってから追加する。
"""

import streamlit as st

from ui import sidebar, state, tab_graph, tab_query, tab_whatif


def main() -> None:
    st.set_page_config(page_title="KG 予行練習", layout="wide")
    status, show_sparql, explain_on = sidebar.render()

    tab1, tab2, tab3 = st.tabs([state.TAB_ASK, state.TAB_WHATIF, state.TAB_GRAPH], key=state.MAIN_TABS, on_change="rerun")
    if not status.ok:
        with tab1:
            st.warning("Fuseki に接続できません。サイドバーの接続先を確認してください。")
        return
    # 開いているタブだけを描画する（説明文生成などの重い処理を隠れたタブで走らせない）
    if tab1.open:
        with tab1:
            tab_query.render(show_sparql, explain_on)
    if tab2.open:
        with tab2:
            tab_whatif.render()
    if tab3.open:
        with tab3:
            tab_graph.render()


main()
