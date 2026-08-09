# Translation grouping blind eval — aggregate report

- Dataset: translation-grouping-v1 (24 articles, 8 per category, real licensed texts, manifest.json)
- Arms: deterministic = production deterministic planner; semantic = blind sub-agent planner (provider-free proxy for an LLM planner)
- Judge: 24 independent blind sub-agents (one per article); sides randomized (seed 20260808), key withheld from judges
- Rubric: translation-grouping-blind-v1 (coherence .35 / boundary_naturalness .30 / granularity .25 / structural_respect .10)

## Results

| bucket | articles | semantic wins | deterministic wins | ties | semantic win rate (Wilson 95%) | mean weighted det vs sem |
|---|---|---|---|---|---|---|
| short-news | 8 | 4 | 4 | 0 | 0.5 [0.215, 0.785] | 4.031 vs 4.106 |
| long-form | 8 | 7 | 1 | 0 | 0.875 [0.529, 0.978] | 3.269 vs 4.487 |
| structural | 8 | 8 | 0 | 0 | 1.0 [0.676, 1.0] | 2.25 vs 4.138 |
| overall | 24 | 19 | 5 | 0 | 0.792 [0.595, 0.908] | 3.183 vs 4.244 |

## Per-article outcomes

- long-form-01 (long-form): semantic_win (det 2.8 vs sem 4.65) — X chunks mechanically by threes, isolating 4, 14, 24, 41, 48 mid-paragraph and splitting 45-48 apart. Y follows topic/paragraph boundaries (1-4, 13-14, 42-45, 46-48) with only the heading singleton at 25.
- long-form-02 (long-form): deterministic_win (det 4.35 vs sem 3.7) — Y splits 26|27 mid-thought inside the Etymology paragraph (26-28 form one topic); X keeps 26-28 together and isolates heading 31 cleanly. X's uniform 3-sentence narrative beats (5-7, 8-10, 17-19) are coherent; Y's uniform pairs fragment more.
- long-form-03 (long-form): semantic_win (det 2.8 vs sem 4.65) — X keeps tight 2-3 sentence topic units (10-12 subduction balance, 31-32 crust-formation pair, 21 heading alone). Y orphans continuations (4, 8, 12, 46), splits the 11-12 surface-balance chain and 31-32 parallel pair, mixes 41-42.
- long-form-04 (long-form): semantic_win (det 3.55 vs sem 4.75) — X isolates topic-shift sentences 15 and 20 and pairs related items (27-28, 29-30, 33-34). Y merges origin sentence 15 into surface geology 13-15, and orphans short sentences 30 and 34, fragmenting natural pairs. Headings 25/38 intact in both.
- long-form-05 (long-form): semantic_win (det 2.35 vs sem 4.65) — Y splits the Omar legend mid-story (36|37-38), merges heading 31 with content, detaches quote intro 59 from quote 60-62, and strands singletons 13,20,28,29. X keeps headings/quotes intact with clean 2-3 sentence topical groups.
- long-form-06 (long-form): semantic_win (det 3.45 vs sem 4.75) — Y splits 6|7 mid-thought (consequence isolated) and 23|24, separating the non-inherited claim from its suntan example; X cuts at topic shifts (4-5, 6-7, 23-26). X's lone sentence-30 group is minor fragmentation; both keep heading 17 intact.
- long-form-07 (long-form): semantic_win (det 3.3 vs sem 5.0) — X uses uniform 2-3-sentence topic groups; single-sentence groups only at headings (15,25,26). Y fragments prose: isolated 12, 24, 33, 49 and back-to-back singletons 44,45; splits name topic (31-33) and linked pair 44-45. Both keep quote 41-43 intact.
- long-form-08 (long-form): semantic_win (det 3.55 vs sem 3.75) — X groups by topic but splits cost discussion (34/35) and hull numbers (41/42), with six one-sentence groups; Y's uniform pairs keep those together but cram unrelated sentences (3-4, 5-6) and orphan 38. Y's boundaries are cleaner overall.
- short-news-01 (short-news): deterministic_win (det 4.1 vs sem 3.45) — X merges sentence 5 (trip's significance) with 6-7 (Catholic demographics), mixing topics and missing the natural pause after 5. Y separates them cleanly; its extra singleton (5) is not back-to-back fragmentation. Date line kept intact by both.
- short-news-02 (short-news): semantic_win (det 3.55 vs sem 4.75) — Y unites announcement, timing, background, and Trump quote (2-5) as one coherent block; X fragments them into consecutive one-sentence groups (3-5), splitting 4 mid-flow. Both keep the Ghalibaf quote (7-9) intact.
- short-news-03 (short-news): semantic_win (det 3.85 vs sem 4.35) — X isolates sentence 5 as a fragment though it continues sentence 4's victim details; Y pairs related sentences cleanly (2-3, 4-5, 6-7). Y's only weak seam is 8-9 mixing confirmation with historical background. Both keep date standalone.
- short-news-04 (short-news): semantic_win (det 4.0 vs sem 4.4) — Both contiguous, identical except sentences 9-11. X splits before 11 (new speaker/topic: PM on long-term economics) — natural boundary; Y's 9-11 mixes panic-buying analysis with the PM quote. X costs one extra singleton group, hurting granularity slightly.
- short-news-05 (short-news): deterministic_win (det 4.75 vs sem 4.05) — X keeps bus-trip narrative 7-9 intact and isolates background sentence 10 at a natural topic shift. Y splits the bus event mid-thought (8|9) and pairs protest sentence 9 with general police background 10.
- short-news-06 (short-news): deterministic_win (det 4.4 vs sem 2.85) — G1-G4 identical. X's G5 crams unrelated sentences 10 (next hearing), 11 (supporting bodies), 12 (ban background) into one incoherent group. Y splits them; each is a complete thought, though three one-sentence groups slightly fragment the tail.
- short-news-07 (short-news): deterministic_win (det 4.15 vs sem 4.0) — X pairs loosely related sentences (6+7 station/jobs, 8+9 strategy/contact). Y's 2-4 is one clean announcement unit with natural splits, but five consecutive singletons (5-9) fragment granularity. Y edges ahead on coherence and boundaries.
- short-news-08 (short-news): semantic_win (det 3.45 vs sem 5.0) — X splits cleanly at the topic shift (deployment 2-5 vs legal analysis 6-9). Y isolates 2 from its details 3-5 and cuts 9 off mid-argument from 6-8, leaving fragmented one-sentence groups.
- structural-01 (structural): semantic_win (det 2.6 vs sem 3.9) — Y keeps CSS code block intact (27-36), respects numbered list items (55-56, 57-58, 59-60), pairs 21-22, joins solution code 89-90; X splits code blocks (23-26, 27-36, 89-90) and mixes list items (55-57).
- structural-02 (structural): semantic_win (det 1.6 vs sem 4.1) — X keeps code blocks intact (G9: 18-42, G14: 51-75, G37: 126-156, G40: 161-191) and notes together (8-10, 118-120). Y mechanically chops CSS blocks into 3-sentence fragments ending mid-declaration (e.g. 18-20 ends 'margin:'), destroying structural integrity.
- structural-03 (structural): semantic_win (det 1.6 vs sem 4.75) — X keeps every code block intact (11-14, 21-26, 31-39, 43-51, 53-63, 70-77, 79-87, 96-101) and splits at headings/lead-ins. Y chops code blocks mid-statement (e.g. 21-23/24-26, 43-45/46-47) and isolates closing fence 14, breaking self-contained structures.
- structural-04 (structural): semantic_win (det 1.9 vs sem 4.1) — X splits code blocks and mid-thought: 10-14 into G7/G8, 42-45 into G21/G22, 46-49, 93-100 into three groups; 71 groups, many singletons. Y keeps code intact (10-14, 42-45, 101-105, 129-134) and merges directive fragments into complete thoughts (93-100, 67-72).
- structural-05 (structural): semantic_win (det 2.35 vs sem 4.75) — X keeps each method directive intact (G7-G17, sentences 8-75) and heading underlines whole (G23, G28, G33). Y fragments them: isolates 'noindex:' (G8, G11...), splits heading dashes (G47-G50), creating many tiny mid-structure groups.
- structural-06 (structural): semantic_win (det 2.35 vs sem 3.75) — X chops heading-underline runs (20-34, 91-104) into meaningless 3-line chunks, splits intro paragraph at 5|6, and has ~20 one-sentence groups. Y keeps full paragraphs (59-62, 63-66) and heading blocks intact; only mildly bloated on underline runs.
- structural-07 (structural): semantic_win (det 2.25 vs sem 4.1) — X uses mechanical 3-sentence windows, splitting mid-sentence (29/30, 125/126, 131/132, 185/186) and shredding heading underlines into dash-only groups (G14-G17). Y keeps headings, code blocks (18-23), and logical sentences intact.
- structural-08 (structural): semantic_win (det 3.35 vs sem 3.65) — X keeps prose thoughts whole (5-7, 33-34, 43-44/45-46, 51-54, 63-65); Y splits parallel rules and consequences (33|34, 53|54, 63|64). Y handles the wrap-fragmented table better (16-18, 19-21 vs X's singletons 16/19/20/21/25). Coherence weight favors X.
