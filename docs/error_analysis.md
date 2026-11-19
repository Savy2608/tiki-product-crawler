# Error Analysis – LLM Enrichment v1

Where the LLM output (`claude-haiku-5-5`, prompts `enrich_v1` and `flag_v1`) disagrees with the evaluation labels, grouped by cause, with the fix tried or proposed. Same style as [bug_report.md](bug_report.md). All examples are real rows from `data/results/eval_errors_v1.jsonl`.

## About the labels (read this first)

The 200 evaluation labels were made by **Gemini**, following [labeling_guide.md](labeling_guide.md). A person then checked **50 randomly chosen rows** (seed 42) and corrected **0** of them ([label_review.json](../data/labels/label_review.json)). The reviewer could see Gemini's answers, which makes agreement more likely. So:

- The numbers below measure **agreement between two models, spot-checked by a human**, not accuracy against independent human labels.
- Where the two models disagree, either one can be wrong. Several groups below are cases where the labels, not the model, are debatable.

| Metric (200 rows) | Value |
| --- | --- |
| Category agreement / macro F1 | 73.0% / 0.52 |
| Brand exact match | 92.5% |
| Brand null when the label names one | 7.8% (8 of 103) |
| High-confidence (≥ 0.9) rows that disagree | 9.6% (12 of 125) |
| `spam_or_low_quality` precision / recall | 0.47 / 1.00 (TP 18, FP 20) |
| `counterfeit_or_brand_risk` precision / recall | 0.33 / 1.00 (TP 2, FP 4) |
| `name_description_mismatch` precision / recall | 0.50 / 0.38 (TP 3, FP 3, FN 5) |

There were 81 rows with at least one disagreement: 54 category, 15 brand, 20 spam, 4 counterfeit, 8 mismatch.

---

## 1. Accessories filed under clothing (31 of 54 category errors)

- **Examples:** watches (`142450272` Royal Crown, `107708790` MVMT), belts (`56876244`, `242889281`), wallets (`144250427`, `76105833`), ties (`145236266`, `97770849`), sunglasses (`122560987`), earrings and rings (`112633589`, `32260421`). Labels: `shoes_bags_accessories`. Model: `fashion_women` or `fashion_men`, often with confidence 0.8–0.93.
- **Cause:** `enrich_v1` gives the model only the category *names*. The labeling guide defines `shoes_bags_accessories` as "shoes, bags, wallets, jewelry, watches as fashion, belts, hats", but the model never saw that definition, so it filed accessories by gender.
- **Fix proposed:** `enrich_v2` with a one-line definition per category, copied from the labeling guide, so the model and the labeler use the same rules. This is the single largest gain available: fixing this group alone would raise category agreement from 73% to about 88%.

## 2. Unisex clothing (9 errors)

- **Examples:** `196455942` "áo khoác … unisex nam nữ", `9472195` couple T-shirts, `230365029`, `119363721`. Labels: `fashion_women` (the guide's tie-break rule). Model: `fashion_men`.
- **Cause:** the tie-break rule exists only in the labeling guide. Nothing in the text decides it.
- **Fix proposed:** state the same rule in `enrich_v2`, or add a `unisex` value. These are not product-understanding errors; they would disappear with a shared rule.

## 3. Category decided by use, not by form (8 errors)

- **Examples:** hair dryer `138010291` (label `beauty_personal_care`, model `home_appliances`); car air purifier `74981497` (`automotive_motorbike` vs `home_appliances`); baby bottle warmer `123353368` (`mother_baby` vs `home_appliances`, confidence 0.95); maternity shirt `254903176` (`fashion_women` vs `mother_baby`); collagen drinks `217535079`, `263445174` (`health_medical` vs `beauty_personal_care`); sanitary pads `243060589`; workout leggings `180568083` and an Under Armour sports T-shirt `251788308` (`sports_outdoor` vs fashion).
- **Cause:** both answers are defensible. The fixed list has overlapping categories, and neither prompt nor guide says whether the product type or the use wins.
- **Fix proposed:** one rule in both places ("choose by who uses it and where it is sold on Tiki, for example baby appliances go to `mother_baby`"), then re-label these rows.

## 4. Brand: shop names, product lines and model names (15 errors)

- **Brand only in the description, model returned null:** `263485787` Sakura, `91848566` Vương Miện, `184773980` SAM CLO, `33826324` sennio, `164015304` B Record Plus.
- **Shop or product name taken as the brand:** `43152885` HANMYNGHE (shop), `103751720` "July Dress", `22030494` "Donnut worry", `116345618` KEN BLOCK (a print motif).
- **Partial names:** `74026612` "HGcork Corkor" vs Corkor, `109816492` "Thành Long" vs "Đồ Da Thành Long", `263510078` "Beauty Leaf" (product line) vs "Golden Health" (company).
- **Cause:** the prompt says "brand or manufacturer" but does not separate shop, company and product line. Exact match also counts near-misses such as "Thành Long" as wrong.
- **Fix proposed:** in `enrich_v2`, "the manufacturer, not the shop or the product line", plus a fuzzy match (token overlap) reported next to exact match.

## 5. Spam: every "inbox" and every hashtag block flagged (20 false positives, 0 missed)

- **Examples:** `216434697` "Giá sỉ: Vui lòng Inbox"; `149968843` "Inbox shop lấy mã giảm giá"; `82910642` "INBOX để được tư vấn"; hashtag blocks in `113919437`, `191648625`, `217316079`; repeated phrases in `79665870` and `201515572`.
- **Cause:** `flag_v1` lists "contact details … that push buyers to order outside Tiki" and "unrelated keywords". The model treated "inbox" (which on Tiki is usually the in-platform chat) and any hashtag list as spam. The labels flag only phone numbers, Zalo, Facebook, Instagram or links, and text that is *mostly* keywords.
- **Fix proposed:** `flag_v2` says that chatting with the shop on Tiki is not off-platform, and that hashtags count only when they make up most of the description. Recall is already 1.0, so precision can improve without missing real spam.
- **Labels still open:** for `162149114` and `19746130` (a shop or warehouse address, no phone), and `115827674` and `137013873` ("sỉ inbox"), it is a policy question whether an address alone is spam. The current labels say yes; they were kept as they are.

## 6. Counterfeit: brand names without proof treated as risk (4 false positives)

- **Examples:** `188975976` "hàng QC" read as a counterfeit term; `205945413` "#TOMMY" in a hashtag; `116345618` KEN BLOCK print; `74981497` Michelin car air purifier (Michelin licenses products under its name).
- **True positives found:** `194809450` "miumiu" set for under 100k VND, `109602185` "Iscale Iphone" health scale.
- **Cause:** `flag_v1` asks for "a famous brand name used for a product that the brand does not make". The model also flags cases where it simply cannot see proof of authorization.
- **Fix proposed:** `flag_v2`: flag only explicit copy wording (fake, replica, hàng nhái, like auth, 1:1, F1) or a brand clearly outside its product range; a missing authorization statement is not evidence. With only 6 positives, these numbers have very wide uncertainty.

## 7. Mismatch: small contradictions missed, wording differences flagged (5 missed, 3 false positives)

- **Missed:** `7758708` name 100 ml, description 150 ml; `112957009` "da thật" (leather) in the name, rubber in the description; `24952261` model code H1SO3 vs H1PD1M0; `197314947` SYMBALYON vs SWEEDER; `19746130` "dài dưới gối" vs a 40 cm skort.
- **False positives:** `22613627` men's jeans whose copy-pasted text says "phái đẹp"; `104926377` collar wording; `123927251` a size guide copied from another product.
- **Cause:** `flag_v1` defines a mismatch as "a different product". The labels also count contradicting attributes (size, material, model code), and they do not count copy-paste boilerplate. The prompt and the guide use different definitions.
- **Fix proposed:** align the definition: same product type but a contradicting key attribute counts; generic boilerplate does not.

## 8. Confidence is not calibrated

- 12 of the 125 rows with confidence ≥ 0.9 disagree with the label, for example `123353368` at 0.95 and the ties `145236266`, `97770849` at 0.92–0.93. Most of these belong to groups 1 and 3, where the model is confident but uses a different rule.
- **Fix proposed:** do not use confidence as a filter until it is re-measured after `enrich_v2`.

## 9. Rule check: `price_outlier` flags too much

- **Symptom:** 97 of 1,000 products (about 10%) were flagged by 1.5 × IQR on the raw price.
- **Cause:** prices are strongly right-skewed (a few listings cost 10–100 times the median), so raw IQR bounds flag many normal expensive items.
- **Fix tried:** the same rule on log(price) flags **32** products instead of 97. Not yet adopted: the flagged rows were not labeled, so it is unknown which version finds more real problems.

---

## What these errors say about v1

1. Most category errors are **specification gaps**, not model failures: the model was not told the rules the labels follow.
2. Both flag prompts are **high recall, low precision**, a reasonable default for a review queue, but each false positive costs a reviewer's time.
3. The evaluation itself has limits: one labeling model, a human check that saw the labels, and few positive flags (6–38 per type).

**Next step:** `enrich_v2` and `flag_v2` with the fixes above, scored on the same 200 rows and compared with v1 (stretch item in the [spec](llm_enrichment_spec.md)).
