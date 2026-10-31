# Design decisions

[← README](../README.md) · English · [繁體中文](zh-TW/decisions.md)

Most of what this project does differently came from measuring something and not liking the result. Each section below is one of those moments: the problem, how it showed up, what was decided, and what it cost.

## 1. Let code answer whatever code can answer

**Problem.** Shelter lists were answered by llama3.2:3b copying rows it had been given. Copying was slow (about 40 s for a list) and lossy: one evaluation reply labelled every 台東市 shelter as 宜蘭, and answers about a single shelter kept going with invented lines such as 「宜蘭社區活動中心 30 人」.

**Decision.** When the rules layer has already decided which shelters answer the question (a region, township, facility, capacity condition or a named shelter), the reply is formatted by code straight from the index metadata. The model is only used for what code cannot answer: questions with coordinates, simulation follow-ups and plain semantic questions.

**Result.** On the 207-question generation set, 201 replies no longer call the model. p50 latency went from 19.7 s to 0.2 s and p95 from 61.5 s to 0.5 s, and answer recall rose by 0.050 (95% CI [+0.021, +0.084]), because a list written by code cannot drop or misplace a shelter.

**Cost.** The questions left to the model are the weakest ones. 「知本附近有避難所嗎」 retrieves shelters in 吉安, because the place name only exists in the embedding, not in the rules. → [Direct answers](chat.md#direct-answers)

## 2. Return official guidance verbatim, never rewritten

**Problem.** The first version let the model rewrite the disaster-preparedness passages under a strict system prompt: no additions, no inference, numbers verbatim. In the knowledge evaluation, 20 of 46 answers still contained sentences with no source, and one turned 「往溪流兩側的高地疏散」 (evacuate to the high ground on either side of the stream) into "evacuate downstream", the opposite direction.

**Decision.** A knowledge question is answered with the closest passages verbatim, followed by their sources, without calling the model. In a mixed question (what to do, and where the shelters are), code sends the passage and the model only lists shelters; it never sees the passage, so it cannot rewrite it.

**Result.** Knowledge answers take under a second instead of about 11 s, and 40 of 40 positive questions return the right passage.

**Cost.** Answers cannot be tailored to the wording of the question. The same rule applies to translation: English mode shows the Chinese passage until a translation has been reviewed, because a mistranslated instruction is as dangerous as a rewritten one. → [Disaster-Preparedness Knowledge](chat.md#disaster-preparedness-knowledge)

## 3. Refuse before the model sees the question

**Problem.** The generation evaluation reported abstention as perfect, but its ten negative questions were eight out-of-area county names, which the rules already caught, plus two with no place name at all. The distance threshold for "no relevant data" had effectively been calibrated on two questions. When asked about a shelter that does not exist, the model invented its capacity and address, or answered with a similar-sounding one, even with an explicit warning in the prompt.

**Decision.** A layer of deterministic checks runs before retrieval: counties outside the coverage area, off-topic questions, names that are not in the shelter list (answered with real shelters whose names start the same way), and questions with no shelter-related clue at all. A misspelled name that matches exactly one shelter is corrected first. The distance threshold stays only as a last resort.

**Result.** A dedicated boundary set of 53 questions is answered or refused correctly in all 53 cases. All 34 should-refuse questions are stopped before the model, and the distance threshold decides none of them.

**Cost.** The checks are word lists, and word lists need maintenance; the list of generic words used to decide whether a question names a place is a blacklist, and every missed word is a misroute. The routing evaluation (56 questions) is the guard rail. → [Abstention and boundary questions](evaluation.md#abstention-and-boundary-questions)

## 4. Be suspicious of your own best number

**Problem.** After adding an answer guard (a reply line naming a place that is not in the shelter data is removed), the hallucination rate dropped from 0.320 to 0.000.

**Decision.** That number was not reported as a win. The guard and the hallucination metric use similar rules, so most of the drop is one rule removing what the other would have flagged. The 69 removed lines were reviewed by hand instead.

**Result.** The review found that 46 of the 69 lines came from 9 single-shelter questions, where the model answered and then kept inventing entries, which is a real failure the guard does fix. It also found 3 wrong removals (the road name 女中路 read as a school, 台東大學 not matching 國立臺東大學), fixed after the run. The evaluation write-up says plainly that 0.000 is not independent evidence. → [Generation quality](evaluation.md#generation-quality-evaluation)

**Cost.** An invented name without a facility suffix passes both the guard and the metric.

## 5. Decide the evacuation in one place

**Problem.** The evacuation used to be decided in the browser: crowd points were scattered at random and each picked a shelter. The same parameters gave a different allocation every run, and the AI assistant could only quote the numbers after the animation had finished and written them back.

**Decision.** The backend computes a deterministic plan (every township–shelter pair, fastest first, never over capacity), writes it to the database before responding, and the frontend only replays it.

**Result.** The map, the database and the AI's answer show the same numbers as soon as the simulation returns, and the same parameters always give the same plan.

**Cost.** The greedy rule does not minimise total travel time; that would be a min-cost-flow problem. → [Evacuation assignment](simulation-and-routing.md#evacuation-assignment)

## 6. Roads, not straight lines, and hazards change roads instead of removing them

**Problem.** Straight-line distance misleads in eastern Taiwan. From 太麻里鄉, 北里活動中心 is 2.16 km away in a straight line but 5.44 km by road. Across 112 sample scenarios on the east coast, road distance changed the allocation in 66. Then the routes themselves ran through debris-flow zones.

**Decision.** Distances and routes come from the OpenStreetMap road network (pgRouting), and hazards change the cost of the roads they touch. A red debris-flow alert closes the roads inside its zone. A yellow alert, or a flood simulation, makes the part of a road inside a zone more expensive but never closes it, because the zones cover 564 km of road including the coastal highways, and closing them all would cut the east coast apart. When no safe road exists, the shelter is reported as unreachable instead of falling back to a straight line, because the straight line would cross the closed zone.

**Result.** In a 太麻里 flood simulation, route length inside zones went from 6.18 km to 2.21 km for 3.3 km more driving. A red alert on the four zones north of 南澳 closes the Su-Hua Highway: the 東澳 shelters become unreachable, 437 people are placed instead of 544, and the answer says why.

**What measuring caught.** The first version weighted a whole road segment if any part of it touched a zone. Mountain segments run for kilometres, so one route was reported as 12.87 km inside zones against 0.95 km measured. Only the part inside a zone is weighted now. → [Avoiding hazard zones and live alerts](simulation-and-routing.md#avoiding-hazard-zones-and-live-alerts)

**Cost.** Speeds are fixed per road class with no traffic or congestion, roads are two-way, and the alert feed has not yet been observed with a real alert.

## 7. Tests must not touch what they test

**Problem.** The web page kept hanging after code changes: the server's index rebuild failed with "Collection … does not exist" and the startup sync had to retry. The cause was not in the application. Tests run inside the same container inherited `CHROMA_PATH`, so they built and replaced collections in the live server's vector index while the server was rebuilding it.

**Decision.** The root test configuration forces an in-memory index for every test run, regardless of the environment.

**Result.** The error has not come back, and the test suite got faster (about 30 s to 24 s), since it no longer writes to disk.

**Lesson.** The failure surfaced as a flaky server, not a failing test. It was found by lining up the server log with the times the tests ran.
