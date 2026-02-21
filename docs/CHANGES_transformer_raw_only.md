# Planned Changes: Transformer Raw-Only + ReFitdTagger for All Intelligent Tagging

## Summary
- **Product transformer**: Only scrape and store raw metadata. Remove all inferred fields (style, formality, fit, weight) and their inference logic.
- **ReFitdTagger**: Remains the single source for style_identity, formality, fit, silhouette, etc. (no code changes to tagger).
- **Loaders / pipeline / viewer**: Stop passing and displaying the removed fields; viewer shows only `tags_final` (ReFitd canonical tags).

---

## 1. `src/transformers/product_transformer.py`

### Remove from file
- **Classes**: `FormalityInfo`, `WeightInfo`, `StyleTagInfo` (keep `PriceInfo`, `SizeInfo`).
- **ProductMetadata fields**: `fit`, `weight`, `style_tags`, `formality`.
- **ProductTransformer**:
  - All class-level dictionaries: `FORMALITY_LABELS`, `FIT_PATTERNS`, `WEIGHT_PATTERNS`, `STYLE_RULES`, `GARMENT_FORMALITY`, `COLOR_FORMALITY`, `MATERIAL_FORMALITY`, `PATTERN_FORMALITY`, `STRUCTURE_FORMALITY`, `CATEGORY_FORMALITY`.
  - Methods: `_extract_fit()`, `_extract_weight()`, `_infer_style_tags()`, `_infer_formality()`.

### Keep in ProductMetadata (raw only)
- `product_id`, `name`, `brand`, `category`, `subcategory`, `url`, `price`, `description`, `colors`, `sizes`, `materials`, `images`, `scraped_at`.

### Update `transform()`
- Remove calls to `_extract_fit`, `_extract_weight`, `_infer_style_tags`, `_infer_formality`.
- Remove `fit`, `weight`, `style_tags`, `formality` from the `ProductMetadata(...)` constructor call.
- Docstring: state that the transformer only normalizes raw scraped data; intelligent tagging is done by ReFitdTagger.

---

## 2. `src/loaders/supabase_loader.py`

### In `save_product()`
- **Remove parameters**: `fit`, `weight`, `style_tags`, `formality`.
- **Remove from `product_data`**: `"fit"`, `"weight"`, `"style_tags"`, `"formality"`.
- **Docstring**: Remove references to fit, weight, style_tags, formality.

---

## 3. `src/pipeline.py`

### In `_load()` (Supabase save loop)
- Remove construction of `weight_dict`, `style_tags_list`, `formality_dict`.
- Remove from `await self.supabase_loader.save_product(...)`:
  - `fit=product.fit`
  - `weight=weight_dict`
  - `style_tags=style_tags_list`
  - `formality=formality_dict`

---

## 4. `viewer.py`

### Product payload (Supabase transform)
- Remove from the product dict passed to the frontend:
  - `"fit": p.get("fit")`
  - `"weight": p.get("weight")`
  - `"style_tags": p.get("style_tags", [])`
  - `"formality": p.get("formality")`

### Product card (displayProduct)
- Remove the block that builds `styleTags` from `product.style_tags` (lines ~2593–2607). The variable is not used in the template; removing it avoids referencing removed data.
- **Keep**: All `tags_final` UI (ReFitd Canonical Tags section: style_identity, formality, fit, silhouette, length, pattern, context, construction_details, pairing_tags, shoe fields, top_layer_role). No change to tags_final display.

### Optional (no schema/API change)
- Curate inputs for “style_tag”, “fit”, “weight” (and related curated/rejected APIs) can remain for backward compatibility with existing curation tables; they will simply have no product data to show for those fields. Alternatively they could be hidden/removed in a follow-up. For this change set we only stop passing and displaying the removed fields from the product.

---

## 5. Schema / DB

- **No migration to drop columns**: `products.fit`, `products.weight`, `products.style_tags`, `products.formality` stay in the DB. New scrapes will no longer write them; existing rows keep historical data. Dropping columns can be a separate migration if desired.

---

## Files to modify (in order)

1. `src/transformers/product_transformer.py` – remove inference classes/fields/methods/dicts; simplify `transform()`.
2. `src/loaders/supabase_loader.py` – remove fit, weight, style_tags, formality from `save_product`.
3. `src/pipeline.py` – remove passing of fit, weight, style_tags, formality to `save_product`.
4. `viewer.py` – remove fit, weight, style_tags, formality from product payload; remove `styleTags` build block.

---

## Result

- Scraper/transformer: **raw metadata only** (id, name, brand, category, subcategory, url, price, description, colors, sizes, materials, images, scraped_at).
- All intelligent tagging (style_identity, fit, silhouette, formality, etc.): **ReFitdTagger only**, stored in `tags_ai_raw` / `tags_final`.
- Viewer: **only shows tags_final** (ReFitd canonical tags) for style/fit/formality/silhouette; raw fields (description, colors, sizes, composition) unchanged.
