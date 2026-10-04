"""data/kg.trig + ontology/schema.ttl を ontology/shapes.ttl で SHACL 検証する。

Fuseki への再投入なしに、変換済みの TriG を検証だけしたい場合に使う。
src/load.py も判定の後に check() で Fuseki 上の全グラフを検証する。

  uv run src/validate.py           # data/kg.trig を検証
  uv run src/validate.py --fuseki  # Fuseki 上の全名前付きグラフ（urn:src:evidence を含む）を検証
"""

import sys
from pathlib import Path

import httpx
from pyshacl import validate
from rdflib import Dataset, Graph

import fuseki

ROOT = Path(__file__).resolve().parent.parent
TRIG = ROOT / "data" / "kg.trig"
SCHEMA = ROOT / "ontology" / "schema.ttl"
SHAPES = ROOT / "ontology" / "shapes.ttl"


def load_trig() -> Graph:
    if not TRIG.exists():
        print(f"ERROR: {TRIG} が見つかりません。先に scripts/xlsx_to_rdf.py で生成してください。", file=sys.stderr)
        sys.exit(1)
    ds = Dataset()
    ds.parse(TRIG, format="trig")
    data = Graph()
    for g in ds.graphs():
        data += g
    return data


def load_fuseki() -> Graph:
    resp = httpx.post(
        f"{fuseki.FUSEKI_URL}/query",
        data={"query": fuseki.load_query("validate_dump.rq")},
        headers={"Accept": "text/turtle"},
        timeout=fuseki.TIMEOUT,
    )
    resp.raise_for_status()
    return Graph().parse(data=resp.text, format="turtle")


def check(data: Graph) -> bool:
    """data にスキーマを足して SHACL 検証し、結果を表示する。違反があれば False（レポートは先頭 3000 文字）。"""
    data.parse(SCHEMA, format="turtle")
    conforms, _, text = validate(
        data,
        shacl_graph=Graph().parse(SHAPES, format="turtle"),
        advanced=True,
    )
    print("SHACL conforms:", conforms)
    if not conforms:
        print(text[:3000])
    return conforms


def main() -> None:
    data = load_fuseki() if "--fuseki" in sys.argv[1:] else load_trig()
    if not check(data):
        sys.exit(1)


if __name__ == "__main__":
    main()
