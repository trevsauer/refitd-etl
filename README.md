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

## Curation Workflow & Fine-Tuning Pipeline

The curation system allows human reviewers to correct AI-generated tags, building a training dataset for fine-tuning a custom tagging model.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CURATION & FINE-TUNING PIPELINE                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │  CURATION    │───▶│   EXPORT     │───▶│  FINE-TUNE   │              │
│  │     UI       │    │   JSONL      │    │   OPENAI     │              │
│  │              │    │              │    │              │              │
│  │ • Review AI  │    │ • Training   │    │ • Upload     │              │
│  │   tags       │    │   format     │    │   dataset    │              │
│  │ • Correct    │    │ • Curator    │    │ • Train      │              │
│  │   errors     │    │   feedback   │    │   model      │              │
│  │ • Add notes  │    │   included   │    │ • Deploy     │              │
│  └──────────────┘    └──────────────┘    └──────────────┘              │
│         │                                                               │
│         ▼                                                               │
│  ┌──────────────────────────────────────────────────────┐              │
│  │                    SUPABASE                           │              │
│  │  ┌────────────┐  ┌─────────────────┐  ┌───────────┐  │              │
│  │  │  products  │  │ curation_history│  │ curation_ │  │              │
│  │  │            │  │                 │  │  status   │  │              │
│  │  │ curated_at │  │ original_ai_tags│  │           │  │              │
│  │  │ curated_by │  │ corrected_tags  │  │ curator   │  │              │
│  │  │ tags_final │  │ change_summary  │  │ status    │  │              │
│  │  └────────────┘  └─────────────────┘  └───────────┘  │              │
│  └──────────────────────────────────────────────────────┘              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Quick Start: Curation

```bash
# Navigate to project (auto-activates venv via direnv)
cd /path/to/refitd-etl

# Start curation server
python curate.py

# Opens at http://127.0.0.1:8000
```

### Curation UI Workflow

1. **Select curator name** from dropdown
2. **Navigate to product** using Previous/Next or category filters
3. **Review AI-generated tags** (shown with confidence scores)
4. **Make corrections:**
   - **Remove tags:** Click ✕ on incorrect tags, enter reason in modal
   - **Add tags:** Use dropdown to add missing tags, enter reason
   - **Modify tags:** Change values, enter reason for the change
5. **Set metadata:**
   - Check applicable error type boxes
   - Set confidence rating (1-5)
   - Add optional notes
6. **Click "Mark as Complete"**

### What Gets Saved

When you click "Mark as Complete", the system saves:

| Table | Fields Updated |
|-------|----------------|
| `products` | `curated_at`, `curated_by`, `training_eligible`, `tags_final` |
| `curation_status` | `product_id`, `curator`, `status`, `notes` |
| `curation_history` | Full record for training export (see below) |

**`curation_history` record:**
```json
{
  "product_id": "00761437_blue",
  "original_ai_tags": { /* tags_ai_raw snapshot */ },
  "corrected_tags": { /* tags_final with feedback */ },
  "change_summary": "Removed 'everyday' from context; Added 'work-appropriate' to context; Changed fit from 'oversized' to 'baggy'",
  "curator_notes": "Optional general notes",
  "error_types": ["incorrect_value", "missing_tag"],
  "confidence_in_correction": 4,
  "include_in_training": true,
  "curator_id": "Kiki"
}
```

### Export Training Data

Export curated products in OpenAI fine-tuning format (JSONL):

```bash
# Basic export
python scripts/export_training_data.py --output-file training.jsonl

# With options
python scripts/export_training_data.py \
  --output-file training.jsonl \
  --min-confidence 4 \
  --limit 500

# Include all records (even those marked exclude from training)
python scripts/export_training_data.py \
  --output-file training.jsonl \
  --no-approved-only
```

**Output format (each line):**
```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a fashion item tagging system...\n\nCURATOR FEEDBACK\n- Removed 'everyday' from context: not appropriate for work\n- Added 'work-appropriate' to context: fits office dress code\n- Changed fit from 'oversized' to 'baggy': more accurate term"
    },
    {
      "role": "user", 
      "content": "{\"title\": \"Colorblock T-Shirt\", \"category\": \"Tops\", ...}"
    },
    {
      "role": "assistant",
      "content": "{\"style_identity\": [\"normcore\"], \"fit\": \"baggy\", ...}"
    }
  ]
}
```

### Validate Training Data

Check JSONL format before uploading to OpenAI:

```bash
python scripts/validate_training_data.py training.jsonl

# Strict mode (treat warnings as errors)
python scripts/validate_training_data.py training.jsonl --strict
```

**Validation checks:**
- Valid JSON on each line
- Correct message structure (system, user, assistant)
- Required tags present (style_identity, fit, formality, etc.)
- Tag values from controlled vocabulary

**Example output:**
```
✅ Validation passed

Statistics
----------------------------------------
  Total examples:  500
  Est. tokens:     ~1,250,000
  Avg tokens/example: ~2,500
  Est. cost (GPT-4o): ~$31.25

Category distribution
----------------------------------------
  Tops                             200
  Bottoms                          150
  Outerwear                        100
  Footwear                          50
```

### Database Management

```bash
# Preview what will be deleted
python scripts/wipe_database.py --dry-run

# Wipe all data (requires confirmation)
python scripts/wipe_database.py
# Type: DELETE EVERYTHING

# Force wipe without confirmation
python scripts/wipe_database.py --force
```

**Tables wiped (in order for FK constraints):**
1. `curation_history`
2. `tag_correction_feedback`
3. `rejected_inferred_tags`
4. `curated_metadata`
5. `curation_status`
6. `ai_generated_tags`
7. `products`

### Verify Curation Data (SQL)

Run in Supabase SQL Editor:

```sql
-- Check curated products
SELECT product_id, name, curated_at, curated_by 
FROM products 
WHERE curated_at IS NOT NULL;

-- Check curation history
SELECT * FROM curation_history 
ORDER BY created_at DESC 
LIMIT 10;

-- View tag feedback details
SELECT 
  product_id,
  tags_final -> 'deleted_tags' as deleted_tags,
  tags_final -> 'added_tags' as added_tags,
  tags_final -> 'modified_tags' as modified_tags
FROM products
WHERE curated_at IS NOT NULL;
```

### Environment Setup (Auto-activation)

The repo uses `direnv` for automatic environment activation:

```bash
# First time setup (if direnv not installed)
brew install direnv

# Add to your shell (choose one):
# Fish:  echo 'direnv hook fish | source' >> ~/.config/fish/config.fish
# Bash:  echo 'eval "$(direnv hook bash)"' >> ~/.bashrc
# Zsh:   echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc

# Allow the .envrc file
cd /path/to/refitd-etl
direnv allow
```

**What happens on `cd`:**
```
direnv: loading ~/Desktop/scripts/refitd-etl/.envrc
🔧 refitd-etl environment activated (Python venv + .env loaded)
```

### Fine-Tuning Workflow (End-to-End)

```bash
# 1. Start curation server
python curate.py

# 2. Curate 500-1000 products in browser
#    http://127.0.0.1:8000

# 3. Export training data
python scripts/export_training_data.py -o training.jsonl

# 4. Validate format
python scripts/validate_training_data.py training.jsonl

# 5. Upload to OpenAI and fine-tune
openai api fine_tunes.create -t training.jsonl -m gpt-4o-2024-11-20

# 6. Monitor training
openai api fine_tunes.follow -i <fine_tune_id>

# 7. Update tagger to use fine-tuned model
# Edit src/ai/refitd_tagger.py: MODEL = "ft:gpt-4o-2024-11-20:..."
```

### Scripts Reference

| Script | Purpose |
|--------|---------|
| `curate.py` | Curation UI server (Flask) |
| `scripts/export_training_data.py` | Export to JSONL for fine-tuning |
| `scripts/validate_training_data.py` | Validate JSONL format |
| `scripts/wipe_database.py` | Clean database for fresh start |
| `scripts/manage_fine_tune.py` | OpenAI fine-tuning management |

### Troubleshooting

**Export returns "No training examples found":**
- Ensure products are marked complete in curation UI
- Check `curation_history` table has records
- Try `--no-approved-only` flag

**Validation fails with missing tags:**
- Some tags may be intentionally null (e.g., removed by curator)
- Check if it's a false positive on nullable fields
- Use `--strict` only for final validation

**Curation UI shows "Curated" but database is empty:**
- This was a bug, now fixed in `curate.py`
- Ensure you're running the latest version

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
