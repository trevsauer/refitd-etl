# ReFitd ETL Pipeline

**Automated product ingestion and AI tagging for the ReFitd outfit generation system.**

ReFitd ETL scrapes retail product catalogs, extracts structured metadata, and applies AI-powered canonical tagging to build a curated inventory database that powers intelligent outfit recommendations.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Pipeline Workflow](#pipeline-workflow)
- [AI Tagging System](#ai-tagging-system)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [CLI Reference](#cli-reference)
- [Database Schema](#database-schema)
- [Development](#development)

---

## Overview

ReFitd is a personal outfit-generation system that helps users assemble coherent outfits from real, in-stock retail products. This ETL pipeline is the **data ingestion layer** that:

1. **Extracts** product data from retail websites (currently Zara)
2. **Transforms** raw data into normalized, validated metadata
3. **Loads** products into Supabase with images stored in cloud storage
4. **Tags** products using AI vision analysis with controlled vocabularies

### Key Principles

- **Speed over guarantees** — Generation queries the database, not live inventory
- **Freshness decay** — Availability confidence decreases over time
- **Controlled vocabularies** — AI proposes, policy decides, humans review edge cases
- **Sensor/Policy separation** — AI outputs confidence scores; rules determine acceptance

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ReFitd ETL Pipeline                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │   EXTRACT    │───▶│  TRANSFORM   │───▶│     LOAD     │              │
│  │              │    │              │    │              │              │
│  │ • Playwright │    │ • Validate   │    │ • Supabase   │              │
│  │ • Stealth    │    │ • Normalize  │    │ • Storage    │              │
│  │ • Rate limit │    │ • Structure  │    │ • Tracking   │              │
│  └──────────────┘    └──────────────┘    └──────┬───────┘              │
│                                                  │                      │
│                                                  ▼                      │
│                                          ┌──────────────┐              │
│                                          │   AI TAG     │              │
│                                          │              │              │
│                                          │ • GPT Vision │              │
│                                          │ • Tag Policy │              │
│                                          │ • Curation   │              │
│                                          └──────────────┘              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
refitd-etl/
├── main.py                 # CLI entry point
├── config/
│   └── settings.py         # Pipeline configuration
├── src/
│   ├── extractors/         # Web scraping (Zara)
│   ├── transformers/       # Data normalization
│   ├── loaders/            # Supabase & file storage
│   ├── ai/                 # AI tagging system
│   │   ├── refitd_tagger.py   # GPT Vision sensor
│   │   └── tag_policy.py      # Policy layer
│   ├── tracking/           # Scrape deduplication
│   └── services/           # Curation history
├── scripts/                # Utility scripts
├── docs/                   # Specifications
└── data/                   # Local storage (gitignored)
```

---

## Pipeline Workflow

The pipeline executes four phases sequentially:

### Phase 1: Extract

```python
# Scrape product pages using Playwright with stealth mode
async with ZaraExtractor(config) as extractor:
    products = await extractor.extract_product(url, category)
```

**What it captures:**
| Field | Description |
|-------|-------------|
| Product ID | Unique identifier from retailer |
| Name | Product title |
| Price | Current and original prices |
| Description | Full product description |
| Colors | All available color variants |
| Sizes | Size availability (in_stock, low_stock, out_of_stock) |
| Materials | Composition from product page |
| Images | Full image gallery URLs |

**Anti-detection measures:**
- Playwright with stealth patches
- Randomized delays between requests
- User-agent rotation
- Headless browser with realistic viewport

### Phase 2: Transform

```python
transformer = ProductTransformer()
products = transformer.transform_batch(raw_products)
```

**Transformations applied:**
- Validate required fields
- Normalize price formats
- Parse composition/materials into structured data
- Map retailer categories to ReFitd slots
- Generate display-ready metadata

### Phase 3: Load

```python
await supabase_loader.save_product(
    product_id=product.product_id,
    name=product.name,
    image_urls=selected_images,  # 2 best lay-flat images
    image_urls_all=all_images,   # Full gallery preserved
    ...
)
```

**Storage strategy:**
- **Supabase PostgreSQL** — Product metadata, tags, availability
- **Supabase Storage** — Product images (2 per product for generator)
- **Local tracking DB** — SQLite for scrape deduplication

**Image selection rules by category:**
| Category | Selection Rule | Rationale |
|----------|---------------|-----------|
| Pants/Jeans | 2nd-to-last pair | Lay-flat images, skip model shots |
| Shoes/Boots | 3rd & 4th from end (reversed) | Best angle first |
| Swimwear | First 2 | No model photos |
| Default | Last 2 | Typically lay-flat |

### Phase 4: AI Tagging

```python
async with ReFitdTagger() as tagger:
    ai_output = await tagger.tag_product(image_urls, title, category, ...)
    policy_result = apply_tag_policy(ai_output)
```

This phase is detailed in the next section.

---

## AI Tagging System

The tagging system converts raw retail data into **structured, canonical signals** that power the outfit generator. It uses a **Sensor/Policy architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│                     AI TAGGING PIPELINE                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐         ┌─────────────┐         ┌───────────┐ │
│  │   SENSOR    │────────▶│   POLICY    │────────▶│  OUTPUT   │ │
│  │ (GPT-4V)    │         │ (Rules)     │         │           │ │
│  │             │         │             │         │           │ │
│  │ Proposes    │         │ Accepts or  │         │ tags_final│ │
│  │ tags with   │         │ suppresses  │         │ (no conf.)│ │
│  │ confidence  │         │ based on    │         │           │ │
│  │             │         │ thresholds  │         │ curation_ │ │
│  │ tags_ai_raw │         │ & rules     │         │ status    │ │
│  └─────────────┘         └─────────────┘         └───────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Sensor Layer (GPT-4 Vision)

The sensor analyzes product images and returns structured tags with confidence scores:

```json
{
  "style_identity": [
    { "tag": "minimal", "confidence": 0.86 },
    { "tag": "elevated-basics", "confidence": 0.72 }
  ],
  "fit": { "tag": "regular", "confidence": 0.82 },
  "silhouette": { "tag": "relaxed", "confidence": 0.78 },
  "formality": { "tag": "casual", "confidence": 0.85 },
  "context": [
    { "tag": "everyday", "confidence": 0.82 }
  ],
  "pairing_tags": [
    { "tag": "neutral-base", "confidence": 0.75 }
  ]
}
```

### Controlled Vocabulary

Tags are restricted to predefined vocabularies. The AI cannot invent new tags.

#### Style Identity (1-2 required)
```
classic, elevated-basics, grunge, minimal, normcore, outdoorsy, 
preppy, punk, rugged, sporty, streetwear, tailoring, utilitarian, 
vintage, western, workwear
```

#### Fit (1 required for apparel)
```
Tops/Outerwear: skinny, slim, regular, relaxed, oversized
Bottoms: skinny, slim, regular, relaxed, baggy
```

#### Silhouette (1 required)
```
Bottoms: straight, tapered, wide
Tops/Outerwear: neutral, relaxed, boxy, structured, tailored, longline
```

#### Formality (1 required)
```
athletic → casual → smart-casual → business-casual → formal
```

#### Context (1-2 required)
```
everyday, evening, travel, weekend, work-appropriate
```

### Policy Layer (Rules Engine)

The policy layer applies confidence thresholds and category-specific rules:

```python
# Confidence thresholds by field
THRESHOLDS = {
    "style_identity": 0.55,    # Lower threshold, important for matching
    "fit": 0.50,               # Common field, accept most
    "silhouette": 0.50,
    "formality": 0.50,
    "context": 0.45,           # Lowest threshold
    "pairing_tags": 0.60,      # Higher bar for optional tags
}
```

**Policy decisions:**

| Confidence | Action |
|------------|--------|
| ≥ threshold | Accept tag |
| < threshold | Suppress tag, log reason |
| Missing required | Apply sensible default |

### Curation Status

Each product receives a curation status based on tag coverage:

| Status | Meaning |
|--------|---------|
| `approved` | All required tags present with sufficient confidence |
| `needs_review` | Missing optional tags or borderline confidence |
| `needs_fix` | Missing required tags or very low confidence |

### Data Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Product      │     │ GPT-4 Vision │     │ Policy       │
│ Images       │────▶│ Analysis     │────▶│ Engine       │
│ + Metadata   │     │              │     │              │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                     ┌────────────────────────────┼────────────────────────────┐
                     │                            │                            │
                     ▼                            ▼                            ▼
              ┌──────────────┐           ┌──────────────┐           ┌──────────────┐
              │ tags_ai_raw  │           │ tags_final   │           │ curation_    │
              │              │           │              │           │ status_refitd│
              │ Full sensor  │           │ Canonical    │           │              │
              │ output with  │           │ tags without │           │ approved /   │
              │ confidence   │           │ confidence   │           │ needs_review │
              └──────────────┘           └──────────────┘           └──────────────┘
```

### What Tags Are NOT

The tagging system explicitly avoids:

- ❌ **Color tags** — Already scraped from product page
- ❌ **Material tags** — Already scraped as composition
- ❌ **Era/decade tags** — No "90s", "Y2K", etc.
- ❌ **Garment archetypes** — No "bomber jacket", "chore coat"
- ❌ **Trend language** — No "vibes", "aesthetic"
- ❌ **Free-text descriptors** — Everything is controlled vocabulary

---

## Quick Start

### Prerequisites

- Python 3.10+
- Supabase account (for cloud storage)
- OpenAI API key (for AI tagging)

### Installation

```bash
# Clone the repository
git clone https://github.com/trevsauer/refitd-etl.git
cd refitd-etl

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Configuration

Create a `.env` file:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Supabase Configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_KEY=your_supabase_key

# OpenAI Configuration (for AI tagging)
OPENAI_API_KEY=sk-your-openai-api-key
```

### Run the Pipeline

```bash
# Quick test: 1 product from each category
python main.py --sample-all

# Scrape 5 products per category
python main.py -n 5

# Full scrape of all products
python main.py --all

# Specific categories only
python main.py --all -c tshirts jeans jackets
```

---

## CLI Reference

### Basic Usage

```bash
python main.py [OPTIONS]
```

### Scraping Options

| Option | Description |
|--------|-------------|
| `-n NUM`, `--products NUM` | Products per category (default: 2) |
| `--all`, `-a` | Scrape ALL products |
| `--sample-all` | 1 product from each category (quick test) |
| `-c CAT [CAT ...]` | Specific categories to scrape |
| `--no-images` | Skip image downloads |
| `--headless false` | Watch browser (debugging) |

### Storage Options

| Option | Description |
|--------|-------------|
| `--supabase` | Save to Supabase (default) |
| `--no-supabase` | Local files only |
| `--local` | Save to both Supabase AND local |
| `-o DIR` | Local output directory |

### Database Management

| Option | Description |
|--------|-------------|
| `--force`, `-f` | Re-scrape already-scraped products |
| `--clear-tracking` | Reset tracking database |
| `--stats` | Show scraping statistics |
| `--wipe` | ⚠️ DELETE all products |

### AI Tagging

| Option | Description |
|--------|-------------|
| `--ai-status` | Check OpenAI availability |
| `--refitd-tags` | Generate canonical tags for all products |
| `--tag-existing` | Tag existing products (no scraping) |
| `--tag-untagged-only` | Only tag products without tags |

### Common Workflows

```bash
# First-time full scrape with tagging
python main.py --all

# Daily update (skips already-scraped)
python main.py --all

# Re-tag existing products
python main.py --tag-existing

# Tag only products missing tags
python main.py --tag-existing --tag-untagged-only

# Complete refresh
python main.py --wipe
python main.py --all
```

---

## Database Schema

### Products Table

```sql
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    product_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    brand_name TEXT DEFAULT 'Zara',
    category TEXT,
    category_refitd TEXT,          -- ReFitd slot: top_base, top_mid, bottom, outerwear, footwear
    top_layer_role TEXT,           -- For tops: base or mid
    url TEXT,
    price_current DECIMAL(10, 2),
    price_original DECIMAL(10, 2),
    currency TEXT DEFAULT 'USD',
    description TEXT,
    colors JSONB,
    sizes JSONB,
    materials JSONB,
    composition TEXT,
    composition_structured JSONB,
    
    -- Images
    image_urls TEXT[],             -- 2 lay-flat images for generator
    image_urls_all TEXT[],         -- Full gallery
    image_paths TEXT[],            -- Supabase storage paths
    
    -- AI Tagging
    tags_ai_raw JSONB,             -- Full sensor output with confidence
    tags_final JSONB,              -- Canonical tags (no confidence)
    curation_status_refitd TEXT,   -- approved / needs_review / needs_fix
    model_version TEXT,
    prompt_version TEXT,
    tag_policy_version TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ
);
```

### tags_final Structure

```json
{
  "style_identity": ["minimal", "elevated-basics"],
  "fit": "regular",
  "silhouette": "relaxed",
  "length": "regular",
  "formality": "casual",
  "context": ["everyday", "weekend"],
  "construction_details": ["flat-front"],
  "pattern": "solid",
  "pairing_tags": ["neutral-base", "high-versatility"],
  "composition": {
    "cotton": 98,
    "elastane": 2
  }
}
```

---

## Development

### Running Tests

```bash
# Test the tagger directly
python -m src.ai.refitd_tagger

# Test policy layer
python -m src.ai.tag_policy
```

### Adding a New Retailer

1. Create extractor in `src/extractors/`
2. Implement `extract_product()` returning `RawProductData`
3. Add category mapping in `src/loaders/refitd_category_mapping.py`
4. Update `main.py` to support the new source

### Modifying Tag Vocabulary

1. Edit allowed tags in `src/ai/refitd_tagger.py`
2. Update thresholds in `src/ai/tag_policy.py`
3. Bump `PROMPT_VERSION` and `TAG_POLICY_VERSION`

---

## License

Private repository. All rights reserved.

---

## Related Documentation

- [Item Tagging System Specification](docs/Item%20Tagging%20System%20-%20RF%20(1.15.2026).md)
- [Inventory Ingestion Pipeline](docs/Inventory%20Ingestion%20%26%20Freshness%20Pipeline%20-%20RF%20(1.15.2026).md)
- [Product Definition](docs/Product%20Definition%20(Revised%20%26%20Consolidated)%20-%20RF%20(1.15.2026).md)
- [AI Tag Generation](docs/AI_TAG_GENERATION.md)
- [Full Tagging Prompt](docs/refitd_tagging_prompt_full.md)
