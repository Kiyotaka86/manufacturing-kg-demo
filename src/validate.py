"""data/kg.trig + ontology/schema.ttl を ontology/shapes.ttl で SHACL 検証する。

Fuseki への再投入なしに、変換済みの TriG を検証だけしたい場合に使う。
"""

import sys
from pathlib import Path

from pyshacl import validate
from rdflib import Dataset, Graph

ROOT = Path(__file__).resolve().parent.parent
TRIG = ROOT / "data" / "kg.trig"
SCHEMA = ROOT / "ontology" / "schema.ttl"
SHAPES = ROOT / "ontology" / "shapes.ttl"


def main() -> None:
    if not TRIG.exists():
        print(f"ERROR: {TRIG} が見つかりません。先に scripts/xlsx_to_rdf.py で生成してください。", file=sys.stderr)
        sys.exit(1)

    ds = Dataset()
    ds.parse(TRIG, format="trig")
    data = Graph()
    for g in ds.graphs():
        data += g
    data.parse(SCHEMA, format="turtle")

    conforms, _, text = validate(
        data,
        shacl_graph=Graph().parse(SHAPES, format="turtle"),
        advanced=True,
    )
    print("SHACL conforms:", conforms)
    if not conforms:
        print(text[:3000])
        sys.exit(1)


if __name__ == "__main__":
    main()
