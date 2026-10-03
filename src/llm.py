"""Claude API 呼び出し（APP_SPEC.md 第4節）。説明文生成と自然文→SPARQL の2箇所のみ。

説明文: Claude に渡すのは SPARQL が返した値の組み替えだけで、数値の計算はさせない。
  生成文は audit() で根拠データとの突き合わせを行い、根拠にない ID・数値・推測表現を検出する。
自然文→SPARQL: 生成した SPARQL は実行しない（実行はアプリ側でユーザーの承認後）。
"""

import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import anthropic
import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel

import evidence
import fuseki
import viz

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
MODEL = "claude-sonnet-5-5"
# APP_SPEC の 500 は1ルール分。複数ルールを1本にまとめるときは該当ルール数に比例させる
MAX_TOKENS_PER_JUDGEMENT = 500
MAX_TOKENS_SPARQL = 1500
# 自然文→SPARQL の参考クエリ（prompts/nl_to_sparql.md の {{EXAMPLES}}）。経路・集計・ルール参照の例
NL_EXAMPLES = ["cq01.rq", "cq03.rq", "cq04.rq"]

PREFIXES = {
    "http://example.org/kg/data/": "data:",
    "http://example.org/kg/ontology#": "ex:",
}

# 説明文に渡す根拠ノードの属性（ルール別。判定経路の説明に使うものだけ）。数量・地域などは渡さない。
# 経路グラフのツールチップには全属性を出す
EXPLAIN_ATTRS = {
    "R01": set(),
    "R02": {"start_date", "started_at", "maint_interval_days", "maint_date", "maint_type"},
    "R03": {"measured_value", "result", "inspected_at", "parameter", "min_value", "max_value", "unit"},
    "R05": {"rating", "parameter", "part_type"},  # 評価（R04 の判定根拠）・規格のパラメータ・部品種別
}

# prompts/evidence_to_text.md の制約（推測・断定回避・提案の禁止）に反する表現
HEDGES = ["思われ", "可能性", "推測", "考えられ", "おそらく", "かもしれ", "見られ", "恐れ", "推奨", "べき", "対策"]

# ルールの比較方向（comparison の値）の読み
COMPARISON_JA = {"gt": "閾値を超える", "ge": "閾値以上", "lt": "閾値未満", "le": "閾値以下"}

# 判定不能を「該当なし」と読み替える表現。payload に入るのは該当か判定不能だけなので、常に誤り
NOT_APPLICABLE = ["該当なし", "該当しない", "該当せず", "非該当", "問題なし", "問題ない"]
# 代替候補に推奨の強さ・優劣を付ける表現（R05 は条件を満たすかどうかだけを判定している）
STRENGTH = ["最適", "推奨度", "より良い", "より優れ", "最も", "おすすめ", "ベスト", "優先", "第一候補", "有力"]

# 英数字に続かない ID（タイムスタンプ "2026-06-24T15:00" の "T15" を ID と誤認しない）
ID_TOKEN = re.compile(r"(?<![0-9A-Za-z])[A-Z]{1,3}\d{2,}")
NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?")
# 根拠データに無い個数・序数（「2つのルール」「1つ目の候補」）。数字が偶然データにあっても検出する
COUNT_TOKEN = re.compile(r"[0-9０-９一二三四五六七八九]+(?:つ目|番目|点目|つの|点で)")


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


def vocabulary() -> dict[str, dict]:
    """スキーマのクラス・プロパティ（ローカル名）→ 日本語ラベルと文型（ex:sentenceTemplate）。"""
    df = fuseki.select(fuseki.load_query("llm_vocabulary.rq"))
    return {t.rsplit("#", 1)[-1]: {"label": lab, "template": tpl}
            for t, lab, tpl in zip(df.term, df.label, df.template)}


def _clean(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


class _TripleBuilder:
    """根拠を（主語・述語・目的語）の三つ組の一覧にする。述語は日本語ラベル、文はスキーマの文型で作る。"""

    def __init__(self, vocab: dict[str, dict]):
        self.vocab = vocab
        self.labels: dict[str, str] = {}
        self.triples: list[dict] = []
        self._seen: set[tuple] = set()

    def name_node(self, iri: str, cls: str | None, name: str | None) -> str:
        """表示名「<クラス名><ID>（<名前>）」。例: 部品PT013（モーター 13）"""
        if iri not in self.labels:
            cls_label = self.vocab.get(viz.local(cls or ""), {}).get("label") or ""
            self.labels[iri] = f"{cls_label}{viz.local(iri)}" + (f"（{name}）" if _clean(name) else "")
        return self.labels[iri]

    def add(self, s: str, prop: str, o, label: str | None = None) -> None:
        """prop はプロパティのローカル名。label を渡すとラベル・文型の代わりに「{s} の {label} は {o}」を使う。"""
        o = _clean(o)
        if o is None or (s, prop, label, o) in self._seen:
            return
        self._seen.add((s, prop, label, o))
        v = self.vocab.get(prop, {})
        pred = label or _clean(v.get("label")) or prop
        template = "{s} の " + label + " は {o}" if label else (_clean(v.get("template")) or "{s} の " + pred + " は {o}")
        o_text = self.labels.get(o, o) if isinstance(o, str) else o
        self.triples.append({"主語": s, "述語": pred, "目的語": o_text,
                             "文": template.replace("{s}", s).replace("{o}", str(o_text))})


def evidence_payload(detail: pd.DataFrame, edges: pd.DataFrame, props: pd.DataFrame) -> dict:
    """根拠を三つ組の一覧として渡す（値は SPARQL の結果のまま。比率のみ画面と同じ小数2桁表記）。

    - judgements: 判定の一覧（結論の重い順。evidence.CONCLUSION_ORDER）。各判定の Evidence・ルール・結論の表示名
    - triples: 根拠経路の実在トリプル（Evidence → 根拠ノード、根拠ノード同士）、Evidence の値、
      根拠ノードの属性（ルール別に EXPLAIN_ATTRS で絞る）、ルールと前提ルールの属性。
      分子・分母はルールの numerator_label / denominator_label を述語にする（「件」などへの読み替えをさせない）
    """
    tb = _TripleBuilder(vocabulary())
    rules = rule_table()
    for r in detail.drop_duplicates("node").itertuples():
        tb.name_node(r.node, r.type, r.name)
    for rule in rules.values():
        tb.name_node(rule["iri"], "Rule", rule["name"])

    judgements = []
    for ev_iri in evidence.order_by_conclusion(detail):
        rows = detail[detail.evidence == ev_iri]
        head = next(rows.itertuples())
        ev = tb.name_node(ev_iri, "Evidence", None)
        rule_label = tb.labels[rows[rows.role == "rule"].node.iloc[0]]
        judgements.append({"判定": ev, "ルール": rule_label, "結論": head.conclusion})

        # Evidence の値
        tb.add(ev, "conclusion", head.conclusion)
        tb.add(ev, "undeterminedReason", head.undeterminedReason)
        if _clean(head.observedValue) is not None:
            tb.add(ev, "observedValue", viz.display_value(head.observedValue, head.thresholdUnit))
        tb.add(ev, "observedNumerator", head.numerator, label=_clean(head.numeratorLabel) or "分子")
        tb.add(ev, "observedDenominator", head.denominator, label=_clean(head.denominatorLabel) or "分母")
        tb.add(ev, "appliedThreshold", head.threshold)

        # 根拠経路の実在トリプル（Evidence → 根拠ノード、根拠ノード同士）
        nodes = set(rows.node)
        for e in edges.drop_duplicates().itertuples():
            if (e.s == ev_iri or e.s in nodes) and (e.o in nodes):
                tb.add(tb.labels.get(e.s, e.s), viz.local(e.p), e.o)

        # 根拠ノードの属性（判定経路の説明に使うものだけ）
        allowed = EXPLAIN_ATTRS.get(head.ruleId, set())
        for r in props[props.node.isin(nodes)].itertuples():
            if viz.local(r.p) in allowed:
                tb.add(tb.labels[r.node], viz.local(r.p), r.o)

        # 適用ルールと前提ルール（R05 は R04）の属性
        for rid in [head.ruleId, *evidence.RULE_PRECONDITIONS.get(head.ruleId, [])]:
            rule = rules[rid]
            for prop in ("rule_name", "description", "threshold", "comparison", "min_denominator"):
                value = rule[prop]
                if prop == "comparison" and _clean(value):
                    value = f"{value}（{COMPARISON_JA.get(value, value)}）"
                tb.add(tb.labels[rule["iri"]], prop, value)

    return {"judgements": judgements, "triples": tb.triples}


def rule_table() -> dict[str, dict]:
    """rules グラフのルール（ID → iri と各属性）。値は rules グラフのまま。"""
    df = fuseki.select(fuseki.load_query("app_rules.rq"))
    return {r.ruleId: {"iri": r.rule, "id": r.ruleId, "name": r.ruleName, "rule_name": r.ruleName,
                       "description": r.description, "threshold": r.threshold, "comparison": r.comparison,
                       "min_denominator": r.minDenominator}
            for r in df.itertuples()}


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
    issues += [f"推奨の強さ・優劣: 「{w}」" for w in STRENGTH if w in text]
    issues += [f"根拠にない個数・序数: 「{w}」" for w in sorted(set(COUNT_TOKEN.findall(text)))]
    return issues


def _strip_fences(text: str) -> str:
    """コードフェンスが付いて返ってきた場合に外す（プロンプトでは禁止しているが念のため）。"""
    m = re.search(r"```(?:sparql)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def nl_to_sparql(question: str, previous: str | None = None, error: str | None = None) -> str:
    """自然文の質問から SPARQL を生成する。previous/error を渡すと、失敗内容を添えて再生成する。"""
    examples = "\n\n".join((ROOT / "queries" / name).read_text(encoding="utf-8") for name in NL_EXAMPLES)
    system = (load_prompt("nl_to_sparql.md")
              .replace("{{SCHEMA}}", (ROOT / "ontology" / "schema.ttl").read_text(encoding="utf-8"))
              .replace("{{EXAMPLES}}", examples))
    messages = [{"role": "user", "content": question}]
    if previous is not None:
        messages += [
            {"role": "assistant", "content": previous},
            {"role": "user", "content": f"このクエリは失敗した。\n{error}\n失敗の原因を直した SPARQL 本文だけを出力して。"},
        ]
    response = anthropic.Anthropic().beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_SPARQL,
        thinking={"type": "between_tools"},
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=messages,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("SPARQL の生成が拒否されました（stop_reason=refusal）")
    text = "".join(b.text for b in response.content if b.type == "text")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"SPARQL が max_tokens={MAX_TOKENS_SPARQL} で途切れました")
    return _strip_fences(text)


class ClaimCheck(BaseModel):
    claim: str
    triple_ids: list[int]
    supported: bool
    reason: str


class RelationAudit(BaseModel):
    claims: list[ClaimCheck]


def relation_audit(text: str, payload: dict) -> list[ClaimCheck]:
    """試行用の関係監査: 説明文の各主張が三つ組のどれに対応するかを Claude に判定させる。常時実行しない。

    audit() は ID・名前・数値の「存在」しか見ないため、実在するノード同士を誤った関係で結ぶ文
    （三つ組を辿った先を直接結ぶなど）は検出できない。それを補う確認として使う。
    """
    numbered = "\n".join(f"[{i}] {t['文']}" for i, t in enumerate(payload["triples"]))
    prompt = load_prompt("relation_audit.md").replace("{{TRIPLES}}", numbered).replace("{{TEXT}}", text)
    response = anthropic.Anthropic().messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": prompt}],
        output_format=RelationAudit,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("関係監査が拒否されました（stop_reason=refusal）")
    return response.parsed_output.claims
