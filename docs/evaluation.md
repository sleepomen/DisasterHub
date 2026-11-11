# Evaluation

[← README](../README.md) · English · [繁體中文](zh-TW/evaluation.md)

## RAG Recall Evaluation

### On the official data (644 shelters, 207 questions)

`--label official_bge-m3_rules`, bge-m3 + enriched documents + rules layer:

```
          n   |rel|   metrics that apply
single   92   1       MRR 0.984   hit@3 0.989   hit@10 0.989   R@all 0.989
multi   105   35.8    R-Prec 0.658   R@all 0.658   P@all 0.951   (R@10 0.621, its ceiling 0.634)
negatives 10/10 rejected by the rules layer
```

- **Single-answer questions are nearly saturated.** One question still misses: 「馬蘭榮家」, an abbreviation of 行政院國軍退除役官兵輔導委員會馬蘭榮譽國民之家 that no rule produces and bge-m3 does not connect.
- **List recall is low by design.** Listing questions now average 35.8 correct shelters each. A filtered result returns at most 30 shelters, and above 15 matches the prompt gets the overview with 10. Region questions (≈ 200 each) score R@all 0.047, and that is exactly what the model is shown.
- **What the listing numbers mean now.** Precision on listing questions stays at 0.951: whatever is returned matches the condition. For listings, the useful number is now whether the answer gives the right total and distribution, not R@all.

The tables below are the history on the previous 50-shelter data, where a county had at most 20 shelters and every listing fit in the prompt.

The evaluation set `evals/datasets/rag_eval.jsonl` (130 questions across 9 categories) is generated from `data_for_refuge/` by `evals/lib/build_eval_set.py`. Ground truth for the structured categories (name, region, township, facilities, capacity) is derived programmatically; alias, semantic and negative cases are labeled by hand.

**Limitation worth stating in any write-up:** the township and facility ground truth (32 of the 130 questions) is computed by the same `township_of()` and `facility_of()` functions in `services/retrieval/shelter_profile.py` that write the index metadata, so for those two categories the evaluation partly tests the retriever against itself, and part of the gain from "enriched documents" is circular. The alias, semantic and negative categories are hand-labeled and not affected. A held-out set written by someone who has not seen the data format is the only real fix.

```bash
docker exec -it disaster_app python evals/lib/build_eval_set.py
docker exec -it disaster_app python evals/run_rag_eval.py --embedder minilm --label baseline
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3
```

The run reports, per category, Recall@3/5/10 with the ceiling of Recall@10 next to it, R-Precision, Precision, MRR, the mean distance of the relevant documents, the top-1 distance for the negative cases (used to pick a similarity threshold), and query latency p50/p95. It then repeats the aggregate split into single-answer and listing questions, because Recall@K and MRR only mean something on the former. Full results land in `evals/results/<label>.json`. `--threshold` sets a cosine distance cutoff for computing the false-positive rate on negatives, and `--doc-style legacy` builds the index with the old document template for comparison.

Results so far (macro; R@all is scored over the full returned list, "misses" counts questions with no relevant document in the top 10):

| Configuration | R@3 | R@10 | R-Prec | R@all | P@all | MRR | Misses | Negatives rejected |
|---|---|---|---|---|---|---|---|---|
| MiniLM + legacy documents (baseline) | 0.379 | 0.619 | – | 0.619 | – | 0.561 | 27 | 0/10 |
| MiniLM + enriched documents | 0.530 | 0.775 | – | 0.775 | – | 0.726 | 10 | 0/10 |
| bge-m3 + legacy documents | 0.674 | 0.865 | – | 0.865 | – | 0.864 | 4 | 0/10 |
| bge-m3 + enriched documents | 0.748 | 0.898 | – | 0.898 | 0.335 | 0.926 | 2 | 0/10 |
| **bge-m3 + enriched documents + rules layer (current)** | **0.801** | **0.939** | **0.992** | **1.000** | **0.926** | **0.996** | **0** | **8/10** |

**R@10 on this set has a ceiling of 0.939, not 1.0.** Forty-one of the 130 questions ask for every shelter
in a region, township or facility class, and nine of those have 14 to 20 correct answers, so no ranking can
fit all of them into the top 10: `min(10, |rel|) / |rel|` averages 0.939 over the set. The current
configuration's R@10 is 0.939 — *exactly* the ceiling. That number measures the shape of the evaluation set,
not retrieval quality. `run_rag_eval.py` now prints the ceiling next to R@10 (`ceil@10`) and splits the
questions by how many correct answers they have, because Recall@K is only comparable across questions
when K ≥ |rel|:

```
          n   |rel|   metrics that apply
single   64   1       MRR 0.992   hit@3 1.000   hit@10 1.000   R@all 1.000
multi    56   6.6     R-Prec 1.000   R@all 1.000   P@all 0.986   (R@10 0.907, its ceiling 0.907)
```

For single-answer questions the ranking metrics are meaningful and nearly saturated. For listing questions
the honest metrics are R-Precision (precision at K = |rel|) and R@all, and both are 1.000: with the rules
layer, retrieval returns exactly the right set. What is left to improve is P@all on the two hand-labeled
categories — alias 0.642 and semantic 0.763 — where a metadata filter cannot help and the dense retriever
returns extra documents alongside the right ones. Those two categories are where RAG is doing the work on
this data set.

Without the rules layer retrieval always returns the top 10, so R@all equals R@10. With it, the region, township, facility and capacity categories all reach R@all and P@all of 1.0 ("Which shelters are in Yilan?" returns all 20 with no noise). The bare street-name query "四維路" used to be the one partial miss (R@all 0.5 in the `bge-m3_rules` run); the road metadata filter added afterwards fixed it, and every category now reaches R@all 1.000. A BM25 hybrid is therefore no longer motivated by a miss on this set — the reason to measure it would be to quantify how much the dense retriever still contributes once the rules layer is in place, which is what an ablation against a rules-only / SQL-only baseline would show. The two negatives that are not rejected ("What's the weather today?" and "How do I apply for disaster relief?") are not geographic questions; the prompt rules answer them with "no relevant data".

Scored this way, the `--no-rules` comparison says something the old table could not: the two components
carry different question types.

| | single: MRR | single: hit@10 | listing: R-Prec | listing: R@all | listing: P@all | negatives rejected |
|---|---|---|---|---|---|---|
| bge-m3 + enriched docs, **no rules layer** | 0.969 | 1.000 | 0.794 | 0.876 | 0.493 | 0/10 |
| **+ rules layer (current)** | 0.992 | 1.000 | 1.000 | 1.000 | 0.986 | 8/10 |

Dense retrieval alone already answers single-answer questions — hit@10 is 1.000 without the rules layer,
and MRR only moves 0.969 → 0.992. Everything the rules layer buys is on listing questions
(R-Precision 0.794 → 1.000, P@all 0.493 → 0.986) and on declining out-of-scope questions (0/10 → 8/10).
That is the honest division of labour between the two mechanisms, and it is the comparison an ablation
should extend rather than replace.

Pass `--no-rules` to run the comparison without the rules layer:

```bash
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3_rules
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3 --no-rules
```

"Enriched documents" means the township, facility types, capacity tier and aliases that `services/retrieval/shelter_profile.py` derives from the name and address, written into both the vector document and its metadata. The "rules layer" is `services/chat/query_rules.py`: before vector retrieval it extracts the region (Yilan / Hualien / Taitung), township (29 of them, taken from the population model, including suffix-less forms such as 礁溪), facility type (school / elementary school / gymnasium / township office…) and capacity conditions (over a thousand, more than 500, 300 or fewer, small…), turns them into a ChromaDB `where` filter and returns every matching document; "largest / most" questions are re-ordered by capacity; an empty filtered result falls back to plain semantic retrieval. A question that names a county outside the coverage area (Taipei, Kaohsiung…) with no eastern place name in it is declined by the rules layer and never reaches the vector index.

```
DisasterHub/
├── evals/
│   ├── run_rag_eval.py         # Runs the retrieval evaluation
│   ├── run_route_eval.py       # Runs the routing evaluation (no services needed)
│   ├── run_boundary_eval.py    # Reports which layer refused, --generate hits the real LLM
│   ├── run_knowledge_eval.py   # Knowledge routing, retrieval and, with --generate, answer faithfulness
│   ├── run_gen_eval.py         # Runs the generation evaluation (real ChatService + Ollama)
│   ├── run_faithfulness_eval.py # Does the model follow a changed / incomplete context?
│   ├── run_simulation_eval.py  # Simulation follow-ups through the real API (allocation, unplaced, affected, excluded)
│   ├── run_place_eval.py       # Small place names: are the shelters in the right township?
│   ├── run_assignment_eval.py  # Evacuation assignment: coordinated (min-cost flow) against everyone heading for the nearest shelter
│   ├── run_coordinate_check.py # Shelter coordinates: distance to the road network and to their own village
│   ├── run_sensitivity_eval.py # Evacuation assignment when demand, capacity or origins change
│   ├── run_heldout_eval.py     # Questions written by other people: answer, grade by hand, score
│   ├── datasets/               # Question sets
│   │   ├── rag_eval.jsonl      # The evaluation set
│   │   ├── route_eval.jsonl    # Routing test set (59 questions, all twelve routes)
│   │   ├── boundary_eval.jsonl # Boundary / abstention set (54 questions, five defences)
│   │   ├── knowledge_eval.jsonl # Knowledge questions (positive / not covered / mixed / shelter look-alikes)
│   │   ├── place_eval.jsonl    # Small place names with the township each lies in
│   │   └── heldout/            # Instructions and template for questions written by other people
│   ├── lib/                    # Scoring, statistics and tools shared by the runners
│   │   ├── metrics.py          # Recall / Precision / MRR / R-Precision / ceilings / bootstrap CIs
│   │   ├── gen_metrics.py      # Answer recall / hallucination / number faithfulness / format rules
│   │   ├── faithfulness.py     # Counterfactual context perturbations and their scoring
│   │   ├── build_eval_set.py   # Generates the evaluation set
│   │   └── compare_runs.py     # Paired bootstrap on the difference between two runs
│   └── results/                # Output of each run
```

## Generation Quality Evaluation

> **On the official data (644 shelters, 207 questions, llama3.2:3b, 2026-10-25):**
>
> | Run | ansR | ansP | full | halluc | f_abst | num_ok | fmt_ok | negatives | p50 |
> |---|---|---|---|---|---|---|---|---|---|
> | first run on the official data | 0.624 | 0.831 | 0.521 | 0.320 | 0.008 | 0.978 | 0.931 | 1.000 | 21.2 s |
> | + metric fixes + answer guard | 0.621 | 0.830 | 0.512 | 0.000 | 0.008 | 0.997 | 0.996 | 1.000 | 19.7 s |
> | **+ lists and named shelters formatted by code (2026-10-29)** | **0.684** | **0.857** | **0.594** | 0.000 | 0.000 | 1.000 | 1.000 | 1.000 | **0.2 s** |
>
> The last row is the [direct answers](chat.md#direct-answers) change: 201 of the 207 questions no longer call the model, p95 latency dropped from 61.5 s to 0.5 s, and a paired bootstrap against the previous run puts every gain outside zero (answer recall +0.050 [+0.021, +0.084], precision +0.028 [+0.005, +0.052], complete +0.076 [+0.035, +0.122], micro). `halluc` and `num_ok` are true by construction for answers written by code (they are scored against the answer's own source data), so the meaningful columns are `ansR` / `ansP` / `full`. What the gains measure is the model no longer dropping or misplacing shelters while copying them: named questions went from 0.954 to 1.000, facility lists from 0.412 to 0.535 complete. Listing recall stays low by design (the overview shows ten of a region's 200).
>
> - **Metric fixes.** `halluc` no longer flags a fragment of a real name (the facility-suffix rule cuts 礁溪鄉公所二樓 at 礁溪鄉公所), and `fmt_ok` no longer flags English letters that are part of a real name (F Hotel). Most of the first run's township `halluc` 0.516 and region 0.667 were these false positives.
> - **Answer guard** (`services/chat/answer_guard.py`). A reply line that names something place-like (ends in 國小, 活動中心, 公所…) that matches none of the shelters sent in the prompt is removed; streaming is buffered one line at a time, so a removed line never reaches the screen. If every line would be removed, the reply is kept as is. In this run it removed 69 lines from 22 of 207 replies. 46 of them were in 9 single-shelter questions, where the 3B model answered the one shelter and then kept going with invented entries such as 「宜蘭社區活動中心：容量 30 人」.
> - **`halluc` 0.000 is not independent evidence.** The guard and the hallucination metric use similar rules, so a large part of the drop is one rule removing what the other rule would have flagged. What it does show is that no remaining line contains an unsupported place-like name; an invented name without a facility suffix passes both.
> - Reviewing the 69 removed lines found 3 wrong removals, fixed after the run and not reflected in the numbers above: the road name 女中路 in an address was read as a school (三聖宮's line), and 台東大學 did not match 國立臺東大學 (臺 / 台). Two near-correct names are still removed: 八桑安社區活動中心 (the data has 八桑安社區社活動中心) and 鳳禮活動中心 (the data has 鳳禮,鳳智里活動中心).
> - Recall did not change (0.624 → 0.621, 95% CI [0.565, 0.679]). Low recall on listing questions is by design: a wide question gets totals plus the ten most relevant shelters instead of all of them (region `ansR` 0.047, see [On the official data](#on-the-official-data-644-shelters-207-questions)).
> - The tables below were measured on the previous 50-shelter data.

Retrieval recall only says whether the right documents reached the model. `evals/run_gen_eval.py` measures what the model finally says: every question goes through the real `ChatService` path (rules layer, retrieval, prompt, Ollama) and the reply is scored by the rules in `evals/lib/gen_metrics.py`. No LLM judge is involved, so the numbers are reproducible and do not depend on a second model.

```bash
docker exec -it disaster_app python evals/run_gen_eval.py --embedder ollama:bge-m3 --model llama3.2:3b --label gen_llama3.2-3b
docker exec -it disaster_app python evals/run_gen_eval.py --embedder ollama:bge-m3 --model qwen2.5:7b --label gen_qwen2.5-7b
docker exec -it disaster_app python evals/run_gen_eval.py --limit 10 --category region   # quick spot check
```

The same 130-question set is used. `evals/lib/build_eval_set.py` attaches an `expect` block to every positive case with the capacity of each relevant shelter and, for "largest / most" questions, the shelter that must come first.

| Metric | Meaning |
|---|---|
| `ansR` answer recall | Share of the relevant shelters that are actually named in the reply (display name or alias) |
| `ansP` answer precision | Share of the shelters named in the reply that are relevant |
| `full` | Reply names every relevant shelter |
| `halluc` | Reply names a shelter that was not in the retrieved context, or a facility-like name that matches no known shelter |
| `f_abst` false abstain | Reply says "no data" although relevant data was retrieved |
| `num_ok` | Every integer ≥ 50 in the reply also appears in the retrieved context (capacity, occupancy, remaining space are not altered or invented) |
| `cap_ok` | Single-shelter questions: the reply states that shelter's capacity |
| `top1` | Ranking questions: the first shelter named is the correct largest one |
| `fmt_ok` | Traditional Chinese only, no English letters, no simplified characters, no emoji |
| `trunc` | Ollama stopped because `num_predict` ran out (the list was cut off) |
| negatives `abstain_correct` | Out-of-scope or unanswerable questions are declined without naming any shelter |

Each run also records latency p50/p95, output tokens and the full reply per question in `evals/results/<label>.json`.

Results so far (macro averages over the eight positive categories; `negatives` is the separate abstention score, `p50` the median end-to-end latency per question):

| Configuration | ansR | ansP | full | halluc | f_abst | num_ok | fmt_ok | trunc | negatives | p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| llama3.2:3b, prompt before this round (baseline) | 0.725 | 0.719 | 0.707 | 0.022 | 0.214 | 0.986 | 1.000 | 0.014 | 0.900 | 9.4 s |
| **llama3.2:3b, current pipeline** | 0.961 | 0.913 | 0.943 | 0.006 | 0.016 | 0.994 | 1.000 | 0.000 | 1.000 | 8.6 s |
| **qwen2.5:7b, current pipeline** | 0.971 | 0.962 | 0.941 | 0.028 | 0.000 | 1.000 | 1.000 | 0.000 | 1.000 | 24.9 s |

The baseline's dominant failure was not hallucination but over-refusal: on 21% of the questions the model answered "no relevant data" even though the retrieved context listed exactly the shelters it had been asked about. Three changes removed it.

- The system prompt now says the shelter block is pre-filtered by the rules layer, so whatever is listed there must be used; refusing is only allowed when the block is empty or the question is not about shelters at all.
- A question that names a place ("near Zhiben", "around Zhonghua Road section 1") goes to retrieval instead of being bounced back with a request for coordinates.
- When a question names a shelter outright, retrieval keeps only that shelter, and the answer format is fixed at one line per shelter. Together these removed truncation on the 3B model (0.044 → 0.000), which used to answer a single-shelter question by listing ten and running out of output budget.

Out-of-scope questions are declined before generation rather than by the model: the rules layer refuses a county outside the coverage area or a question that is not about shelters at all, and a distance gate catches what is left — if plain semantic retrieval's closest document is farther than `RAG_MAX_DISTANCE`, a fixed reply is returned and no generation happens. The claim this paragraph used to make — that unanswerable questions sit at 0.588 and above while the farthest answerable one sits at 0.483 — held only for the two negatives in the original eval set. On a wider set of boundary questions the two classes overlap and no threshold separates them; see [Abstention and Boundary Questions](#abstention-and-boundary-questions).

`capacity_stated` in the baseline row is not comparable with the rows below it: that column now applies only to questions that actually ask about capacity or occupancy, instead of every single-shelter question.

What remains on both models is dropped or mistyped names rather than invented facts: 濤強國小 written as 涙強國小, 中興國小附幼 shortened to 中興國小, 台東縣立體育場 written as 台東縣立體場. That is what most of qwen2.5:7b's `halluc` 0.028 is, and it also explains the recall that is missing from 1.000. Every number either model printed was traceable to the retrieved context, nothing was truncated, and all ten out-of-scope questions were declined. The 3B model's `top1` of 0.667 is one of the three ranking questions, where it named the second-largest shelter first.

## Abstention and Boundary Questions

> **On the official data (644 shelters, 2026-10-29):** 53 of 53 scored questions correct (1.000). On 2026-10-24 it was 52 of 53; the one miss was a named question where llama3.2:3b appended 「目前沒有相關資料」 after a correct answer, and named questions are now answered by code (see [Direct answers](chat.md#direct-answers)). The `llm` layer in this report now means "passed every refusal gate", whether the reply was then written by code or by the model.
>
> - All 34 should-refuse questions are stopped before the LLM: 2 by out-of-area rules, 13 by off-topic, 12 by nonexistent name, 7 by no-clue, 0 by the distance gate. All 12 nonexistent shelters are acknowledged, with none fabricated or substituted.
> - 7 of 8 named and misspelled questions name the right shelter with the right numbers. The one miss is the known llama3.2:3b habit of appending 「目前沒有相關資料」 after a correct answer (「公證國小體育館在哪」).
> - The named-answer judge now also accepts numbers from the shelter's own address. Before, 「中正國小：花蓮縣花蓮市中正路210號」 was marked as a wrong number because of the house number 210.
> - The analysis below was written on the previous 50-shelter data.

The generation evaluation reported abstention as perfect (`negatives: abstain_correct 1.000`), but its
ten negatives were eight out-of-area county names — caught by the rules layer — plus two with no place
name at all. So the entire empirical basis for `RAG_MAX_DISTANCE = 0.56` was **two questions**, and the
most likely real-world irrelevant question (one that mentions a local place) was not represented.

`evals/datasets/boundary_eval.jsonl` (54 questions) probes exactly those gaps, and `run_boundary_eval.py` reports
**which of the five defences decided**, with `--generate` running the real LLM for whatever reaches it:

```bash
docker exec -it disaster_app python evals/run_boundary_eval.py --generate
```

| layer | decides when |
|---|---|
| `rules` | the question names a county outside Yilan / Hualien / Taitung |
| `off_topic` | the question names a non-shelter topic and the sentence mentions no shelter topic |
| `unknown_name` | the question names a facility that is not in the shelter list (see [Nonexistent shelters](#nonexistent-shelters)) |
| `distance` | plain semantic retrieval's nearest document is farther than `RAG_MAX_DISTANCE` |
| `llm` | nothing caught it; the shelters go into the prompt and only the model can still refuse |

**First measurement — the abstention claim did not hold.** Of 17 questions that must be refused, the
retrieval layer caught 4 and 13 reached the model, which refused only 4 of those 13. Asking
「宜蘭的超商在哪」(where are Yilan's convenience stores) returned a list of shelters. Accuracy 14/23.

Two independent causes:

1. Any in-scope place name produces a metadata filter, and **the distance gate is skipped on filtered
   paths by design** ("a filter hit is itself evidence of relevance"). The prompt header then tells the
   model "14 shelters match 花蓮地區", while `SYSTEM_PROMPT` rule 3 says whatever is listed must be used.
   The whole pipeline pushes the model towards answering.
2. The distance signal does not separate the classes anyway: 「花蓮的天氣」 sits at 0.427, *closer* than
   several answerable questions.

**The fix was chosen by measuring two alternatives**, not by picking one:

| | false rejections on 120 positives | boundary refusals caught (of 17) | failure direction |
|---|---|---|---|
| positive gate (require shelter vocabulary) | **16 (13.3%)** | 12 | unsafe — rejects valid questions |
| **negative gate (blocklist AND no shelter word in the sentence)** | **0** | **13** | safe — unlisted words fall through to today's behaviour |

The positive gate's false rejections are the terse location-only queries — 「礁溪」, 「冬山」,
「壯圍有哪些」 — which in a shelter app obviously mean "shelters there". The negative gate needs the
whole sentence, so 「下雨天要去哪個避難所」 (which shelter on a rainy day) is not blocked by 「下雨」.

**Result:** 23/28 on the full 29-question set, with the 130-question retrieval evaluation unchanged —
every positive metric identical, and `negatives` `rejected_by_rules` 8/10 → 10/10.

```
of the 22 that must be refused: rules 2, off_topic 13, distance 2, left to the LLM 5
```

### The distance threshold cannot be calibrated

The five remaining failures are two classes, and the first one is a dead end:

| question | top1 distance | should |
|---|---|---|
| 有沒有可以借錢的地方 (somewhere to borrow money) | 0.505 | refuse |
| **避難所有提供飲水嗎** (do shelters provide water) | **0.510** | **answer** |
| 有沒有人可以幫我顧小孩 (someone to watch my child) | 0.530 | refuse |
| 哪裡可以洗衣服 (where can I do laundry) | 0.556 | refuse |
| 怎麼聯絡里長 (how to contact the village head) | 0.570 | refuse |
| 垃圾車幾點來 (when does the garbage truck come) | 0.588 | refuse |

The classes **overlap**. At 0.56 the gate catches the last two and misses the first three; lowering it to
0.50 would start rejecting a legitimate question. The gap the old README claimed (unanswerable ≥ 0.588,
answerable ≤ 0.483) was an artefact of having two negatives. No threshold on this signal separates
off-topic questions from semantic ones — which is the honest limitation to state, rather than tuning the
number until the small sample looks clean.

The second class is **nonexistent shelters**, covered in the next section.

The three off-topic questions above are now the only scored questions whose outcome depends on the
model, and generation runs at `temperature=0.3`, so the same question can be refused on one run and
answered on the next. Either set the temperature to 0 for reproducible numbers or report an average
over several runs.

**Update — the threshold was raised to 0.70.** Once the `off_topic` and `no_clue` layers
existed, every question that must be refused (34 boundary questions, plus the 10 negatives in
`rag_eval.jsonl`) was stopped before retrieval, and the gate's only remaining effect was refusing two
legitimate questions: 「我們一家五口可以去哪」 (0.583) and 「土石流要撤離到哪」 (0.646). At 0.70 both
are answered from the retrieved shelters, nothing that should be refused reaches the model, and the
boundary set goes from 50/53 to 52/53. The gate stays as a backstop for wildly off-target phrasing.
The one remaining failure is the occasional stray 「目前沒有相關資料」 tail after a correct answer.

### Nonexistent shelters

「花蓮縣立游泳池還有空位嗎」 is a shelter question; it just names a place the data does not contain, so the
off-topic blocklist rightly ignores it. To see how large this gap is, the set gained ten **near-miss
names**, each one character away from a real shelter: 新生高中 next to 新生國中, 頭城國中 next to 頭城國小,
羅東高中 next to 台東高中. For these rows a refusal is not required — recommending a similar shelter is
helpful — but the reply must say the asked place is not in the data:

| verdict | meaning |
|---|---|
| acknowledged | says the place is not in the data (suggesting others afterwards is fine) |
| fabricated | gives the nonexistent place a capacity, a remaining space, an address or a status |
| substituted | answers with a different shelter without saying it is a different one |

**Measured on llama3.2:3b (the default model), over three runs: only 2–4 of the 12 acknowledged.** The
rest looked like ordinary answers:

- 「頭城國中：宜蘭縣頭城鎮，容量 400 人，剩餘空位 400 人」 — 頭城國小's data under a name that does not exist
- 「知本國中：尚有 400 個空位」, 「礁溪國小：沒有空位」 — an invented status for a place that does not exist
- asking for 蘇澳國中 returned 「蘇澳國小：容量 320 人」 with no word about the switch

In an evacuation the first two send people to a building that is not a shelter. The counterfactual
evaluation (`evals/run_faithfulness_eval.py`, which removes the asked shelter from the context) had shown
the same tendency on qwen2.5:7b: 57.8% of its replies silently answered with another shelter. It never
invented numbers; the 3B model does.

**A prompt instruction does not fix it.** Six of the twelve went down the filter-miss path, whose header
already tells the model *"no shelter matches 頭城鎮、國中 … tell the user first that nothing fully
matches"*. Not one of the six complied. So the decision is no longer left to the model:
`query_rules.unknown_shelter_name()` extracts a named facility (2–8 characters plus a facility suffix,
including suffixes the data never has, such as 游泳池 or 美術館), and when it matches nothing in the
index, `build_context` returns a fixed reply before retrieval and before any generation:

```
查無「頭城國中」這個避難收容場所的資料，本系統的避難所名單裡沒有這一間。
名稱相近的避難所：
- 頭城國小：宜蘭縣頭城鎮，容量 400 人，剩餘空位 400 人
如果要找的是其他地方，可以告訴我鄉鎮名稱，例如「頭城鎮有哪些避難所」。
```

The suggestions are the shelters whose names start the same way, with numbers copied from the index
metadata, so nothing in the reply comes from the model. The hard part is not flagging typos:

| question | decision | why |
|---|---|---|
| 頭城國小在哪裡, 宜蘭國民小學 | retrieval | the name or an alias exists |
| 中興國小 | retrieval | the first part of 中興國小附幼 |
| 原民會館 | retrieval | an abbreviation: its characters appear in order in 原住民文化會館 |
| 涙強國小 | retrieval | one character off a real name (濤強國小) → a typo |
| 中正國少, 花蓮縣立體育官 | retrieval | the suffix is not a facility type, so no name is extracted |
| 宜蘭的國小, 花蓮市國小, 大型體育館 | retrieval | asks about a category, not a named place |
| **羅東高中** | **查無** | one character off 台東高中, but 羅東 is itself a place, so it is a different school |
| **新生高中** | **查無** | same start as 新生國中 but a different facility type |

When the shelter list is empty (the index is not built yet) or cannot be read, the check is skipped and
the question goes to retrieval as before.

**Result:** all 12 acknowledged, 0 fabricated, 0 substituted. Boundary accuracy went from 25/38 to
**35/38**; none of the 120 positive questions in `rag_eval.jsonl` is flagged (a test pins this), and the
routing evaluation is 42/42 with `unknown_name` as a new route.

```
of the 32 that must be refused: rules 2, off_topic 13, unknown_name 12, distance 2, left to the LLM 3
```

### Misspelled shelter names

Typos were left to retrieval, and the model was not reliable on them — one run answered 涙強國小 with
250 people, while 濤強國小 holds 300. The boundary judge could not see that, because for questions that
should be answered it only checked "did not refuse". So two things changed.

**The judge now checks the answer itself.** Every question that names a shelter (correctly or with a
typo) carries the real shelter in `boundary_eval.jsonl`, and `judge_named_answer()` requires that the
first shelter named in the reply is that one and that every number ≥ 50 in the reply is its capacity or
remaining space. Eight such questions, six of them typos. With typo handling switched off, **2 of 8**
passed:

- 「澳化國小：宜蘭縣南澳鄉，容量 150 人」 — the right data under the wrong name, so the user never learns
  the shelter is called 澳花國小
- 「馬籃國小：沒有資料」 — the data was there
- 「清講國小可以收幾個人」 — eight schools listed, the user picks

**A one-character typo is now mapped to the shelter before retrieval.** `query_rules.typo_correction()`
returns the real name, and `ChatService` puts it into both retrieval (so the named-shelter path sends only
that shelter) and the prompt (so the model does not copy the typo). The reply starts with a line that code
adds, not the model:

```
（找不到「康落國小」，以下是名稱最接近的「康樂國小」的資料）
康樂國小：台東縣台東市，容量 250 人，剩餘空位 250 人。
```

The conditions are deliberately narrow, because a wrong correction answers a question nobody asked:

| question | result | why |
|---|---|---|
| 涙強國小, 馬籃國小 | → 濤強國小, 馬蘭國小 | first part one character off, and only one shelter is that close |
| 中正國少, 花蓮縣立體育官 | → 中正國小, 花蓮縣立體育館 | only the **last** character differs |
| 中強國小 | unchanged | one character from both 濤強國小 and 中正國小 — guessing could be wrong either way |
| 羅東高中 | 查無 | 羅東 is a place, so this is another school, not a typo of 台東高中 |
| 台中體育館 | out-of-scope reply | must never become 台東體育館 |
| 宜蘭的小型避難所 | unchanged | 「宜蘭的小」 is one character from 宜蘭國小, but not in the last position |

The last row is why only the final character may differ for suffix typos: the first version allowed either
of the last two and rewrote that question. A test now asserts that none of the 130 retrieval questions is
rewritten.

**Result:** named questions **2/8 → 8/8**, boundary accuracy 33/42 → **38/42**, routing 42/42.

Of the four failures left, three are the unlisted off-topic questions above. The fourth is 「中正國少的容量」,
where the model answered 中正國小 correctly and then appended 「目前沒有相關資料」. After the correction the
prompt is identical to the one for 「中正國小的容量」, and five repeats of the typo question produced that
tail once — sampling noise at `temperature=0.3`, not the correction.

## Statistical Uncertainty

With 130 questions and 50 shelters, a 0.03 gap between two configurations is not obviously meaningful.
Both runners now print a 95% bootstrap confidence interval (1000 resamples, fixed seed so the numbers
reproduce) next to every headline metric. `macro` is resampled *within* each category so the category
sizes stay fixed — resampling across all questions would change the category mix and stop being a macro
average.

```
95% CI (bootstrap 1000, seed=20261012; macro resampled within categories)
  macro  recall@3       0.801  [0.768, 0.839]  width 0.071
  micro  recall@3       0.856  [0.802, 0.906]  width 0.104
  micro  recall@10      0.957  [0.933, 0.976]  width 0.043
  micro  mrr            0.996  [0.988, 1.000]  width 0.012
```

So R@3 carries roughly ±0.05 of noise on this set: a 0.03 improvement in R@3 is not a result.

**To compare two runs, do not check whether their intervals overlap** — overlapping intervals do not
mean there is no difference. `evals/lib/compare_runs.py` pairs the two runs question by question and puts the
interval on the *difference*, which also cancels the variance that comes from some questions simply being
harder than others:

```bash
docker exec -it disaster_app python evals/lib/compare_runs.py   evals/results/gen_qwen2.5-7b.json evals/results/gen_llama3.2-3b.json
```

| | A = qwen2.5:7b | B = llama3.2:3b | A − B | 95% CI of A − B | |
|---|---|---|---|---|---|
| answer_recall | 0.970 | 0.955 | +0.015 | [−0.024, +0.054] | indistinguishable |
| answer_precision | 0.956 | 0.900 | **+0.055** | **[+0.007, +0.109]** | **difference** |
| complete | 0.942 | 0.942 | +0.000 | [−0.050, +0.058] | indistinguishable |
| hallucinated | 0.025 | 0.008 | +0.017 | [−0.017, +0.050] | indistinguishable |
| false_abstain | 0.000 | 0.017 | −0.017 | [−0.042, +0.000] | indistinguishable |

On 130 paired questions the only metric that separates the 7B model from the 3B model is answer
precision. Everything else is inside the noise — while the 7B model is 2.9× slower (p50 24.9 s against
8.6 s). That is the honest basis for a model choice, and it is not what the point estimates alone suggest.

The same test says both of this project's own claims do hold:

| Claim | metric | A − B | 95% CI |
|---|---|---|---|
| Prompt changes removed over-refusal | `false_abstain` | −0.200 | [−0.275, −0.125] |
| | `answer_recall` | +0.241 | [+0.160, +0.328] |
| | `hallucinated` | −0.017 | [−0.042, +0.000] — *not* significant |
| Rules layer improves retrieval | `precision@all` | +0.631 | [+0.562, +0.697] |
| | `r_precision` | +0.121 | [+0.081, +0.171] |

Note the third row: the prompt rewrite measurably removed over-refusal but did **not** measurably change
hallucination. A tool that reports "significant" for everything would not be worth running.

## Routing Evaluation

The system's design claim is hybrid routing: distance questions go straight to PostGIS, simulation
follow-ups read the snapshot, and only the rest enter RAG. Neither the retrieval nor the generation
evaluation measured that decision — and of the 130 questions in `rag_eval.jsonl`, only four contain a
geographic keyword and none contain a simulation keyword, so **four of the six routes had no test
coverage at all**, including both of the non-RAG paths that make the routing "hybrid".

`evals/datasets/route_eval.jsonl` is a separate 42-question set labelled with the route each question *should*
take, including the state-dependent case ("請給我疏散建議" must go to `no_simulation_advice` with no
simulation and to `simulation` while one is running). Two routes were added later: `off_topic` and
`unknown_name` (see [Nonexistent shelters](#nonexistent-shelters)), and with the disaster-preparedness passages `knowledge` and `knowledge_mixed` (10 more questions, 56 in total; small place names later added `place` and three more, 59 in total; the runner stubs the knowledge store as well, returning the first passage of the hazard so a mixed question has knowledge hits). The runner stubs the vector store and
the repository, because `metrics.route()` is recorded in `build_context` *before* retrieval or the
database is touched. The stub returns the real shelter list read from the JSON files in
`data_for_refuge/`, since `unknown_name` must be decided against real names. So it needs no Ollama, no
ChromaDB and no PostgreSQL, and it is the one evaluation that can run in CI:

```bash
docker exec -it disaster_app python evals/run_route_eval.py
python evals/run_route_eval.py --min-accuracy 1.0   # what CI runs
```

```
=== routing eval: 42/42 correct (accuracy 1.000) ===

expected route           n  correct     acc
nearest                  4        4   1.000
needs_coords             4        4   1.000
no_simulation_advice     4        4   1.000
off_topic                3        3   1.000
out_of_scope             4        4   1.000
rag                     13       13   1.000
simulation               8        8   1.000
unknown_name             2        2   1.000
```

The first run scored **33/34** and the one failure was a real defect: "離我最近的收容所是哪一間" is a
pure distance question with no place in it, but it was routed to RAG. `_needs_coordinates()` is
subtractive — it strips known words and treats anything left as a place name — and here the leftovers
were "離哪一間", i.e. a preposition and a measure word. The user got a semantic search instead of being
asked for coordinates. Adding those words to the blocklist fixes it, which is exactly the whack-a-mole
this kind of heuristic invites; the comment on `GEO_GENERIC_WORDS` says so, and `test_chat_service.py`
pins the case.

**How to read this number honestly:** the question set was written by the author after reading the
routing code, so 42/42 is a regression harness, not an independent measurement of the router. Its value
is that all eight routes are now covered, the state-dependent case is pinned, and CI fails if the accuracy
drops. An independent routing number needs the same thing the retrieval evaluation needs — questions
written by someone who has not seen the code.

## Knowledge Evaluation

`evals/datasets/knowledge_eval.jsonl` (56 questions) checks the knowledge route end to end:

- **40 positive questions.** Each lists the passage that should be returned and the key facts the answer must contain (「18 小時」, 「趴下、掩護、穩住」, 「往兩側」).
- **8 questions the passages do not cover**: fire escape, school closures, subsidies, insurance, SMS alerts, earthquake prediction.
- **4 mixed questions** that ask for steps and shelters together.
- **4 shelter look-alikes**: 「地震了要去哪裡」 must stay on the shelter route.

```bash
docker exec -it disaster_app python evals/run_knowledge_eval.py                      # routing + retrieval, no LLM
docker exec -it disaster_app python evals/run_knowledge_eval.py --generate --label knowledge_gen_llama3.2-3b
```

Routing and retrieval (bge-m3):

| Set | n | Correct route | Right passage returned | Right passage first | Precision of returned passages |
|---|---|---|---|---|---|
| positive | 40 | 1.000 | 1.000 | 0.975 | 0.912 |
| mixed | 4 | 1.000 | 1.000 | 1.000 | 0.875 |
| shelter look-alikes | 4 | 1.000 | – | – | – |
| not covered | 8 | refused 6 of 8 | | | |

- **The first run caught five routing gaps**, all in the rules, not in retrieval:
  - yes/no questions (「住山坡地颱風前要撤離嗎」, 「地震時可以跑出去嗎」)
  - 「什麼是土石流」
  - 「往哪個方向」
  - 「海水突然退很多」, which never says 海嘯
  
  After they were added, positive coverage went from 0.875 to 1.000.
- **The cost of the yes/no rule.** 「地震可以預測嗎」 now also routes to knowledge and gets the earthquake-preparation passage. Together with 「颱風警報怎麼收到簡訊通知」 that is 2 of 8 uncovered questions answered with a passage that does not answer them. No rule was added for these two, because the questions, the passages and the rules share an author, and tuning rules to this set would only make the numbers circular.
- **Distance does not separate covered from uncovered questions.** The nearest passage is 0.156–0.424 for positives and 0.327–0.461 for uncovered questions, the same overlap seen with shelter retrieval. So `KNOWLEDGE_MAX_DISTANCE = 0.45` only catches the far end. The rules decide what counts as a knowledge question.

Answers (llama3.2:3b, the 44 questions that return a passage):

| Version | Key facts | Numbers in source | Sentences with no source | Stray English | p50 |
|---|---|---|---|---|---|
| model rewrites the passages | 0.909 | 1.000 | 0.181 | 4 answers | 11.3 s |
| **passages verbatim (current)** | 1.000 | 1.000 | 0.003 | 0 | < 1 s |

- **What the rewrite got wrong.**
  - 「往溪流的下游方向撤離」 for a question about which way to evacuate from a debris flow. The source says the high ground on either side of the stream.
  - 「需要立即離開危險區域」 added to intensity 4.
  - The instruction itself echoed back (「將震度7級的定義簡述出來」).
  - English labels such as 「warning：」.
- **How "sentences with no source" is measured.** A sentence counts when fewer than half of its character trigrams appear in the material. This is an approximation for comparing versions.
- **The current numbers are partly true by construction.** A verbatim passage scores full marks on facts and faithfulness. What matters for knowledge-only questions is the retrieval table above, because the user sees exactly the passage that was retrieved. The remaining 0.003 is a mixed answer's 「花蓮市有以下避難所」 heading.
- **Mixed questions still call the model** for the shelter list (about 40 s), and those numbers checked out against the shelter data.
- **The eval set needs an independent author.** It was written by the same person as the passages and the rules, like the other evaluation sets here.

## Simulation Follow-up Evaluation

`evals/run_simulation_eval.py` runs five simulations through the real API (log in, simulate, ask, reset) and asks each the same four follow-ups: the allocation per shelter, how many people were not placed, which shelters are affected, and which were excluded. Scenarios: 花蓮市 earthquake 10 km, 台東市 flood 10 km by car, 南澳 flood 10 km with a red alert inserted as test data, 宜蘭市 earthquake 5 km, 玉里 flood 15 km by car.

```bash
docker exec disaster_app python evals/run_simulation_eval.py --label sim_code
```

Scoring checks each line that names one shelter: its numbers must include that shelter's own value (headcount, capacity, occupancy or free space). Coverage is the share of the expected shelters mentioned. Note sentences that name several shelters with distances (「以下避難所靠近…：甲（約 137 公尺）、乙…」) are not pair-checked, and a shelter mentioned in a sentence that says it is still used is not counted as "said to be excluded".

| | Answered by the model (`sim_llama3.2-3b`) | Answered by code (`sim_code`) |
|---|---|---|
| Allocation: coverage | 0.742 | **1.000** |
| Affected: coverage | 0.522 | **1.000** |
| Answers cut off | 5 | **0** |
| Name–number pairs | 1.000 | 1.000 |
| Unplaced count stated | 5 / 5 | 5 / 5 |
| "Excluded" answered correctly | 1 of 5 roughly | **5 / 5** |
| Invented or out-of-range shelters | 0 | 0 |
| p50 time | 34.9 s (lists 34–108 s) | **0.1 s** |

- **The model run scored only what the model wrote** (`--model-text-only`): the notes appended by code were removed first. The code run scores the whole reply, since the exclusion notes are the answer.
- **What the model got wrong.** In 台東市 it said 「有 26 個避難所被排除」 when none were. In 玉里 it listed 21 shelters that had received people as excluded, all tagged with the same unrelated zone. In 花蓮市 it answered the number of unplaced people instead, missing the 18 shelters not designated for earthquakes. In 宜蘭市 it invented a reason.
- **What this does not measure.** In all five scenarios demand exceeds capacity, so every shelter is filled and its headcount equals its free space. Whether the model answered with the headcount or just copied the capacity could not be told apart (0 distinguishing cases).

## Place-Name Evaluation

`evals/datasets/place_eval.jsonl` has 42 small place names in the three counties: 19 that are village names in the shelter data (知本, 富岡, 都蘭, 東澳…) and 23 landmarks that are not (七星潭, 鯉魚潭, 南方澳, 三仙台, 太魯閣…). Each is labelled with the township it lies in, from geography rather than from the system's place data, so the labels do not come from the thing being tested. Names that are also township names (南澳, 大武), ambiguous ones (大溪, 清水) and places outside the three counties (梨山) are left out. Three phrasings rotate: 「X附近有避難所嗎」「X最近的避難所在哪」「我在X，可以去哪裡避難」.

```bash
docker exec disaster_app python evals/run_place_eval.py --label place_code
```

The script scores what `build_context` returns, without generation: the answer when code answers, or the data handed to the model, in retrieval order. If retrieval puts the wrong area first, the model cannot answer well either.

| | Semantic retrieval (`place_baseline`) | Place names to coordinates (`place_code`) |
|---|---|---|
| First shelter in the right township (hit@1) | 0.667 | **0.929** |
| Share of the first five in the right township | 0.314 | **0.738** |
| Landmarks only, hit@1 | 0.522 | **0.913** |
| Questions sent to the model | 88% | **0%** |

- **The township is a proxy.** 金崙, 加路蘭 and 安通 sit near a township border, and their nearest shelters by road are across it (金峰鄉, 卑南鄉, 富里鄉), which the metric counts as wrong. The same proxy keeps the share of the first five below 1: the fourth and fifth nearest shelters are often in the next township.
- **Two names added by hand.** The first run (hit@1 0.905, 4.8% sent to the model) missed 赤柯山 and 梅花湖. OSM spells the Yuli one 「赤科山」 (its other 「赤柯山」 is in Hsinchu), so 赤柯山 is now a spelling variant of it; 梅花湖's water area has no name in OSM, only the car park, park and treatment plant around it, so it is placed at the average of those three. Both are in `SPELLING_VARIANTS` / `MANUAL_PLACES` in `services/geo/places.py`, with the OSM ids.

## Coordinate check

Routes start where a shelter snaps onto the road network, so a wrong coordinate gives a wrong route. Building the data already drops coordinates outside the county or more than 40 km from the same township's other shelters (10 of 650 rows; 4 corrected from OpenStreetMap, 6 dropped). `evals/run_coordinate_check.py` checks the 644 that remain:

```bash
docker exec disaster_app python evals/run_coordinate_check.py
```

| | Before the fixes | After |
|---|---|---|
| Distance to the nearest road, median / 90th percentile | 19 m / 55 m | 19 m / 55 m |
| More than 200 m from a road | 11 | 10 |
| More than 1 km from a road (cannot snap; costed by straight line) | 2 | 1 |
| More than 5 km from the origin of their own village (582 with a matching village) | 34 | 30 |

- **Four were wrong and are fixed.** 南澳高中 was placed near 澳花, about 15 km south of its address in 南澳; 西林社區活動中心 sat 4.4 km from any road in the mountains, 11 km from 西林; 樟原國民小學 carried the same coordinate as 長濱聯合村辦公處, in another village; 大義社區臨時收容站 was 10 km off. Each matches an OpenStreetMap object at the same address (for 大義 the OSM object is named 大義社區活動中心), so it was moved there and recorded with the OSM id in `data_reference/shelter_coordinate_fixes.json`, which now holds 8 corrections.
- **Four more look wrong but are left as published.** 西林天主堂 (4.6 km from a road), 長濱聯合村辦公處 (the shared coordinate above), 古楓社區活動中心 (25 km from the origin of 古風村) and 金洋國小 (10 km from its village) have no OpenStreetMap object matching both name and address, and the rule from the data build stands: no match, no guess.
- **Most of the rest are real.** The other shelters more than 200 m from a road are on 蘭嶼 and 綠島 or in mountain villages where OpenStreetMap has few roads. Among the 30 far from their village origin, some are labels rather than coordinates: 南山活動中心 sits next to 南山國小 but is filed under 復興村, 33 km away. Large mountain villages also put a correct shelter several kilometres from the village's largest settlement.

## Assignment Evaluation

`evals/run_assignment_eval.py` checks the evacuation assignment three ways. Each of the 41 townships' population-weighted centres is a simulation centre, at radii of 5 and 10 km, for earthquakes and floods (shelters not designated for the disaster are left out): 159 scenarios with shelters. Origins, shelters and the cost of every pair are computed exactly as `/api/simulate_disaster` does (`services/simulation/evacuation.pair_costs`), on foot.

```bash
docker exec disaster_app python evals/run_assignment_eval.py
```

**1. The database and Python solvers agree.** `pgr_maxFlowMinCost` and the pure-Python `min_cost_flow` give the same number placed and the same total time in 159 of 159 scenarios, on foot and by car. The largest scenarios (about 145 villages × 130 shelters) take about 0.2 s in the database and 1.2–1.5 s in Python; the slowest database solve stays under 0.35 s on every run.

**2. Coordinated assignment against everyone heading for the nearest shelter.** The comparison is the nearest-first rule the plan used until 2026-11-02: every village–shelter pair sorted by travel time and filled in that order, so each group goes to the closest shelter that still has room. It stands in for what happens without coordination, and the gap is what coordinating is worth.

| | Capacity fits demand (62) | Demand exceeds capacity (97) |
|---|---|---|
| Scenarios where the number placed differs | 0 | 0 |
| Scenarios where coordination is strictly better | 39 | 72 |
| Extra total time without coordination | mean 3.4%, max 17.8% | mean 2.9%, max 17.5% |
| Minutes per person, nearest-first → coordinated | 40.4 → 39.1 | 30.9 → 29.8 |
| Longest single trip (mean), nearest-first → coordinated | 214.0 → 181.3 min | 124.0 → 106.0 min |

- **The gap grew with village origins.** In the earlier evaluation (before 2026-11-07: 29 township centroids with rounded population as both origins and simulation centres, 114 scenarios) nearest-first was 0.4% slower on average and 6.4% at worst, and matched the optimum in 88 of 114 scenarios. With hundreds of origins competing for the same shelters, it now falls short in 111 of 159 and costs up to 17.8% more (池上鄉 5 km flood: 440 against 362 person-hours; 金峰鄉 10 km flood 17.5%; 池上鄉 10 km flood 16.0%).
- **The longest trip drops by about 15%** (214 → 181 min, 124 → 106 min), although only the total is minimised: without coordination the nearby shelters fill with whoever is closest and the last villages are left the far ones.

**3. Villages against township centroids.** The same evacuees, assigned the same way, once from village origins and once all from their township's population-weighted centre:

| | Township centroids | Villages |
|---|---|---|
| Minutes per person (mean over scenarios) | 67.1 | 33.5 |
| Longest single trip (mean) | 170.9 min | 135.4 min |

- **A centroid makes trips about twice as long on average.** In a spread-out township the population-weighted centre falls between settlements, often in fields or on a slope, and everyone walks from there: 東河鄉 10 km flood 289 → 132 min per person, 卓溪鄉 10 km flood 115 → 22.
- **In 6 scenarios villages are slower.** Five are in the mountain townships 大同鄉 and 金峰鄉, where the centroid happened to lie near the shelters while the villages are far up the valleys (大同鄉 10 km earthquake 65 → 145 min); the sixth is 鳳林鎮 10 km earthquake (38 → 43). That is the model becoming more realistic, not worse.
- **The number placed does not change** (0 of 159): capacity, not origins, decides how many fit.

**4. By car** (`--mode drive --label assignment_eval_drive`: one-way streets, speeds by road class). The pattern holds:

| | Capacity fits demand (62) | Demand exceeds capacity (97) |
|---|---|---|
| Scenarios where the number placed differs | 0 | 0 |
| Scenarios where coordination is strictly better | 42 | 74 |
| Extra total time without coordination | mean 2.8%, max 11.1% | mean 2.8%, max 15.3% |
| Minutes per person, nearest-first → coordinated | 5.8 → 5.6 | 4.4 → 4.2 |
| Longest single trip (mean), nearest-first → coordinated | 25.9 → 23.5 min | 15.4 → 13.5 min |

- **The database and Python still agree** in 159 of 159 scenarios.
- **Largest gaps:** 鹿野鄉 10 km flood (205 against 173 person-hours, 15.3%), 太麻里鄉 5 km earthquake (15.0%), 金峰鄉 10 km earthquake (14.8%).
- **Township centroids overstate driving even more:** 12.2 against 4.8 minutes per person (2.5×), with villages slower in only 2 scenarios. The leg from an origin to the nearest road is charged at walking speed even when driving, but it explains little of the gap: township centres are a median 50 m from a road (6 of 41 over 200 m) against 19 m for village origins (`summary.origin_to_road` in both result files). Most of the gap is that one centre stands in for settlements spread kilometres apart.

**Tests.** The Python solver is checked against brute force on 200 small random cases; the database call is checked for its edge layout, integer costs and the mapping back to pairs, and for falling back to Python when it fails (`tests/simulation/test_min_cost_flow.py`).

## Sensitivity Analysis

The evacuation is a model: the population is the registered one, each village starts from one point, and shelter capacity is taken as listed. `evals/run_sensitivity_eval.py` changes one assumption at a time on the 159 assignment scenarios (on foot) and compares the result with the unchanged run:

```bash
docker exec disaster_app python evals/run_sensitivity_eval.py
```

| Change | Unplaced | Shelters used, overlap with the unchanged run (Jaccard, mean) | Minutes per person | Saved by coordination (mean) |
|---|---|---|---|---|
| None | 46.0% | — | — | 3.1% (helps in 114 scenarios) |
| Evacuees × 0.8 | 36.4% | 0.97 | +4.3% | 3.2% |
| Evacuees × 1.2 | 53.6% | 0.98 | −3.0% | 2.6% |
| Capacity 75% | 58.0% | 0.97 | −4.9% | 2.3% |
| Capacity 50% | 71.3% | 0.94 | −11.9% | 2.0% (up to 22.5%) |
| Origins moved 0.5–1 km, two random seeds | 46.0% | 0.96 | +26% to +35% | 3.0% |

Evacuees × 0.8 and × 1.2 stand for people present who are not registered there, or registered but away; capacity 50% and 75% for damaged or partly unusable shelters; moved origins for people not being at the village's largest settlement. Speed is not varied: on foot every cost scales by the same factor, so the optimal plan cannot change. The shares are over all scenarios together (about 1.0 million evacuees, counted once per scenario). Coordination helps in 114 scenarios here against 111 in the assignment evaluation, which compares totals rounded to 0.1 person-hour.

- **Which shelters are used, and that coordination helps, hold up.** The set of shelters used overlaps the unchanged run by 0.94 or more on average under every change, and coordination still saves 2–3% of total time in 105 to 121 of the 159 scenarios.
- **How many are placed is set by capacity and demand, almost one to one.** Extra evacuees or lost capacity turn into unplaced people; the routing does not absorb them.
- **Travel times depend most on where people start.** Moving every origin 0.5–1 km in a random direction adds 26–35% to the mean time per person, part of it the walk to the nearest road. The minutes in the other sections are best read as orders of magnitude, which matches the township-centroid result above.
- **Tighter capacity shortens the mean trip, but only because the people nearest the shelters take the places.** It is not a faster evacuation: more than half are left unplaced.
