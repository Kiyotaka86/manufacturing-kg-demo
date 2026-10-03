"""kg_toolkit.xlsx → TriG（名前付きグラフ urn:src:<sheet名>）に変換し、SHACL検証を行う。
usage: python xlsx_to_rdf.py kg_toolkit.xlsx schema.ttl shapes.ttl out.trig
"""
import sys, datetime as dt
import pandas as pd
from decimal import Decimal
from rdflib import Dataset, Graph, Namespace, URIRef, Literal, RDF, XSD

EX = Namespace("http://example.org/kg/ontology#")
DATA = Namespace("http://example.org/kg/data/")

# sheet → (class, id列, {列: (オブジェクトプロパティ, 参照先class)})
MAP = {
    "products":   ("Product", "product_id", {}),
    "parts":      ("Part", "part_id", {"spec_id": ("hasSpec", "spec")}),
    "suppliers":  ("Supplier", "supplier_id", {}),
    "customers":  ("Customer", "customer_id", {}),
    "bom":        ("BomLine", "bom_id", {"product_id": ("bomProduct", "product"), "part_id": ("bomPart", "part")}),
    "equipment":  ("Equipment", "equipment_id", {}),
    "specs":      ("Spec", "spec_id", {}),
    "purchases":  ("Purchase", "purchase_id", {"part_id": ("purchasedPart", "part"), "supplier_id": ("purchasedFrom", "supplier")}),
    "lots":       ("Lot", "lot_id", {"product_id": ("lotProduct", "product")}),
    "lot_operations": ("Operation", "op_id", {"lot_id": ("opLot", "lot"), "equipment_id": ("opEquipment", "equipment")}),
    "lot_parts":  ("PartConsumption", "lp_id", {"lot_id": ("consumedInLot", "lot"), "purchase_id": ("consumedPurchase", "purchase")}),
    "inspections": ("Inspection", "inspection_id", {"lot_id": ("inspectedLot", "lot"), "spec_id": ("inspectedSpec", "spec")}),
    "maintenance": ("Maintenance", "maint_id", {"equipment_id": ("maintainedEquipment", "equipment")}),
    "orders_shipments": ("Order", "order_id", {"customer_id": ("orderCustomer", "customer"), "product_id": ("orderProduct", "product"), "lot_id": ("shippedLot", "lot")}),
    "defects":    ("Defect", "defect_id", {"lot_id": ("defectLot", "lot"), "customer_id": ("defectCustomer", "customer")}),
    "rules":      ("Rule", "rule_id", {}),
}
DECIMAL_COLS = {"min_value", "max_value", "measured_value"}

def lit(col, v):
    if pd.isna(v): return None
    if isinstance(v, pd.Timestamp):
        if v.hour or v.minute: return Literal(v.to_pydatetime().isoformat(), datatype=XSD.dateTime)
        return Literal(v.date().isoformat(), datatype=XSD.date)
    if col in DECIMAL_COLS: return Literal(Decimal(str(round(float(v), 4))), datatype=XSD.decimal)
    if isinstance(v, (int,)) or (isinstance(v, float) and v.is_integer()): return Literal(int(v), datatype=XSD.integer)
    if isinstance(v, float): return Literal(Decimal(str(v)), datatype=XSD.decimal)
    return Literal(str(v))

def convert(xlsx):
    ds = Dataset()
    sheets = pd.read_excel(xlsx, sheet_name=list(MAP))
    for sheet, (cls, idcol, refs) in MAP.items():
        g = ds.graph(URIRef(f"urn:src:{sheet}"))
        for _, row in sheets[sheet].iterrows():
            s = DATA[f"{cls[0].lower()+cls[1:]}/{row[idcol]}"] if cls != "Rule" else DATA[f"rule/{row[idcol]}"]
            g.add((s, RDF.type, EX[cls]))
            for col, v in row.items():
                if col in refs:
                    if pd.isna(v): continue
                    prop, tgt = refs[col]
                    g.add((s, EX[prop], DATA[f"{tgt}/{v}"]))
                    continue
                l = lit(col, v)
                if l is not None: g.add((s, EX[col], l))
    return ds

if __name__ == "__main__":
    xlsx, schema, shapes, out = sys.argv[1:5]
    ds = convert(xlsx)
    ds.serialize(out, format="trig")
    print("graphs:", len(list(ds.graphs())) - 1, "triples:", len(ds))
    from pyshacl import validate
    data = Graph()
    for g in ds.graphs(): data += g
    data.parse(schema, format="turtle")
    conforms, _, text = validate(data, shacl_graph=Graph().parse(shapes, format="turtle"), advanced=True)
    print("SHACL conforms:", conforms)
    if not conforms:
        print(text[:3000])
        sys.exit(1)
