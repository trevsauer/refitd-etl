"""
ReFitd canonical slot mapping: retailer category → category_refitd + top_layer_role.

Used by the Supabase loader (on save) and the backfill script.
Generator queries by category_refitd ('outerwear', 'top', 'bottom', 'footwear') and top_layer_role ('base', 'mid') for tops.
"""

from typing import Optional

# Retailer category key (e.g. from Zara scraper) → (category_refitd, top_layer_role or None)
# category_refitd: 'outerwear' | 'top' | 'bottom' | 'footwear'
# top_layer_role: 'base' | 'mid' | None (only for category_refitd='top')
RETAILER_TO_REFITD: dict[str, tuple[str, Optional[str]]] = {
    # Top - Base
    "tshirts": ("top", "base"),
    "shirts": ("top", "base"),
    "polo-shirts": ("top", "base"),
    "polos": ("top", "base"),
    # Top - Mid
    "sweaters": ("top", "mid"),
    "hoodies": ("top", "mid"),
    "quarter-zip": ("top", "mid"),
    "knitwear": ("top", "mid"),
    "sweatshirts": ("top", "mid"),
    "sweatsuits": ("top", "mid"),
    # Bottom
    "trousers": ("bottom", None),
    "jeans": ("bottom", None),
    "shorts": ("bottom", None),
    "swimwear": ("bottom", None),
    # Outerwear
    "jackets": ("outerwear", None),
    "outerwear": ("outerwear", None),
    "leather": ("outerwear", None),
    "blazers": ("outerwear", None),
    "overshirts": ("outerwear", None),
    "coats": ("outerwear", None),
    "suits": ("outerwear", None),
    # Footwear
    "shoes": ("footwear", None),
    "boots": ("footwear", None),
    "footwear": ("footwear", None),
}


def get_refitd_slots(retailer_category: str) -> tuple[str, Optional[str]]:
    """
    Map retailer category (e.g. 'tshirts', 'jackets') to ReFitd slots.

    Returns:
        (category_refitd, top_layer_role)
        category_refitd: 'outerwear' | 'top' | 'bottom' | 'footwear'
        top_layer_role: 'base' | 'mid' | None (only set for category_refitd='top')
    """
    key = (retailer_category or "").strip().lower()
    return RETAILER_TO_REFITD.get(key, ("top", "base"))  # safe default
