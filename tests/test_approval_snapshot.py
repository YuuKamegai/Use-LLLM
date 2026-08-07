"""承認挙動の回帰スナップショット。

ms-data-parser の40ツールについて、現在の分類を凍結する。annotations 単独判定へ
移行しても、ここに書かれた分類が再現されなければならない。意図した差分は
EXPECTED_CHANGES に明記し、それ以外の変化はすべて回帰として落とす。

このフィクスチャに ms-data-parser の実名が並ぶのは意図的。契約が実サーバに対して
成立することを検証するのがこのテストの目的であり、製品コード側に実名は持たない。
"""

from __future__ import annotations

import unittest

from use_lllm.core.policy import ToolSafety, classify_tool

READ_ONLY = ToolSafety.READ_ONLY
LOCAL_WRITE = ToolSafety.LOCAL_WRITE
EXTERNAL = ToolSafety.EXTERNAL_NETWORK
MUTATION = ToolSafety.KNOWLEDGE_MUTATION

CURRENT_CLASSIFICATION: dict[str, ToolSafety] = {
    "arf_list_tags": READ_ONLY,
    "arf_list_classes": READ_ONLY,
    "arf_list_sample_roles": READ_ONLY,
    "arf_exclude": READ_ONLY,
    "arf_preprocess": READ_ONLY,
    "arf_pca_preprocessed": READ_ONLY,
    "arf_parser": READ_ONLY,
    "arf_differential": READ_ONLY,
    "arf_plot_volcano": READ_ONLY,
    "arf2_parser": READ_ONLY,
    "arf2_annotate_identities": READ_ONLY,
    "list_data_files": READ_ONLY,
    "load_dataset": READ_ONLY,
    "dcl_parser": READ_ONLY,
    "dcl_find_msms": READ_ONLY,
    "eic_parser": READ_ONLY,
    "eic_plot_chromatograms": READ_ONLY,
    "eic_plot_compounds": READ_ONLY,
    "eic_rank_by_max_intensity": READ_ONLY,
    "eic_search_by_mz_range": READ_ONLY,
    "eic_search_by_rt_range": READ_ONLY,
    "log_search": READ_ONLY,
    "knowledge_coverage": READ_ONLY,
    "pai2_parser": READ_ONLY,
    "pai2_inspect_peak": READ_ONLY,
    "verify_peak_annotation": READ_ONLY,
    "read_report": READ_ONLY,
    "list_reports": READ_ONLY,
    "sample_search": READ_ONLY,
    "record_objective": LOCAL_WRITE,
    "update_objective": LOCAL_WRITE,
    "write_report": LOCAL_WRITE,
    "save_pca_figure": LOCAL_WRITE,
    "save_volcano_figure": LOCAL_WRITE,
    "save_eic_figure": LOCAL_WRITE,
    "paper_search": EXTERNAL,
    "ingest_stage": MUTATION,
    "ingest_review_queue": MUTATION,
    "ingest_promote": MUTATION,
    "ingest_reject": MUTATION,
}

# annotations 単独判定へ移行したときに意図的に変わるもの（設計書 §6）。
# ingest_review_queue は build_inbox_index() を返すだけの純粋な読み取りで、
# KNOWLEDGE_MUTATION に入っているのは名前リストの分類ミス。ユーザ承認済みの緩和。
EXPECTED_CHANGES: dict[str, ToolSafety] = {
    "ingest_review_queue": READ_ONLY,
}

# 監査ラベルだけ変わるもの。decide_server_tool では LOCAL_WRITE も
# KNOWLEDGE_MUTATION も等しく承認必須なので、実行可否は変わらない。
EXPECTED_LABEL_CHANGES: dict[str, ToolSafety] = {
    "ingest_stage": LOCAL_WRITE,
}


def expected_after_migration() -> dict[str, ToolSafety]:
    """移行後に期待される分類表。"""
    result = dict(CURRENT_CLASSIFICATION)
    result.update(EXPECTED_CHANGES)
    result.update(EXPECTED_LABEL_CHANGES)
    return result


class CurrentClassificationSnapshotTests(unittest.TestCase):
    def test_snapshot_covers_every_ms_data_parser_tool(self) -> None:
        self.assertEqual(len(CURRENT_CLASSIFICATION), 40)

    def test_snapshot_matches_the_name_based_classifier(self) -> None:
        for name, expected in CURRENT_CLASSIFICATION.items():
            self.assertEqual(classify_tool(name), expected, name)


if __name__ == "__main__":
    unittest.main()
