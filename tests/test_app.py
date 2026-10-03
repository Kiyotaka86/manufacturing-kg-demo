"""アプリのヘッドレステスト（streamlit AppTest）。描画結果をスナップショットと比べ、挙動が変わっていないことを確かめる。

- Fuseki（http://localhost:3030/kg）に投入済みのデータが前提。繋がらなければ skip
- LLM（説明文・自然文→SPARQL）は固定値のスタブに差し替え、結果を決定的にする
- what-if グラフを使うため、各テストの前後で urn:whatif:evidence を空にする
- スナップショットの作り直し: UPDATE_SNAPSHOT=1 uv run pytest
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from streamlit.testing.v1 import AppTest

import evidence
import fuseki
import llm

APP = str(ROOT / "src" / "app.py")
SNAPSHOT = Path(__file__).parent / "snapshots" / "app.json"
D = "http://example.org/kg/data/"
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[0-9.:+Z-]*")
STATE_KEYS = ["main_tabs", "cq_select", "target_cq06", "target_cq07", "target_cq08", "evidence_graph",
              "last_run", "whatif", "nl", "nl_question"]

BROKEN = "SELECT * WHERE { BROKEN"
EMPTY = "SELECT ?s WHERE { ?s a <urn:test:nothing> }"
NL_STUB = {  # 質問 → (1回目, 再生成)
    "ok": (fuseki.load_query("cq01.rq"), None),
    "retry-ok": (BROKEN, fuseki.load_query("cq02.rq")),
    "fail": (BROKEN, EMPTY),
}

pytestmark = pytest.mark.skipif(not fuseki.status().ok, reason="Fuseki に接続できない")


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    def explain(payload: dict) -> str:
        return "スタブ説明: " + "、".join(f"{j['ルール']}={j['結論']}" for j in payload["judgements"])

    def nl_to_sparql(question: str, previous: str | None = None, error: str | None = None) -> str:
        first, second = NL_STUB[question]
        return first if previous is None else second

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "explain", explain)
    monkeypatch.setattr(llm, "nl_to_sparql", nl_to_sparql)


@pytest.fixture(autouse=True)
def clean_whatif():
    # st.cache_data はプロセス内で共有されるため、テストごとに空にする（接続状態の ttl=30 などが前のテストから残らないように）
    st.cache_data.clear()
    evidence.clear(evidence.WHATIF_GRAPH)
    yield
    evidence.clear(evidence.WHATIF_GRAPH)


@pytest.fixture(scope="module")
def snapshot():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    actual: dict = {}
    yield expected, actual
    if os.environ.get("UPDATE_SNAPSHOT"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        merged = expected | actual
        SNAPSHOT.write_text(json.dumps(merged, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def mask(value):
    return TIMESTAMP.sub("<TS>", value) if isinstance(value, str) else value


def capture(at: AppTest) -> dict:
    """比較用に、画面に出ている要素と主要な session_state を書き出す。"""
    def frame(df):
        return [{k: mask(str(v)) for k, v in row.items()} for row in df.astype(object).to_dict("records")]

    state = {}
    for k in STATE_KEYS:
        if k in at.session_state:
            v = at.session_state[k]
            if k == "nl" and v:
                v = {**v, "df": None if v["df"] is None else frame(v["df"])}
            state[k] = json.loads(json.dumps(v, default=str))
    return {
        "exception": [str(e.value) for e in at.exception],
        "markdown": [mask(m.value) for m in at.markdown],
        "caption": [mask(c.value) for c in at.caption],
        "warning": [mask(w.value) for w in at.warning],
        "error": [mask(e.value) for e in at.error],
        "info": [i.value for i in at.info],
        "toast": [mask(t.value) for t in at.toast],
        "metric": [[m.label, m.value, m.delta] for m in at.metric],
        "code": [mask(c.value) for c in at.code],
        "dataframe": [frame(d.value) for d in at.dataframe],
        "button": [[b.label, b.key] for b in at.button],
        "expander": [e.label for e in at.expander],
        "radio": [[r.label, r.value] for r in at.radio],
        "selectbox": [[s.label, s.value] for s in at.selectbox],
        "state": state,
    }


def check(snapshot, name: str, at: AppTest) -> None:
    expected, actual = snapshot
    got = capture(at)
    actual[name] = got
    assert not got["exception"], got["exception"]
    if os.environ.get("UPDATE_SNAPSHOT"):
        return
    assert name in expected, f"スナップショットに {name} がありません（UPDATE_SNAPSHOT=1 で作成）"
    for field, value in expected[name].items():
        assert got[field] == value, f"{name}.{field} が基準線と異なる"


def new_app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=180).run()


def click(at: AppTest, label: str, tab: str | None = None) -> None:
    next(b for b in at.button if b.label == label).click()
    rerun(at, tab)


def rerun(at: AppTest, tab: str | None = None) -> None:
    # AppTest は再実行間でタブの選択を保持しないため、タブ1 以外は毎回指定する
    if tab:
        at.session_state["main_tabs"] = tab
    at.run()


def run_cq(at: AppTest, cq_id: str, target: str | None = None) -> None:
    at.selectbox(key="cq_select").set_value(cq_id).run()
    if target:
        at.selectbox(key=f"target_{cq_id}").set_value(target).run()
    click(at, "実行")


def test_initial(snapshot):
    check(snapshot, "initial", new_app())


@pytest.mark.parametrize("cq_id", ["cq01", "cq02", "cq03", "cq04", "cq05"])
def test_table_cqs(snapshot, cq_id):
    at = new_app()
    run_cq(at, cq_id)
    check(snapshot, f"table_{cq_id}", at)


@pytest.mark.parametrize("cq_id,target", [
    ("cq06", D + "lot/L0048"), ("cq06", D + "lot/L0003"),
    ("cq08", D + "part/PT014"), ("cq08", D + "part/PT013"),
])
def test_judgement_cqs(snapshot, cq_id, target):
    at = new_app()
    run_cq(at, cq_id, target)
    check(snapshot, f"judgement_{cq_id}_{target.rsplit('/', 1)[-1]}", at)


def test_sparql_hidden(snapshot):
    at = new_app()
    at.sidebar.checkbox[0].uncheck().run()  # SPARQL を表示
    at.sidebar.checkbox[1].uncheck().run()  # 説明文を生成
    run_cq(at, "cq06", D + "lot/L0048")
    check(snapshot, "options_off_cq06_L0048", at)


def test_cause_ranking(snapshot):
    at = new_app()
    run_cq(at, "cq07", D + "product/P001")
    check(snapshot, "ranking_cq07_P001", at)
    next(b for b in at.button if b.key and b.key.startswith("cq07_E003_")).click().run()
    check(snapshot, "ranking_cq07_to_cq06", at)


@pytest.mark.parametrize("threshold", [0.05, 0.30, 0.50])
def test_whatif(snapshot, threshold):
    tab = "閾値を変える"
    at = new_app()
    rerun(at, tab)
    check(snapshot, f"whatif_before_{threshold}", at)
    at.number_input[0].set_value(threshold)
    rerun(at, tab)
    click(at, "再判定", tab)
    check(snapshot, f"whatif_{threshold}", at)
    next(c for c in at.checkbox if c.label.startswith("判定不能も")).check()
    rerun(at, tab)
    check(snapshot, f"whatif_{threshold}_with_undetermined", at)


def test_whatif_to_tab1(snapshot):
    tab = "閾値を変える"
    at = new_app()
    rerun(at, tab)
    at.number_input[0].set_value(0.30)
    rerun(at, tab)
    click(at, "再判定", tab)
    next(b for b in at.button if b.label.startswith("L0048")).click().run()
    check(snapshot, "whatif_to_cq06_L0048", at)
    # 参照する判定を確定に戻す
    at.radio(key="evidence_graph").set_value(evidence.EVIDENCE_GRAPH).run()
    check(snapshot, "whatif_to_cq06_L0048_confirmed", at)


def test_tab_graph(snapshot):
    at = new_app()
    rerun(at, "グラフを見る")
    check(snapshot, "tab_graph", at)


def test_sidebar_buttons(snapshot):
    at = new_app()
    run_cq(at, "cq06", D + "lot/L0048")
    next(b for b in at.sidebar.button if b.label == "判定を再生成").click().run()
    check(snapshot, "sidebar_regenerate", at)
    next(b for b in at.sidebar.button if b.label == "再読込").click().run()
    check(snapshot, "sidebar_reload", at)


@pytest.mark.parametrize("question", ["ok", "retry-ok", "fail"])
def test_nl(snapshot, question):
    at = new_app()
    at.text_input(key="nl_question").set_value(question).run()
    click(at, "SPARQL を生成")
    check(snapshot, f"nl_{question}_generated", at)
    click(at, "承認して実行")
    check(snapshot, f"nl_{question}_1", at)
    if any(b.label == "承認して実行" for b in at.button):
        click(at, "承認して実行")
        check(snapshot, f"nl_{question}_2", at)
    # 質問を変えると前の結果は消える
    at.text_input(key="nl_question").set_value("ok").run()
    check(snapshot, f"nl_{question}_changed", at)
