"""Fuseki 接続確認スクリプト。

サンプル Turtle を名前付きグラフへ Graph Store Protocol (GSP) で投入し、
SPARQL で件数を確認したのち、当該グラフを削除する。
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from SPARQLWrapper import JSON, SPARQLWrapper

load_dotenv()

FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030/kg")
GRAPH_URI = "urn:src:test"
SAMPLE_TTL = Path(__file__).resolve().parent.parent / "data" / "rdf" / "sample.ttl"


def put_graph() -> None:
    data = SAMPLE_TTL.read_bytes()
    resp = httpx.put(
        f"{FUSEKI_URL}/data",
        params={"graph": GRAPH_URI},
        content=data,
        headers={"Content-Type": "text/turtle"},
    )
    resp.raise_for_status()
    print(f"PUT graph <{GRAPH_URI}>: {resp.status_code}")


def count_triples() -> int:
    sparql = SPARQLWrapper(f"{FUSEKI_URL}/query")
    sparql.setQuery(
        f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{GRAPH_URI}> {{ ?s ?p ?o }} }}"
    )
    sparql.setReturnFormat(JSON)
    results = sparql.query().convert()
    return int(results["results"]["bindings"][0]["n"]["value"])


def delete_graph() -> None:
    resp = httpx.delete(
        f"{FUSEKI_URL}/data",
        params={"graph": GRAPH_URI},
    )
    resp.raise_for_status()
    print(f"DELETE graph <{GRAPH_URI}>: {resp.status_code}")


def main() -> None:
    put_graph()
    n = count_triples()
    print(f"triple count: {n}")
    if n != 3:
        print(f"ERROR: expected 3 triples, got {n}", file=sys.stderr)
        sys.exit(1)
    delete_graph()
    print("OK")


if __name__ == "__main__":
    main()
