"""
Product transformer for cleaning and normalizing scraped data.

Only raw metadata is stored here. All intelligent tagging (style, formality,
fit, silhouette, etc.) is done by ReFitdTagger as a separate step.
"""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class PriceInfo(BaseModel):
    """Validated price info."""

    current: Optional[float] = None
    original: Optional[float] = None
    currency: str = "USD"
    discount_percentage: Optional[float] = None


class SizeInfo(BaseModel):
    """Size with availability status."""

    size: str
    available: bool = True
    availability: Optional[str] = None
    sku: Optional[int] = None


class ProductMetadata(BaseModel):
    """Validated and cleaned product metadata (raw data only).

    Intelligent tags (style, formality, fit, silhouette, etc.) come from
    ReFitdTagger and are stored in tags_final / tags_ai_raw in the DB.
    """

    product_id: str
    name: str
    brand: str = "Zara"
    category: str
    subcategory: Optional[str] = None
    url: str
    price: PriceInfo
    description: Optional[str] = None
    colors: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    scraped_at: str

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        if not v:
            return "Unknown Product"
        v = re.sub(r"\s+", " ", v).strip()
        return v.title()

    @field_validator("description")
    @classmethod
    def clean_description(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = re.sub(r"\s+", " ", v).strip()
        return v if v else None

    @field_validator("colors", "sizes", "materials")
    @classmethod
    def clean_list(cls, v: list) -> list:
        if not v:
            return []
        seen = set()
        result = []
        for item in v:
            cleaned = str(item).strip()
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                result.append(cleaned)
        return result


class ProductTransformer:
    """Transforms raw product data into clean, validated format (raw metadata only)."""

    def __init__(self):
        self.category_mappings = {
            "tshirts": ("Tops", "T-Shirts"),
            "pants": ("Bottoms", "Pants"),
            "jackets": ("Outerwear", "Jackets"),
        }

    def transform(self, raw_data) -> Optional[ProductMetadata]:
        """Transform raw product data into clean ProductMetadata (raw only)."""
        try:
            discount = None
            if raw_data.price_original and raw_data.price_current:
                if raw_data.price_original > raw_data.price_current:
                    discount = round(
                        (1 - raw_data.price_current / raw_data.price_original) * 100, 1
                    )

            category, subcategory = self.category_mappings.get(
                raw_data.category, (raw_data.category.title(), None)
            )

            price = PriceInfo(
                current=raw_data.price_current,
                original=raw_data.price_original,
                currency=raw_data.currency,
                discount_percentage=discount,
            )

            sizes_list = []
            for size_item in raw_data.sizes:
                if isinstance(size_item, dict):
                    sizes_list.append(size_item.get("size", str(size_item)))
                else:
                    sizes_list.append(str(size_item))

            metadata = ProductMetadata(
                product_id=raw_data.product_id,
                name=raw_data.name,
                category=category,
                subcategory=subcategory,
                url=raw_data.url,
                price=price,
                description=raw_data.description,
                colors=raw_data.colors,
                sizes=sizes_list,
                materials=raw_data.materials,
                images=[],
                scraped_at=raw_data.scraped_at,
            )
            return metadata

        except Exception as e:
            print(f"Error transforming product {raw_data.product_id}: {e}")
            return None

    def transform_batch(self, raw_data_list: list) -> list[ProductMetadata]:
        """Transform a batch of raw product data."""
        results = []
        for raw_data in raw_data_list:
            transformed = self.transform(raw_data)
            if transformed:
                results.append(transformed)
        return results
