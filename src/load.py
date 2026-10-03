"""kg_toolkit.xlsx → TriG 変換 → Fuseki 投入。

1. scripts/xlsx_to_rdf.py で xlsx を TriG に変換し、SHACL 検証する（非準拠なら中断）
2. ontology/schema.ttl を既定グラフへ GSP で投入
3. TriG 内の名前付きグラフ（urn:src:<sheet名> ×16）を1つずつ GSP で投入
4. queries/materialize_*.rq を実行し、propertyChainAxiom を urn:derived:chains に実体化
   （Fuseki の Reasoner は使わない）
5. src/evidence.py で判定ルールを実行し、urn:src:evidence を CLEAR → 再生成する。
   タブ2 の what-if 結果（urn:whatif:evidence）は前回の残りなので消す
"""

import os
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rdflib import Dataset
from SPARQLWrapper import JSON, SPARQLWrapper

import evidence

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030/kg")

XLSX = ROOT / "data" / "kg_toolkit.xlsx"
SCHEMA = ROOT / "ontology" / "schema.ttl"
SHAPES = ROOT / "ontology" / "shapes.ttl"
TRIG = ROOT / "data" / "kg.trig"
XLSX_TO_RDF = ROOT / "scripts" / "xlsx_to_rdf.py"

MATERIALIZE_QUERIES = [
    ROOT / "queries" / "materialize_lot_supplier.rq",
    ROOT / "queries" / "materialize_lot_equipment.rq",
]


def convert_and_validate() -> None:
    subprocess.run(
        [sys.executable, str(XLSX_TO_RDF), str(XLSX), str(SCHEMA), str(SHAPES), str(TRIG)],
        check=True,
    )


def put_default_graph() -> None:
    resp = httpx.put(
        f"{FUSEKI_URL}/data?default",
        content=SCHEMA.read_bytes(),
        headers={"Content-Type": "text/turtle"},
    )
    resp.raise_for_status()
    print(f"PUT default graph (schema.ttl): {resp.status_code}")


def put_named_graphs() -> None:
    ds = Dataset()
    ds.parse(TRIG, format="trig")
    n = 0
    for g in ds.graphs():
        if g.identifier == ds.default_graph.identifier:
            continue
        body = g.serialize(format="turtle").encode("utf-8")
        resp = httpx.put(
            f"{FUSEKI_URL}/data",
            params={"graph": str(g.identifier)},
            content=body,
            headers={"Content-Type": "text/turtle"},
        )
        resp.raise_for_status()
        print(f"PUT graph <{g.identifier}>: {resp.status_code} ({len(g)} triples)")
        n += 1
    print(f"named graphs loaded: {n}")


def materialize_property_chains() -> None:
    resp = httpx.post(
        f"{FUSEKI_URL}/update",
        data={"update": "CLEAR SILENT GRAPH <urn:derived:chains>"},
    )
    resp.raise_for_status()
    print(f"CLEAR urn:derived:chains: {resp.status_code}")
    for path in MATERIALIZE_QUERIES:
        resp = httpx.post(
            f"{FUSEKI_URL}/update",
            data={"update": path.read_text(encoding="utf-8")},
        )
        resp.raise_for_status()
        print(f"materialized {path.name}: {resp.status_code}")


def evaluate_rules() -> None:
    evidence.clear(evidence.WHATIF_GRAPH)
    df = evidence.evaluate()
    for row in df.itertuples():
        print(f"evidence {row.rule.rsplit('/', 1)[-1]} {row.conclusion}: {row.evidences}")


def report() -> None:
    sparql = SPARQLWrapper(f"{FUSEKI_URL}/query")
    sparql.setQuery(
        "SELECT (COUNT(DISTINCT ?g) AS ?graphs) (COUNT(*) AS ?triples) "
        "WHERE { GRAPH ?g { ?s ?p ?o } }"
    )
    sparql.setReturnFormat(JSON)
    row = sparql.query().convert()["results"]["bindings"][0]
    print(f"named graphs: {row['graphs']['value']}, triples in named graphs: {row['triples']['value']}")


def main() -> None:
    convert_and_validate()
    put_default_graph()
    put_named_graphs()
    materialize_property_chains()
    evaluate_rules()
    report()
    print("OK")


if __name__ == "__main__":
    main()
