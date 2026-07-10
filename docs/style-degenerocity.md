# Style profile: Degenerocity

Research target: the style VOXCUT output should converge on.
Primary specimen: ["School Drills"](https://www.youtube.com/watch?v=XUfJnqCN_o0)
(8.1 min, 15.2M views, uploaded 2022-12; channel: 2.66M subs). Numbers below
are measured from the actual video (scene detection, transcript, heatmap), not
estimated.

## Measured pacing (the numbers that matter)

| Metric | Value | VOXCUT today |
|---|---|---|
| Visual changes | **43.5/min** (354 cuts in 488s) | hyperactive ≈ 27/min |
| Median shot length | **1.06s** | ~2.2s beats |
| Shot mix | 47% under 1s · 45% 1–3s · 8% 3–6s · ~1% over 6s | mostly 2–4s |
| Narration density | **198 words/min**, near-zero dead air | depends on VO |
| Video length | 8.1 min | matches spec target (8–15) |

Key reading: visuals turn over ~1.4× faster than VOXCUT's fastest tier, and
narration is continuous. Many sub-second "cuts" are effects (zoom punches,
pans) inside a clip, not new clips — so the real recipe is ~2s clips PLUS
motion inside each clip.

## Visual formula (from frame sampling across the video)

Sampled frames show, in order: a movie desert scene, school-hallway footage, a
product close-up, an **anime fight**, **SpongeBob** (Squidward holding guns), a
riot scene from a movie, B&W slapstick, a **watermarked stock photo** of a guy
adjusting glasses (the watermark IS part of the joke), and a **photoshopped
school sign** reading "DEGEN OHIO TECH" with a skull emoji.

Rules this implies:
1. **Zero talking heads.** Not one sampled frame is a person speaking to camera.
2. **Literal + exaggerated.** The visual shows the thing being said, but the
   most absurd available version of it (school "safety" → riot footage).
3. **Source diversity per minute:** movie scenes, cartoons/anime, stock
   photos/footage, viral clips, custom-edited gag images. Same-source runs are
   short.
4. **Stock-photo irony**: cheap watermarked stock used deliberately as a gag.
5. **Custom edited images** (photoshopped signs/labels) for punchlines —
   something VOXCUT can't make yet (v2: image-gen or template compositing).
6. **No subtitle captions.** Sampled frames show no burned subtitles; text on
   screen is rare and is itself a gag (labels, signs).

## Audio formula

- Clips play muted under the VO except when the clip's audio IS the joke
  (anime screams, iconic lines).
- Continuous background music under everything (lo-fi/trap/whimsical,
  switching with tone). **VOXCUT gap: no music layer yet.**

## Structure formula

- **Cold open, no intro**: first sentence is already the bit ("On Earth we
  face a multitude of natural enemies… the Fire Nation").
  Heatmap confirms: peak retention at 0–5s.
- Essay of escalating hypotheticals in first person, slangy, hyperbolic,
  constant pop-culture references.
- **Signature outro**: every video ends "in conclusion…" + a deliberately
  unrelated sentence + the same whiteboard meme image. Heatmap shows a
  second retention peak at the outro (455–465s) — the signature works.

## What VOXCUT should change (revised plan — operator decision 2026-07-10)

Operator direction: the ultra-fast cutting and motion effects of the reference
are NOT wanted. Pacing stays fully user-controlled through the existing
cut-density setting (chill / normal / hyperactive) chosen at project setup —
no new faster tier, no automatic pan/zoom variants, no choppiness. What we
adopt from the reference is the *visual taste*, not the tempo.

**Adopt (pending operator confirmation):**
1. **Planner style pack** — encode the visual taste rules into the planner
   prompt: exaggerated-literal visuals (show the most absurd available version
   of the thing being said), high source diversity across a video (movies /
   cartoons+anime / stock / viral clips — avoid three of the same flavor in a
   row), ironic stock footage allowed as a gag. This improves *what* gets
   picked at any density.
2. **Subtitle captions default OFF** — the reference burns no subtitles; keep
   planner-written joke captions/labels only. (Stays toggleable per event.)
3. **Signature outro card** — optional project setting: a closing line + image
   appended at the end ("in conclusion…" pattern). Off by default.

**Explicitly rejected:** `degen` density tier (~1.5s beats), automatic motion
variants / zoom punches. Keep clips steady; respect the chosen density.

**Deferred (v2):** music bed with auto-duck; custom gag-image generation.

## Sources

- Measured: yt-dlp metadata/heatmap, ffmpeg scdet (354 cuts), auto-subs
  transcript (1,611 words) of [School Drills](https://www.youtube.com/watch?v=XUfJnqCN_o0).
- [Degenerocity — Wikitubia](https://youtube.fandom.com/wiki/Degenerocity):
  "short and comedic commentary videos… sarcastic and/or exaggerated…
  small clips from memes, anime, and various media while talking over it";
  team of writers/editors; signature "in conclusion" ending with whiteboard image.
- [Favoree channel review](https://www.favoree.io/channel/degenerocity-664e6accd200e614ef3c33eb):
  "random images, gifs or videos in the background as he's talking."
- [Channel](https://www.youtube.com/@Degenerocity): 2.6M subs, 380M+ total views.
