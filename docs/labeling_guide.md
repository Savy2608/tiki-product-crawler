# Labeling Guide – LLM Evaluation Set

This guide is for the person who fills `data/labels/eval_labels.csv` by hand. The labels are the ground truth that the LLM output is measured against, so they must come from a person reading the listing, never from an LLM.

## Before you start

- Open the CSV in Google Sheets or Excel. It is UTF-8 with a BOM, so Vietnamese text shows correctly.
- There are 200 rows. Fill only the label columns. Do not change `id`, `name`, `price_vnd`, `tiki_url` or `description`.
- The CSV does not show what the model answered. Label from the listing only.
- Read the `name` and the `description`. Open `tiki_url` only when the text is not enough. The product may no longer be on Tiki.
- The description was cut at 2,000 characters, so judge only the text you see, just as the model did.
- Target time: 2–3 hours, about 40–50 seconds per row. When a row is unclear, pick your best answer and write why in `notes`.

## Columns to fill

### `true_category`

Exactly one of these values (copy them as written):

| Value | Use for |
| --- | --- |
| `books` | books, comics, textbooks |
| `phones_tablets` | phones, tablets |
| `computers_accessories` | laptops, PCs, keyboards, mice, printers, storage |
| `electronics_audio_camera` | TVs, speakers, headphones, cameras, smart watches |
| `home_appliances` | rice cookers, fans, air purifiers, kitchen and cleaning machines |
| `home_living` | furniture, kitchenware, bedding, decor, storage boxes |
| `beauty_personal_care` | cosmetics, skin care, hair care, perfume |
| `health_medical` | supplements, medical devices, masks, health scales |
| `mother_baby` | baby clothes, diapers, milk powder, strollers, baby care |
| `toys_games` | toys, puzzles, plush toys, models, board games |
| `fashion_women` | women's clothing and underwear, unisex clothing worn mainly by women |
| `fashion_men` | men's clothing and underwear |
| `shoes_bags_accessories` | shoes, bags, wallets, jewelry, watches as fashion, belts, hats |
| `sports_outdoor` | sports equipment, sportswear sold as sports gear, camping, bicycles |
| `automotive_motorbike` | car and motorbike parts and accessories |
| `food_beverages` | food, drinks, coffee, snacks |
| `stationery_office` | pens, notebooks, office supplies, art supplies |
| `pets` | pet food and pet supplies |
| `other` | only when nothing above fits |

Pick the category a shopper would browse to find the product. Unisex clothing: use the gender the listing mainly shows or names; when it is truly neutral, use `fashion_women` and write "unisex" in `notes`.

### `true_brand`

- The brand or manufacturer named in the text, written as in the listing (for example `Samsung`, `Thiên Long`). Capital letters and extra spaces do not matter.
- Write `none` when the text names no brand. Do not guess from the product type.
- Books: always `none` (the author and publisher are not brands for this field).
- A shop name that is not the product's brand: `none`.
- A licensed character or franchise (Pokemon, Gundam, Disney) without a named manufacturer: `none`, and note the franchise in `notes`.

### Flag columns: `1` = the problem is clearly there, `0` = it is not

| Column | Write `1` when |
| --- | --- |
| `name_description_mismatch` | the description is about a different product than the name (for example the name says a lipstick shade or product type that the description contradicts). A short or generic description is `0`. |
| `counterfeit_or_brand_risk` | the text suggests a copy or unauthorized version of a brand: "replica", "fake", "hàng nhái", "like auth", "rep 1:1", "F1", or a famous brand name used for a product that brand does not make. |
| `spam_or_low_quality` | the text is mostly unrelated keywords or repeated hashtags, filler or meaningless text, or it pushes buyers to order outside Tiki (phone number, Zalo, Facebook, "inbox shop"). |

Every flag column must contain `0` or `1`. Most rows are `0` in all three.

### `notes`

Optional. Write anything that explains a hard choice: "unisex", "brand only in the description", "franchise: Pokemon", "description cut off", and so on. Notes are used for `docs/error_analysis.md`.

## When you are done

Save the file in the same place and format (CSV, UTF-8), then run:

```bash
python -m enrichment.evaluate
```

If a row is incomplete, the command lists the line numbers to fix and writes no metrics.
