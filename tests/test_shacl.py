"""ontology/shapes.ttl の Evidence 時系列制約のテスト（Fuseki 不要）。

他の制約には違反しない最小のグラフ（ロット1件・Evidence 1件）に、ケースごとの根拠事実を足して検証し、
出た違反メッセージの集合を期待値と比べる。期待値が空なら「準拠」。

- 評価時点: 根拠事実の日付・日時が evaluatedAt より後なら違反。
  xsd:date はその日の 00:00、タイムゾーンの無い日時は +09:00 とみなして比べる
- 判定の基準日: ロットを対象とする判定で、根拠の保全・購買がロットの製造開始日より後なら違反
"""

from pathlib import Path

import pytest
from pyshacl import validate
from rdflib import Graph, Namespace

ROOT = Path(__file__).resolve().parent.parent
SHAPES = Graph().parse(ROOT / "ontology" / "shapes.ttl", format="turtle")
SH = Namespace("http://www.w3.org/ns/shacl#")

EVAL_TIME = "根拠事実の日付が評価時点より後になっている（時系列矛盾）"
BASE_DATE = "ロットを対象とする判定の根拠のうち、保全・購買はロットの製造開始日以前であること"

# 評価時点 2026-10-04 13:00 (+09:00)、ロットの製造開始日 2026-09-01
BASE = """
@prefix ex:  <http://example.org/kg/ontology#> .
@prefix d:   <http://example.org/kg/data/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

d:P1 a ex:Product ; ex:product_id "P1" ; ex:product_name "製品" .
d:L1 a ex:Lot ; ex:lot_id "L1" ; ex:lotProduct d:P1 ; ex:start_date "2026-09-01"^^xsd:date ; ex:status "完了" .
d:E1 a ex:Equipment ; ex:equipment_id "E1" ; ex:maint_interval_days 30 .
d:SP1 a ex:Spec ; ex:spec_id "SP1" ; ex:min_value 1.0 ; ex:max_value 2.0 .
d:PT1 a ex:Part ; ex:part_id "PT1" ; ex:hasSpec d:SP1 .
d:S1 a ex:Supplier ; ex:supplier_id "S1" ; ex:rating 2 .
d:R1 a ex:Rule ; ex:rule_id "R1" ; ex:description "テスト" ; ex:conclusion "要注意" .

d:EV1 a ex:Evidence ; ex:evidenceRule d:R1 ; ex:evidenceSubject d:L1 ; ex:evidenceFact d:F ;
    ex:conclusion "要注意" ; ex:observedNumerator 1 ; ex:appliedThreshold 0.05 ;
    ex:evaluatedAt "2026-10-04T13:00:00+09:00"^^xsd:dateTime .
"""


def operation(started_at: str) -> str:
    return f'd:F a ex:Operation ; ex:opLot d:L1 ; ex:opEquipment d:E1 ; ex:started_at "{started_at}"^^xsd:dateTime .'


def maintenance(date: str) -> str:
    return f'd:F a ex:Maintenance ; ex:maintainedEquipment d:E1 ; ex:maint_date "{date}"^^xsd:date .'


def purchase(date: str) -> str:
    return (f'd:F a ex:Purchase ; ex:purchase_id "PU1" ; ex:purchasedPart d:PT1 ; ex:purchasedFrom d:S1 ; '
            f'ex:purchase_date "{date}"^^xsd:date .')


def inspection(inspected_at: str) -> str:
    return (f'd:F a ex:Inspection ; ex:inspectedLot d:L1 ; ex:inspectedSpec d:SP1 ; ex:measured_value 1.5 ; '
            f'ex:result "OK" ; ex:inspected_at "{inspected_at}"^^xsd:dateTime .')


CASES = {
    # 評価時点との比較（日付の型・タイムゾーンの違い）
    "TZなし日時・評価より後": (operation("2026-10-04T14:00:00"), {EVAL_TIME}),
    "TZなし日時・評価より前": (operation("2026-10-04T12:00:00"), set()),
    "TZ付き日時・評価より後（UTC 05:30 = JST 14:30）": (operation("2026-10-04T05:30:00Z"), {EVAL_TIME}),
    "TZ付き日時・評価より前（UTC 03:00 = JST 12:00）": (operation("2026-10-04T03:00:00Z"), set()),
    "date・評価の翌日": (maintenance("2026-10-05"), {EVAL_TIME, BASE_DATE}),
    "date・評価と同じ日": (purchase("2026-08-31") + ' d:E1 ex:install_date "2026-10-04"^^xsd:date . '
                           'd:EV1 ex:evidenceFact d:E1 .', set()),
    # 判定の基準日（ロットの製造開始日）との比較
    "保全・開始日より後": (maintenance("2026-09-02"), {BASE_DATE}),
    "保全・開始日と同じ日": (maintenance("2026-09-01"), set()),
    "購買・開始日より後": (purchase("2026-09-10"), {BASE_DATE}),
    "購買・開始日より前": (purchase("2026-08-20"), set()),
    "検査・開始日より後（正常）": (inspection("2026-09-15T10:00:00"), set()),
}


def violations(extra: str) -> set[str]:
    data = Graph().parse(data=BASE + extra, format="turtle")
    _, results, _ = validate(data, shacl_graph=SHAPES, advanced=True)
    return {str(m) for m in results.objects(None, SH.resultMessage)}


@pytest.mark.parametrize("extra, expected", CASES.values(), ids=CASES.keys())
def test_evidence_temporal(extra, expected):
    assert violations(extra) == expected
