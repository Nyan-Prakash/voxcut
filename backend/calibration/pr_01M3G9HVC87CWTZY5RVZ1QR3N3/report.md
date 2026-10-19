# Frame-judge calibration — pr_01M3G9HVC87CWTZY5RVZ1QR3N3

79 placed events, 15 labelled (5 bad, 10 good).

## Summary

| judge | n | mean | median | <0.45 escalate | <0.7 2nd finalist | bad caught (<0.45) | good kept (>=0.6) | agreement |
|---|---|---|---|---|---|---|---|---|
| stored | 77 | 0.83 | 0.90 | 0 (0%) | 6 (8%) | 0/5 | 10/10 | 0.67 |
| old | 77 | 0.64 | 0.90 | 21 (27%) | 23 (30%) | 3/5 | 7/10 | 0.67 |
| new_v1 | 77 | 0.36 | 0.30 | 42 (55%) | 50 (65%) | 5/5 | 8/10 | 0.87 |
| new_v2 | 77 | 0.44 | 0.60 | 37 (48%) | 41 (53%) | 5/5 | 9/10 | 0.93 |
| new_v3 | 29 | 0.54 | 0.70 | 9 (31%) | 14 (48%) | 4/5 | 10/10 | 0.93 |
| new_v4 | 29 | 0.64 | 0.70 | 6 (21%) | 10 (34%) | 4/5 | 10/10 | 0.93 |

## Labelled events

| idx | label | stored | old | new_v1 | new_v2 | new_v3 | new_v4 | flags (last run) | clip |
|---|---|---|---|---|---|---|---|---|---|
| 0 | good | 0.90 | 0.00 | 0.50 | 0.70 | 0.70 | 0.70 |  | Yada Yada-ing Sex | The Yada Yada | Sein |
| 3 | bad | 0.90 | 0.90 | 0.00 | 0.00 | 0.40 | 0.40 | is_product_or_toy_demo(cap) | Animal Zoo Papo Playset Plus Fun Wildlif |
| 10 | bad | 0.90 | 0.00 | 0.00 | 0.00 | 0.50 | 0.00 | is_title_card_or_text_overlay | Funny Wasp Nest Removal Fails |
| 12 | good | 0.85 | 0.00 | 0.00 | 0.00 | 0.90 | 0.90 |  | bees in slow motion pollinating apple bl |
| 17 | bad | 0.50 | 0.20 | 0.00 | 0.00 | 0.00 | 0.70 |  | Excavator driver Attacked by Angry Wasps |
| 19 | bad | 0.80 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | is_title_card_or_text_overlay | German Cat Original vs Meme Vs Original  |
| 20 | good | 0.90 | 0.90 | 0.90 | 0.80 | 0.90 | 0.90 |  | Evil Chihuahua Growling |
| 21 | good | 0.80 | 0.90 | 0.90 | 0.80 | 0.90 | 0.80 |  | Beverly Hills Chihuahua - Theatrical Rel |
| 22 | good | 0.90 | 0.00 | 0.80 | 0.90 | 0.90 | 0.90 |  | Air Bud - golden retriever plays basketb |
| 23 | good | 0.90 | 0.90 | 0.60 | 0.70 | 0.60 | 0.60 |  | Air Bud - golden retriever plays basketb |
| 25 | good | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 |  | Chui's Evil Chihuahua Face |
| 26 | good | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 |  | My roommate's chihuahua wants to kill me |
| 27 | good | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 |  | Angry Chihuahua |
| 28 | good | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 |  | My roommate's chihuahua wants to kill me |
| 30 | bad | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | is_logo_or_watermark_or_branding | Bed Bugs | National Geographic |

## What the `new_v4` judge saw (chosen windows)

- #0 [good] 0.7 (raw 0.7, rank 0) — Man in red jacket, woman raising hand, man looking down — Recognizable scene from the show, fits the meme intent.
- #1 [?] 0.7 (raw 0.7, rank 1, info:has_small_corner_watermark) — Man in suit, shocked expression, office setting — Strong reaction fits the narration's tone, recognizable scene.
- #2 [?] 0.9 (raw 0.9, rank 0, info:has_subtitles_or_small_text) — Characters with Pikachu, surprised expressions — Shows the exact surprised Pikachu meme moment, matching the narration's intent.
- #3 [bad] 0.4 (raw 0.4, rank 3, cap:is_product_or_toy_demo, info:has_small_corner_watermark) — Hand placing toy animals in a trailer — Shows animals being separated into a trailer, matching the narration about separation.
- #7 [?] 0.8 (raw 0.8, rank 0, info:has_small_corner_watermark) — Beetles on leaves, mongoose on rocks — Shows invasive species (beetles, mongoose) clearly, matching the narration.
- #9 [?] 0.7 (raw 0.7, rank 1, info:has_small_corner_watermark) — Animated characters in a crowd, one speaking — Shows an animated crowd, fitting the 'angry mob' theme. Matches the narration's intent.
- #10 [bad] 0.0 (raw 0.0, rank 2, is_title_card_or_text_overlay, info:has_small_corner_watermark) — Person near door with text overlay 'WASPS AFRAID' — Text overlay is the main content, hard fail.
- #12 [good] 0.9 (raw 0.9, rank 0, info:has_small_corner_watermark) — Bee on apple blossom, close-up — Perfectly matches the narration about bees pollinating.
- #13 [?] 0.7 (raw 0.7, rank 1, info:has_small_corner_watermark) — Person reacting to something on porch — The person is reacting, which fits the narration about getting stung.
- #14 [?] 0.8 (raw 0.8, rank 0, info:has_small_corner_watermark) — Person holding honeycomb, bees on honeycomb — Shows bees on honeycomb, directly related to honey production.
- #17 [bad] 0.7 (raw 0.7, rank 0) — Person running away from something outdoors — Shows a person running, likely reacting to wasps, fitting the narration.
- #19 [bad] 0.0 (raw 0.0, rank 0, is_title_card_or_text_overlay) — Grid with cat, meme, and person images — Title card or text overlay present, hard fail.
- #20 [good] 0.9 (raw 0.9, rank 0) — Chihuahua being held, growling expression — Shows a Chihuahua with a growling expression, matching the narration perfectly.
- #21 [good] 0.8 (raw 0.8, rank 0) — Chihuahuas in colorful headdresses, drumming — Shows Chihuahuas in a cute, theatrical setting, matching the narration about movies.
- #22 [good] 0.9 (raw 0.9, rank 0) — Dog and boy playing basketball, dog scores — Shows the dog playing basketball, which is the literal subject of the narration.
- #23 [good] 0.6 (raw 0.6, rank 3) — Boy sitting with golden retriever and basketball — Shows the dog and basketball, relevant to 'Air Bud' theme.
- #25 [good] 0.9 (raw 0.9, rank 0) — Chihuahua showing teeth, looking aggressive — The chihuahua's aggressive face fits the narration about annoying faces perfectly.
- #26 [good] 0.9 (raw 0.9, rank 0) — Close-up of a chihuahua baring teeth — Perfectly captures the aggressive behavior of the chihuahua, matching the narration.
- #27 [good] 0.9 (raw 0.9, rank 0) — Chihuahua with glowing eyes, showing teeth — Perfectly matches the narration about Chihuahuas being 'small little devils' with an exaggerated, aggressive expression.
- #28 [good] 0.9 (raw 0.9, rank 0) — Close-up of chihuahua baring teeth aggressively — The chihuahua is clearly acting aggressively, matching the narration perfectly.
- #30 [bad] 0.0 (raw 0.0, rank 0, is_logo_or_watermark_or_branding, info:has_small_corner_watermark) — Close-up of a bed bug on fabric — Contains a large National Geographic logo, marking it as branded content.
- #33 [?] 0.5 (raw 0.5, rank 2, info:has_subtitles_or_small_text,has_small_corner_watermark) — People screaming in a dark forest setting. — The reaction of screaming fits the clip_reaction intent, but the giant mosquito bite is not visible.
- #42 [?] 0.8 (raw 0.8, rank 0, info:has_small_corner_watermark) — Hands on keyboard, mannequin head, game screen — The mannequin head adds a humorous element, fitting the 'gamer rage' theme and reaction intent.
- #45 [?] 0.6 (raw 0.6, rank 1, info:has_subtitles_or_small_text,has_small_corner_watermark) — Donkey faces through a window labeled 'Burro'. — Humorous donkey faces fit the reaction intent, matching the narration about separation.
- #48 [?] 0.0 (raw 0.0, rank 0, is_logo_or_watermark_or_branding, info:has_small_corner_watermark) — Crabs on ocean floor, stingray approaches — Contains BBC branding, marking it as documentary content.
- #67 [?] 0.8 (raw 0.8, rank 0, info:has_small_corner_watermark) — Businessman walking, goose approaching on path — The clip shows a goose approaching a businessman, matching the narration's context of unexpected behavior.
- #68 [?] 0.9 (raw 0.9, rank 0, info:has_small_corner_watermark) — Person attacked by duck near 'No Feeding Ducks' sign — Directly shows a person being attacked by a duck, matching the narration.
- #74 [?] 0.5 (raw 0.5, rank 1, info:has_small_corner_watermark) — Character in purple shirt enters a room — Character entering a room fits the 'growing up' theme, but lacks strong reaction.
- #76 [?] 0.3 (raw 0.3, rank 2, info:has_subtitles_or_small_text) — Person pouring drink, text 'So how's Jeremy doing?' — Text suggests a comment-like interaction, but not directly related to narration.
