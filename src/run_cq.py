"""CQ をコマンドラインから実行する。実行ロジックは fuseki.py に持たせ、ここは表示のみ。

使い方:
    uv run src/run_cq.py                 # CQ 一覧
    uv run src/run_cq.py cq01            # 既定の対象で実行
    uv run src/run_cq.py cq01 http://example.org/kg/data/product/P002
"""

import sys

import pandas as pd

import fuseki


def main() -> None:
    cqs = {cq.id: cq for cq in fuseki.list_cqs()}
    if len(sys.argv) < 2:
        for cq in cqs.values():
            print(f"{cq.id}  {cq.title}")
        return
    cq = cqs[sys.argv[1]]
    target = sys.argv[2] if len(sys.argv) > 2 else None
    _, df = fuseki.run_cq(cq, target)
    print(cq.title)
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(df)
    print(f"{len(df)} rows")


if __name__ == "__main__":
    main()
