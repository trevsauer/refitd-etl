# Full ReFitd Tagging Prompt (GPT Vision)

This is the complete user prompt sent to the vision model when tagging a product. It is built in `src/ai/refitd_tagger.py` by `build_user_prompt()`. The **PRODUCT TO ANALYZE** section is filled with the actual product (brand, title, category, description). The rest is fixed except for category-specific blocks (e.g. footwear gets footwear-specific fields; apparel gets fit/length/silhouette).

---

## PRODUCT TO ANALYZE

**Brand:** {brand or "Unknown"}  
**Title:** {product title}  
**Category:** {category: top_base | top_mid | bottom | outerwear | footwear}  
**Description:** {description or "No description provided"}

---

## ALLOWED TAGS (Use ONLY these)

NOTE: Color and composition/materials are already scraped - DO NOT include them.  
NOTE: Fit IS required for apparel (not footwear) - describes volume/ease only.  
NOTE: Length is optional for apparel (not footwear) - describes garment termination/proportion.  
NOTE: Formality IS required - use the scale below.

### Style Identity (1-2 max, REQUIRED):

classic, elevated-basics, grunge, minimal, normcore, outdoorsy, preppy, punk, rugged, sporty, streetwear, tailoring, utilitarian, vintage, western, workwear

Style Identity reflects DESIGN INTENT, not cleanliness, material weight, or isolated details. Follow these canonical rules:

**minimal** — Use when: reduction is the primary design goal; visual noise is intentionally removed; form, proportion, or surface is the focus. Do NOT use when: heritage references are present; texture or material richness is a focal point; the item is meant to be noticed. Clean ≠ minimal.

**classic** — Use when: design is timeless and era-agnostic; proportions are conservative and balanced; the item would feel appropriate across decades. Do NOT use when: trend exaggeration or novelty drives the design. Default choice when nothing else clearly dominates.

**tailoring** — Use when: garment belongs to a tailored wardrobe context; refinement, structure, or formality is central; item pairs naturally with trousers, jackets, or suits. Do NOT use when: casual function outweighs refinement.

**preppy** — Use when: ivy-inspired, socially conservative casual style; polished, respectable, campus-rooted dressing; restraint and propriety are the aesthetic. Do NOT use when: expressive, playful, or disruptive details appear; cleanliness alone is the only signal.

**workwear** — Use when: heritage labor garments influence the design; durability and construction are visually referenced; function is implied, but not overtly technical. Do NOT use when: design is luxury-driven or purely aesthetic.

**utilitarian** — Use when: function-first design is visibly expressed; utility is clearly signaled through construction; modularity, hardware, or technical logic is present. Do NOT use when: function is hidden; pockets are decorative or symmetrical; material is luxury-focused.

**rugged** — Use when: toughness or durability is visually emphasized; distressed surfaces or outdoor signaling appear; the garment looks built for hard use. Do NOT use when: finish is clean or refined; the item is primarily fashion-styled.

**streetwear** — Use when: youth or subcultural expression drives the design; graphics, logos, or attitude are central; the item is meant to signal identity. Do NOT use when: design is restrained, heritage-driven, or conservative.

**elevated-basics** — Use when: foundational items are refined through fabric or cut; the garment is meant to disappear into outfits; simplicity is intentional, not expressive. Do NOT use when: the piece functions as a focal point; novelty or detailing draws attention.

**normcore** — Use when: deliberate anonymity is the goal; the garment avoids signaling or expression; uniformity and blending in are intentional. Do NOT use when: graphics, patches, or personality are present.

**vintage** — Use when: design intentionally references a past era; retro proportions, details, or styling are central; nostalgia is part of the appeal. Do NOT use when: the piece is simply classic or timeless.

**western** — Use when: western heritage elements are present; cowboy lineage or frontier references are visible; even when executed cleanly or modernly. Do NOT use when: western influence is purely incidental.

**sporty** — Use when: athletic function or training lineage is visible; performance cues influence the design. Do NOT use when: athletic inspiration is purely stylistic.

**outdoorsy** — Use when: nature or outdoor activity is a clear reference; hiking, trail, or utility-outdoor cues appear. Do NOT use when: technical features are subtle or hidden.

**grunge** — Use when: deliberate messiness or anti-polish is central; distressed, raw, or chaotic aesthetics dominate. Do NOT use when: the item is simply casual or worn-in.

**punk** — Use when: rebellion or confrontation is explicit; aggressive detailing or subcultural signaling exists. Do NOT use when: edginess is mild or purely aesthetic.

**utilitarian vs workwear (quick rule):** workwear = heritage labor reference (e.g. chore coat, Carhartt-style). utilitarian = modern function-first design (e.g. tech vest, cargo modularity).

**Final default rule:** If multiple identities seem plausible but none are dominant, use **classic** or a single identity only. More tags ≠ better tags.

---

### [APPAREL ONLY] Fit (exactly 1, REQUIRED - describes volume/ease only):

- **Tops/outerwear:** skinny, slim, regular, relaxed, oversized  
- **Bottoms:** skinny, slim, regular, relaxed, baggy  

Fit describes tightness / ease only, NOT leg shape, tapering, or length.

### [APPAREL ONLY] Length (0-1, REQUIRED for apparel - describes garment termination/proportion):

cropped, regular, long  

Length does not replace silhouette or describe fit/volume. Always include one of: cropped, regular, or long.

---

### Silhouette (exactly 1, REQUIRED for apparel - describes imposed geometry/shape):

- **Bottoms:** straight, tapered, wide  
- **Tops/outerwear:** neutral, relaxed, boxy, structured, tailored, longline  

- Silhouette describes geometry, NOT tightness  
- **neutral** = no imposed shape (tops/outerwear only)  
- **relaxed** = intentionally soft / draped shape (tops/outerwear only)

### Formality (exactly 1, REQUIRED - indicates dress code appropriateness):

athletic, business-casual, casual, formal, smart-casual  

Scale: athletic (1) → casual (2) → smart-casual (3) → business-casual (4) → formal (5)

### Context (1-2, REQUIRED for apparel - when/where the item is worn):

everyday, evening, travel, weekend, work-appropriate  

Always include at least one context tag (e.g. everyday, work-appropriate, weekend) when applicable.

### Construction / Details (0-2, optional):

- **Bottoms:** pleated, flat-front, cargo, drawstring, elastic-waist  
- **Tops/outerwear:** structured-shoulder, dropped-shoulder  

### Pattern (0-1, optional):

check, solid, stripe, textured  

### Pairing & Versatility (0-3, optional scoring tags):

easy-dress-down, easy-dress-up, high-versatility, neutral-base, statement-piece  

---

### [FOOTWEAR ONLY] Footwear-specific (Required for footwear):

- Shoe Type (exactly 1): boots, derbies, dress-shoes, loafers, oxfords, sandals, sneakers  
- Profile / Bulk (exactly 1): chunky, sleek, standard  
- Closure (0-1): lace-up, slip-on, etc.

---

## CONFIDENCE GUIDELINES

IMPORTANT: Prefer including tags with lower confidence rather than omitting them, unless they would be misleading.

| Confidence | Meaning |
|------------|---------|
| 0.85–1.00 | Visually obvious or explicitly stated |
| 0.65–0.84 | Strong inference from images + text |
| 0.45–0.64 | Plausible but uncertain - STILL INCLUDE |
| < 0.45 | Only omit if it would be misleading |

> It's better to include a tag with 0.5 confidence than to omit it entirely.

---

## OUTPUT FORMAT (JSON ONLY)

Return ONLY valid JSON matching this structure. For apparel (non-footwear): always include "length" and "context" (1-2 tags). Omit optional fields only when not applicable (e.g. footwear omit length):

```json
{
  "style_identity": [
    { "tag": "minimal", "confidence": 0.86 }
  ],
  "fit": { "tag": "regular", "confidence": 0.82 },
  "silhouette": { "tag": "relaxed", "confidence": 0.78 },
  "length": { "tag": "regular", "confidence": 0.75 },
  "formality": { "tag": "casual", "confidence": 0.85 },
  "context": [
    { "tag": "everyday", "confidence": 0.82 }
  ],
  "construction_details": [
    { "tag": "flat-front", "confidence": 0.74 }
  ],
  "pattern": { "tag": "solid", "confidence": 0.92 },
  "pairing_tags": [
    { "tag": "neutral-base", "confidence": 0.75 }
  ]
}
```

For FOOTWEAR, also include:
- "shoe_type": { "tag": "sneakers", "confidence": 0.92 }
- "profile": { "tag": "sleek", "confidence": 0.77 }
- "closure": { "tag": "lace-up", "confidence": 0.85 }

And OMIT: fit, silhouette, length, construction_details

---

## EXPLICITLY FORBIDDEN

- ❌ Color or color_family tags (already scraped)
- ❌ Materials or composition tags (already scraped)
- ❌ Fit tags for footwear
- ❌ Length tags for footwear
- ❌ Era / decade tags (70s, 90s, Y2K)
- ❌ Garment archetypes (bomber, chore jacket, parka)
- ❌ Trend language or vibes
- ❌ Free-text descriptors
- ❌ Any tags NOT in the allowed lists above
- ❌ Any text outside the JSON

Now analyze the product and return ONLY the JSON:
