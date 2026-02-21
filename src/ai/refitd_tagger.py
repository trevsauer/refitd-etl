"""
ReFitd Canonical Item Tagger

Implements the ReFitd Item Tagging Specification for structured,
controlled fashion tagging with confidence scores.

This is the SENSOR LAYER - it produces tags with confidence scores.
The POLICY LAYER (tag_policy.py) then decides which tags to accept.

Usage:
    from src.ai import ReFitdTagger

    async with ReFitdTagger() as tagger:
        result = await tagger.tag_product(
            image_url="https://...",
            title="Relaxed Fit Cotton T-Shirt",
            category="top_base",
            description="100% cotton crew neck tee..."
        )
        # Returns AI sensor output with confidence scores
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, Optional, TypedDict

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# Import OpenAI client
try:
    from .openai_client import OpenAIClient, OpenAIConfig

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


# Model and prompt version tracking (update when model or prompt changes significantly)
MODEL_VERSION = "gpt-4o-2024-11-20"  # Update when model changes
PROMPT_VERSION = "v2.1"  # Update when prompt changes significantly


# =============================================================================
# CANONICAL TAG VOCABULARIES (from ReFitd Item Tagging Specification)
# =============================================================================

# Product categories
CATEGORIES = Literal["top_base", "top_mid", "bottom", "outerwear", "footwear"]

# Style Identity (1-2 max, primary retrieval signal)
STYLE_IDENTITY_TAGS = frozenset(
    {
        "minimal",
        "classic",
        "preppy",
        "workwear",
        "streetwear",
        "rugged",
        "tailoring",
        "elevated-basics",
        "normcore",
        "sporty",
        "outdoorsy",
        "western",
        "vintage",
        "grunge",
        "punk",
        "utilitarian",
    }
)

# Fit tags (exactly one required, category-aware)
FIT_TAGS = frozenset(
    {
        "skinny",  # Bottoms only (tops & bottoms per spec, but skinny tops are rare)
        "slim",
        "regular",
        "relaxed",
        "baggy",  # Bottoms only
        "oversized",  # Tops and outerwear only
    }
)

# Fit tags by category for validation
FIT_TAGS_BOTTOM = frozenset({"skinny", "slim", "regular", "relaxed", "baggy"})
FIT_TAGS_UPPER = frozenset({"skinny", "slim", "regular", "relaxed", "oversized"})

# Length tags (optional 0-1, NOT for footwear)
LENGTH_TAGS = frozenset(
    {
        "cropped",
        "regular",
        "long",
    }
)

# Silhouette - category aware (exactly one required)
SILHOUETTE_BOTTOM_TAGS = frozenset(
    {
        "straight",
        "tapered",
        "wide",
    }
)

SILHOUETTE_UPPER_TAGS = frozenset(
    {
        "neutral",  # No imposed shape
        "relaxed",  # Intentionally soft / draped shape
        "boxy",
        "structured",
        "tailored",
        "longline",
    }
)

# Formality (exactly one required, ordered scale 1-5)
FORMALITY_TAGS = frozenset(
    {
        "athletic",  # Level 1
        "casual",  # Level 2
        "smart-casual",  # Level 3
        "business-casual",  # Level 4
        "formal",  # Level 5
    }
)

# Context (0-2 max, optional supporting layer)
CONTEXT_TAGS = frozenset(
    {
        "everyday",
        "work-appropriate",
        "travel",
        "evening",
        "weekend",
    }
)

# Materials - apparel (1-2 max)
MATERIALS_APPAREL_TAGS = frozenset(
    {
        "denim",
        "cotton",
        "wool",
        "linen",
        "leather",
        "synthetic",
        "blend",
    }
)

# Materials - footwear (1-2 max)
MATERIALS_SHOES_TAGS = frozenset(
    {
        "leather",
        "suede",
        "canvas",
        "knit",
        "synthetic",
        "blend",
    }
)

# Construction / Details - category aware (0-2 max)
DETAILS_BOTTOM_TAGS = frozenset(
    {
        "pleated",
        "flat-front",
        "cargo",
        "drawstring",
        "elastic-waist",
    }
)

DETAILS_UPPER_TAGS = frozenset(
    {
        "structured-shoulder",
        "dropped-shoulder",
    }
)

# Color Family (exactly one required)
COLOR_FAMILY_TAGS = frozenset(
    {
        "black",
        "white",
        "grey",
        "navy",
        "brown",
        "beige",
        "olive",
        "blue",
        "green",
        "red",
        "multi",
    }
)

# Pattern (0-1 max)
PATTERN_TAGS = frozenset(
    {
        "solid",
        "stripe",
        "check",
        "textured",
    }
)

# Pairing & Versatility (0-3 max, scoring only)
PAIRING_TAGS = frozenset(
    {
        "neutral-base",
        "statement-piece",
        "easy-dress-up",
        "easy-dress-down",
        "high-versatility",
    }
)

# Shoe-specific tags
SHOE_TYPE_TAGS = frozenset(
    {
        "sneakers",
        "boots",
        "loafers",
        "derbies",
        "oxfords",
        "sandals",
        "dress-shoes",  # Fallback bucket
    }
)

SHOE_PROFILE_TAGS = frozenset(
    {
        "sleek",
        "standard",
        "chunky",
    }
)

SHOE_CLOSURE_TAGS = frozenset(
    {
        "lace-up",
        "slip-on",
        "buckle",
    }
)


# =============================================================================
# TYPE DEFINITIONS
# =============================================================================


class TagWithConfidence(TypedDict):
    """Single tag with confidence; reasoning optional (for debugging/fine-tuning)."""
    tag: str
    confidence: float
    reasoning: NotRequired[str]  # 1-2 sentence explanation of WHY this tag was chosen


class AITagOutput(TypedDict, total=False):
    """Sensor layer output with confidence scores.

    Note: Color and materials/composition are scraped directly, not AI-generated.
    Fit, Length, Formality are now AI-generated.
    """

    # Apparel fields
    style_identity: list[TagWithConfidence]
    fit: TagWithConfidence  # Exactly 1, category-aware (not for footwear)
    silhouette: TagWithConfidence
    length: TagWithConfidence  # Optional 0-1, not for footwear
    formality: TagWithConfidence  # AI-generated formality
    context: list[TagWithConfidence]
    construction_details: list[TagWithConfidence]
    pattern: TagWithConfidence
    pairing_tags: list[TagWithConfidence]

    # Shoe-specific fields
    shoe_type: TagWithConfidence
    profile: TagWithConfidence
    closure: TagWithConfidence


# =============================================================================
# SYSTEM PROMPT (Canonical)
# =============================================================================

SYSTEM_PROMPT = """You are a fashion item tagging system for ReFitd.

Your task is to analyze real retail fashion products using:
• product images
• product title
• product category
• product description

and return structured, controlled tags that follow the ReFitd Item Tagging Specification exactly.

You are not generating opinions, recommendations, outfits, marketing copy, or explanations.
You are producing machine-readable tags for a deterministic outfit generation engine.

NOTE: Color and composition/materials are already scraped from the retailer, so you do NOT need to tag those.
NOTE: Color does NOT affect style_identity, fit, silhouette, length, or formality. Base these only on shape, construction, and context; ignore color when choosing these tags.
NOTE: Fit IS required for apparel (not footwear) - describes volume/ease only.
NOTE: Length is optional for apparel (not footwear) - describes garment termination/proportion.
NOTE: Formality IS required - use the formality scale to indicate the dress code appropriateness.

IMPORTANT: If something is uncertain, prefer including the tag with lower confidence rather than omitting it, unless it would be misleading. It's better to provide a tag with 0.5-0.7 confidence than to omit it entirely.

Never invent tags outside the allowed vocabulary. Never exceed tag count limits."""


def build_user_prompt(
    category: str,
    title: str,
    description: str = "",
    brand: str = "",
) -> str:
    """Build the user prompt for tagging a product (FINAL COMPLETE REFITD TAGGING PROMPT)."""
    return f"""## PRODUCT TO ANALYZE

**Brand:** {brand or "Unknown"}
**Title:** {title}
**Category:** {category}
**Description:** {description or "No description provided"}

---

## CORE RULES (non-negotiable)

1. **Only tags from the allowed lists** in this prompt — never invent or paraphrase.
2. **Every tag object** must include `tag`, `confidence`, and `reasoning` (1–2 sentences).
3. **Return valid JSON only** — no markdown, no code fences, no text before or after.
4. **Color and materials** are already scraped — do not include them.
5. **Required tags** for the product category must always be present (see REQUIRED TAGS below).

---

## CORE PRINCIPLES

1. **Be conservative with style_identity** — Only use specialty tags when clear aesthetic markers present
2. **Be comprehensive with structure tags** — Always tag fit, silhouette, formality, length
3. **Be generous with context & pairing** — Most items should get 1-2 of each
4. **Use exact tag strings** — No variations or alternatives
5. **Return only JSON** — No explanations, preambles, or markdown

**IMPORTANT:** Color and materials are already scraped separately. DO NOT include them.

---

## REQUIRED TAGS (Must Always Be Present)

### For APPAREL (top_base, top_mid, bottom, outerwear):
- **style_identity** (1-2 tags)
- **fit** (exactly 1)
- **silhouette** (exactly 1)
- **formality** (exactly 1)
- **length** (1 tag) - REQUIRED, default to `regular` if unclear

### For FOOTWEAR:
- **shoe_type** (exactly 1)
- **profile** (exactly 1)
- **formality** (exactly 1)

---

## STRONGLY RECOMMENDED TAGS

These should be tagged on MOST items:

- **context** (1-2 tags) - Most items should have at least ONE
- **pairing_tags** (1-2 tags) - Most items should have at least ONE
- **pattern** (0-1) - Tag if pattern is distinct

---

## OPTIONAL TAGS (Only When Clear)

- **construction_details** (0-2) - Only for structural features
- **closure** (0-1, footwear only) - Only if clearly visible

---

## STYLE IDENTITY (1-2 max, REQUIRED)

**Allowed:** Use only the tags listed here.
minimal, classic, preppy, workwear, streetwear, rugged, tailoring, elevated-basics, normcore, sporty, outdoorsy, western, vintage, grunge, punk, utilitarian

### Decision Tree (Use This When Uncertain):

1. **Does it have explicit aesthetic markers?** (western yoke, punk studs, preppy madras) → Use that specific tag
2. **Is it workwear/heritage labor or modern utility?** → workwear vs utilitarian
3. **Is it deliberately minimal/designed?** → minimal (rare!)
4. **Is it high-quality but simple?** → elevated-basics
5. **Is it timeless and unremarkable?** → **classic** (your default)

**When in doubt: classic**

---

### **classic** (Your Safe Default)
Use when: Traditional styling with balanced proportions; would work in any decade; no strong aesthetic signals toward other categories

Examples:
- ✅ Oxford button-down shirt (solid or subtle pattern)
- ✅ Plain crew neck sweater
- ✅ Standard chinos
- ✅ Basic denim jacket (no distressing, no oversized fit)
- ✅ Plain tee with small logo

**This is your DEFAULT when nothing else clearly dominates. MOST items should be classic.**

---

### **elevated-basics**
Use when: Premium materials, refined construction, or thoughtful details on otherwise simple designs; high-quality staple, not a statement

Key indicators:
- High-quality fabric (merino, cashmere, premium cotton)
- Clean construction with subtle refinement
- Minimal branding but clear attention to quality
- "Quiet luxury" aesthetic

Examples:
- ✅ High-quality plain tee with perfect drape
- ✅ Premium knit sweater, no decoration
- ✅ Well-cut basic chinos in luxury fabric
- ❌ Plain tee in standard cotton (that's classic)

**Ask: "Is this a BETTER version of a basic, or does it have an aesthetic?"**

---

### **minimal**
Use when: Intentional design reduction is THE CONCEPT; visual noise is actively removed; form, proportion, or negative space is the focus; the simplicity is DELIBERATE and MODERN

Key indicators:
- Modernist aesthetic (not timeless - specifically contemporary minimal)
- No visible branding, labels, or decorative elements
- Unusual proportions or cuts that emphasize form
- Monochromatic or extremely limited color palette
- Japanese/Scandinavian design influence

Examples:
- ✅ Unbranded tee with dropped shoulders and boxy cut
- ✅ Architectural coat with clean lines, no hardware
- ✅ Pants with invisible pockets, seamless construction
- ❌ Plain polo shirt (that's classic)
- ❌ Basic crew sweater (that's elevated-basics or classic)

**Critical test: Does this look DESIGNED to be minimal, or is it just plain?**

**Clean ≠ minimal. Minimal is a deliberate aesthetic choice, not the absence of decoration.**

**USE SPARINGLY - Most "plain" items are classic or elevated-basics, NOT minimal.**

---

### **streetwear**
Use when: Youth/subcultural expression where graphics/attitude are THE FOCAL POINT

**CRITICAL - Graphics/Logo Size Rules:**
- ✅ Large/bold graphics covering chest/back → streetwear
- ✅ Oversized brand logo as main design element → streetwear
- ✅ All-over print/pattern with attitude → streetwear
- ❌ Small embroidered logo on chest → classic or elevated-basics
- ❌ Subtle brand text on sleeve → classic or elevated-basics
- ❌ Small graphic on back → classic
- ❌ "Embroidered text" or "contrasting embroidery" → classic

**Key test: Would you buy this BECAUSE of the graphic, or despite it?**

Examples:
- ✅ Large graphic print across chest/back
- ✅ Oversized logo hoodie as main design
- ✅ Bold all-over print
- ❌ Tee with small embroidered text (classic)
- ❌ Shirt with subtle logo (elevated-basics or classic)

**Size and prominence matter. Small graphics/logos/text → NOT streetwear** (use classic or elevated-basics)

---

### **sporty**
Use when: Athletic/training lineage is STRUCTURALLY visible in the design; performance features are PART OF THE DESIGN

**CRITICAL - Not Just Fabric or Elasticity:**
- ✅ Performance details (mesh panels, athletic cuts, sport-specific features) → sporty
- ✅ Visible athletic construction (raglan sleeves, athletic fit designed for movement) → sporty
- ❌ Technical fabric alone → classic or elevated-basics
- ❌ "Technical interlock cotton" → NOT sporty by itself
- ❌ "Fabric with elasticity" → NOT sporty by itself
- ❌ "Performance fabric" in description but looks like regular tee → classic

**Key test: Could this be worn for actual athletic activity BY DESIGN (not just comfort)?**

Examples:
- ✅ Performance tee with mesh panels and athletic cut
- ✅ Athletic shorts with sport-specific design
- ✅ Training jacket with performance features
- ❌ Plain tee in technical fabric (classic or elevated-basics)
- ❌ Sweatshirt with "elasticity" mentioned (classic)
- ❌ Regular tee that happens to be comfortable (classic)

**Technical fabric alone ≠ sporty. Sporty requires VISIBLE athletic design elements.**

---

### **workwear**
Use when: Design references AMERICAN/EUROPEAN heritage labor garments; construction and durability are visually emphasized; traditional workwear details present

Key indicators:
- Chore coat / barn coat styling
- Heavy-duty fabrics (duck canvas, heavy denim, moleskin)
- Patch pockets, tool pockets
- Visible stitching, bar tacks, reinforced stress points
- Triple-stitch details
- Heritage brand aesthetic (Carhartt, vintage Levi's)

Examples:
- ✅ Denim chore jacket with patch pockets
- ✅ Duck canvas work pants
- ✅ Hickory stripe shirt
- ❌ Generic cargo pants (that's utilitarian if modern, or classic)
- ❌ Tech jacket with pockets (that's utilitarian)

**Distinction: workwear = HERITAGE labor; utilitarian = MODERN function**

---

### **utilitarian**
Use when: Contemporary function-first design; utility is ACTIVELY SIGNALED through visible features; modern technical or tactical aesthetic

Key indicators:
- Multiple visible pockets with clear function (cargo systems, tech vests)
- Modular elements (removable components, convertible features)
- Modern hardware (D-rings, carabiners, webbing straps)
- Technical materials shown prominently
- Techwear aesthetic

Examples:
- ✅ Cargo pants with multiple pocket systems
- ✅ Technical vest with modular storage
- ✅ Jacket with visible webbing/straps
- ❌ Pants with simple side pockets (classic)
- ❌ Jacket with hidden inside pockets (function isn't visible)

**Critical test: Is the utility PART OF THE DESIGN and VISIBLE, or just practical?**

**Hidden function ≠ utilitarian. The utility must be visible and intentional.**

---

### **tailoring**
Use when: Garment belongs to TAILORED wardrobe (suits, sport coats, dress trousers); refinement, structure, and formal construction are central

Key indicators:
- Structured shoulders, canvas interlining
- Tailored darts, waist suppression
- Formal fabrics (worsted wool, tropical wool, gabardine)
- Suit jacket construction details
- Dress trouser details (crease, cuffs, suspender buttons)

Examples:
- ✅ Blazers, sport coats, suit jackets
- ✅ Dress trousers with formal construction
- ✅ Formal overcoats (topcoats, Chesterfields)
- ❌ Casual shirt even if fitted (classic or preppy)
- ❌ Structured casual jacket (might be workwear or classic)

**Only use for FORMAL tailored garments, not just "fitted" or "structured" items.**

---

### **preppy**
Use when: IVY LEAGUE / COLLEGIATE aesthetic; polished conservative casual; East Coast American trad style; "old money" casual

Key indicators:
- Oxford cloth, madras, seersucker fabrics
- Repp stripe ties, grosgrain ribbon
- Nautical references (navy blazers, boat shoes)
- Traditional American sportswear
- Brooks Brothers / Ralph Lauren aesthetic
- Specific patterns: argyle, fair isle, repp stripes
- Polo styling, tennis whites

Examples:
- ✅ Oxford button-down shirt (especially OCBD style)
- ✅ Cable knit sweater with traditional styling
- ✅ Madras shorts
- ✅ Boat shoes, penny loafers
- ❌ Generic polo shirt (usually classic)
- ❌ Plain chinos (classic unless clearly preppy styled)

**Critical test: Could you wear this at a New England yacht club or prep school?**

**Clean and polished ≠ preppy. Preppy has SPECIFIC Ivy/trad aesthetic references.**

---

### **rugged**
Use when: Item LOOKS built for hard use; outdoor durability signals; distressed/worn aesthetic; tough/masculine styling

Key indicators:
- Deliberately distressed or worn finish
- Heavy-weight fabrics that look durable
- Outdoor/adventure aesthetic
- Oil-finish leather, waxed canvas
- Visible reinforcement beyond normal construction
- "Beat up" or "broken in" appearance

Examples:
- ✅ Distressed leather jacket
- ✅ Waxed canvas jacket with weathered look
- ✅ Heavy denim with fading/wear patterns
- ✅ Boots with deliberately rough leather
- ❌ Clean workwear jacket (that's workwear, not rugged)
- ❌ New-looking outdoor jacket (that's outdoorsy, not rugged)

**Ask: Does this look like it's BEEN THROUGH SOMETHING or READY for hard use?**

---

### **outdoorsy**
Use when: HIKING / TRAIL / NATURE-BASED outdoor aesthetic; outdoor recreation is the clear design reference

Key indicators:
- Hiking boot styling
- Trail/outdoor-specific features (not just weather protection)
- Earth tones, nature-inspired colors
- Patagonia / Arc'teryx outdoor brand aesthetic
- Technical features for hiking/camping (not urban tech)

Examples:
- ✅ Hiking boots
- ✅ Fleece jackets with outdoor styling
- ✅ Trail pants with outdoor-specific features
- ❌ Generic parka (classic)
- ❌ Water-resistant jacket (utilitarian or classic)

**Don't confuse "outdoor" (nature activities) with "outerwear" (just jackets).**

---

### **western**
Use when: EXPLICIT cowboy / American West / rodeo references; frontier heritage styling even when modernized

Key indicators:
- Western shirt styling (snap buttons, pointed yokes, smile pockets)
- Cowboy boots, western boot cut
- Denim with western detailing
- Fringe, conchos, western stitching
- Bolo ties, belt buckles
- Ranch/rodeo aesthetic

Examples:
- ✅ Western shirt with snap buttons and yokes
- ✅ Cowboy boots
- ✅ Jeans with western-style stitching
- ❌ Regular denim jacket (workwear or classic)
- ❌ Boots that aren't cowboy-styled (classic or rugged)

**Western references must be EXPLICIT. Don't use for general Americana.**

---

### **vintage**
Use when: Design INTENTIONALLY references a specific past decade; retro styling is the PRIMARY aesthetic; nostalgia is the point

Key indicators:
- Clear decade reference (50s, 60s, 70s, 80s, 90s)
- Retro proportions or silhouettes
- Period-specific details (wide lapels, bell bottoms, etc.)
- Deliberately old-fashioned styling
- "Throwback" aesthetic

Examples:
- ✅ 70s-style wide lapel jacket
- ✅ 90s baggy jeans (if intentionally retro)
- ✅ 50s-inspired bowling shirt
- ✅ Retro track jacket
- ❌ Timeless design (that's classic, not vintage)
- ❌ Distressed/worn item (that's rugged, unless styling is retro)

**Vintage = retro DESIGN, not just old or worn. Classic items aren't vintage.**

---

### **normcore** (Extremely Rare)
Use when: INTENTIONALLY bland/generic; anti-fashion as a deliberate choice; "dad core" / deliberately uncool aesthetic

Examples:
- ✅ Generic fleece pullover
- ✅ "Dad jeans" with high rise, relaxed fit
- ❌ Simple elevated basics (that's elevated-basics)
- ❌ Clean minimal design (that's minimal or classic)

**Extremely rare tag. Only use when item is DELIBERATELY generic/unstylish.**
**Most "plain" items are classic or elevated-basics, not normcore.**

---

### **grunge** (Extremely Rare)
Use when: 1990s Seattle-inspired aesthetic; deliberately unkempt; anti-fashion/anti-establishment styling

**Very rarely used. Most distressed items are vintage or rugged, not grunge.**

---

### **punk** (Extremely Rare)
Use when: Punk rock aesthetic; studs, chains, aggressive styling; confrontational details

**Only use when item has explicit punk references. Most "edgy" items are streetwear or classic, not punk.**

---

## STYLE IDENTITY RULES:

- Max 2 tags total
- Prefer 1 unless item clearly bridges two aesthetics
- **When multiple seem possible but none dominant → use classic only**
- **Small graphics/logos/text → NOT streetwear** (use classic or elevated-basics)
- **Technical fabric alone → NOT sporty** (use classic or elevated-basics)
- **MOST items should be classic** - the specialty tags are for items with clear aesthetic markers
- **Use minimal, normcore, grunge, punk VERY rarely** - they're for specific aesthetics only

---

## FIT (exactly 1, REQUIRED for apparel)

Describes **tightness/ease only**, NOT shape or taper. Use only the tags listed below.

**Tops/outerwear:**
skinny, slim, regular, relaxed, oversized

**Bottoms:**
skinny, slim, regular, relaxed, baggy

**When unclear:** default to `regular` or `relaxed`

---

## SILHOUETTE (exactly 1, REQUIRED for apparel)

Describes **imposed geometry/shape**, NOT tightness. Use only the tags listed below.

**Bottoms:**
straight, tapered, wide

**Tops/outerwear:**
neutral, relaxed, boxy, structured, tailored, longline

**Key distinctions:**
- `neutral` → No imposed shape (tops/outerwear only)
- `relaxed` → Intentionally soft/draped (tops/outerwear only)
- `boxy` → Square, drop-shoulder, minimal shape
- `structured` → Shoulder definition, holds form
- `tailored` → Precise shaping, darts, waist suppression
- `longline` → Extended length below typical hem

---

## LENGTH (exactly 1, REQUIRED for apparel)

**Allowed (use only these):** cropped, regular, long

**IMPORTANT:** Always tag length. Default to `regular` if uncertain.

**Visual cues to look for:**

### **Bottoms:**
- `cropped`: Hem at/above ankle (visible ankle gap when standing)
- `regular`: Hem at top of shoe (no stacking, no ankle showing) - **DEFAULT**
- `long`: Fabric stacking/bunching at ankles (excess length visible, scrunch at bottom)

### **Tops:**
- `cropped`: Hem above waist (midriff area visible or nearly visible)
- `regular`: Hem at/slightly below waist (standard length) - **DEFAULT**
- `long`: Hem at mid-thigh or lower (longline/tunic styling)

### **Outerwear:**
- `cropped`: Hem at/above waist (shorter than standard jacket)
- `regular`: Hem at hip (standard jacket length) - **DEFAULT**
- `long`: Hem at mid-thigh or lower (overcoat/trench length)

**Key principle:** Look at where the hem falls in the image. If you can't tell clearly, default to `regular` with 0.65-0.75 confidence.

**Always include length - it's crucial for outfit generation (e.g., balloon fit pants = wide + tapered + cropped).**

---

## FORMALITY (exactly 1, REQUIRED)

**Allowed (use only these):**
athletic, casual, smart-casual, business-casual, formal

**Scale:** athletic (1) → casual (2) → smart-casual (3) → business-casual (4) → formal (5)

Independent from style identity. A minimal sneaker is still `casual`.

---

## CONTEXT (1-2, STRONGLY RECOMMENDED)

**Allowed (use only these):** everyday, work-appropriate, travel, evening, weekend

**IMPORTANT:** Most items should have at least ONE context tag. This enables the AI to filter by occasion.

### Decision tree:

1. **Could this be worn to work?** (office/professional setting)
   - Dress shirts, blazers, dress trousers, oxfords → `work-appropriate`
   - Polo shirts (solid), chinos, loafers → `work-appropriate`
   - ❌ Graphic tees, distressed jeans, sneakers, hoodies → NOT work-appropriate

2. **Is this elevated/dressy for evening events?**
   - Blazers, dress shirts, formal outerwear, dress shoes → `evening`
   - Can combine with `work-appropriate`

3. **Is this casual weekend-specific?**
   - Graphic tees, hoodies, distressed denim → `weekend`
   - Can combine with `everyday`

4. **Is this travel-friendly?**
   - Packable items, wrinkle-resistant, comfortable pants → `travel`
   - Don't overuse

5. **Default for basic staples:**
   - Plain tees, jeans, casual shirts, basic sneakers → `everyday`

### Common patterns:
- Dress shirts → `work-appropriate`, `everyday` or `evening`
- Blazers → `work-appropriate`, `evening`
- Dress trousers → `work-appropriate`, `everyday`
- Graphic tees → `weekend`, `everyday`
- Hoodies → `weekend`, `everyday`
- Basic tees/jeans → `everyday`
- Chinos → `everyday`, `work-appropriate`

**Tag liberally - most items should get 1-2 context tags. Context enables the AI chat to filter by occasion.**

---

## PATTERN (0-1, OPTIONAL)

**Allowed (use only these):** solid, stripe, check, textured

**Only tag if pattern is distinct.**

Plain solid items → omit entirely.

---

## CONSTRUCTION DETAILS (0-2, OPTIONAL)

Use only the tags listed below.

**Bottoms only:**
pleated, flat-front, cargo, drawstring, elastic-waist

**Tops/outerwear only:**
structured-shoulder, dropped-shoulder

**Only tag structural features that affect fit/layering.**
**Omit if standard construction.**

---

## PAIRING & VERSATILITY (1-2, RECOMMENDED)

**Allowed (use only these):** neutral-base, statement-piece, easy-dress-up, easy-dress-down, high-versatility

**IMPORTANT:** Most items should have 1-2 pairing tags. These enable the AI to balance outfits.

### Quick rules:

**neutral-base** (MOST COMMON - 40-50% of items)
- Solid neutral colors (black, white, navy, gray, beige, olive)
- Simple, unfussy design
- Classic or elevated-basics style
- Would pair with almost anything

**statement-piece** (10-20% of items)
- Bold patterns or graphics
- Unique design details
- Bright or unusual colors
- Would be "the focal point" in an outfit
- **Conflicts with neutral-base** - pick one or the other, not both

**high-versatility** (30-40% of items)
- Works across multiple contexts and formality levels
- Core wardrobe staple
- Often combines with neutral-base

**easy-dress-up** (20-30% of casual items)
- Casual item that can be elevated
- Clean, polished styling despite being casual
- Could work in a smarter outfit
- Examples: clean sneakers, dark jeans, plain polos

**easy-dress-down** (20-30% of dressy items)
- Dressy item that can be casualized
- Not too formal
- Examples: casual blazers, oxford shirts, chinos

### Common combinations:
- White tee → `neutral-base`, `high-versatility`
- Black jeans → `neutral-base`, `high-versatility`, `easy-dress-up`
- Graphic tee → `statement-piece`
- Chinos → `neutral-base`, `high-versatility`, `easy-dress-up`
- Oxford shirt → `neutral-base`, `easy-dress-down`, `high-versatility`
- Casual blazer → `easy-dress-down`
- Patterned jacket → `statement-piece`

**Don't use:**
- Both neutral-base AND statement-piece (contradictory)
- All three dress-up/down/versatility tags (pick 1-2 max)

**Tag generously - pairing tags help the AI balance outfits (neutral vs statement).**

---

## FOOTWEAR-ONLY TAGS

Use only the tags listed in each line below.

**Shoe Type (exactly 1, REQUIRED):**
sneakers, boots, loafers, derbies, oxfords, sandals, dress-shoes

**Profile (exactly 1, REQUIRED):**
sleek, standard, chunky

Describes visual weight/bulk, NOT sole height.

**Closure (0-1, OPTIONAL):**
lace-up, slip-on, buckle

---

## CONFIDENCE SCORING

Confidence expresses **certainty**, not quality.

| Range | When to Use | Action |
|-------|-------------|---------|
| **0.85-1.00** | Visually obvious OR explicitly stated | Include |
| **0.70-0.84** | Strong inference from images + text | Include |
| **0.60-0.69** | Some ambiguity | Include for REQUIRED and RECOMMENDED tags |
| **< 0.60** | Uncertain or unclear | **OMIT ENTIRELY** |

**Tag-specific thresholds:**
- **style_identity, fit, silhouette, formality:** Include if ≥ 0.70
- **length:** Include if ≥ 0.60 (always tag, default to regular)
- **context, pairing_tags:** Include if ≥ 0.60 (tag generously)
- **construction_details:** Include if ≥ 0.70 (only when clear)
- **pattern:** Include if ≥ 0.70 (only distinct patterns)

---

## REASONING REQUIREMENTS

For **EVERY** tag, include a `reasoning` field explaining your decision.

**Reasoning should:**
- Be 1-2 sentences (concise)
- Reference specific visual details or text
- Explain WHY you chose this tag
- Explain why similar tags DIDN'T apply (when relevant)

**Examples:**

style_identity: "classic"
✅ Good: "Plain oxford shirt with timeless proportions and no distinctive aesthetic markers; would work across decades"
❌ Bad: "Looks classic"

style_identity: "streetwear"
✅ Good: "Large graphic print covering chest is the focal design element; bold youth-oriented expression"
❌ Bad: "Has graphics"

fit: "relaxed"
✅ Good: "Noticeably looser through body with extra room visible in how fabric drapes; more ease than standard fit"
❌ Bad: "Looks comfortable"

context: "work-appropriate"
✅ Good: "Smart-casual formality and polished styling appropriate for business casual office"
❌ Bad: "Could work for work"

---

## OUTPUT FORMAT

Return **ONLY** valid JSON. No markdown, no explanations, no preamble.

Each tag object should include:
- `tag`: The exact tag string
- `confidence`: Your confidence level (0.0-1.0)
- `reasoning`: 1-2 sentence explanation of WHY you chose this tag

**Reasoning guidelines:**
- Be specific about what you saw/read
- Reference visual details or text from description
- Explain why OTHER tags didn't apply
- Keep it concise (1-2 sentences max)

### For APPAREL:

```json
{{
  "style_identity": [
    {{ "tag": "classic", "confidence": 0.86, "reasoning": "Plain oxford button-down with traditional styling and no distinctive aesthetic markers; timeless design that would work in any decade" }}
  ],
  "fit": {{ "tag": "regular", "confidence": 0.82, "reasoning": "Standard fit visible in images; neither tight nor oversized based on how fabric drapes on model" }},
  "silhouette": {{ "tag": "neutral", "confidence": 0.78, "reasoning": "No imposed shape or structure; natural drape without boxy or tailored elements" }},
  "length": {{ "tag": "regular", "confidence": 0.75, "reasoning": "Hem falls at waist level in product images; standard shirt length" }},
  "formality": {{ "tag": "smart-casual", "confidence": 0.85, "reasoning": "Button-down collar and structured fabric elevate it above casual; but not formal enough for business settings" }},
  "context": [
    {{ "tag": "work-appropriate", "confidence": 0.72, "reasoning": "Clean styling and smart-casual formality make it suitable for business casual offices" }},
    {{ "tag": "everyday", "confidence": 0.78, "reasoning": "Versatile staple that works for daily wear across multiple contexts" }}
  ],
  "pattern": {{ "tag": "solid", "confidence": 0.92, "reasoning": "Single color with no visible pattern, texture, or print" }},
  "pairing_tags": [
    {{ "tag": "neutral-base", "confidence": 0.75, "reasoning": "Simple solid color and classic design pair easily with most other items" }},
    {{ "tag": "high-versatility", "confidence": 0.70, "reasoning": "Works across multiple contexts (work, casual, evening) and formality levels" }}
  ]
}}
```

### For FOOTWEAR:

```json
{{
  "style_identity": [
    {{ "tag": "classic", "confidence": 0.84, "reasoning": "Clean low-profile sneaker with minimal branding; timeless athletic-casual design" }}
  ],
  "shoe_type": {{ "tag": "sneakers", "confidence": 0.92, "reasoning": "Laced athletic shoe with rubber sole and cushioned construction; clearly a sneaker" }},
  "profile": {{ "tag": "sleek", "confidence": 0.77, "reasoning": "Low profile and streamlined silhouette; not chunky or bulky" }},
  "formality": {{ "tag": "casual", "confidence": 0.88, "reasoning": "Athletic shoe styling; appropriate for casual and weekend wear, not office" }},
  "context": [
    {{ "tag": "everyday", "confidence": 0.71, "reasoning": "Versatile sneaker suitable for daily casual wear" }}
  ],
  "closure": {{ "tag": "lace-up", "confidence": 0.85, "reasoning": "Lace closure visible in product images; not slip-on or buckle" }},
  "pattern": {{ "tag": "solid", "confidence": 0.89, "reasoning": "Single color with no visible pattern or graphic" }},
  "pairing_tags": [
    {{ "tag": "high-versatility", "confidence": 0.73, "reasoning": "Neutral sneaker pairs with jeans, chinos, and casual outfits" }}
  ]
}}
```

**Include these tags on most items:**
- length (always)
- context (1-2 tags)
- pairing_tags (1-2 tags)

**Omit these unless clearly applicable:**
- construction_details (only if structural features present)

---

## EXPLICITLY FORBIDDEN

❌ **Never include:**
- Color or color_family tags (already scraped)
- Materials or composition tags (already scraped)
- Era/decade tags (70s, 90s, Y2K)
- Garment archetypes (bomber, chore jacket, parka)
- Fit tags for footwear
- Length tags for footwear
- Silhouette tags for footwear
- Construction_details tags for footwear
- Trend language or "vibes"
- Tags not in allowed lists above
- Any text outside JSON

---

## CRITICAL REMINDERS

**Non-negotiables (same as CORE RULES):**
- Only tags from the allowed lists in this prompt — never invent or paraphrase
- Every tag object must include `tag`, `confidence`, and `reasoning`
- Return valid JSON only — no markdown, no code fences, no text before or after
- Required tags for the product category must always be present

**For style_identity:**
- Small graphics/embroidery → NOT streetwear (use classic)
- Technical fabric alone → NOT sporty (use classic)
- Most items → classic (it's the safe default)
- Use minimal, normcore, grunge, punk VERY rarely

**For structure tags (always include):**
- fit, silhouette, formality, length → Required on all apparel

**For context & pairing (tag generously):**
- Most items should get 1-2 context tags
- Most items should get 1-2 pairing tags
- These enable AI chat filtering and outfit balancing

**For optional tags:**
- construction_details: Only if structural features present
- pattern: Only if distinct pattern

---

## Before you respond

Confirm: (1) Every tag has `tag`, `confidence`, and `reasoning`. (2) Only tags from the allowed lists above. (3) Output is valid JSON only — no markdown or extra text.

---

Now analyze the product and return **ONLY** the JSON."""


# =============================================================================
# RESPONSE PARSING
# =============================================================================


def _clamp_confidence(value: Any) -> float:
    """Clamp confidence value to 0.0-1.0 range."""
    try:
        conf = float(value)
        return max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        return 0.5


def _tag_entry(item: dict, tag_key: str = "tag", confidence_default: float = 0.5) -> dict:
    """Build a TagWithConfidence dict from raw item; include reasoning if present."""
    entry: dict = {
        "tag": item[tag_key],
        "confidence": _clamp_confidence(item.get("confidence", confidence_default)),
    }
    if item.get("reasoning") is not None and str(item.get("reasoning", "")).strip():
        entry["reasoning"] = str(item["reasoning"]).strip()
    return entry


def parse_ai_response(response: str, category: str) -> Optional[AITagOutput]:
    """
    Parse and validate the AI response.

    Args:
        response: Raw AI response string
        category: Product category for validation

    Returns:
        Validated AITagOutput or None if parsing fails
    """
    # Extract JSON from response
    json_match = re.search(r"\{[\s\S]*\}", response)
    if not json_match:
        console.print("[red]No JSON found in response[/red]")
        return None

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        console.print(f"[red]JSON parse error: {e}[/red]")
        return None

    # Validate and clean the response
    result: AITagOutput = {}

    # Style Identity (required, 1-2 max)
    if "style_identity" in data:
        valid_styles = []
        for item in data["style_identity"][:2]:
            if isinstance(item, dict) and item.get("tag") in STYLE_IDENTITY_TAGS:
                valid_styles.append(_tag_entry(item))
        if valid_styles:
            result["style_identity"] = valid_styles

    # Category-specific validation
    if category == "footwear":
        # Shoe Type (required for footwear)
        if "shoe_type" in data:
            item = data["shoe_type"]
            if isinstance(item, dict) and item.get("tag") in SHOE_TYPE_TAGS:
                result["shoe_type"] = _tag_entry(item)

        # Profile (required for footwear)
        if "profile" in data:
            item = data["profile"]
            if isinstance(item, dict) and item.get("tag") in SHOE_PROFILE_TAGS:
                result["profile"] = _tag_entry(item)

        # Closure (optional for footwear)
        if "closure" in data:
            item = data["closure"]
            if isinstance(item, dict) and item.get("tag") in SHOE_CLOSURE_TAGS:
                result["closure"] = _tag_entry(item)
    else:
        # Apparel: Fit (required for apparel, exactly 1, category-aware)
        if "fit" in data:
            item = data["fit"]
            tag = item.get("tag") if isinstance(item, dict) else None

            # Category-aware validation
            valid_fits = FIT_TAGS_BOTTOM if category == "bottom" else FIT_TAGS_UPPER
            if tag in valid_fits:
                result["fit"] = _tag_entry(item)

        # Apparel: Silhouette (required for apparel)
        if "silhouette" in data:
            item = data["silhouette"]
            tag = item.get("tag") if isinstance(item, dict) else None

            valid_silhouettes = (
                SILHOUETTE_BOTTOM_TAGS
                if category == "bottom"
                else SILHOUETTE_UPPER_TAGS
            )
            if tag in valid_silhouettes:
                result["silhouette"] = _tag_entry(item)

        # Apparel: Length (optional, 0-1)
        if "length" in data:
            item = data["length"]
            tag = item.get("tag") if isinstance(item, dict) else None

            if tag in LENGTH_TAGS:
                result["length"] = _tag_entry(item)

        # Construction Details (optional, category-aware)
        if "construction_details" in data:
            valid_details_set = (
                DETAILS_BOTTOM_TAGS if category == "bottom" else DETAILS_UPPER_TAGS
            )
            valid_details = []
            for item in data["construction_details"][:2]:
                if isinstance(item, dict) and item.get("tag") in valid_details_set:
                    valid_details.append(_tag_entry(item))
            if valid_details:
                result["construction_details"] = valid_details

    # Formality (required, exactly 1)
    if "formality" in data:
        item = data["formality"]
        if isinstance(item, dict) and item.get("tag") in FORMALITY_TAGS:
            result["formality"] = _tag_entry(item)

    # Context (optional, 0-2)
    if "context" in data:
        valid_context = []
        for item in data["context"][:2]:
            if isinstance(item, dict) and item.get("tag") in CONTEXT_TAGS:
                valid_context.append(_tag_entry(item))
        if valid_context:
            result["context"] = valid_context

    # Pattern (optional, 0-1)
    if "pattern" in data:
        item = data["pattern"]
        if isinstance(item, dict) and item.get("tag") in PATTERN_TAGS:
            result["pattern"] = _tag_entry(item)

    # Pairing Tags (optional, 0-3)
    if "pairing_tags" in data:
        valid_pairing = []
        for item in data["pairing_tags"][:3]:
            if isinstance(item, dict) and item.get("tag") in PAIRING_TAGS:
                valid_pairing.append(_tag_entry(item))
        if valid_pairing:
            result["pairing_tags"] = valid_pairing

    return result if result else None


# =============================================================================
# MAIN TAGGER CLASS
# =============================================================================


@dataclass
class ReFitdTaggerConfig:
    """Configuration for the ReFitd tagger."""

    temperature: float = 0.0  # Deterministic: same product + images → same tags every run
    max_tokens: int = 1024
    retry_attempts: int = 2


class ReFitdTagger:
    """
    ReFitd Canonical Item Tagger.

    Sensor layer that produces structured tags with confidence scores.
    Uses GPT-5.2 vision to analyze product images and metadata.

    Output follows the ReFitd Item Tagging Specification exactly.
    """

    def __init__(
        self,
        config: Optional[ReFitdTaggerConfig] = None,
        ai_client: Optional["OpenAIClient"] = None,
    ):
        self.config = config or ReFitdTaggerConfig()
        self.client = ai_client
        self._owns_client = ai_client is None

    async def __aenter__(self):
        """Async context manager entry."""
        if self._owns_client:
            if not OPENAI_AVAILABLE:
                raise RuntimeError(
                    "OpenAI client not available. Install openai package."
                )
            self.client = OpenAIClient()
            await self.client.connect()
            console.print("[green]ReFitd Tagger initialized (GPT vision)[/green]")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._owns_client and self.client:
            await self.client.close()

    async def tag_product(
        self,
        image_url: Optional[str] = None,
        image_urls: Optional[list[str]] = None,
        title: str = "",
        category: str = "",
        description: str = "",
        brand: str = "",
    ) -> Optional[AITagOutput]:
        """
        Generate structured tags for a product.

        This is the SENSOR LAYER output - contains confidence scores.
        Use tag_policy.apply_policy() to get final canonical tags.

        Args:
            image_url: Single image URL (used if image_urls not provided)
            image_urls: Multiple image URLs (product + model photos) for better style context
            title: Product title
            category: One of: top_base, top_mid, bottom, outerwear, footwear
            description: Product description
            brand: Brand name

        Returns:
            AITagOutput with confidence scores, or None if tagging fails
        """
        if not self.client:
            raise RuntimeError("Tagger not initialized. Use async context manager.")

        # Prefer multiple images when provided (model + lay flat = better style tags)
        urls = image_urls if image_urls else ([image_url] if image_url else [])

        if not urls:
            console.print("[yellow]No image URL(s) provided for tagging[/yellow]")
            return None

        # Validate category
        if category not in ("top_base", "top_mid", "bottom", "outerwear", "footwear"):
            console.print(f"[yellow]Warning: Unknown category '{category}'[/yellow]")

        # Build prompt (mention multiple views when we send more than one image)
        user_prompt = build_user_prompt(
            category=category,
            title=title,
            description=description,
            brand=brand,
        )
        if len(urls) > 1:
            user_prompt += "\n\nNote: You are seeing multiple product images (may include model/lifestyle and lay-flat). Use all views to infer style, fit, and formality."

        # Call vision model (single or multi-image)
        for attempt in range(self.config.retry_attempts):
            try:
                if len(urls) > 1:
                    response = await self.client.generate_with_images(
                        prompt=user_prompt,
                        image_urls=urls,
                        temperature=self.config.temperature,
                        max_images=10,
                    )
                else:
                    response = await self.client.generate_with_image(
                        prompt=user_prompt,
                        image=urls[0],
                        temperature=self.config.temperature,
                    )

                if not response:
                    console.print(
                        f"[yellow]Empty response (attempt {attempt + 1})[/yellow]"
                    )
                    continue

                # Parse response
                result = parse_ai_response(response, category)

                if result:
                    # Add category to result
                    result["category"] = category
                    return result

                console.print(
                    f"[yellow]Failed to parse response (attempt {attempt + 1})[/yellow]"
                )

            except Exception as e:
                console.print(f"[red]Error on attempt {attempt + 1}: {e}[/red]")

        return None

    async def tag_products_batch(
        self,
        products: list[dict],
        show_progress: bool = True,
    ) -> dict[str, AITagOutput]:
        """
        Generate tags for multiple products.

        Args:
            products: List of dicts with keys:
                - id or product_id: Product identifier
                - image_url: URL to product image
                - name or title: Product name
                - category: Product category
                - description: Optional description
                - brand: Optional brand
            show_progress: Show progress bar

        Returns:
            Dict mapping product_id to AITagOutput
        """
        results = {}

        if show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task(
                    f"Tagging {len(products)} products...",
                    total=len(products),
                )

                for product in products:
                    product_id = product.get("id") or product.get(
                        "product_id", "unknown"
                    )
                    image_urls = product.get("image_urls") or (
                        [product.get("image_url")] if product.get("image_url") else []
                    )
                    title = product.get("name") or product.get("title", "")
                    category = product.get("category", "top_base")
                    description = product.get("description", "")
                    brand = product.get("brand", "")

                    if not image_urls:
                        console.print(
                            f"[yellow]Skipping {product_id}: no image URL(s)[/yellow]"
                        )
                        progress.update(task, advance=1)
                        continue

                    result = await self.tag_product(
                        image_urls=image_urls,
                        title=title,
                        category=category,
                        description=description,
                        brand=brand,
                    )

                    if result:
                        results[product_id] = result
                    else:
                        console.print(f"[yellow]Failed to tag {product_id}[/yellow]")

                    progress.update(task, advance=1)
        else:
            for product in products:
                product_id = product.get("id") or product.get("product_id", "unknown")
                image_urls = product.get("image_urls") or (
                    [product.get("image_url")] if product.get("image_url") else []
                )
                title = product.get("name") or product.get("title", "")
                category = product.get("category", "top_base")
                description = product.get("description", "")
                brand = product.get("brand", "")

                if image_urls:
                    result = await self.tag_product(
                        image_urls=image_urls,
                        title=title,
                        category=category,
                        description=description,
                        brand=brand,
                    )
                    if result:
                        results[product_id] = result

        return results


# =============================================================================
# TESTING
# =============================================================================


async def test_tagger():
    """Test the ReFitd tagger."""
    from dotenv import load_dotenv

    load_dotenv()

    console.print("\n[bold cyan]Testing ReFitd Canonical Tagger[/bold cyan]\n")

    # Test product (would need a real Supabase image URL)
    test_product = {
        "image_url": "https://static.zara.net/assets/public/a95b/5c8f/3d324a14a5c8/b8c8e8a3a84a/00761306250-e1/00761306250-e1.jpg",
        "title": "RELAXED FIT LINEN BLEND SHIRT",
        "category": "top_base",
        "description": "Relaxed fit shirt made of a linen blend fabric. Lapel collar and long sleeves with buttoned cuffs.",
        "brand": "Zara",
    }

    async with ReFitdTagger() as tagger:
        console.print(f"[cyan]Tagging: {test_product['title']}[/cyan]")
        console.print(f"[dim]Category: {test_product['category']}[/dim]\n")

        result = await tagger.tag_product(
            image_url=test_product["image_url"],
            title=test_product["title"],
            category=test_product["category"],
            description=test_product["description"],
            brand=test_product["brand"],
        )

        if result:
            console.print("[green]AI Sensor Output (with confidence):[/green]")
            console.print_json(json.dumps(result, indent=2))
        else:
            console.print("[red]Failed to generate tags[/red]")


if __name__ == "__main__":
    asyncio.run(test_tagger())
