# Question answering

[← README](../README.md) · English · [繁體中文](zh-TW/chat.md)

## Direct answers

Most shelter questions are answered by code, not by the LLM (`services/chat/direct_answer.py`). Whenever the rules layer has already decided *which* shelters answer the question, the reply is formatted straight from the index metadata:

- a filtered list (region, township, facility, capacity, hazard)
- a capacity ranking
- an overview of a wide match (total, capacity, distribution, the ten most relevant, how to narrow down)
- a named shelter (address, capacity, occupancy, the disasters it is designated for)
- a filter that matched nothing ("no shelter matches; the closest are…")
- a question about the data itself (「最近有沒有新增避難所」, 「資料多久更新」): the dataset and its version date. The data has no date for when each shelter was set up, so the answer says it cannot tell which are new; the model used to answer 「沒有新增避難所。」 on its own
- a question about live news (「颱風最新消息」, 「颱風現在到哪了」, 「蘇花公路路況」): the system has no live news, weather, typhoon tracks or road conditions, so the answer says so and points to the Central Weather Administration and the county emergency operations centers. It is checked before the knowledge route, so 「地震最新消息」 does not get the earthquake guidance. A question that also mentions shelters (「目前還有空位嗎」) or asks about debris-flow alerts (「現在有土石流警戒嗎」) is not caught. The model used to answer 「颱風最新消息」 with a list of unrelated shelters
- current debris-flow alerts (「現在有土石流警戒嗎」): read from the live alerts, see [Avoiding hazard zones and live alerts](simulation-and-routing.md#avoiding-hazard-zones-and-live-alerts). The knowledge route used to answer with what the alert levels mean

The same goes for the nearest shelters to a pair of coordinates: road distance, walking and driving time, capacity and hazard warnings are formatted by code from the routing result.

Simulation follow-ups are answered by code as well (see [Simulation follow-ups](simulation-and-routing.md#simulation-follow-ups)). Small place names (「知本附近」) are turned into coordinates and answered like nearest-shelter questions (see [Small place names](#small-place-names)). Only plain semantic questions with no condition (「有沒有可以帶寵物的避難所」) still go to the model.

- **Nearest shelters were the last to move.** They were still copied by the model until a clean-install run on 2026-10-31: asked from the centre of Hualien City, the model wrote the second shelter (國立花蓮農業職業學校, 0.91 km, capacity 382) as 花蓮高商, the first one's name. The answer guard cannot catch that, because 花蓮高商 is in the data. Formatted by code, the answer takes about a second instead of about 29 s.

- **Why.** These answers are structured data that the model was copying line by line. Copying was slow (listing p50 about 40 s) and lossy: one evaluation reply labelled every Taitung City shelter as 宜蘭, and named questions used to continue with invented 「宜蘭社區活動中心 30 人」 lines. The knowledge route had just shown that returning the source verbatim removes both problems (see [Disaster-Preparedness Knowledge](#disaster-preparedness-knowledge)).
- **Result.** On the 207-question generation set, 201 replies no longer call the model, p50 latency went from 19.7 s to 0.2 s, and answer recall and precision improved significantly (see [Generation Quality Evaluation](evaluation.md#generation-quality-evaluation)). Mixed knowledge questions now need no generation at all: the passage, the shelter list and the sources are all assembled by code.
- **Interface.** `VectorStore.search_result()` returns both the prompt text and the formatted answer; `ChatService` returns the formatted answer as an early reply. `search()` still returns the prompt text for the evaluation scripts.
- **Known weak spot.** The questions that remain with the model are the weakest. 「知本附近有避難所嗎」 used to retrieve shelters in Hualien's 吉安 instead of Taitung's 知本, because the place name only existed in the embedding; small place names are now resolved to coordinates (see [Small place names](#small-place-names)), but a place missing from the place list still falls back to retrieval.

## Small place names

「知本附近有避難所嗎」, 「我在七星潭」, 「三仙台最近的避難所在哪」: these name a place that is not a township, so the rules layer used to pass them to semantic retrieval, which guessed. 「七星潭」 got shelters in Yilan, 「南方澳」 got 南澳鄉 because the names look alike, and only 31% of the first five shelters were in the right township (see [Place-name evaluation](evaluation.md#place-name-evaluation)).

Now a small place name is turned into coordinates and answered like a question with coordinates: the nearest shelters by road, formatted by code (`services/geo/places.py`).

- **Where the names come from.** `data_reference/east_places.json` holds 3,475 OpenStreetMap place points in the three counties: villages, hamlets and tribal settlements, neighbourhoods, bays, lakes, peaks, hot springs and attractions (`tools/build_place_names.py`, © OpenStreetMap contributors, ODbL). Village names from the shelter data fill in what OSM lacks: 「多良村」 becomes 「多良」, located at the average position of that village's shelters. Township and county names are left out; the rules layer already handles them.
- **Only in location phrases.** A name counts only before 附近 / 最近 / 周邊 / 一帶 / 的避難所 or after 我在 / 住在 / 人在. Many two-character place names are ordinary words (和平, 復興, 平台), and matching them anywhere in a sentence would misfire. The longest name wins, so 「七星潭」 is not read as 「七星」.
- **Two-character names inside longer ones.** 「溪口」 is a hamlet in 壽豐, but 「蘭陽溪口附近」 is about the mouth of the Lanyang River. A two-character name that follows another Chinese character counts only if that character is a joining word (去, 到, 在, 問…) or the county or township the place is actually in: 「壽豐溪口」 counts, 「卑南溪口」 and 「南澳農場」 do not. A county prefix is still loose: 「花蓮溪口」 is read as 壽豐's 溪口, which is in 花蓮縣 but not at the river mouth.
- **Same name, different places.** Points within 5 km are one place. A name found in two or three places is answered for each, labelled with its county and township (「花蓮縣豐濱鄉的新社」「宜蘭縣壯圍鄉的新社」); naming the township in the question picks one. A name spread over four or more places (觀景台, 生態池, 清水) is treated as a common word and ignored.
- **Coordinates win.** A question with coordinates still takes the nearest-shelter route.
- **Rebuilding.** Download the Geofabrik extract as for the road network, then run `tools/build_place_names.py` in a throwaway container (the command is in its docstring); it takes about a minute.
- **Added by hand.** Two names the evaluation found missing: 赤柯山 as another spelling of OSM's 「赤科山」, and 梅花湖, whose lake has no name in OSM.
- **A place nobody knows is reported, not guessed.** 「龜山島附近」 names a place that is in no source and is not a township, road, facility or shelter. Semantic retrieval used to answer it with whatever shelters looked similar, elsewhere; now code replies that the place is not known and suggests a township or village name, or coordinates (route `unknown_place`). Only the words right before 附近, 周邊, 一帶 or 最近的 count as the place, so 「最近有沒有新增避難所」 (最近 as "recently") is not read as one, and relative places (我家, 公司) still ask for coordinates.
- **A name that starts a longer one is not that place.** 「我在龜山島」 contains 龜山, a village on the coast facing the island; a known name followed by 島, 山, 溪, 港, 路 and the like is the start of a longer name and is not matched. 村 and 里 do not count, so 「我在龜山里」 still is 龜山.

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
- **The remaining LLM path** (plain semantic questions) get an English system prompt that keeps names and addresses in Chinese.
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
