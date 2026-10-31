# Question answering

[← README](../README.md) · English · [繁體中文](zh-TW/chat.md)

## Direct answers

Most shelter questions are answered by code, not by the LLM (`services/chat/direct_answer.py`). Whenever the rules layer has already decided *which* shelters answer the question, the reply is formatted straight from the index metadata:

- a filtered list (region, township, facility, capacity, hazard)
- a capacity ranking
- an overview of a wide match (total, capacity, distribution, the ten most relevant, how to narrow down)
- a named shelter (address, capacity, occupancy, the disasters it is designated for)
- a filter that matched nothing ("no shelter matches; the closest are…")

The same goes for the nearest shelters to a pair of coordinates: road distance, walking and driving time, capacity and hazard warnings are formatted by code from the routing result.

Only plain semantic questions with no condition (「知本附近有避難所嗎」) and simulation questions still go to the model.

- **Nearest shelters were the last to move.** They were still copied by the model until a clean-install run on 2026-10-31: asked from the centre of Hualien City, the model wrote the second shelter (國立花蓮農業職業學校, 0.91 km, capacity 382) as 花蓮高商, the first one's name. The answer guard cannot catch that, because 花蓮高商 is in the data. Formatted by code, the answer takes about a second instead of about 29 s.

- **Why.** These answers are structured data that the model was copying line by line. Copying was slow (listing p50 about 40 s) and lossy: one evaluation reply labelled every Taitung City shelter as 宜蘭, and named questions used to continue with invented 「宜蘭社區活動中心 30 人」 lines. The knowledge route had just shown that returning the source verbatim removes both problems (see [Disaster-Preparedness Knowledge](#disaster-preparedness-knowledge)).
- **Result.** On the 207-question generation set, 201 replies no longer call the model, p50 latency went from 19.7 s to 0.2 s, and answer recall and precision improved significantly (see [Generation Quality Evaluation](evaluation.md#generation-quality-evaluation)). Mixed knowledge questions now need no generation at all: the passage, the shelter list and the sources are all assembled by code.
- **Interface.** `VectorStore.search_result()` returns both the prompt text and the formatted answer; `ChatService` returns the formatted answer as an early reply. `search()` still returns the prompt text for the evaluation scripts.
- **Known weak spot.** The questions that remain with the model are the weakest. 「知本附近有避難所嗎」 retrieves shelters in Hualien's 吉安 instead of Taitung's 知本, because the place name only exists in the embedding, not in the rules.

## Disaster-Preparedness Knowledge

The shelter index answers *where to go*. `data_reference/knowledge/` answers *what to do* and *what an alert means*: 34 passages across typhoon, earthquake, debris flow, tsunami and heavy rain / flooding, covering before / during / after and every alert level (typhoon warnings and intensity, seismic intensity 0–7, debris-flow yellow / red, tsunami information types and wave-height grades, heavy-rain levels, flood alerts).

- **Sources.** Each passage is a short rewrite of an official page: the Central Weather Administration's 颱風百問, seismic-intensity table and 2025 tsunami information rules, the National Fire Agency's 消防防災館, and the Agency of Rural Development and Soil and Water Conservation's debris-flow site. All are published under the Open Government Data License v1 with attribution. Numbers (rain thresholds, hours, wave heights, intensity levels) are copied verbatim; sentences the sources do not support were removed in review. Every passage carries its source URL and retrieval date. The flood-alert passage is the only one based on a news quote of the Water Resources Agency, and is marked unconfirmed.
- **Separate collection.** The passages live in their own ChromaDB collection (`knowledge`), on the same client and embedding model as the shelters. Mixed into the shelter index they would compete with 644 shelters for the top-k slots, and none of the shelter metadata filters apply to them.
- **Routing.** `query_rules.knowledge_request` treats a question as a knowledge question when it names a hazard or an alert *and* asks how or what it means (怎麼辦, 要準備什麼, 是什麼意思, 差在哪, a yes/no 「要撤離嗎」…).
  - Having a hazard word alone is not enough: 「地震了要去哪裡」 and 「土石流要撤離到哪」 still go to the shelters.
  - When the same question also asks for shelters (「颱風要準備什麼，花蓮市有哪些避難所」), both are retrieved and the knowledge side is searched with the how-to clause only.
  - Knowledge questions are checked before the out-of-area rule, because what to do in an earthquake does not depend on the county. Simulation questions still win.
  - Off-topic words other than weather (subsidy, insurance, and now 停班停課) still refuse.
- **Answering: the passage itself, not the model.** A knowledge question is answered with the closest one or two passages verbatim, followed by their sources, without calling the LLM. The first version let llama3.2:3b rewrite the passages under a strict system prompt (no additions, no inference, numbers verbatim). In the knowledge evaluation, 20 of 46 answers still contained sentences with no source, and one turned 「往溪流兩側的高地疏散」 (evacuate to the high ground on either side of the stream) into "evacuate downstream", the opposite direction. The passages are already written as short answers (each title is a question), so the model added only risk. Answers now take under a second instead of about 11 s.
- **Mixed questions.** The passage is sent first by code, and the model only lists the shelters. It sees the shelter data and the shelter half of the question (「花蓮市有哪些避難所」), not the passage, so it cannot rewrite it. The shelter half is also routed on its own, so it behaves exactly as if asked alone. 「地震了在家要怎麼辦，附近收容所還有空位嗎」 gets the passage plus the request for coordinates, instead of a model listing shelters from nothing.
- **Fixed replies.** If no passage is within `KNOWLEDGE_MAX_DISTANCE` (0.45), the reply is a fixed "no data" sentence. If the index failed to build, knowledge questions get a fixed "unavailable" sentence while everything else keeps working.

## English mode

A language toggle in the top bar (EN / 中文, remembered in the browser) switches the interface and the answers to English. This is phase one: **answers** are in English, but **questions** still work best in Chinese, because the rules layer's keyword tables (regions, facilities, capacity conditions, how-to phrases) are Chinese.

- **Shelter names and addresses stay in Chinese.** The official data has no English names; translating or romanizing them would not match the signs on site, and the answer guard would no longer recognise an invented name. Townships use the official English names of the township offices (`services/web/i18n.py`): 吉安鄉 → Ji'an Township.
- **Code-formatted answers** (lists, rankings, overviews, named shelters, refusals) have English templates, so most English answers still take under a second.
- **Disaster-preparedness passages** have English drafts in `data_reference/knowledge/en/`, each marked `reviewed: no`. Until a passage is reviewed, English mode shows the official Chinese text with a note that the translation is pending, because a mistranslated safety instruction is as dangerous as a model rewriting one. Flipping a passage to `reviewed: yes` puts its English version live after the next restart or `/api/sync`.
- **The remaining LLM paths** (simulation follow-ups, plain semantic questions) get an English system prompt that keeps names and addresses in Chinese.
- **Not done yet (phase two):** English keyword tables so English questions get the same filters, recalibrated distance thresholds for cross-language retrieval, an answer guard for English-only invented names, and an English evaluation set.

## Example AI Queries

```
Which shelter in Hualien has the largest capacity?
→ Sorts the database directly and returns the correct answer

Which shelter is closest to latitude 23.99, longitude 121.60?
→ Real geographic distance ordering via PostGIS ST_Distance

Which shelters are affected?
→ Reads the simulation result directly, bypassing RAG

Do the affected shelters still have room?
→ Reads the simulation result directly while a simulation is active; falls back to RAG otherwise

Which shelters are in Yilan? / Elementary schools in Hualien / Places in Taitung that can take a thousand people
→ The rules layer turns the question into metadata filters and returns every matching shelter

Shelters in Kaohsiung?
→ Declined by the rules layer: the system only covers Yilan, Hualien and Taitung

Give me evacuation advice
→ With a simulation active, answers from the affected shelters and population estimate; otherwise asks you to run a simulation first

What should I prepare before a typhoon? / What does a yellow debris-flow alert mean?
→ Answered from the official disaster-preparedness passages, with the sources appended

What should I prepare for a typhoon, and which shelters are in Hualien City?
→ Both: the preparation steps from the passages, then the shelters
```
