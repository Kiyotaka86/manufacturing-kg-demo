"""Fuseki への接続とクエリ実行。

SPARQL 本文は queries/ のファイルから読み、Python には埋め込まない（CLAUDE.md）。
CQ の対象差し替えは、クエリ本文の `VALUES ?var { <iri> }` 行を書き換えて行う。
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "queries"
FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030/kg")
TIMEOUT = 30.0

# VALUES ?var { <iri> } — 対象を IRI で指定している行だけを「対象選択」とみなす
VALUES_IRI = re.compile(r"VALUES\s+\?(\w+)\s*\{\s*<([^>]+)>\s*\}")


def load_query(name: str) -> str:
    return (QUERIES / name).read_text(encoding="utf-8")


def select(query: str) -> pd.DataFrame:
    """SELECT を実行し、値を SPARQL が返した文字列のまま DataFrame にする（再計算しない）。"""
    resp = httpx.post(
        f"{FUSEKI_URL}/query",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    cols = body["head"]["vars"]
    rows = [
        {c: b[c]["value"] if c in b else None for c in cols}
        for b in body["results"]["bindings"]
    ]
    return pd.DataFrame(rows, columns=cols)


def update(request: str) -> None:
    resp = httpx.post(f"{FUSEKI_URL}/update", data={"update": request}, timeout=TIMEOUT)
    resp.raise_for_status()


@dataclass(frozen=True)
class Status:
    ok: bool
    graphs: int = 0
    triples: int = 0
    error: str = ""


def status() -> Status:
    try:
        row = select(load_query("app_status.rq")).iloc[0]
    except httpx.HTTPError as e:
        return Status(ok=False, error=str(e))
    return Status(ok=True, graphs=int(row["graphs"]), triples=int(row["triples"]))


@dataclass(frozen=True)
class CQ:
    id: str  # "cq01"
    title: str  # ファイル冒頭の `# ` コメント1行目
    text: str
    target_var: str | None  # VALUES で差し替える変数名（無ければ None）
    default_target: str | None  # VALUES の既定 IRI


def list_cqs() -> list[CQ]:
    cqs = []
    for path in sorted(QUERIES.glob("cq*.rq")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].removeprefix("#").strip()
        m = VALUES_IRI.search(text)
        cqs.append(
            CQ(
                id=path.stem,
                title=title,
                text=text,
                target_var=m.group(1) if m else None,
                default_target=m.group(2) if m else None,
            )
        )
    return cqs


def bind_values(text: str, var: str, iri: str) -> str:
    """`VALUES ?var { <...> }` の IRI を差し替える。該当行が無ければ ValueError。"""
    pattern = re.compile(rf"(VALUES\s+\?{re.escape(var)}\s*\{{\s*)<[^>]+>(\s*\}})")
    new, n = pattern.subn(rf"\g<1><{iri}>\g<2>", text, count=1)
    if n == 0:
        raise ValueError(f"VALUES ?{var} が見つかりません")
    return new


def target_options(sample_iri: str) -> pd.DataFrame:
    """sample_iri と同じクラスの個体を列挙する（列: s, name）。"""
    query = bind_values(load_query("app_targets.rq"), "sample", sample_iri)
    return select(query)


def run_cq(cq: CQ, target: str | None = None) -> tuple[str, pd.DataFrame]:
    """CQ を実行し、実際に流した SPARQL と結果を返す。"""
    query = cq.text
    if cq.target_var and target:
        query = bind_values(query, cq.target_var, target)
    return query, select(query)
