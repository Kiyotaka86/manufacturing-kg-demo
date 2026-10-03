"""Claude API 呼び出し（APP_SPEC.md 第4節）。現時点は説明文生成のみ。

Claude に渡すのは SPARQL が返した値の組み替えだけで、数値の計算はさせない。
生成文は audit() で根拠データとの突き合わせを行い、根拠にない ID・数値・推測表現を検出する。
"""

import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import anthropic
import pandas as pd
from dotenv import load_dotenv

import evidence
import viz

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
MODEL = "claude-sonnet-5-5"
# APP_SPEC の 500 は1ルール分。複数ルールを1本にまとめるときは該当ルール数に比例させる
MAX_TOKENS_PER_JUDGEMENT = 500

PREFIXES = {
    "http://example.org/kg/data/": "data:",
    "http://example.org/kg/ontology#": "ex:",
}

# 説明文に渡す根拠ノードの属性（判定経路の説明に使うものだけ）。数量・評価・地域などは渡さない。
# 経路グラフのツールチップには全属性を出す
EXPLAIN_ATTRS = {
    "start_date",  # Lot（R02: 経過日数の起点）
    "started_at",  # Operation
    "maint_interval_days",  # Equipment（R02 の閾値）
    "maint_date", "maint_type",  # Maintenance（R02 の最終保全）
    "measured_value", "result", "inspected_at",  # Inspection（R03）
    "parameter", "min_value", "max_value", "unit",  # Spec（R03 の規格上下限）
}

# prompts/evidence_to_text.md の制約（推測・断定回避・提案の禁止）に反する表現
HEDGES = ["思われ", "可能性", "推測", "考えられ", "おそらく", "かもしれ", "見られ", "恐れ", "推奨", "べき", "対策"]

# 英数字に続かない ID（タイムスタンプ "2026-06-24T15:00" の "T15" を ID と誤認しない）
# 判定不能を「該当なし」と読み替える表現。payload に入るのは該当か判定不能だけなので、常に誤り
NOT_APPLICABLE = ["該当なし", "該当しない", "該当せず", "非該当", "問題なし", "問題ない"]

ID_TOKEN = re.compile(r"(?<![0-9A-Za-z])[A-Z]{1,3}\d{2,}")
NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?")


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def load_prompt(name: str) -> str:
    """プロンプト Markdown の `---` より後ろ（人間向けの前書きを除いた本文）を返す。"""
    text = (PROMPTS / name).read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1].strip()


def compact(value):
    """IRI を接頭辞付きに短縮し、欠損（NaN）は None にする。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        for ns, prefix in PREFIXES.items():
            if value.startswith(ns):
                return prefix + value[len(ns):]
    return value


def evidence_payload(detail: pd.DataFrame, edges: pd.DataFrame, props: pd.DataFrame) -> dict:
    """evidence_detail / evidence_edges / evidence_node_props の結果を判定単位の JSON に組み替える（値は無加工）。

    判定は結論の重い順（evidence.CONCLUSION_ORDER）に並べる。分子・分母は Rule のラベルと対にして渡し、
    ラベルに無い意味づけ（「件」への読み替えなど）を Claude にさせない。
    """
    edges = edges.drop_duplicates().map(compact)
    attrs: dict[str, dict] = {}
    for r in props[props.p.map(lambda p: p.rsplit("#", 1)[-1] in EXPLAIN_ATTRS)].itertuples():
        attrs.setdefault(compact(r.node), {})[compact(r.p)] = r.o
    judgements = []
    for ev_iri in evidence.order_by_conclusion(detail):
        group = detail[detail.evidence == ev_iri]
        rows = [{k: compact(v) for k, v in r.items()} for r in group.to_dict("records")]
        head = rows[0]
        ev_iri = compact(ev_iri)
        nodes = {r["node"] for r in rows}
        measures = [
            {"label": head[f"{part}Label"], "value": head[part]}
            for part in ("numerator", "denominator")
            if head[part] is not None
        ]
        judgements.append({
            "evidence": ev_iri,
            "conclusion": head["conclusion"],
            "rule": {"id": head["ruleId"], "name": head["ruleName"], "threshold_unit": head["thresholdUnit"]},
            # 画面と同じ表記（比率は小数2桁）。説明文と画面の数値を一致させる
            "observedValue": viz.display_value(head["observedValue"], head["thresholdUnit"]),
            "measures": measures,
            "threshold": head["threshold"],
            "undeterminedReason": head["undeterminedReason"],
            "evaluatedAt": head["evaluatedAt"],
            "nodes": [
                {"iri": r["node"], "role": r["role"], "class": r["type"], "name": r["name"],
                 "attributes": attrs.get(r["node"], {})}
                for r in rows
            ],
            "path": [
                {"s": e.s, "p": e.p, "o": e.o}
                for e in edges.itertuples()
                if e.s == ev_iri or (e.s in nodes and e.o in nodes)
            ],
        })
    return {"judgements": judgements}


def explain(payload: dict) -> str:
    """判定結果 JSON から、該当した全ルールを1本にまとめた説明文を生成する。"""
    max_tokens = MAX_TOKENS_PER_JUDGEMENT * max(1, len(payload["judgements"]))
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    prompt = load_prompt("evidence_to_text.md").replace("{{EVIDENCE_JSON}}", data)
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        # 根拠 JSON を文章に置き換えるだけの短い変換なので、思考は使わない
        thinking={"type": "between_tools"},
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("説明文の生成が拒否されました（stop_reason=refusal）")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"説明文が max_tokens={max_tokens} で途切れました: {text}")
    return text


def _numbers(text: str) -> set[Decimal]:
    out = set()
    for tok in NUM_TOKEN.findall(text):
        try:
            out.add(Decimal(tok).normalize())
        except InvalidOperation:
            pass
    return out


def audit(text: str, payload: dict, known_names: list[str] = ()) -> list[str]:
    """説明文のうち、根拠データに無い ID・名前・数値、および推測/提案表現を列挙する（空なら問題なし）。

    known_names には KG 上の全 *_name 値を渡す。根拠に含まれない名前が本文に出たら検出する。
    """
    source = json.dumps(payload, ensure_ascii=False)
    issues = []

    issues += [f"根拠にない名前: {n}" for n in sorted(set(known_names)) if n in text and n not in source]

    ids = set(ID_TOKEN.findall(text))
    issues += [f"根拠にない ID: {i}" for i in sorted(ids - set(ID_TOKEN.findall(source)))]

    stripped = ID_TOKEN.sub(" ", text)
    allowed = _numbers(ID_TOKEN.sub(" ", source))
    issues += [f"根拠にない数値: {n}" for n in sorted(_numbers(stripped) - allowed)]

    issues += [f"推測・提案表現: 「{h}」" for h in HEDGES if h in text]
    issues += [f"該当なしへの読み替え: 「{w}」" for w in NOT_APPLICABLE if w in text]
    return issues
