# 舊的評測結果

`evals/results/` 只放每項評測最新的一次結果。這裡是被取代的舊結果（日期是 2026 年 commit 的那天），留著是因為
[docs/evaluation.md](../../../docs/zh-TW/evaluation.md) 的歷史表格和前後比較用的是它們的數字。
重跑評測一樣寫到 `evals/results/<label>.json`，不會寫到這裡。

## 檢索（`run_rag_eval.py`）

| 檔案 | 日期 | 資料 | 這次在測什麼 | 現在看 |
|---|---|---|---|---|
| `baseline.json` | 09-01 | 50 間 | minilm + 舊文件 | `rag_after_names.json` |
| `bge-m3_olddocs.json` | 09-02 | 50 間 | bge-m3 + 舊文件 | 同上 |
| `minilm_newdocs.json` | 09-02 | 50 間 | minilm + 擴充文件 | 同上 |
| `bge-m3.json` | 09-02 | 50 間 | bge-m3 + 擴充文件，沒有規則層 | `ablation_dense_only.json` |
| `bge-m3_rules.json` | 09-08 | 50 間 | 再加上規則層 | `rag_after_names.json` |
| `retrieval_after_gen_fixes.json` | 09-14 | 50 間 | 規則層調整期間的重跑 | 同上 |
| `retrieval_round3.json` | 09-14 | 50 間 | 同上 | 同上 |
| `retrieval_grouped.json` | 10-12 | 50 間 | 同上 | 同上 |
| `grouped_norules.json` | 10-12 | 50 間 | 同上，沒有規則層 | `ablation_dense_only.json` |
| `retrieval_ci.json` | 10-12 | 50 間 | 規則層調整期間的重跑 | `rag_after_names.json` |
| `retrieval_offtopic.json` | 10-15 | 50 間 | 同上 | 同上 |
| `official_bge-m3_rules.json` | 10-25 | 644 間 | 官方資料第一次，近似搜尋修正前 | 同上 |

## 生成（`run_gen_eval.py`）

| 檔案 | 日期 | 資料 | 這次在測什麼 | 現在看 |
|---|---|---|---|---|
| `gen_llama3.2-3b_baseline.json` | 09-14 | 50 間 | 修改提示之前 | `gen_after_names.json` |
| `gen_llama3.2-3b.json` | 09-14 | 50 間 | 修改提示之後 | 同上 |
| `gen_qwen2.5-7b.json` | 09-14 | 50 間 | 同上，換成 qwen2.5:7b | 同上 |
| `official_gen_llama3.2-3b.json` | 10-25 | 644 間 | 官方資料第一次 | 同上 |
| `official_gen_llama3.2-3b_v2.json` | 10-25 | 644 間 | 指標修正 + 回答檢查 | 同上 |
| `official_gen_llama3.2-3b_v3_direct.json` | 10-29 | 644 間 | 列表與點名改由程式回答 | 同上 |
| `official_gen_after_hnsw_fix.json` | 11-11 | 644 間 | 近似搜尋修正後 | 同上 |

## 其他

| 檔案 | 日期 | 這次在測什麼 | 現在看 |
|---|---|---|---|
| `boundary_eval.json` | 10-29 | 邊界題，近似搜尋修正前 | `boundary_after_hnsw_fix.json` |
| `sim_llama3.2-3b.json` | 11-02 | 模擬追問，還交給模型回答的時候 | `sim_code.json` |
| `place_baseline.json` | 11-02 | 小地名，用語意檢索 | `place_code.json` |

`*.log` 是當時的終端輸出，沒有進版控。
