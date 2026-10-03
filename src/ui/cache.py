"""Fuseki・LLM の結果のキャッシュ（st.cache_data）。「再読込」「判定を再生成」で st.cache_data.clear() により消える。"""

import json

import streamlit as st

import fuseki
import llm


@st.cache_data(ttl=30, show_spinner=False)
def cached_status() -> fuseki.Status:
    return fuseki.status()


@st.cache_data(show_spinner=False)
def cached_targets(sample_iri: str) -> list[tuple[str, str]]:
    df = fuseki.target_options(sample_iri)
    return [(row.s, f"{row.s.rsplit('/', 1)[-1]} {row.name or ''}".strip()) for row in df.itertuples()]


@st.cache_data(show_spinner=False)
def cached_names() -> list[str]:
    return fuseki.select(fuseki.load_query("app_names.rq"))["name"].tolist()


@st.cache_data(show_spinner="説明文を生成中…")
def cached_explanation(payload_json: str) -> str:
    return llm.explain(json.loads(payload_json))
