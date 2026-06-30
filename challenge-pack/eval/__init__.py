"""AutoLoan-DocIntel candidate-runnable evaluation CLIs.

This package ships REAL, self-contained metric implementations so candidates can
score their own systems locally before submission. The same metric code is what
the private grading kit uses, so what you measure here is what you get graded on.

Modules
-------
- ``eval.levenshtein``    : pure-python edit distance + CER/WER helpers.
- ``eval.ocr_eval``       : TEDS / TEDS-Struct, GriTS-Top/Con, cell-F1, CER/WER.
- ``eval.sql_eval``       : NL->SQL Execution Accuracy, Exact-Set-Match, valid-SQL rate.
- ``eval.rag_eval``       : Recall@40, MRR, nDCG@10 with pre/post-rerank lift.
- ``eval.underwrite_eval``: AUC-ROC, PR-AUC, Brier, decision-agreement.

All metric math is dependency-light. Optional/heavy libraries (lxml, sqlglot,
psycopg, numpy, sklearn) are imported lazily inside the functions that need them
so that simply importing this package never fails.
"""

__all__ = [
    "levenshtein",
    "ocr_eval",
    "sql_eval",
    "rag_eval",
    "underwrite_eval",
]

__version__ = "1.0.0"
