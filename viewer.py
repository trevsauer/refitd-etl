#!/usr/bin/env python3
"""
Simple web viewer for browsing scraped product data.

Supports two data sources:
  - Local files (default): Reads from data/zara/mens directory
  - Supabase database: Reads from cloud database (use --supabase flag)

Usage:
    python viewer.py              # Load from local files
    python viewer.py --supabase   # Load from Supabase database

Then open http://localhost:5000 in your browser.
"""
import argparse
import io
import json
import os
import re
from datetime import datetime
import sys
import subprocess
import threading
import urllib.request
from pathlib import Path

# Ensure project root is on path for src imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request, send_from_directory

# Load environment variables (optional - credentials are hardcoded as fallback)
load_dotenv(Path(__file__).parent / ".env")

app = Flask(__name__)

# Data directory for local files
DATA_DIR = Path(__file__).parent / "data" / "zara" / "mens"

# ============================================
# SUPABASE CREDENTIALS (Hardcoded for easy sharing)
# ============================================
# These credentials allow anyone who clones the repo to connect immediately
DEFAULT_SUPABASE_URL = "https://uochfddhtkzrvcmfwksm.supabase.co"
DEFAULT_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVvY2hmZGRodGt6cnZjbWZ3a3NtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njg1MDA1NDEsImV4cCI6MjA4NDA3NjU0MX0.mzBTf1GV8_Vk-nIMvf26PxI_MAqZfStzRTEZBEvHyLU"

# Global flag for data source
USE_SUPABASE = False
supabase_client = None
BUCKET_NAME = "product-images"

# ============================================
# SCRAPER STATUS TRACKING
# ============================================
scraper_status = {
    "running": False,
    "progress": 0,
    "total": 0,
    "current_category": "",
    "current_product": "",
    "products_scraped": 0,
    "products_skipped": 0,
    "error": None,
    "completed": False,
    "start_time": None,
    "end_time": None,
    "logs": [],  # Store log lines for display
    "refresh_handled": False,  # Prevent multiple refreshes
}


def init_supabase():
    """Initialize Supabase client."""
    global supabase_client

    # Use environment variables if available, otherwise use hardcoded defaults
    supabase_url = os.getenv("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    supabase_key = os.getenv("SUPABASE_KEY") or DEFAULT_SUPABASE_KEY

    from supabase import create_client

    supabase_client = create_client(supabase_url, supabase_key)
    return supabase_client


def get_products_from_supabase():
    """Fetch all products from Supabase database."""
    if not supabase_client:
        return []

    try:
        result = supabase_client.table("products").select("*").execute()
        products = result.data or []

        # Transform database format to match local file format for frontend compatibility
        transformed = []
        for p in products:
            # Build image URLs from storage paths (the 2 we store)
            image_paths = p.get("image_paths", [])
            supabase_url = os.getenv("SUPABASE_URL") or DEFAULT_SUPABASE_URL
            image_urls_stored = [
                f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{path}"
                for path in image_paths
            ]
            # Full list of scraped URLs for viewer display; only the 2 above are in DB/storage
            image_urls_all = p.get("image_urls_all") or []
            image_urls_stored_indices = p.get("image_urls_stored_indices") or []

            transformed.append(
                {
                    "product_id": p.get("product_id"),
                    "name": p.get("name"),
                    "brand": p.get("brand_name") or p.get("brandName") or p.get("brand") or "Zara",
                    "brandName": p.get("brand_name") or p.get("brandName") or p.get("brand") or "Zara",
                    "category": p.get("category"),
                    "subcategory": p.get("category"),  # Use category as subcategory
                    "url": p.get("url"),
                    "price": {
                        "current": (
                            float(p.get("price_current"))
                            if p.get("price_current")
                            else None
                        ),
                        "original": (
                            float(p.get("price_original"))
                            if p.get("price_original")
                            else None
                        ),
                        "currency": p.get("currency", "USD"),
                        "discount_percentage": None,
                    },
                    "description": p.get("description"),
                    "colors": p.get("colors", []),
                    "color": p.get("color"),  # Single color for this variant
                    "parent_product_id": p.get(
                        "parent_product_id"
                    ),  # Original product ID if color variant
                    "sizes": p.get("sizes", []),
                    "sizes_availability": p.get(
                        "sizes_availability", []
                    ),  # New field with availability
                    "sizes_checked_at": p.get(
                        "sizes_checked_at"
                    ),  # When sizes were last checked
                    "materials": p.get("materials", []),
                    "composition": p.get(
                        "composition"
                    ),  # Fabric composition (e.g., "100% cotton")
                    "composition_structured": p.get(
                        "composition_structured"
                    ),  # Hierarchical composition data
                    "images": image_paths,  # Store full paths for Supabase
                    "image_urls": image_urls_stored,  # The 2 stored (Supabase public URLs)
                    "image_urls_all": image_urls_all,  # Full list of scraped URLs (viewer display)
                    "image_urls_stored_indices": image_urls_stored_indices,  # Which of image_urls_all are stored (for badge)
                    "scraped_at": p.get("scraped_at"),
                    "_source": "supabase",  # Mark source for frontend
                    # ReFitd Canonical Tagging System fields
                    "tags_ai_raw": p.get(
                        "tags_ai_raw"
                    ),  # AI sensor output with confidence
                    "tags_final": p.get("tags_final"),  # Canonical tags for generator
                    "curation_status_refitd": p.get(
                        "curation_status_refitd", "pending"
                    ),
                    "tag_policy_version": p.get("tag_policy_version"),
                }
            )

        # Sort by product_id
        transformed.sort(key=lambda x: x.get("product_id", ""))
        return transformed

    except Exception as e:
        print(f"Error fetching from Supabase: {e}")
        return []


def get_products_from_local():
    """Scan data directory and load all product metadata from local files."""
    products = []

    if not DATA_DIR.exists():
        return products

    # Scan category directories
    for category_dir in DATA_DIR.iterdir():
        if category_dir.is_dir() and category_dir.name != "__pycache__":
            # Scan product directories within category
            for product_dir in category_dir.iterdir():
                if product_dir.is_dir():
                    metadata_file = product_dir / "metadata.json"
                    if metadata_file.exists():
                        try:
                            with open(metadata_file, "r") as f:
                                metadata = json.load(f)
                                # Add category folder name for image paths
                                metadata["category"] = category_dir.name
                                metadata["_source"] = "local"
                                metadata.setdefault("brand", "Zara")
                                metadata.setdefault("brandName", metadata.get("brand", "Zara"))
                                products.append(metadata)
                        except json.JSONDecodeError:
                            print(f"Error reading {metadata_file}")

    # Sort by product_id for consistent ordering
    products.sort(key=lambda x: x.get("product_id", ""))
    return products


def get_all_products():
    """Get products from configured source (Supabase or local)."""
    if USE_SUPABASE:
        return get_products_from_supabase()
    else:
        return get_products_from_local()


# HTML Template with embedded CSS and JavaScript
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zara Scraper - Product Viewer</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js" async></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        /* Pulse animation for low stock indicator */
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #f5f5f5;
            min-height: 100vh;
        }

        header {
            background: #000;
            color: #fff;
            padding: 20px;
            text-align: center;
        }

        header h1 {
            font-size: 24px;
            font-weight: 300;
            letter-spacing: 2px;
        }

        .data-source {
            font-size: 12px;
            margin-top: 5px;
            padding: 4px 12px;
            background: {{ '#4CAF50' if use_supabase else '#2196F3' }};
            display: inline-block;
            border-radius: 12px;
        }

        /* Curate Mode Styles */
        .curate-btn {
            background: #ff9800;
            color: #fff;
            border: none;
            padding: 8px 20px;
            font-size: 14px;
            cursor: pointer;
            border-radius: 4px;
            margin-left: 15px;
            transition: background 0.2s;
        }

        .curate-btn:hover {
            background: #f57c00;
        }

        .curate-btn.active {
            background: #4CAF50;
        }

        .curator-selector {
            display: none;
            margin-left: 10px;
        }

        .curator-selector.visible {
            display: inline-block;
        }

        .curator-selector select {
            padding: 8px 15px;
            font-size: 14px;
            border-radius: 4px;
            border: none;
            cursor: pointer;
        }

        .curator-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            margin-left: 10px;
        }

        .curator-reed { background: #4CAF50; color: white; }
        .curator-gigi { background: #9C27B0; color: white; }
        .curator-kiki { background: #E91E63; color: white; }

        /* Curate Input Styles */
        .curate-input-wrapper {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 10px;
        }

        .curate-input {
            flex: 1;
            padding: 8px 12px;
            font-size: 14px;
            border: 2px solid #ddd;
            border-radius: 4px;
            outline: none;
            transition: border-color 0.2s;
        }

        .curate-input:focus {
            border-color: #ff9800;
        }

        .curate-input::placeholder {
            color: #999;
        }

        .curated-tag {
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 13px;
            color: white;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }

        .curated-tag .curator-name {
            font-size: 10px;
            opacity: 0.8;
        }

        .tag-delete-btn {
            display: none;
            margin-left: 5px;
            background: rgba(255,0,0,0.2);
            border: none;
            color: #c00;
            font-size: 12px;
            cursor: pointer;
            padding: 2px 6px;
            border-radius: 3px;
            line-height: 1;
        }

        .tag-delete-btn:hover {
            background: rgba(255,0,0,0.4);
        }

        .curate-mode .tag-delete-btn {
            display: inline-block;
        }

        .tag-container {
            display: inline-flex;
            align-items: center;
            gap: 2px;
        }

        /* Rejected inferred tag styling */
        .rejected-tag {
            background: #ffebee !important;
            color: #c62828 !important;
            text-decoration: line-through;
            opacity: 0.7;
        }

        .rejected-tag .tag-delete-btn {
            background: rgba(76, 175, 80, 0.2);
            color: #2e7d32;
        }

        .rejected-tag .tag-delete-btn:hover {
            background: rgba(76, 175, 80, 0.4);
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
            padding: 20px;
            display: flex;
            gap: 20px;
        }

        /* Category Sidebar */
        .category-sidebar {
            width: 260px;
            flex-shrink: 0;
            background: #fff;
            border-radius: 12px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            padding: 20px;
            height: fit-content;
            position: sticky;
            top: 20px;
        }

        .sidebar-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
        }

        .sidebar-header h3 {
            margin: 0;
            font-size: 16px;
            font-weight: 600;
            color: #333;
        }

        .sidebar-header .category-icon {
            font-size: 20px;
        }

        .category-list {
            list-style: none;
            padding: 0;
            margin: 0;
        }

        .category-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 14px;
            margin-bottom: 6px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            background: #f8f9fa;
            border: 2px solid transparent;
        }

        .category-item:hover {
            background: #e8f4fd;
            border-color: #e0e0e0;
        }

        .category-item.active {
            background: linear-gradient(135deg, #e3f2fd, #bbdefb);
            border-color: #2196F3;
            box-shadow: 0 2px 8px rgba(33, 150, 243, 0.2);
        }

        .category-item.all-categories {
            background: linear-gradient(135deg, #f5f5f5, #eeeeee);
            font-weight: 600;
            margin-bottom: 12px;
        }

        .category-item.all-categories.active {
            background: linear-gradient(135deg, #333, #555);
            color: #fff;
            border-color: #333;
        }

        .category-item.all-categories.active .category-count {
            background: rgba(255,255,255,0.2);
            color: #fff;
        }

        .category-name {
            font-size: 14px;
            font-weight: 500;
            color: #333;
            text-transform: capitalize;
        }

        .category-item.active .category-name {
            color: #1565c0;
        }

        .category-item.all-categories.active .category-name {
            color: #fff;
        }

        .category-count {
            background: #e0e0e0;
            color: #666;
            font-size: 12px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 12px;
            min-width: 28px;
            text-align: center;
        }

        .category-item.active .category-count {
            background: #2196F3;
            color: #fff;
        }

        /* Subcategory styling */
        .category-item.category-header {
            background: linear-gradient(135deg, #f5f5f5, #eeeeee);
            font-weight: 600;
            margin-top: 12px;
        }

        .category-item.category-header:first-of-type {
            margin-top: 0;
        }

        .category-item.subcategory-item {
            padding-left: 28px;
            background: #fff;
            border-left: 3px solid #e0e0e0;
            margin-left: 8px;
            margin-bottom: 4px;
            font-size: 13px;
        }

        .category-item.subcategory-item:hover {
            border-left-color: #2196F3;
        }

        .category-item.subcategory-item.active {
            background: linear-gradient(135deg, #e3f2fd, #bbdefb);
            border-left-color: #2196F3;
        }

        .category-item.subcategory-item .category-name {
            font-size: 13px;
            font-weight: 400;
        }

        .category-item.subcategory-item .category-count {
            font-size: 11px;
            padding: 2px 8px;
        }

        /* Main content area adjustment */
        .main-content {
            flex: 1;
            min-width: 0;
        }

        .navigation {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 20px;
            margin-bottom: 30px;
            background: #fff;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }

        .nav-btn {
            background: #000;
            color: #fff;
            border: none;
            padding: 12px 30px;
            font-size: 14px;
            cursor: pointer;
            border-radius: 4px;
            transition: background 0.2s;
        }

        .nav-btn:hover {
            background: #333;
        }

        .nav-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
        }

        .counter {
            font-size: 16px;
            color: #666;
        }

        .product-card {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 40px;
            background: #fff;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            overflow: hidden;
        }

        .image-section {
            padding: 30px;
            background: #fafafa;
        }

        .main-image {
            width: 100%;
            max-height: 500px;
            object-fit: contain;
            border-radius: 4px;
        }

        .thumbnail-row {
            display: flex;
            gap: 10px;
            margin-top: 15px;
            flex-wrap: wrap;
        }

        .thumbnail {
            width: 80px;
            height: 100px;
            object-fit: cover;
            border: 2px solid transparent;
            border-radius: 4px;
            cursor: pointer;
            transition: border-color 0.2s;
        }

        .thumbnail:hover,
        .thumbnail.active {
            border-color: #000;
        }

        .thumbnail.selected-for-storage {
            box-shadow: 0 0 0 3px #4CAF50;
        }

        .thumbnail-wrap {
            position: relative;
        }

        .stored-badge {
            position: absolute;
            bottom: 4px;
            left: 4px;
            background: rgba(76, 175, 80, 0.95);
            color: white;
            font-size: 9px;
            font-weight: 600;
            padding: 2px 5px;
            border-radius: 3px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }

        .stored-badge-main {
            position: absolute;
            bottom: 12px;
            left: 12px;
            background: rgba(76, 175, 80, 0.95);
            color: white;
            font-size: 12px;
            font-weight: 600;
            padding: 6px 12px;
            border-radius: 6px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2);
        }

        .metadata-section {
            padding: 30px;
        }

        .category-badge {
            display: inline-block;
            background: #e0e0e0;
            color: #666;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }

        .category-dropdown-wrapper {
            display: inline-block;
            margin-bottom: 10px;
        }

        .category-dropdown {
            padding: 6px 32px 6px 12px;
            border-radius: 20px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            border: 2px solid #4CAF50;
            background: white;
            color: #333;
            cursor: pointer;
            font-weight: 600;
            appearance: none;
            -webkit-appearance: none;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23666' d='M3 4l3 4 3-4'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 10px center;
        }

        .category-dropdown:hover {
            border-color: #45a049;
            background-color: #f9fff9;
        }

        .category-dropdown:focus {
            outline: none;
            border-color: #2e7d32;
            box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.2);
        }

        .category-dropdown optgroup {
            font-weight: 700;
            color: #333;
            background: #f5f5f5;
            padding: 8px 0;
        }

        .category-dropdown option {
            font-weight: 400;
            color: #666;
            padding: 8px 12px;
        }

        .product-name {
            font-size: 28px;
            font-weight: 400;
            margin-bottom: 10px;
            color: #000;
        }

        .product-id {
            color: #999;
            font-size: 12px;
            margin-bottom: 20px;
        }

        .price-section {
            margin-bottom: 25px;
        }

        .current-price {
            font-size: 24px;
            font-weight: 600;
            color: #000;
        }

        .original-price {
            font-size: 18px;
            color: #999;
            text-decoration: line-through;
            margin-left: 10px;
        }

        .discount-badge {
            display: inline-block;
            background: #c00;
            color: #fff;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            margin-left: 10px;
        }

        .section-title {
            font-size: 14px;
            font-weight: 600;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
            margin-top: 20px;
        }

        .description {
            color: #444;
            line-height: 1.6;
        }

        .tag-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }

        .tag {
            background: #f0f0f0;
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 13px;
            color: #444;
        }

        .color-variant-link {
            transition: all 0.2s ease;
        }

        .color-variant-link:hover {
            background: #1565c0 !important;
            color: white !important;
            transform: translateY(-1px);
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }

        .url-link {
            color: #0066cc;
            text-decoration: none;
            word-break: break-all;
            font-size: 13px;
        }

        .url-link:hover {
            text-decoration: underline;
        }

        .scraped-time {
            color: #999;
            font-size: 12px;
            margin-top: 30px;
        }

        .no-data {
            text-align: center;
            padding: 100px 20px;
            color: #666;
        }

        .no-data h2 {
            margin-bottom: 10px;
        }

        /* Tab Navigation Styles */
        .tab-nav {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-bottom: 20px;
        }

        .tab-btn {
            padding: 12px 30px;
            font-size: 14px;
            font-weight: 500;
            border: 2px solid #000;
            background: #fff;
            color: #000;
            cursor: pointer;
            border-radius: 4px;
            transition: all 0.2s;
        }

        .tab-btn:hover {
            background: #f5f5f5;
        }

        .tab-btn.active {
            background: #000;
            color: #fff;
        }

        .tab-content {
            display: none;
        }

        .tab-content.active {
            display: block;
        }

        /* Dashboard Styles */
        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .stat-card {
            background: #fff;
            border-radius: 8px;
            padding: 25px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
        }

        .stat-card .stat-value {
            font-size: 42px;
            font-weight: 700;
            color: #000;
        }

        .stat-card .stat-label {
            font-size: 14px;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-top: 5px;
        }

        .stat-card.success .stat-value { color: #4CAF50; }
        .stat-card.warning .stat-value { color: #ff9800; }
        .stat-card.info .stat-value { color: #2196F3; }

        .chart-container {
            background: #fff;
            border-radius: 8px;
            padding: 25px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }

        .chart-title {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 15px;
            color: #333;
        }

        .activity-list {
            background: #fff;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }

        .activity-item {
            padding: 12px 0;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .activity-item:last-child {
            border-bottom: none;
        }

        .activity-product {
            font-weight: 500;
        }

        .activity-curator {
            font-size: 12px;
            padding: 4px 8px;
            border-radius: 12px;
            color: #fff;
        }

        .activity-time {
            font-size: 12px;
            color: #999;
        }

        /* Mark Complete Button */
        .complete-btn {
            background: #4CAF50;
            color: #fff;
            border: none;
            padding: 12px 25px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            border-radius: 4px;
            transition: background 0.2s;
            margin-right: 10px;
        }

        .complete-btn:hover {
            background: #388E3C;
        }

        .complete-btn.completed {
            background: #81C784;
        }

        .complete-btn.undo {
            background: #ff9800;
        }

        .complete-btn.undo:hover {
            background: #f57c00;
        }

        .curation-status-badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
            margin-left: 10px;
        }

        .curation-status-badge.complete {
            background: #e8f5e9;
            color: #2e7d32;
        }

        .curation-status-badge.pending {
            background: #fff3e0;
            color: #e65100;
        }

        /* Scraper Section Styles */
        .scraper-section {
            background: #fff;
            border-radius: 8px;
            padding: 25px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            margin-bottom: 30px;
            text-align: center;
        }

        .scraper-section h3 {
            font-size: 20px;
            margin-bottom: 15px;
            color: #333;
        }

        .scraper-controls {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 15px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }

        .scraper-controls label {
            font-size: 14px;
            color: #666;
        }

        .scraper-controls select,
        .scraper-controls input {
            padding: 8px 12px;
            font-size: 14px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }

        .go-btn {
            background: linear-gradient(135deg, #4CAF50, #45a049);
            color: #fff;
            border: none;
            padding: 15px 50px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            border-radius: 8px;
            transition: all 0.3s;
            box-shadow: 0 4px 15px rgba(76, 175, 80, 0.3);
        }

        .go-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(76, 175, 80, 0.4);
        }

        .go-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }

        .progress-container {
            margin-top: 20px;
            display: none;
        }

        .progress-container.visible {
            display: block;
        }

        .progress-bar-wrapper {
            background: #e0e0e0;
            border-radius: 10px;
            height: 20px;
            overflow: hidden;
            margin-bottom: 10px;
        }

        .progress-bar {
            height: 100%;
            background: linear-gradient(90deg, #4CAF50, #8BC34A);
            border-radius: 10px;
            transition: width 0.5s ease;
            width: 0%;
        }

        .progress-text {
            font-size: 14px;
            color: #666;
        }

        .progress-status {
            font-size: 16px;
            font-weight: 500;
            color: #333;
            margin-bottom: 10px;
        }

        .progress-details {
            font-size: 13px;
            color: #888;
        }

        /* Log Viewer Styles */
        .log-viewer {
            background: #1e1e1e;
            border-radius: 8px;
            padding: 15px;
            margin-top: 15px;
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 12px;
            line-height: 1.5;
            text-align: left;
            display: none;
        }

        .log-viewer.visible {
            display: block;
        }

        .log-viewer .log-line {
            color: #d4d4d4;
            margin: 0;
            padding: 2px 0;
            white-space: pre-wrap;
            word-break: break-all;
        }

        .log-viewer .log-line.error {
            color: #f44336;
        }

        .log-viewer .log-line.success {
            color: #4CAF50;
        }

        .log-viewer .log-line.warning {
            color: #ff9800;
        }

        .log-viewer .log-line.info {
            color: #2196F3;
        }

        .log-viewer .log-line.command {
            color: #9cdcfe;
        }

        .log-toggle {
            background: #333;
            color: #fff;
            border: none;
            padding: 8px 16px;
            font-size: 12px;
            cursor: pointer;
            border-radius: 4px;
            margin-top: 10px;
        }

        .log-toggle:hover {
            background: #444;
        }

        .validation-section {
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
        }

        .validation-btn {
            padding: 10px 20px;
            margin-right: 10px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }

        .btn-valid {
            background: #4CAF50;
            color: white;
        }

        .btn-invalid {
            background: #f44336;
            color: white;
        }

        .validation-status {
            margin-top: 10px;
            font-size: 14px;
        }

        @media (max-width: 900px) {
            .product-card {
                grid-template-columns: 1fr;
            }
        }

            /* AI Section Styles */
            .ai-section {
                background: #fff;
                border-radius: 8px;
                padding: 25px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                margin-bottom: 30px;
            }

            .ai-section h3 {
                font-size: 20px;
                margin-bottom: 15px;
                color: #333;
                display: flex;
                align-items: center;
                gap: 10px;
            }

            .ai-status {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 500;
            }

            .ai-status.online {
                background: #e8f5e9;
                color: #2e7d32;
            }

            .ai-status.offline {
                background: #ffebee;
                color: #c62828;
            }

            .ai-status .dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
            }

            .ai-status.online .dot {
                background: #4CAF50;
                animation: pulse 2s infinite;
            }

            .ai-status.offline .dot {
                background: #f44336;
            }

            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }

            /* AI Search */
            .ai-search-container {
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
            }

            .ai-search-input {
                flex: 1;
                padding: 15px 20px;
                font-size: 16px;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                outline: none;
                transition: border-color 0.2s, box-shadow 0.2s;
            }

            .ai-search-input:focus {
                border-color: #9c27b0;
                box-shadow: 0 0 0 3px rgba(156, 39, 176, 0.1);
            }

            .ai-search-input::placeholder {
                color: #999;
            }

            .ai-search-btn {
                padding: 15px 30px;
                background: linear-gradient(135deg, #9c27b0, #7b1fa2);
                color: #fff;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s;
            }

            .ai-search-btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 4px 15px rgba(156, 39, 176, 0.3);
            }

            .ai-search-btn:disabled {
                background: #ccc;
                cursor: not-allowed;
                transform: none;
                box-shadow: none;
            }

            /* AI Results */
            .ai-results {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                gap: 20px;
                margin-top: 20px;
            }

            .ai-result-card {
                background: #fafafa;
                border-radius: 8px;
                overflow: hidden;
                transition: transform 0.2s, box-shadow 0.2s;
                cursor: pointer;
            }

            .ai-result-card:hover {
                transform: translateY(-4px);
                box-shadow: 0 8px 25px rgba(0,0,0,0.15);
            }

            .ai-result-card img {
                width: 100%;
                height: 200px;
                object-fit: cover;
            }

            .ai-result-card .card-content {
                padding: 15px;
            }

            .ai-result-card .card-title {
                font-size: 14px;
                font-weight: 500;
                margin-bottom: 5px;
                color: #333;
            }

            .ai-result-card .card-price {
                font-size: 16px;
                font-weight: 600;
                color: #000;
            }

            .ai-result-card .card-similarity {
                font-size: 11px;
                color: #9c27b0;
                margin-top: 5px;
            }

            /* Reset to Original Button */
            .reset-metadata-btn {
                background: linear-gradient(135deg, #ef5350, #c62828);
                color: #fff;
                border: none;
                padding: 8px 14px;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s;
                margin-left: 10px;
            }

            .reset-metadata-btn:hover {
                background: linear-gradient(135deg, #f44336, #b71c1c);
                transform: translateY(-1px);
                box-shadow: 0 2px 8px rgba(198, 40, 40, 0.3);
            }

            .reset-metadata-btn:disabled {
                opacity: 0.6;
                cursor: not-allowed;
                transform: none;
            }

            /* AI Generated Tag Styling - Teal/Cyan color */
            .ai-generated-tag {
                background: linear-gradient(135deg, #00bcd4, #0097a7) !important;
                color: #fff !important;
                padding: 6px 12px;
                border-radius: 4px;
                font-size: 13px;
                display: inline-flex;
                align-items: center;
                gap: 5px;
            }

            .ai-generated-tag .ai-badge {
                font-size: 10px;
                opacity: 0.9;
                background: rgba(255,255,255,0.2);
                padding: 1px 4px;
                border-radius: 3px;
            }

            .ai-generated-tag .tag-delete-btn {
                display: none;
                margin-left: 5px;
                background: rgba(255,255,255,0.2);
                border: none;
                color: #fff;
                font-size: 12px;
                cursor: pointer;
                padding: 2px 6px;
                border-radius: 3px;
                line-height: 1;
            }

            .ai-generated-tag .tag-delete-btn:hover {
                background: rgba(255,0,0,0.3);
            }

            .curate-mode .ai-generated-tag .tag-delete-btn {
                display: inline-block;
            }

            .curate-mode .ai-tag-delete {
                display: inline-block;
            }

            .ai-progress {
                display: none;
                align-items: center;
                gap: 10px;
                color: #666;
            }

            .ai-progress.visible {
                display: flex;
            }

            .ai-spinner {
                width: 20px;
                height: 20px;
                border: 3px solid #e0e0e0;
                border-top-color: #9c27b0;
                border-radius: 50%;
                animation: spin 1s linear infinite;
            }

            @keyframes spin {
                to { transform: rotate(360deg); }
            }

            /* AI Chat Widget */
            .ai-chat-container {
                background: #f8f9fa;
                border-radius: 8px;
                padding: 20px;
                margin-top: 20px;
            }

            .ai-chat-messages {
                max-height: 400px;
                overflow-y: auto;
                margin-bottom: 15px;
                padding: 10px;
                background: #fff;
                border-radius: 8px;
                min-height: 200px;
            }

            .ai-chat-message {
                margin-bottom: 15px;
                padding: 12px 15px;
                border-radius: 12px;
                max-width: 85%;
            }

            .ai-chat-message.user {
                background: #e3f2fd;
                margin-left: auto;
                border-bottom-right-radius: 4px;
            }

            .ai-chat-message.assistant {
                background: #f3e5f5;
                margin-right: auto;
                border-bottom-left-radius: 4px;
            }

            .ai-chat-message .role {
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
                margin-bottom: 5px;
                color: #666;
            }

            .ai-chat-input-container {
                display: flex;
                gap: 10px;
            }

            .ai-chat-input {
                flex: 1;
                padding: 12px 15px;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                font-size: 14px;
                outline: none;
            }

            .ai-chat-input:focus {
                border-color: #9c27b0;
            }

            .ai-chat-send {
                padding: 12px 25px;
                background: #9c27b0;
                color: #fff;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 500;
            }

            .ai-chat-send:hover {
                background: #7b1fa2;
            }

            .no-results {
                text-align: center;
                padding: 40px;
                color: #666;
            }
    </style>
</head>
<body>
    <header>
        <h1>ZARA PRODUCT VIEWER</h1>
        <div style="margin-top: 10px;">
            <span class="data-source">{{ '🗄️ Supabase Database' if use_supabase else '📁 Local Files' }}</span>
            <button class="curate-btn" id="curateBtn" onclick="toggleCurateMode()">✏️ Curate</button>
            <span class="curator-selector" id="curatorSelector">
                <select id="curatorSelect" onchange="selectCurator(this.value)">
                    <option value="">Select curator...</option>
                    <option value="Reed">Reed</option>
                    <option value="Gigi">Gigi</option>
                    <option value="Kiki">Kiki</option>
                </select>
            </span>
            <span class="curator-badge" id="curatorBadge" style="display: none;"></span>
        </div>
    </header>

    <div class="container">
        <!-- Category Sidebar -->
        <aside class="category-sidebar" id="categorySidebar">
            <div class="sidebar-header">
                <span class="category-icon">📂</span>
                <h3>Categories</h3>
            </div>
            <ul class="category-list" id="categoryList">
                <li class="category-item all-categories active" data-category="all" onclick="filterByCategory('all')">
                    <span class="category-name">All Products</span>
                    <span class="category-count" id="allCount">0</span>
                </li>
                <!-- Categories will be populated dynamically -->
            </ul>
        </aside>

        <!-- Main Content Area -->
        <div class="main-content">
        <!-- Tab Navigation -->
        <div class="tab-nav">
            <button class="tab-btn active" id="tabProducts" onclick="switchTab('products')">📦 Products</button>
                <button class="tab-btn" id="tabAI" onclick="switchTab('ai')">🤖 AI Assistant</button>
            <button class="tab-btn" id="tabDashboard" onclick="switchTab('dashboard')">📊 Dashboard</button>
        </div>

        <!-- Products Tab Content -->
        <div id="productsTab" class="tab-content active">
            <div class="navigation">
                <button class="nav-btn" id="prevBtn" onclick="navigate(-1)">← Previous</button>
                <span class="counter" id="counter">Loading...</span>
                <button class="nav-btn" id="nextBtn" onclick="navigate(1)">Next →</button>
            </div>

            <div id="productCard" class="product-card">
                <div class="no-data">
                    <h2>Loading products...</h2>
                </div>
            </div>
        </div>

        <!-- AI Tab Content -->
        <div id="aiTab" class="tab-content">
            <div class="ai-section">
                <h3>
                    🔍 Semantic Search
                    <span class="ai-status" id="aiStatus">
                        <span class="dot"></span>
                        <span id="aiStatusText">Checking...</span>
                    </span>
                </h3>
                <p style="color: #666; margin-bottom: 15px;">Search products using natural language. Describe what you're looking for and AI will find matching items.</p>

                <div class="ai-search-container">
                    <input type="text"
                           class="ai-search-input"
                           id="aiSearchInput"
                           placeholder="e.g., 'minimal white t-shirt', 'casual summer outfit', 'formal dark blazer'..."
                           onkeypress="handleAISearchKeypress(event)">
                    <button class="ai-search-btn" id="aiSearchBtn" onclick="performAISearch()">🔍 Search</button>
                </div>

                <div class="ai-progress" id="searchProgress">
                    <div class="ai-spinner"></div>
                    <span>Searching...</span>
                </div>

                <div id="aiSearchResults"></div>
            </div>

            <div class="ai-section">
                <h3>💬 Fashion Assistant</h3>
                <p style="color: #666; margin-bottom: 15px;">Chat with the AI about styling advice, outfit recommendations, and product questions.</p>

                <div class="ai-chat-container">
                    <div class="ai-chat-messages" id="chatMessages">
                        <div class="ai-chat-message assistant">
                            <div class="role">Assistant</div>
                            <div>Hello! I'm your fashion assistant. Ask me about styling advice, outfit combinations, or help finding the perfect items from our catalog.</div>
                        </div>
                    </div>
                    <div class="ai-chat-input-container">
                        <input type="text"
                               class="ai-chat-input"
                               id="chatInput"
                               placeholder="Ask about styling, outfits, or products..."
                               onkeypress="handleChatKeypress(event)">
                        <button class="ai-chat-send" onclick="sendChatMessage()">Send</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- Dashboard Tab Content -->
        <div id="dashboardTab" class="tab-content">
            <div id="dashboardContent">
                <div class="no-data">
                    <h2>Loading dashboard...</h2>
                </div>
            </div>
        </div>
        </div><!-- End main-content -->
    </div>

    <script>
        let products = [];
        let allProducts = [];  // Store all products for filtering
        let filteredProducts = [];  // Currently filtered products
        let currentIndex = 0;
        let currentImageIndex = 0;
        let currentCategory = 'all';  // Track selected category
        const useSupabase = {{ 'true' if use_supabase else 'false' }};

        // Category organization structure - matches Zara's website navigation
        const CATEGORY_STRUCTURE = {
            tops_base: {
                label: 'Base Layer',
                icon: '👕',
                subcategories: {
                    tshirts: { label: 'T-Shirts', icon: '👕', keywords: ['t-shirt', 'tshirt', 'tee', 't shirt', 'basic', 'tank top'] },
                    shirts: { label: 'Shirts', icon: '👔', keywords: ['shirt', 'button', 'oxford', 'poplin', 'linen shirt', 'cotton shirt'] },
                    polos: { label: 'Polo Shirts', icon: '👕', keywords: ['polo'] },
                    tanks: { label: 'Tank Tops', icon: '🎽', keywords: ['tank', 'sleeveless', 'muscle'] }
                }
            },
            tops_mid: {
                label: 'Mid Layer',
                icon: '🧶',
                subcategories: {
                    sweaters: { label: 'Sweaters', icon: '🧶', keywords: ['sweater', 'knit', 'pullover', 'jumper', 'knitwear'] },
                    cardigans: { label: 'Cardigans', icon: '🧶', keywords: ['cardigan'] },
                    quarterzip: { label: 'Quarter Zip', icon: '🧶', keywords: ['quarter zip', 'quarter-zip', 'half zip', 'half-zip'] },
                    hoodies: { label: 'Hoodies', icon: '🧥', keywords: ['hoodie', 'hooded'] },
                    sweatshirts: { label: 'Sweatshirts', icon: '👕', keywords: ['sweatshirt', 'fleece', 'crewneck', 'crew neck', 'french terry'] }
                }
            },
            bottoms: {
                label: 'Bottoms',
                icon: '👖',
                subcategories: {
                    pants: { label: 'Pants', icon: '👖', keywords: ['pant', 'trouser', 'chino', 'jogger', 'cargo', 'slack'] },
                    jeans: { label: 'Jeans', icon: '👖', keywords: ['jean', 'denim'] },
                    shorts: { label: 'Shorts', icon: '🩳', keywords: ['short', 'bermuda'] },
                    swimwear: { label: 'Swimwear', icon: '🩱', keywords: ['swim trunk', 'swim short', 'swimwear', 'bathing', 'beach short', 'board short'] },
                    sweatsuits: { label: 'Sweatsuits', icon: '🏃', keywords: ['sweatsuit', 'tracksuit', 'track pant', 'jogger set'] }
                }
            },
            outerwear: {
                label: 'Outerwear',
                icon: '🧥',
                subcategories: {
                    jackets: { label: 'Jackets', icon: '🧥', keywords: ['jacket', 'bomber', 'windbreaker', 'trucker', 'down jacket', 'leather jacket', 'biker jacket', 'moto jacket'] },
                    coats: { label: 'Coats', icon: '🧥', keywords: ['coat', 'overcoat', 'trench', 'parka', 'puffer', 'quilted', 'padded', 'leather coat'] },
                    blazers: { label: 'Blazers', icon: '🤵', keywords: ['blazer', 'sport coat'] },
                    suits: { label: 'Suits', icon: '🤵', keywords: ['suit'] },
                    overshirts: { label: 'Overshirts', icon: '👔', keywords: ['overshirt', 'shacket', 'shirt jacket'] },
                    vests: { label: 'Vests', icon: '🦺', keywords: ['vest', 'gilet', 'waistcoat', 'bodywarmer'] }
                }
            },
            shoes: {
                label: 'Footwear',
                icon: '👟',
                subcategories: {
                    shoes: { label: 'Shoes', icon: '👟', keywords: ['shoe', 'sneaker', 'loafer', 'derby', 'oxford', 'sandal', 'slipper', 'moccasin', 'espadrille'] },
                    boots: { label: 'Boots', icon: '🥾', keywords: ['boot', 'chelsea', 'ankle boot', 'combat boot'] }
                }
            }
        };

        /**
         * GARMENT TYPE DETECTION
         *
         * This function classifies products based on the PRODUCT NAME only.
         * The category field in the database is UNRELIABLE and should not be trusted.
         *
         * Classification priority (checked in order - first match wins):
         * 1. Bottoms (pants, shorts, jeans, trousers) - very distinctive names
         * 2. Shoes/Boots (footwear, sneakers, boots) - very distinctive names
         * 3. Outerwear (jackets, coats, blazers, leather) - check before tops
         * 4. Mid layer (sweaters, hoodies, sweatshirts, quarter-zip) - check before base
         * 5. Base layer (t-shirts, shirts, polos) - most generic, check last
         */

        // Helper function to check if a word exists as a complete word (not part of another word)
        function hasWord(text, word) {
            // Create a regex that matches the word with word boundaries
            // This prevents "pants" from matching in "participants"
            // Note: Escaping special regex characters in the word
            const escaped = word.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
            const regex = new RegExp('\\\\b' + escaped + '(s|es)?\\\\b', 'i');
            return regex.test(text);
        }

        // Helper function to check if any of the keywords match
        function hasAnyWord(text, keywords) {
            return keywords.some(kw => hasWord(text, kw));
        }

        function classifyProduct(product) {
            // ONLY use the product name for classification - it's the most reliable
            const name = (product.name || '').toLowerCase();

            // ============================================================
            // STEP 1: Check for BOTTOMS first (pants, shorts, jeans, trousers, swimwear)
            // These have very distinctive names that won't conflict with other categories
            // ============================================================

            // Use DB category if it's swimwear (scraped from Zara beachwear)
            if ((product.category || '').toLowerCase() === 'swimwear') {
                return { main: 'bottoms', sub: 'swimwear', displayCategory: 'Swimwear' };
            }
            // DB stores "footwear" for shoe products (generator query); display as Footwear
            if ((product.category || '').toLowerCase() === 'footwear') {
                return { main: 'shoes', sub: 'shoes', displayCategory: 'Footwear' };
            }

            // Check for swimwear by name (swim trunks, beach shorts, etc.) BEFORE shorts
            if (hasAnyWord(name, ['swim trunk', 'swim short', 'swimwear', 'bathing short', 'beach short', 'board short'])) {
                return { main: 'bottoms', sub: 'swimwear', displayCategory: 'Swimwear' };
            }

            // Check for shorts FIRST (before pants, since "shorts" is more specific)
            if (hasAnyWord(name, ['short', 'bermuda'])) {
                // Make sure it's not "short sleeve" which is a top
                if (!hasAnyWord(name, ['sleeve', 'shirt', 'top', 'tee', 't-shirt'])) {
                    return { main: 'bottoms', sub: 'shorts', displayCategory: 'Shorts' };
                }
            }

            // Check for jeans (before generic pants)
            if (hasAnyWord(name, ['jean', 'denim pant', 'denim trouser'])) {
                return { main: 'bottoms', sub: 'jeans', displayCategory: 'Jeans' };
            }

            // Check for sweatsuits/tracksuits
            if (hasAnyWord(name, ['sweatsuit', 'tracksuit', 'track pant', 'jogger set', 'matching set'])) {
                return { main: 'bottoms', sub: 'sweatsuits', displayCategory: 'Sweatsuits' };
            }

            // Check for pants/trousers
            if (hasAnyWord(name, ['pant', 'trouser', 'chino', 'jogger', 'cargo pant', 'dress pant', 'suit pant', 'slack'])) {
                return { main: 'bottoms', sub: 'pants', displayCategory: 'Pants' };
            }

            // ============================================================
            // STEP 2: Check for FOOTWEAR (Shoes & Boots)
            // ============================================================

            // Check boots first (more specific than shoes)
            if (hasAnyWord(name, ['boot', 'chelsea', 'combat boot', 'ankle boot', 'hiking boot'])) {
                return { main: 'shoes', sub: 'boots', displayCategory: 'Boots' };
            }

            // Check for shoes
            if (hasAnyWord(name, ['shoe', 'sneaker', 'loafer', 'derby', 'sandal', 'slipper', 'moccasin', 'espadrille', 'trainer'])) {
                return { main: 'shoes', sub: 'shoes', displayCategory: 'Shoes' };
            }

            // ============================================================
            // STEP 3: Check for OUTERWEAR (jackets, coats, blazers, vests)
            // Check these BEFORE tops because "jacket" might contain other words
            // ============================================================

            // Blazers (specific outerwear)
            if (hasAnyWord(name, ['blazer', 'sport coat', 'sportcoat'])) {
                return { main: 'outerwear', sub: 'blazers', displayCategory: 'Blazers' };
            }

            // Suits (check before generic jacket - "suit jacket" should be suits)
            if (hasWord(name, 'suit') && !hasAnyWord(name, ['sweatsuit', 'tracksuit'])) {
                return { main: 'outerwear', sub: 'suits', displayCategory: 'Suits' };
            }

            // Coats (includes puffers, parkas, trenches)
            if (hasAnyWord(name, ['coat', 'parka', 'puffer', 'trench', 'overcoat', 'topcoat'])) {
                return { main: 'outerwear', sub: 'coats', displayCategory: 'Coats' };
            }

            // Vests/Gilets
            if (hasAnyWord(name, ['vest', 'gilet', 'waistcoat', 'bodywarmer'])) {
                return { main: 'outerwear', sub: 'vests', displayCategory: 'Vests' };
            }

            // Overshirts / Shackets (between shirt and jacket)
            if (hasAnyWord(name, ['overshirt', 'shacket', 'shirt jacket'])) {
                return { main: 'outerwear', sub: 'overshirts', displayCategory: 'Overshirts' };
            }

            // Jackets (general - check after more specific outerwear)
            if (hasAnyWord(name, ['jacket', 'bomber', 'windbreaker', 'anorak', 'trucker', 'down jacket', 'quilted', 'padded'])) {
                return { main: 'outerwear', sub: 'jackets', displayCategory: 'Jackets' };
            }

            // ============================================================
            // STEP 4: Check for MID LAYER tops (sweaters, hoodies, sweatshirts, quarter-zip)
            // Check these BEFORE base layer because "sweatshirt" contains "shirt"
            // ============================================================

            // Quarter-zip (check before sweaters)
            if (hasAnyWord(name, ['quarter zip', 'quarter-zip', 'half zip', 'half-zip', '1/4 zip'])) {
                return { main: 'tops_mid', sub: 'quarterzip', displayCategory: 'Quarter Zip' };
            }

            // Sweatshirts (check BEFORE checking for "shirt")
            if (hasAnyWord(name, ['sweatshirt', 'crewneck sweat', 'crew neck sweat', 'fleece'])) {
                return { main: 'tops_mid', sub: 'sweatshirts', displayCategory: 'Sweatshirts' };
            }

            // Hoodies
            if (hasAnyWord(name, ['hoodie', 'hooded'])) {
                return { main: 'tops_mid', sub: 'hoodies', displayCategory: 'Hoodies' };
            }

            // Cardigans (check before generic sweaters)
            if (hasWord(name, 'cardigan')) {
                return { main: 'tops_mid', sub: 'cardigans', displayCategory: 'Cardigans' };
            }

            // Sweaters/Knits
            if (hasAnyWord(name, ['sweater', 'knit', 'pullover', 'jumper', 'knitwear'])) {
                return { main: 'tops_mid', sub: 'sweaters', displayCategory: 'Sweaters' };
            }

            // ============================================================
            // STEP 5: Check for BASE LAYER tops (t-shirts, shirts, polos, tanks)
            // These are the most generic categories - check last
            // ============================================================

            // T-shirts (check before generic "shirt")
            if (hasAnyWord(name, ['t-shirt', 'tshirt', 'tee'])) {
                return { main: 'tops_base', sub: 'tshirts', displayCategory: 'T-Shirts' };
            }

            // Tank tops
            if (hasAnyWord(name, ['tank', 'sleeveless top', 'muscle tee'])) {
                return { main: 'tops_base', sub: 'tanks', displayCategory: 'Tank Tops' };
            }

            // Polos
            if (hasWord(name, 'polo')) {
                return { main: 'tops_base', sub: 'polos', displayCategory: 'Polo Shirts' };
            }

            // Shirts (most generic - only if nothing else matched)
            if (hasWord(name, 'shirt')) {
                return { main: 'tops_base', sub: 'shirts', displayCategory: 'Shirts' };
            }

            // ============================================================
            // STEP 6: Fallback - use tags_final if available
            // ============================================================
            const tagsFinal = product.tags_final;
            if (tagsFinal && tagsFinal.category) {
                const cat = tagsFinal.category.toLowerCase();
                if (cat === 'bottom') {
                    return { main: 'bottoms', sub: 'pants', displayCategory: 'Pants' };
                }
                if (cat === 'outerwear') {
                    return { main: 'outerwear', sub: 'jackets', displayCategory: 'Jackets' };
                }
                if (cat === 'shoes') {
                    return { main: 'shoes', sub: 'shoes', displayCategory: 'Shoes' };
                }
                if (cat === 'top_mid' || (tagsFinal.top_layer_role === 'mid')) {
                    return { main: 'tops_mid', sub: 'sweaters', displayCategory: 'Sweaters' };
                }
                if (cat === 'top_base' || cat === 'top' || (tagsFinal.top_layer_role === 'base')) {
                    return { main: 'tops_base', sub: 'tshirts', displayCategory: 'T-Shirts' };
                }
            }

            // ============================================================
            // STEP 7: Last resort - uncategorized
            // ============================================================
            return { main: 'other', sub: null, displayCategory: 'Other' };
        }

        // Get the display category name for a product (used for the badge)
        function getDisplayCategory(product) {
            const classification = classifyProduct(product);
            return classification.displayCategory || 'Other';
        }

        // Build category dropdown options HTML for reclassification
        function buildCategoryDropdownOptions(currentSubcategory) {
            let html = '';
            const orderedCategories = ['tops_base', 'tops_mid', 'bottoms', 'outerwear', 'shoes'];

            for (const mainKey of orderedCategories) {
                const mainConfig = CATEGORY_STRUCTURE[mainKey];
                if (!mainConfig) continue;

                html += `<optgroup label="${mainConfig.icon} ${mainConfig.label}">`;
                for (const [subKey, subConfig] of Object.entries(mainConfig.subcategories)) {
                    const selected = (subKey === currentSubcategory) ? 'selected' : '';
                    html += `<option value="${subKey}" ${selected}>${subConfig.label}</option>`;
                }
                html += '</optgroup>';
            }

            // Add "Other" option
            html += `<optgroup label="📦 Other">`;
            html += `<option value="accessories">Accessories</option>`;
            html += `<option value="bags">Bags</option>`;
            html += `<option value="colognes">Colognes</option>`;
            html += `</optgroup>`;

            return html;
        }

        // Handle category reclassification
        async function handleCategoryChange(selectElement) {
            const newCategory = selectElement.value;
            const product = getCurrentProduct();

            if (!product) return;

            // Get the display name for the new category
            let displayName = newCategory;
            for (const [mainKey, mainConfig] of Object.entries(CATEGORY_STRUCTURE)) {
                if (mainConfig.subcategories[newCategory]) {
                    displayName = mainConfig.subcategories[newCategory].label;
                    break;
                }
            }

            console.log(`Reclassifying product ${product.product_id} to: ${newCategory} (${displayName})`);

            try {
                // Update in Supabase
                const response = await fetch('/api/update_product_category', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_id: product.product_id,
                        new_category: newCategory
                    })
                });

                if (response.ok) {
                    // Update local data
                    product.category = newCategory;

                    // Show success notification
                    showNotification(`Category changed to ${displayName}`, 'success');

                    // Rebuild the sidebar to update counts
                    buildCategorySidebar();
                } else {
                    const error = await response.json();
                    showNotification(`Failed to update category: ${error.message}`, 'error');
                }
            } catch (err) {
                console.error('Failed to update category:', err);
                showNotification('Failed to update category', 'error');
            }
        }

        // Helper to get current product
        function getCurrentProduct() {
            if (currentCategory === 'all') {
                return filteredProducts[currentIndex];
            }
            return filteredProducts[currentIndex];
        }

        // Build category sidebar from products data
        function buildCategorySidebar() {
            const categoryList = document.getElementById('categoryList');
            if (!categoryList || !allProducts.length) return;

            // Initialize counts structure dynamically from CATEGORY_STRUCTURE
            const counts = { other: { total: 0 } };
            for (const [mainCat, config] of Object.entries(CATEGORY_STRUCTURE)) {
                counts[mainCat] = { total: 0 };
                for (const subKey of Object.keys(config.subcategories)) {
                    counts[mainCat][subKey] = 0;
                }
            }

            // Classify each product
            allProducts.forEach(product => {
                const { main, sub } = classifyProduct(product);
                if (counts[main]) {
                    counts[main].total++;
                    if (sub && counts[main][sub] !== undefined) {
                        counts[main][sub]++;
                    }
                } else {
                    counts.other.total++;
                }
            });

            // Update "All Products" count
            document.getElementById('allCount').textContent = allProducts.length;

            // Clear existing category items (except "All Products")
            const allCategoryItem = categoryList.querySelector('.all-categories');
            categoryList.innerHTML = '';
            categoryList.appendChild(allCategoryItem);

            // Build organized category structure
            const orderedCategories = ['tops_base', 'tops_mid', 'bottoms', 'outerwear', 'shoes'];

            orderedCategories.forEach(mainCat => {
                const config = CATEGORY_STRUCTURE[mainCat];
                const mainCount = counts[mainCat]?.total || 0;

                // Main category header
                const mainLi = document.createElement('li');
                mainLi.className = 'category-item category-header';
                mainLi.setAttribute('data-category', mainCat);
                mainLi.onclick = () => filterByOrganizedCategory(mainCat, null);
                mainLi.innerHTML = `
                    <span class="category-name">${config.icon} ${config.label}</span>
                    <span class="category-count">${mainCount}</span>
                `;
                categoryList.appendChild(mainLi);

                // Subcategories
                const subEntries = Object.entries(config.subcategories);
                if (subEntries.length > 0) {
                    subEntries.forEach(([subKey, subConfig]) => {
                        const subCount = counts[mainCat]?.[subKey] || 0;
                        const subLi = document.createElement('li');
                        subLi.className = 'category-item subcategory-item';
                        subLi.setAttribute('data-category', `${mainCat}-${subKey}`);
                        subLi.onclick = () => filterByOrganizedCategory(mainCat, subKey);
                        subLi.innerHTML = `
                            <span class="category-name">${subConfig.icon} ${subConfig.label}</span>
                            <span class="category-count">${subCount}</span>
                        `;
                        categoryList.appendChild(subLi);
                    });
                }
            });

            // Add "Other" if there are uncategorized items
            if (counts.other.total > 0) {
                const otherLi = document.createElement('li');
                otherLi.className = 'category-item';
                otherLi.setAttribute('data-category', 'other');
                otherLi.onclick = () => filterByOrganizedCategory('other', null);
                otherLi.innerHTML = `
                    <span class="category-name">📦 Other</span>
                    <span class="category-count">${counts.other.total}</span>
                `;
                categoryList.appendChild(otherLi);
            }
        }

        // Filter by organized category
        function filterByOrganizedCategory(mainCat, subCat) {
            currentCategory = subCat ? `${mainCat}-${subCat}` : mainCat;

            // Update active state in sidebar
            document.querySelectorAll('.category-item').forEach(item => {
                item.classList.remove('active');
                if (item.getAttribute('data-category') === currentCategory) {
                    item.classList.add('active');
                }
            });

            // Filter products
            if (mainCat === 'all') {
                filteredProducts = [...allProducts];
            } else {
                filteredProducts = allProducts.filter(p => {
                    const { main, sub } = classifyProduct(p);
                    if (subCat) {
                        return main === mainCat && sub === subCat;
                    }
                    return main === mainCat;
                });
            }

            // Update products array and reset to first product
            products = filteredProducts;
            currentIndex = 0;
            currentImageIndex = 0;

            if (products.length > 0) {
                displayProduct(0);
            } else {
                document.getElementById('productCard').innerHTML = `
                    <div class="no-data">
                        <h2>No products found</h2>
                        <p>No products in this category</p>
                    </div>
                `;
            }
        }

        // Get icon for category (legacy support)
        function getCategoryIcon(category) {
            const icons = {
                'shirts': '👔',
                't-shirts': '👕',
                'pants': '👖',
                'jeans': '👖',
                'shorts': '🩳',
                'jackets': '🧥',
                'coats': '🧥',
                'suits': '🤵',
                'blazers': '🤵',
                'shoes': '👟',
                'sneakers': '👟',
                'boots': '🥾',
                'hats': '🧢',
                'sweaters': '🧶',
                'hoodies': '🧥',
                'swimwear': '🩱',
                'activewear': '🏃',
                'default': '📦'
            };
            const lowerCategory = category.toLowerCase();
            for (const [key, icon] of Object.entries(icons)) {
                if (lowerCategory.includes(key)) return icon;
            }
            return icons.default;
        }

        // Format category name for display
        function formatCategoryName(category) {
            // Map internal category keys to human-readable names
            const categoryDisplayNames = {
                'all': 'All Products',
                // Base Layer
                'tops_base': 'Base Layer',
                'tops_base-tshirts': 'T-Shirts',
                'tops_base-shirts': 'Shirts',
                'tops_base-polos': 'Polo Shirts',
                'tops_base-tanks': 'Tank Tops',
                // Mid Layer
                'tops_mid': 'Mid Layer',
                'tops_mid-sweaters': 'Sweaters',
                'tops_mid-cardigans': 'Cardigans',
                'tops_mid-quarterzip': 'Quarter Zip',
                'tops_mid-hoodies': 'Hoodies',
                'tops_mid-sweatshirts': 'Sweatshirts',
                // Bottoms
                'bottoms': 'Bottoms',
                'bottoms-pants': 'Pants',
                'bottoms-jeans': 'Jeans',
                'bottoms-shorts': 'Shorts',
                'bottoms-swimwear': 'Swimwear',
                'bottoms-sweatsuits': 'Sweatsuits',
                // Outerwear
                'outerwear': 'Outerwear',
                'outerwear-jackets': 'Jackets',
                'outerwear-coats': 'Coats',
                'outerwear-leather': 'Leather',
                'outerwear-blazers': 'Blazers',
                'outerwear-suits': 'Suits',
                'outerwear-overshirts': 'Overshirts',
                'outerwear-vests': 'Vests',
                // Footwear (DB stores "footwear" for generator queries)
                'shoes': 'Footwear',
                'footwear': 'Footwear',
                'shoes-shoes': 'Shoes',
                'shoes-boots': 'Boots',
                // Other
                'other': 'Other'
            };

            if (categoryDisplayNames[category]) {
                return categoryDisplayNames[category];
            }

            // Fallback: convert snake_case-subcategory to Title Case
            return category
                .split(/[-_]/)
                .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
                .join(' ');
        }

        // Filter products by category (legacy support)
        function filterByCategory(category) {
            if (category === 'all') {
                filterByOrganizedCategory('all', null);
                return;
            }
            // For legacy category names, try to map to organized structure
            filterByOrganizedCategory(category, null);
        }

        async function loadProducts() {
            const counterEl = document.getElementById('counter');
            const cardEl = document.getElementById('productCard');

            function showError(msg) {
                if (counterEl) counterEl.textContent = 'Error';
                if (cardEl) cardEl.innerHTML = `
                    <div class="no-data">
                        <h2>Error loading products</h2>
                        <p>${msg}</p>
                    </div>
                `;
            }

            try {
                const response = await fetch('/api/products');
                if (!response.ok) {
                    const errText = await response.text();
                    showError(`Server returned ${response.status}. ${errText.slice(0, 200)}`);
                    return;
                }

                const data = await response.json();

                // Ensure we got an array (API may return {error: "..."} on failure)
                const list = Array.isArray(data) ? data : ((data && data.products) || []);
                if (!Array.isArray(list)) {
                    showError('Invalid response: expected product list');
                    return;
                }

                // Store all products for filtering
                allProducts = list;
                filteredProducts = [...allProducts];
                products = filteredProducts;

                // Build the category sidebar
                buildCategorySidebar();

                if (products.length > 0) {
                    await displayProduct(0);
                } else {
                    if (cardEl) cardEl.innerHTML = `
                        <div class="no-data">
                            <h2>No products found</h2>
                            <p>${useSupabase ? 'No products in Supabase database. Run: <code>python main.py --supabase</code>' : 'Run the scraper first: <code>python main.py</code>'}</p>
                        </div>
                    `;
                    if (counterEl) counterEl.textContent = 'No products';
                }
            } catch (error) {
                console.error('Error loading products:', error);
                showError(String(error.message || error));
            }
        }

        function getImageUrl(product, index) {
            // For Supabase: show all photos when image_urls_all exists; otherwise just the 2 stored
            if (product._source === 'supabase') {
                const all = product.image_urls_all || [];
                if (all.length > 0 && all[index]) {
                    return all[index];
                }
                if (product.image_urls && product.image_urls[index]) {
                    return product.image_urls[index];
                }
            }
            // For local files, construct the path
            const images = product.images || [];
            if (images[index]) {
                return `/images/${product.category}/${product.product_id}/${images[index]}`;
            }
            return 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="400" height="500" fill="%23ccc"><rect width="100%" height="100%"/><text x="50%" y="50%" text-anchor="middle" fill="%23999">No Image</text></svg>';
        }

        function isImageStored(product, index) {
            if (!product.image_urls_stored_indices || !Array.isArray(product.image_urls_stored_indices)) return false;
            return product.image_urls_stored_indices.indexOf(index) !== -1;
        }

        async function displayProduct(index) {
            if (index < 0 || index >= products.length) return;

            currentIndex = index;
            currentImageIndex = 0;
            const product = products[index];

            // Update counter
            // Update counter - show category filter if active
            const categoryLabel = currentCategory === 'all' ? '' : ` in ${formatCategoryName(currentCategory)}`;
            document.getElementById('counter').textContent = `Product ${index + 1} of ${products.length}${categoryLabel}`;

            // Update navigation buttons
            document.getElementById('prevBtn').disabled = index === 0;
            document.getElementById('nextBtn').disabled = index === products.length - 1;

            // Fetch curated metadata for this product (if using Supabase)
            let curatedTags = [];
            let curatedFit = [];
            let curatedWeight = [];
            let rejectedTags = [];
            let aiGeneratedTags = [];
            let curationStatus = null;
            if (useSupabase) {
                // Fetch curated data
                try {
                    const curatedResponse = await fetch(`/api/curated/${product.product_id}`);
                    const curatedData = await curatedResponse.json();
                    if (Array.isArray(curatedData)) {
                        curatedTags = curatedData.filter(c => c.field_name === 'style_tag');
                        curatedFit = curatedData.filter(c => c.field_name === 'fit');
                        curatedWeight = curatedData.filter(c => c.field_name === 'weight');
                    }
                } catch (error) {
                    console.error('Error fetching curated data:', error);
                }

                // Fetch rejected tags (may fail if table doesn't exist yet)
                try {
                    const rejectedResponse = await fetch(`/api/rejected_tags/${product.product_id}`);
                    const rejectedData = await rejectedResponse.json();
                    if (Array.isArray(rejectedData)) {
                        rejectedTags = rejectedData;
                    }
                } catch (error) {
                    console.warn('Could not fetch rejected tags (table may not exist yet):', error);
                }

                // Fetch AI-generated tags (may fail if table doesn't exist yet)
                try {
                    const aiTagsResponse = await fetch(`/api/ai_tags/${product.product_id}`);
                    const aiTagsData = await aiTagsResponse.json();
                    if (Array.isArray(aiTagsData)) {
                        aiGeneratedTags = aiTagsData.filter(t => t.field_name === 'style_tag');
                    }
                } catch (error) {
                    console.warn('Could not fetch AI-generated tags (table may not exist yet):', error);
                }

                // Fetch curation status
                try {
                    const statusResponse = await fetch(`/api/curation_status/${product.product_id}`);
                    curationStatus = await statusResponse.json();
                } catch (error) {
                    console.warn('Could not fetch curation status:', error);
                }
            }

            // Store for global access
            window.currentCurationStatus = curationStatus;

            // Store original tags for curation history (tags_ai_raw = AI prediction, fallback to tags_final)
            window.originalTagsForCuration = window.originalTagsForCuration || {};
            window.originalTagsForCuration[product.product_id] =
                product.tags_ai_raw != null ? product.tags_ai_raw : product.tags_final;

            // Store rejected tags globally for easy lookup
            window.currentRejectedTags = rejectedTags;

            // Store AI-generated tags globally
            window.currentAIGeneratedTags = aiGeneratedTags;

            // Helper function to check if an inferred tag is rejected
            function isTagRejected(fieldName, fieldValue) {
                return rejectedTags.some(r => r.field_name === fieldName && r.field_value === fieldValue);
            }

            // Helper function to get rejection info for a tag
            function getRejectionInfo(fieldName, fieldValue) {
                return rejectedTags.find(r => r.field_name === fieldName && r.field_value === fieldValue);
            }

            // Build image gallery (use image_urls_all when present so curator sees all photos)
            const images = product.images || [];
            const imageCount = product._source === 'supabase' && (product.image_urls_all || []).length > 0
                ? (product.image_urls_all || []).length
                : (product._source === 'supabase' ? (product.image_urls || []).length : images.length);
            const mainImageSrc = getImageUrl(product, 0);

            const canReselectStored = product._source === 'supabase' && (product.image_urls_all || []).length >= 2;
            let thumbnails = '';
            for (let i = 0; i < imageCount; i++) {
                const imgSrc = getImageUrl(product, i);
                const stored = isImageStored(product, i);
                thumbnails += `
                    <div class="thumbnail-wrap" style="position:relative;display:inline-block;" data-thumb-index="${i}">
                        <img src="${imgSrc}"
                             class="thumbnail ${i === 0 ? 'active' : ''}"
                             onclick="thumbnailClick(${i})"
                             alt="Thumbnail ${i + 1}">
                        ${stored ? '<span class="stored-badge" title="Stored for outfit generator">Stored</span>' : ''}
                    </div>
                `;
            }

            // Build price display
            let priceHtml = '';
            if (product.price) {
                const current = product.price.current;
                const original = product.price.original;
                const discount = product.price.discount_percentage;

                priceHtml = `<span class="current-price">$${current || 'N/A'}</span>`;
                if (original && original > current) {
                    priceHtml += `<span class="original-price">$${original}</span>`;
                }
                if (discount) {
                    priceHtml += `<span class="discount-badge">-${discount}%</span>`;
                }
            }

            // Build clickable color tags that link to color variants
            // First, build a map of color variants for this product
            const currentColor = product.color || '';
            const parentId = product.parent_product_id || product.product_id.split('_')[0];

            const colorTags = (product.colors || []).map(c => {
                // Generate the color slug to find the matching product
                const colorSlug = c.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'unknown';
                const variantId = parentId + '_' + colorSlug;

                // Check if this is the current color
                const isCurrentColor = c.toLowerCase() === currentColor.toLowerCase();

                // Find if the color variant exists in our products
                const variantExists = allProducts.some(p => p.product_id === variantId);

                if (isCurrentColor) {
                    // Current color - highlight it
                    return `<span class="tag" style="background:#4CAF50;color:white;font-weight:bold;" title="Current color">${c}</span>`;
                } else if (variantExists) {
                    // Clickable link to the variant
                    return `<span class="tag color-variant-link" style="cursor:pointer;background:#e3f2fd;color:#1565c0;" data-variant-id="${variantId}" onclick="navigateToColorVariant('${variantId}')" title="Click to view ${c} variant">${c}</span>`;
                } else {
                    // Variant not in database yet
                    return `<span class="tag" style="opacity:0.6;" title="Color variant not scraped yet">${c}</span>`;
                }
            }).join('');

            // Build size tags with availability styling
            // Try to use sizes_availability first (new format with availability), fallback to sizes (old format)
            const sizesAvailability = product.sizes_availability || [];
            const sizesOld = (product.sizes || []).filter(s => s && s.trim() && s !== 'Add');

            let sizeTags = '';
            if (sizesAvailability.length > 0) {
                // New format: [{"size": "M", "available": true, "availability": "in_stock"}, ...]
                sizeTags = sizesAvailability.map(s => {
                    const sizeLabel = typeof s === 'object' ? s.size : s;
                    const isAvailable = typeof s === 'object' ? s.available : true;
                    const availability = typeof s === 'object' ? (s.availability || 'unknown') : 'unknown';

                    // Determine styling and tooltip based on availability
                    let style = '';
                    let tooltip = '';
                    let indicator = '';

                    if (availability === 'out_of_stock' || !isAvailable) {
                        style = 'background: #f5f5f5; color: #999; text-decoration: line-through;';
                        tooltip = 'Out of stock';
                    } else if (availability === 'low_on_stock') {
                        style = 'background: #fff3e0; color: #e65100; border: 1px solid #ffcc80;';
                        tooltip = 'Low stock – only a few left';
                        indicator = '<span style="display: inline-block; width: 6px; height: 6px; background: #ff9800; border-radius: 50%; margin-left: 6px; animation: pulse 1.5s infinite;"></span>';
                    } else {
                        style = 'background: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9;';
                        tooltip = 'In stock';
                    }

                    return `<span class="tag" style="${style} cursor: default; transition: all 0.2s;" title="${tooltip}">${sizeLabel}${indicator}</span>`;
                }).join('');
            } else if (sizesOld.length > 0) {
                // Old format: ["S", "M", "L"]
                sizeTags = sizesOld.map(s => `<span class="tag">${s}</span>`).join('');
            }

            const materialTags = (product.materials || []).map(m => `<span class="tag">${m}</span>`).join('');

            // Parse composition for better display
            // Prefer structured composition data if available, otherwise parse the string
            let compositionHtml = '';

            if (product.composition_structured && product.composition_structured.parts) {
                // Use structured composition data - hierarchical display
                const parts = product.composition_structured.parts;
                compositionHtml = parts.map(part => {
                    const partName = part.name || '';
                    const areasHtml = (part.areas || []).map(area => {
                        const areaName = area.name || '';
                        const components = (area.components || []).map(c =>
                            `<span class="tag" style="background: #f5f5f5; color: #333; font-size: 12px;">${c.percentage} ${c.material}</span>`
                        ).join('');

                        if (areaName) {
                            // Has sub-area name (e.g., MAIN FABRIC, SECONDARY FABRIC)
                            return `
                                <div style="margin-left: 12px; margin-bottom: 8px;">
                                    <div style="font-size: 9px; font-weight: 500; color: #888; margin-bottom: 4px; text-transform: uppercase;">${areaName}</div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 6px;">${components}</div>
                                </div>
                            `;
                        } else {
                            // Direct components under the part
                            return `
                                <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-left: 12px;">${components}</div>
                            `;
                        }
                    }).join('');

                    if (partName) {
                        return `
                            <div style="margin-bottom: 16px;">
                                <div style="font-size: 10px; font-weight: 600; color: #666; margin-bottom: 6px; text-transform: uppercase;">${partName}</div>
                                ${areasHtml}
                            </div>
                        `;
                } else {
                        return areasHtml;
                    }
                }).join('');
            } else if (product.composition) {
                const comp = product.composition;

                // Check if this is a complex shoe-style composition with part names
                // Sort by length descending to match longer parts first (OUTSOLE before SOLE, etc.)
                const partNames = ['OUTSOLE', 'MIDSOLE', 'INSOLE', 'FOOTBED', 'COUNTER', 'TONGUE', 'LINING', 'UPPER', 'OUTER', 'INNER', 'SOLE', 'HEEL', 'TOE', 'MAIN FABRIC', 'SECONDARY FABRIC', 'OUTER SHELL'];

                // Find all part matches with their positions
                let partMatches = [];
                for (const partName of partNames) {
                    // Match part name case-insensitively
                    const regex = new RegExp(partName, 'gi');
                    let match;
                    while ((match = regex.exec(comp)) !== null) {
                        // Check if this position overlaps with an already found (longer) part
                        let overlaps = false;
                        for (const existing of partMatches) {
                            if ((match.index >= existing.start && match.index < existing.end) ||
                                (match.index + partName.length > existing.start && match.index + partName.length <= existing.end)) {
                                overlaps = true;
                                break;
                            }
                        }
                        if (!overlaps) {
                            partMatches.push({
                                name: partName.toUpperCase(),
                                start: match.index,
                                end: match.index + partName.length
                            });
                        }
                    }
                }

                // Sort by position
                partMatches.sort((a, b) => a.start - b.start);

                const hasParts = partMatches.length > 0;

                if (hasParts) {
                    // Parse each section
                    let sections = [];
                    for (let i = 0; i < partMatches.length; i++) {
                        const partName = partMatches[i].name;
                        const startPos = partMatches[i].end;
                        const endPos = (i + 1 < partMatches.length) ? partMatches[i + 1].start : comp.length;

                        let materialsStr = comp.substring(startPos, endPos).trim();
                        // Remove leading colon or space if present
                        materialsStr = materialsStr.replace(/^[:\\s]+/, '');

                        // Parse materials: "37% polyurethane32% polyester" -> ["37% polyurethane", "32% polyester"]
                        const materialList = materialsStr.match(/\d+%\\s*[a-zA-Z][a-zA-Z\\s]*?(?=\d+%|$)/g) || [];
                        const cleanedMaterials = materialList.map(m => m.trim()).filter(m => m);

                        if (cleanedMaterials.length > 0) {
                            sections.push({
                                part: partName,
                                materials: cleanedMaterials
                            });
                        }
                    }

                    if (sections.length > 0) {
                        compositionHtml = sections.map(section => `
                            <div style="margin-bottom: 12px;">
                                <div style="font-size: 10px; font-weight: 600; color: #666; margin-bottom: 6px;">${section.part}</div>
                                <div style="display: flex; flex-wrap: wrap; gap: 6px;">
                                    ${section.materials.map(m => `<span class="tag" style="background: #f5f5f5; color: #333; font-size: 12px;">${m}</span>`).join('')}
                                </div>
                            </div>
                        `).join('');
                    } else {
                        // Fallback to simple display
                        compositionHtml = `<p style="color: #333; font-size: 14px; font-weight: 500; margin: 0; font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', monospace;">${comp}</p>`;
                    }
                } else {
                    // Simple composition like "100% cotton" or "49% polyamide, 29% polyester"
                    // Parse into individual materials for pill display
                    const materials = comp.split(/,\\s*/).map(m => m.trim()).filter(m => m);
                    if (materials.length > 1) {
                        compositionHtml = `
                            <div style="display: flex; flex-wrap: wrap; gap: 6px;">
                                ${materials.map(m => `<span class="tag" style="background: #f5f5f5; color: #333; font-size: 12px;">${m}</span>`).join('')}
                    </div>
                `;
                    } else {
                        compositionHtml = `<p style="color: #333; font-size: 14px; font-weight: 500; margin: 0; font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', monospace;">${comp}</p>`;
                    }
                }
            }

            // Badge for main image: show when current image is one of the 2 stored in DB
            const mainImageStored = isImageStored(product, 0);

            // Render card (tags_final = ReFitd canonical tags only; no inferred style_tags/fit/formality/weight)
            document.getElementById('productCard').innerHTML = `
                <div class="image-section">
                    <div class="main-image-wrap" style="position:relative;">
                        <img id="mainImage" src="${mainImageSrc}" alt="${product.name}" class="main-image">
                        <span id="mainImageStoredBadge" class="stored-badge-main" style="display:${mainImageStored ? 'block' : 'none'};">Stored for outfit generator</span>
                    </div>
                    <div class="thumbnail-row">
                        ${thumbnails}
                    </div>
                    ${canReselectStored ? `
                    <div class="reselect-stored-section" style="margin-top:12px;">
                        <button type="button" class="reselect-stored-btn" onclick="enterReselectStoredMode()" style="padding:6px 12px;font-size:12px;background:#f5f5f5;border:1px solid #ddd;border-radius:6px;cursor:pointer;">Reselect stored images</button>
                        <div id="reselectStoredPanel" style="display:none;margin-top:10px;padding:10px;background:#f0f7ff;border:1px solid #90caf9;border-radius:8px;">
                            <p style="margin:0 0 8px 0;font-size:13px;color:#1565c0;">Click 2 images above to choose which to store for the outfit generator.</p>
                            <span id="reselectStoredCount" style="font-size:12px;color:#666;">Selected: 0/2</span>
                            <div style="margin-top:8px;display:flex;gap:8px;">
                                <button type="button" id="reselectStoredSave" disabled style="padding:6px 14px;font-size:12px;background:#4CAF50;color:white;border:none;border-radius:6px;cursor:pointer;" onclick="saveReselectStored()">Save these 2</button>
                                <button type="button" style="padding:6px 14px;font-size:12px;background:#fff;border:1px solid #ccc;border-radius:6px;cursor:pointer;" onclick="cancelReselectStoredMode()">Cancel</button>
                            </div>
                        </div>
                    </div>
                    ` : ''}
                </div>

                <div class="metadata-section">
                    ${curateMode ? `
                        <div class="category-dropdown-wrapper">
                            <select class="category-dropdown" onchange="handleCategoryChange(this)">
                                ${buildCategoryDropdownOptions(classifyProduct(product).sub || product.category)}
                            </select>
                        </div>
                    ` : `
                        <span class="category-badge">${getDisplayCategory(product)}</span>
                    `}
                    <h2 class="product-name">${product.name}</h2>
                    <p class="product-brand" style="font-size: 13px; color: #666; margin: 4px 0 0 0; text-transform: uppercase; letter-spacing: 0.5px;">${product.brand || product.brandName || 'Zara'}</p>
                    <p class="product-id">ID: ${product.product_id}</p>

                    <div class="price-section">
                        ${priceHtml}
                    </div>

                    ${product.tags_final ? `
                        <div class="ai-section" style="margin-top: 20px; padding: 20px; background: linear-gradient(135deg, #fafafa 0%, #f5f5f5 100%); border-radius: 12px; border: 1px solid #e0e0e0;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;">
                                <span style="font-size: 11px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 1px;">ReFitd Canonical Tags</span>
                                <span style="font-size: 10px; font-weight: 600; padding: 4px 10px; border-radius: 10px; background: ${
                                    product.curation_status_refitd === 'approved' ? '#4CAF50' :
                                    product.curation_status_refitd === 'needs_review' ? '#FF9800' :
                                    product.curation_status_refitd === 'needs_fix' ? '#f44336' : '#bdbdbd'
                                }; color: white; text-transform: uppercase; letter-spacing: 0.5px;">${product.curation_status_refitd || 'pending'}</span>
                            </div>

                            <!-- Style Identity (array field) -->
                            <div style="margin-bottom: 20px;">
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 10px;">Style Identity</div>
                                <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                    ${(product.tags_final.style_identity || []).map(s => `
                                        <span style="display: inline-flex; align-items: center; background: #1a1a1a; color: white; font-weight: 500; padding: 8px 16px; border-radius: 6px; font-size: 13px; gap: 8px;">
                                            ${s}
                                            <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagRemove('style_identity', '${s.replace(/'/g, "\\'")}')" title="Remove ${s}" style="display: none; background: none; border: none; color: rgba(255,255,255,0.7); cursor: pointer; padding: 0; font-size: 16px; line-height: 1; margin-left: 4px;">×</button>
                                        </span>
                                    `).join('')}
                                    ${(product.tags_final.deleted_tags?.style_identity || []).map(s => {
                                        // Handle both old format (string) and new format (object)
                                        const tagValue = typeof s === 'string' ? s : s.value;
                                        const reason = typeof s === 'string' ? '' : (s.reason || '');
                                        const curator = typeof s === 'string' ? '' : (s.curator || '');
                                        const tooltip = reason ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : 'Rejected');
                                        return `
                                            <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #3d1a1a; color: #999; font-weight: 500; padding: 8px 16px; border-radius: 6px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #6d3a3a; cursor: help;" title="${tooltip}">
                                                ${tagValue}
                                                ${reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none;">(${reason.substring(0, 30)}${reason.length > 30 ? '...' : ''})</span>` : ''}
                                                <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagAdd('style_identity', '${tagValue.replace(/'/g, "\\'")}')" title="Restore ${tagValue}" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                            </span>
                                        `;
                                    }).join('')}
                                    ${(product.tags_final.style_identity || []).length === 0 && !(product.tags_final.deleted_tags?.style_identity || []).length ? `<span style="color: #ccc; font-size: 12px;">None</span>` : ''}
                                    <div class="canonical-tag-add-input" style="display: none;">
                                        <select style="padding: 8px 12px; border: 1px dashed #ccc; border-radius: 6px; font-size: 13px; background: white;" onchange="if(this.value){handleCanonicalTagAdd('style_identity', this.value); this.value='';}">
                                            <option value="">Add style...</option>
                                            <option value="minimal">Minimal</option>
                                            <option value="classic">Classic</option>
                                            <option value="preppy">Preppy</option>
                                            <option value="workwear">Workwear</option>
                                            <option value="streetwear">Streetwear</option>
                                            <option value="rugged">Rugged</option>
                                            <option value="tailoring">Tailoring</option>
                                            <option value="elevated-basics">Elevated Basics</option>
                                            <option value="normcore">Normcore</option>
                                            <option value="sporty">Sporty</option>
                                            <option value="outdoorsy">Outdoorsy</option>
                                            <option value="western">Western</option>
                                            <option value="vintage">Vintage</option>
                                            <option value="grunge">Grunge</option>
                                            <option value="punk">Punk</option>
                                            <option value="utilitarian">Utilitarian</option>
                                        </select>
                                    </div>
                                </div>
                            </div>

                            <!-- Formality (single-value field) -->
                            <div style="margin-bottom: 16px; background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Formality</div>
                                <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                    ${product.tags_final.formality ? `
                                        <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                            ${product.tags_final.formality}
                                            <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('formality', null)" title="Remove formality" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                        </span>
                                    ` : `<span style="color: #ccc; font-size: 12px;">Not set</span>`}
                                    ${product.tags_final.deleted_tags?.formality ? (() => {
                                        const dt = product.tags_final.deleted_tags.formality;
                                        const tagValue = typeof dt === 'string' ? dt : (dt?.value || '');
                                        const reason = typeof dt === 'string' ? '' : (dt?.reason || '');
                                        const curator = typeof dt === 'string' ? '' : (dt?.curator || '');
                                        const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                        const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                        return `
                                            <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                ${tagValue}${reasonSnippet}
                                                <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagSet('formality', '${tagValue}')" title="Restore formality" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                            </span>
                                        `;
                                    })() : ''}
                                    <div class="canonical-tag-add-input" style="display: none;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white;" onchange="if(this.value){handleCanonicalTagSet('formality', this.value); this.value='';}">
                                            <option value="">Set formality...</option>
                                            <option value="athletic">Athletic</option>
                                            <option value="casual">Casual</option>
                                            <option value="smart-casual">Smart Casual</option>
                                            <option value="business-casual">Business Casual</option>
                                            <option value="formal">Formal</option>
                                        </select>
                                    </div>
                                </div>
                            </div>

                            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px;">
                                <!-- Fit (single-value field) - NOT for shoes -->
                                <div style="background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Fit</div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                        ${product.tags_final.fit ? `
                                            <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                                ${product.tags_final.fit}
                                                <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('fit', null)" title="Remove fit" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                            </span>
                                        ` : `<span style="color: #ccc; font-size: 12px;">${product.tags_final.shoe_type ? 'N/A' : 'Not set'}</span>`}
                                        ${product.tags_final.deleted_tags?.fit ? (() => {
                                            const dt = product.tags_final.deleted_tags.fit;
                                            const tagValue = typeof dt === 'string' ? dt : (dt?.value || '');
                                            const reason = typeof dt === 'string' ? '' : (dt?.reason || '');
                                            const curator = typeof dt === 'string' ? '' : (dt?.curator || '');
                                            const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                            const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                            return `
                                                <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                    ${tagValue}${reasonSnippet}
                                                    <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagSet('fit', '${tagValue}')" title="Restore fit" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                                </span>
                                            `;
                                        })() : ''}
                                    </div>
                                    <div class="canonical-tag-add-input" style="display: none; margin-top: 8px;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white; width: 100%;" onchange="if(this.value){handleCanonicalTagSet('fit', this.value); this.value='';}">
                                            <option value="">Set fit...</option>
                                            <option value="skinny">Skinny</option>
                                            <option value="slim">Slim</option>
                                            <option value="regular">Regular</option>
                                            <option value="relaxed">Relaxed</option>
                                            <option value="baggy">Baggy</option>
                                            <option value="oversized">Oversized</option>
                                        </select>
                                    </div>
                                </div>
                                <!-- Silhouette (single-value field) -->
                                <div style="background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Silhouette</div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                        ${product.tags_final.silhouette ? `
                                            <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                                ${product.tags_final.silhouette}
                                                <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('silhouette', null)" title="Remove silhouette" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                            </span>
                                        ` : `<span style="color: #ccc; font-size: 12px;">${product.tags_final.shoe_type ? 'N/A' : 'Not set'}</span>`}
                                        ${product.tags_final.deleted_tags?.silhouette ? (() => {
                                            const dt = product.tags_final.deleted_tags.silhouette;
                                            const tagValue = typeof dt === 'string' ? dt : (dt?.value || '');
                                            const reason = typeof dt === 'string' ? '' : (dt?.reason || '');
                                            const curator = typeof dt === 'string' ? '' : (dt?.curator || '');
                                            const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                            const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                            return `
                                                <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                    ${tagValue}${reasonSnippet}
                                                    <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagSet('silhouette', '${tagValue}')" title="Restore silhouette" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                                </span>
                                            `;
                                        })() : ''}
                                    </div>
                                    <div class="canonical-tag-add-input" style="display: none; margin-top: 8px;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white; width: 100%;" onchange="if(this.value){handleCanonicalTagSet('silhouette', this.value); this.value='';}">
                                            <option value="">Set silhouette...</option>
                                            <optgroup label="Bottoms">
                                                <option value="straight">Straight</option>
                                                <option value="tapered">Tapered</option>
                                                <option value="wide">Wide</option>
                                            </optgroup>
                                            <optgroup label="Tops & Outerwear">
                                                <option value="neutral">Neutral</option>
                                                <option value="relaxed">Relaxed</option>
                                                <option value="boxy">Boxy</option>
                                                <option value="structured">Structured</option>
                                                <option value="tailored">Tailored</option>
                                                <option value="longline">Longline</option>
                                            </optgroup>
                                        </select>
                                    </div>
                                </div>
                                <!-- Length (single-value field) - NOT for shoes -->
                                <div style="background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Length</div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                        ${product.tags_final.length ? `
                                            <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                                ${product.tags_final.length}
                                                <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('length', null)" title="Remove length" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                            </span>
                                        ` : `<span style="color: #ccc; font-size: 12px;">${product.tags_final.shoe_type ? 'N/A' : 'Not set'}</span>`}
                                        ${product.tags_final.deleted_tags?.length ? (() => {
                                            const dt = product.tags_final.deleted_tags.length;
                                            const tagValue = typeof dt === 'string' ? dt : (dt?.value || '');
                                            const reason = typeof dt === 'string' ? '' : (dt?.reason || '');
                                            const curator = typeof dt === 'string' ? '' : (dt?.curator || '');
                                            const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                            const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                            return `
                                                <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                    ${tagValue}${reasonSnippet}
                                                    <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagSet('length', '${tagValue}')" title="Restore length" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                                </span>
                                            `;
                                        })() : ''}
                                    </div>
                                    <div class="canonical-tag-add-input" style="display: none; margin-top: 8px;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white; width: 100%;" onchange="if(this.value){handleCanonicalTagSet('length', this.value); this.value='';}">
                                            <option value="">Set length...</option>
                                            <option value="cropped">Cropped</option>
                                            <option value="regular">Regular</option>
                                            <option value="long">Long</option>
                                        </select>
                                    </div>
                                </div>
                                <!-- Pattern (single-value field) -->
                                <div style="background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Pattern</div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                        ${product.tags_final.pattern ? `
                                            <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                                ${product.tags_final.pattern}
                                                <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('pattern', null)" title="Remove pattern" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                            </span>
                                        ` : `<span style="color: #ccc; font-size: 12px;">Not set</span>`}
                                        ${product.tags_final.deleted_tags?.pattern ? (() => {
                                            const dt = product.tags_final.deleted_tags.pattern;
                                            const tagValue = typeof dt === 'string' ? dt : (dt?.value || '');
                                            const reason = typeof dt === 'string' ? '' : (dt?.reason || '');
                                            const curator = typeof dt === 'string' ? '' : (dt?.curator || '');
                                            const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                            const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                            return `
                                                <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                    ${tagValue}${reasonSnippet}
                                                    <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagSet('pattern', '${tagValue}')" title="Restore pattern" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                                </span>
                                            `;
                                        })() : ''}
                                    </div>
                                    <div class="canonical-tag-add-input" style="display: none; margin-top: 8px;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white; width: 100%;" onchange="if(this.value){handleCanonicalTagSet('pattern', this.value); this.value='';}">
                                            <option value="">Set pattern...</option>
                                            <option value="solid">Solid</option>
                                            <option value="stripe">Stripe</option>
                                            <option value="check">Check</option>
                                            <option value="textured">Textured</option>
                                        </select>
                                    </div>
                                </div>
                            </div>

                            <!-- Context (array field) -->
                            <div style="margin-top: 16px; background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">Context</div>
                                <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                    ${(product.tags_final.context || []).map(c => `
                                        <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px;">
                                            ${c}
                                            <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagRemove('context', '${c.replace(/'/g, "\\'")}')" title="Remove ${c}" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                        </span>
                                    `).join('')}
                                    ${(product.tags_final.deleted_tags?.context || []).map(c => {
                                        // Handle both old format (string) and new format (object)
                                        const tagValue = typeof c === 'string' ? c : (c?.value || '');
                                        const reason = typeof c === 'string' ? '' : (c?.reason || '');
                                        const curator = typeof c === 'string' ? '' : (c?.curator || '');
                                        const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                        const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                        return `
                                            <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                ${tagValue}${reasonSnippet}
                                                <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagAdd('context', '${tagValue.replace(/'/g, "\\\\'")}')" title="Restore ${tagValue}" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                            </span>
                                        `;
                                    }).join('')}
                                    ${(product.tags_final.context || []).length === 0 && !(product.tags_final.deleted_tags?.context || []).length ? `<span style="color: #ccc; font-size: 12px;">None</span>` : ''}
                                    <div class="canonical-tag-add-input" style="display: none;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white;" onchange="if(this.value){handleCanonicalTagAdd('context', this.value); this.value='';}">
                                            <option value="">Add context...</option>
                                            <option value="everyday">Everyday</option>
                                            <option value="work-appropriate">Work Appropriate</option>
                                            <option value="travel">Travel</option>
                                            <option value="evening">Evening</option>
                                            <option value="weekend">Weekend</option>
                                        </select>
                                    </div>
                                </div>
                            </div>

                            <!-- Construction Details (array field) -->
                            <div style="margin-top: 12px; background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">Construction</div>
                                <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                    ${(product.tags_final.construction_details || []).map(d => `
                                        <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px;">
                                            ${d}
                                            <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagRemove('construction_details', '${d.replace(/'/g, "\\'")}')" title="Remove ${d}" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                        </span>
                                    `).join('')}
                                    ${(product.tags_final.deleted_tags?.construction_details || []).map(c => {
                                        // Handle both old format (string) and new format (object)
                                        const tagValue = typeof c === 'string' ? c : (c?.value || '');
                                        const reason = typeof c === 'string' ? '' : (c?.reason || '');
                                        const curator = typeof c === 'string' ? '' : (c?.curator || '');
                                        const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                        const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                        return `
                                            <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                ${tagValue}${reasonSnippet}
                                                <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagAdd('construction_details', '${tagValue.replace(/'/g, "\\\\'")}')" title="Restore ${tagValue}" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                            </span>
                                        `;
                                    }).join('')}
                                    ${(product.tags_final.construction_details || []).length === 0 && !(product.tags_final.deleted_tags?.construction_details || []).length ? `<span style="color: #ccc; font-size: 12px;">None</span>` : ''}
                                    <div class="canonical-tag-add-input" style="display: none;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white;" onchange="if(this.value){handleCanonicalTagAdd('construction_details', this.value); this.value='';}">
                                            <option value="">Add detail...</option>
                                            <optgroup label="Bottoms">
                                                <option value="pleated">Pleated</option>
                                                <option value="flat-front">Flat Front</option>
                                                <option value="cargo">Cargo</option>
                                                <option value="drawstring">Drawstring</option>
                                                <option value="elastic-waist">Elastic Waist</option>
                                            </optgroup>
                                            <optgroup label="Tops & Outerwear">
                                                <option value="structured-shoulder">Structured Shoulder</option>
                                                <option value="dropped-shoulder">Dropped Shoulder</option>
                                            </optgroup>
                                        </select>
                                    </div>
                                </div>
                            </div>

                            <!-- Pairing Tags (array field) -->
                            <div style="margin-top: 12px; background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">Pairing</div>
                                <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                    ${(product.tags_final.pairing_tags || []).map(p => `
                                        <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px;">
                                            ${p}
                                            <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagRemove('pairing_tags', '${p.replace(/'/g, "\\'")}')" title="Remove ${p}" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                        </span>
                                    `).join('')}
                                    ${(product.tags_final.deleted_tags?.pairing_tags || []).map(p => {
                                        // Handle both old format (string) and new format (object)
                                        const tagValue = typeof p === 'string' ? p : (p?.value || '');
                                        const reason = typeof p === 'string' ? '' : (p?.reason || '');
                                        const curator = typeof p === 'string' ? '' : (p?.curator || '');
                                        const tooltip = reason && curator ? `Rejected by ${curator}: ${reason}` : (curator ? `Rejected by ${curator}` : (reason ? `Reason: ${reason}` : 'Rejected'));
                                        const reasonSnippet = reason ? `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${reason.length > 30 ? reason.substring(0, 30) + '...' : reason})</span>` : '';
                                        return `
                                            <span class="deleted-tag-display" style="display: inline-flex; align-items: center; background: #fee; color: #999; padding: 6px 12px; border-radius: 4px; font-size: 13px; gap: 8px; text-decoration: line-through; border: 1px dashed #fcc; cursor: help;" title="${tooltip}">
                                                ${tagValue}${reasonSnippet}
                                                <button class="canonical-tag-restore-btn" onclick="handleCanonicalTagAdd('pairing_tags', '${tagValue.replace(/'/g, "\\\\'")}')" title="Restore ${tagValue}" style="display: none; background: none; border: none; color: #4caf50; cursor: pointer; padding: 0; font-size: 12px; line-height: 1;">↩</button>
                                            </span>
                                        `;
                                    }).join('')}
                                    ${(product.tags_final.pairing_tags || []).length === 0 && !(product.tags_final.deleted_tags?.pairing_tags || []).length ? `<span style="color: #ccc; font-size: 12px;">None</span>` : ''}
                                    <div class="canonical-tag-add-input" style="display: none;">
                                        <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px; background: white;" onchange="if(this.value){handleCanonicalTagAdd('pairing_tags', this.value); this.value='';}">
                                            <option value="">Add pairing...</option>
                                            <option value="neutral-base">Neutral Base</option>
                                            <option value="statement-piece">Statement Piece</option>
                                            <option value="easy-dress-up">Easy Dress Up</option>
                                            <option value="easy-dress-down">Easy Dress Down</option>
                                            <option value="high-versatility">High Versatility</option>
                                        </select>
                                    </div>
                                </div>
                            </div>

                            <!-- Shoe-specific fields -->
                            ${product.tags_final.shoe_type || product.tags_final.profile || product.tags_final.closure ? `
                                <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px;">Shoe Details</div>
                                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;">
                                        ${product.tags_final.shoe_type ? `
                                            <div style="background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Type</div>
                                                <span style="display: inline-flex; align-items: center; background: #1a1a1a; color: white; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                                    ${product.tags_final.shoe_type}
                                                    <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('shoe_type', null)" title="Remove shoe type" style="display: none; background: none; border: none; color: rgba(255,255,255,0.7); cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                                </span>
                                            </div>
                                        ` : ''}
                                        ${product.tags_final.profile ? `
                                            <div style="background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Profile</div>
                                                <span style="display: inline-flex; align-items: center; background: #1a1a1a; color: white; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                                    ${product.tags_final.profile}
                                                    <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('profile', null)" title="Remove profile" style="display: none; background: none; border: none; color: rgba(255,255,255,0.7); cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                                </span>
                                            </div>
                                        ` : ''}
                                        ${product.tags_final.closure ? `
                                            <div style="background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Closure</div>
                                                <span style="display: inline-flex; align-items: center; background: #1a1a1a; color: white; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                                    ${product.tags_final.closure}
                                                    <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('closure', null)" title="Remove closure" style="display: none; background: none; border: none; color: rgba(255,255,255,0.7); cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                                </span>
                                            </div>
                                        ` : ''}
                                    </div>
                                </div>
                            ` : ''}

                            <!-- Top Layer Role (only for tops) -->
                            ${product.tags_final.top_layer_role ? `
                                <div style="margin-top: 12px; background: white; padding: 14px 16px; border-radius: 8px; border: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Top Layer Role</div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                                        <span style="display: inline-flex; align-items: center; background: #f5f5f5; color: #333; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; gap: 8px;">
                                            ${product.tags_final.top_layer_role === 'base' ? 'Base Layer' : 'Mid Layer'}
                                            <button class="canonical-tag-delete-btn" onclick="handleCanonicalTagSet('top_layer_role', null)" title="Remove layer role" style="display: none; background: none; border: none; color: #999; cursor: pointer; padding: 0; font-size: 14px; line-height: 1;">×</button>
                                        </span>
                                        <div class="canonical-tag-add-input" style="display: none;">
                                            <select style="padding: 6px 10px; border: 1px dashed #ccc; border-radius: 4px; font-size: 12px;" onchange="if(this.value){handleCanonicalTagSet('top_layer_role', this.value); this.value='';}">
                                                <option value="">Set layer...</option>
                                                <option value="base">Base Layer</option>
                                                <option value="mid">Mid Layer</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>
                            ` : ''}

                            ${product.tag_policy_version ? `
                                <div style="margin-top: 16px; padding-top: 12px; border-top: 1px solid #eee; font-size: 11px; color: #bbb;">
                                    Policy: ${product.tag_policy_version}
                                </div>
                            ` : ''}
                        </div>
                    ` : ''}

                    <!-- Product Details Section -->
                    <div class="product-details-grid" style="margin-top: 24px; display: grid; gap: 20px;">

                    ${product.description ? `
                            <div class="detail-card" style="background: #fafafa; border-radius: 12px; padding: 20px; border: 1px solid #eee;">
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px;">Description</div>
                                <p style="color: #333; line-height: 1.7; font-size: 14px; margin: 0;">${product.description}</p>
                            </div>
                    ` : ''}

                        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px;">
                    ${colorTags ? `
                                <div class="detail-card" style="background: #fafafa; border-radius: 12px; padding: 20px; border: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px;">Colors</div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px;">${colorTags}</div>
                                </div>
                    ` : ''}

                    ${sizeTags ? `
                                <div class="detail-card" style="background: #fafafa; border-radius: 12px; padding: 20px; border: 1px solid #eee;">
                                    <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px;">
                                        Sizes
                                        <span style="font-weight: 400; font-size: 9px; color: #bbb; text-transform: none; letter-spacing: 0; margin-left: 6px;">(hover for stock)</span>
                                    </div>
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px;">${sizeTags}</div>
                                    ${product.sizes_checked_at ? `
                                        <div style="margin-top: 12px; font-size: 10px; font-style: italic; color: #bbb;">
                                            Updated ${new Date(product.sizes_checked_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} at ${new Date(product.sizes_checked_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
                                        </div>
                    ` : ''}
                                </div>
                            ` : ''}
                        </div>

                        ${product.composition || materialTags ? `
                            <div class="detail-card" style="background: #fafafa; border-radius: 12px; padding: 20px; border: 1px solid #eee;">
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px;">Composition</div>
                                ${compositionHtml}
                                ${materialTags && !product.composition ? `
                                    <div style="display: flex; flex-wrap: wrap; gap: 8px;">${materialTags}</div>
                                ` : ''}
                            </div>
                    ` : ''}

                        <div class="detail-card" style="background: #fafafa; border-radius: 12px; padding: 16px 20px; border: 1px solid #eee; display: flex; align-items: center; justify-content: space-between;">
                            <div>
                                <div style="font-size: 10px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;">Source</div>
                                <a href="${product.url}" target="_blank" style="color: #1a1a1a; text-decoration: none; font-size: 13px; font-weight: 500; display: flex; align-items: center; gap: 6px;">
                                    <span style="max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">zara.com</span>
                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.5;"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                                </a>
                            </div>
                            <a href="${product.url}" target="_blank" style="background: #1a1a1a; color: white; text-decoration: none; font-size: 12px; font-weight: 500; padding: 8px 16px; border-radius: 6px; transition: all 0.2s;" onmouseover="this.style.background='#333'" onmouseout="this.style.background='#1a1a1a'">View on Zara →</a>
                        </div>

                    </div>

                    <div class="validation-section" style="margin-top: 24px;">
                        <h3 class="section-title">Curation Status</h3>
                        <div id="curationStatusArea">
                            ${curationStatus && curationStatus.status === 'complete' ? `
                                <span class="curation-status-badge complete">✓ Curated by ${curationStatus.curator}</span>
                                ${curationStatus.notes ? `<p style="font-size:12px;color:#666;margin-top:5px;">Notes: ${curationStatus.notes}</p>` : ''}
                            ` : `
                                <span class="curation-status-badge pending">⏳ Pending Curation</span>
                            `}
                        </div>
                        <div id="curationFormArea" style="margin-top: 15px;"></div>
                        <div id="curationButtonArea" style="margin-top: 15px;"></div>
                    </div>

                    <div class="danger-zone" style="margin-top: 30px; padding: 15px; border: 1px solid #ffcdd2; border-radius: 8px; background: #fff5f5;">
                        <h3 class="section-title" style="color: #c62828; margin-top: 0;">⚠️ Danger Zone</h3>
                        <p style="font-size: 12px; color: #666; margin-bottom: 10px;">Permanently delete this product from the database.</p>
                        <button onclick="deleteProduct('${product.product_id}')"
                                style="background: #f44336; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px;"
                                onmouseover="this.style.background='#d32f2f'"
                                onmouseout="this.style.background='#f44336'">
                            🗑️ Delete Product
                        </button>
                    </div>

                    <p class="scraped-time">Scraped: ${new Date(product.scraped_at).toLocaleString()}</p>
                </div>
            `;
        }

        function thumbnailClick(index) {
            if (window.storedImageSelectionMode) {
                toggleStoredSelection(index);
            } else {
                changeImage(index);
            }
        }

        function enterReselectStoredMode() {
            window.storedImageSelectionMode = true;
            window.selectedStoredIndices = [];
            const panel = document.getElementById('reselectStoredPanel');
            const btn = document.querySelector('.reselect-stored-btn');
            if (panel) panel.style.display = 'block';
            if (btn) btn.style.display = 'none';
            updateReselectStoredUI();
        }

        function cancelReselectStoredMode() {
            window.storedImageSelectionMode = false;
            window.selectedStoredIndices = [];
            const panel = document.getElementById('reselectStoredPanel');
            const btn = document.querySelector('.reselect-stored-btn');
            if (panel) panel.style.display = 'none';
            if (btn) btn.style.display = 'inline-block';
            document.querySelectorAll('.thumbnail.selected-for-storage').forEach(el => el.classList.remove('selected-for-storage'));
        }

        function toggleStoredSelection(index) {
            const sel = window.selectedStoredIndices || [];
            const i = sel.indexOf(index);
            if (i !== -1) {
                sel.splice(i, 1);
            } else if (sel.length < 2) {
                sel.push(index);
                sel.sort((a, b) => a - b);
            }
            window.selectedStoredIndices = sel;
            document.querySelectorAll('.thumbnail-row .thumbnail-wrap').forEach((wrap, idx) => {
                const img = wrap.querySelector('.thumbnail');
                if (!img) return;
                if (sel.indexOf(idx) !== -1) img.classList.add('selected-for-storage');
                else img.classList.remove('selected-for-storage');
            });
            updateReselectStoredUI();
        }

        function updateReselectStoredUI() {
            const sel = window.selectedStoredIndices || [];
            const countEl = document.getElementById('reselectStoredCount');
            const saveBtn = document.getElementById('reselectStoredSave');
            if (countEl) countEl.textContent = 'Selected: ' + sel.length + '/2';
            if (saveBtn) saveBtn.disabled = sel.length !== 2;
        }

        async function saveReselectStored() {
            const sel = (window.selectedStoredIndices || []).slice(0, 2);
            if (sel.length !== 2) return;
            const product = products[currentIndex];
            if (!product || !product.product_id) return;
            const saveBtn = document.getElementById('reselectStoredSave');
            if (saveBtn) saveBtn.disabled = true;
            try {
                const response = await fetch('/api/products/' + product.product_id + '/stored-images', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ stored_indices: sel })
                });
                const data = await response.json();
                if (data.error) {
                    alert('Failed to update stored images: ' + data.error);
                    if (saveBtn) saveBtn.disabled = false;
                    return;
                }
                cancelReselectStoredMode();
                const productId = product.product_id;
                await loadProducts();
                const idx = products.findIndex(p => p.product_id === productId);
                if (idx !== -1) {
                    currentIndex = idx;
                    await displayProduct(idx);
                }
            } catch (e) {
                alert('Error: ' + e.message);
                if (saveBtn) saveBtn.disabled = false;
            }
        }

        function changeImage(index) {
            currentImageIndex = index;
            const product = products[currentIndex];
            document.getElementById('mainImage').src = getImageUrl(product, index);

            const badge = document.getElementById('mainImageStoredBadge');
            if (badge) {
                badge.style.display = isImageStored(product, index) ? 'block' : 'none';
            }

            // Update active thumbnail
            document.querySelectorAll('.thumbnail').forEach((thumb, i) => {
                thumb.classList.toggle('active', i === index);
            });
        }

        async function navigate(direction) {
            const newIndex = currentIndex + direction;
            if (newIndex >= 0 && newIndex < products.length) {
                await displayProduct(newIndex);
                if (curateMode && currentCurator) {
                    showCurateInputs();
                }
            }
        }

        async function navigateToColorVariant(variantId) {
            const variantIndex = products.findIndex(p => p.product_id === variantId);
            if (variantIndex !== -1) {
                await displayProduct(variantIndex);
                if (curateMode && currentCurator) {
                    showCurateInputs();
                }
                document.getElementById('productCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                console.warn('Color variant not found:', variantId);
                alert(`Color variant "${variantId}" not found in the product database.`);
            }
        }

        async function deleteProduct(productId) {
            // Show confirmation dialog
            const productName = products[currentIndex]?.name || productId;
            const confirmed = confirm(`Are you sure you want to delete this product?\n\n"${productName}"\n(ID: ${productId})\n\nThis action cannot be undone.`);

            if (!confirmed) return;

            try {
                const response = await fetch(`/api/products/${productId}`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' }
                });

                const result = await response.json();

                if (response.ok && result.success) {
                    // Remove from local products array
                    const deletedIndex = products.findIndex(p => p.product_id === productId);
                    if (deletedIndex !== -1) {
                        products.splice(deletedIndex, 1);
                    }

                    // Show success message
                    alert(`✓ Product deleted successfully!\n\nImages deleted: ${result.images_deleted || 0}`);

                    // Navigate to next product or reload
                    if (products.length === 0) {
                        document.getElementById('productCard').innerHTML = `
                            <div class="no-data">
                                <h2>No products remaining</h2>
                                <p>All products have been deleted. Run the scraper to add more.</p>
                            </div>
                        `;
                        document.getElementById('counter').textContent = 'No products';
                    } else {
                        // Adjust current index if needed
                        if (currentIndex >= products.length) {
                            currentIndex = products.length - 1;
                        }
                        displayProduct(currentIndex);
                    }
                } else {
                    alert(`❌ Failed to delete product:\n${result.error || 'Unknown error'}`);
                }
            } catch (error) {
                console.error('Error deleting product:', error);
                alert(`❌ Error deleting product:\n${error.message}`);
            }
        }

        function markValid(index) {
            document.getElementById('validationStatus').textContent = '✓ Marked as VALID';
            document.getElementById('validationStatus').style.color = '#4CAF50';
            console.log(`Product ${products[index].product_id} marked as valid`);
        }

        function markInvalid(index) {
            document.getElementById('validationStatus').textContent = '✗ Marked as INVALID';
            document.getElementById('validationStatus').style.color = '#f44336';
            console.log(`Product ${products[index].product_id} marked as invalid`);
        }

        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') navigate(-1);
            if (e.key === 'ArrowRight') navigate(1);
        });

        // ============================================
        // DELETED TAG HELPERS
        // ============================================

        // Helper to get value from deleted tag (handles both old string format and new object format)
        function getDeletedTagValue(deletedTag) {
            return typeof deletedTag === 'string' ? deletedTag : (deletedTag?.value || '');
        }

        // Helper to get rejection reason from deleted tag
        function getDeletedTagReason(deletedTag) {
            return typeof deletedTag === 'string' ? '' : (deletedTag?.reason || '');
        }

        // Helper to get curator from deleted tag
        function getDeletedTagCurator(deletedTag) {
            return typeof deletedTag === 'string' ? '' : (deletedTag?.curator || '');
        }

        // Helper to build tooltip for deleted tag
        function getDeletedTagTooltip(deletedTag) {
            const reason = getDeletedTagReason(deletedTag);
            const curator = getDeletedTagCurator(deletedTag);
            if (reason && curator) return `Rejected by ${curator}: ${reason}`;
            if (curator) return `Rejected by ${curator}`;
            if (reason) return `Reason: ${reason}`;
            return 'Rejected';
        }

        // Helper to render rejection reason snippet (for inline display)
        function renderRejectionReason(deletedTag) {
            const reason = getDeletedTagReason(deletedTag);
            if (!reason) return '';
            const truncated = reason.length > 30 ? reason.substring(0, 30) + '...' : reason;
            return `<span style="font-size: 10px; color: #e57373; font-style: italic; text-decoration: none; margin-left: 4px;">(${truncated})</span>`;
        }

        // ============================================
        // CURATE MODE FUNCTIONALITY
        // ============================================
        let curateMode = false;
        let currentCurator = null;

        const curatorColors = {
            'Reed': { bg: '#4CAF50', class: 'curator-reed' },
            'Gigi': { bg: '#9C27B0', class: 'curator-gigi' },
            'Kiki': { bg: '#E91E63', class: 'curator-kiki' }
        };

        function renderCuratedTagsInline(curatedTags) {
            if (!curatedTags || curatedTags.length === 0) {
                return '';
            }

            return curatedTags.map(tag => {
                const colorInfo = curatorColors[tag.curator] || { bg: '#999' };
                return `<span class="tag-container">
                    <span class="curated-tag" style="background: ${colorInfo.bg};" data-type="curated" data-field="${tag.field_name}" data-value="${tag.field_value}" data-curator="${tag.curator}">
                        ${tag.field_value} <span class="curator-name">(${tag.curator})</span>
                    </span>
                    <button class="tag-delete-btn" onclick="handleCuratedTagDelete('${tag.field_name}', '${tag.field_value}', '${tag.curator}')" title="Delete curated tag">×</button>
                </span>`;
            }).join('');
        }

        // Render AI-generated tags with teal/cyan color
        function renderAIGeneratedTagsInline(aiTags) {
            if (!aiTags || aiTags.length === 0) {
                return '';
            }

            return aiTags.map(tag => {
                return `<span class="tag-container">
                    <span class="ai-generated-tag" style="background: linear-gradient(135deg, #00bcd4, #0097a7); color: #fff; padding: 6px 12px; border-radius: 4px; font-size: 13px; display: inline-flex; align-items: center; gap: 5px;" data-type="ai-generated" data-field="${tag.field_name}" data-value="${tag.field_value}">
                        ${tag.field_value} <span class="ai-badge" style="font-size: 10px; opacity: 0.9; background: rgba(255,255,255,0.2); padding: 1px 4px; border-radius: 3px;">🤖 AI</span>
                    </span>
                    <button class="tag-delete-btn ai-tag-delete" onclick="event.stopPropagation(); handleAITagDelete('${tag.field_name}', '${tag.field_value}')" title="Delete AI-generated tag">×</button>
                </span>`;
            }).join('');
        }

        function renderCuratedTags(curatedTags) {
            if (!curatedTags || curatedTags.length === 0) {
                return '';
            }

            const tagsHtml = curatedTags.map(tag => {
                const colorInfo = curatorColors[tag.curator] || { bg: '#999' };
                return `<span class="curated-tag" style="background: ${colorInfo.bg};">
                    ${tag.field_value} <span class="curator-name">(${tag.curator})</span>
                </span>`;
            }).join('');

            return `
                <h3 class="section-title" style="margin-top: 15px;">Curated Tags</h3>
                <div class="tag-list">${tagsHtml}</div>
            `;
        }

        function toggleCurateMode() {
            const btn = document.getElementById('curateBtn');
            const selector = document.getElementById('curatorSelector');

            if (!curateMode) {
                // Entering curate mode - show curator selector
                selector.classList.add('visible');
                btn.textContent = '❌ Exit Curate';
                btn.classList.add('active');
                document.body.classList.add('curate-mode');
                curateMode = true;
            } else {
                // Exiting curate mode
                exitCurateMode();
            }
        }

        function exitCurateMode() {
            const btn = document.getElementById('curateBtn');
            const selector = document.getElementById('curatorSelector');
            const badge = document.getElementById('curatorBadge');

            selector.classList.remove('visible');
            badge.style.display = 'none';
            btn.textContent = '✏️ Curate';
            btn.classList.remove('active');
            document.getElementById('curatorSelect').value = '';
            document.body.classList.remove('curate-mode');

            curateMode = false;
            currentCurator = null;

            // Hide canonical tag curation controls
            hideCurateInputs();

            // Re-render the product to hide curate inputs
            if (products.length > 0) {
                displayProduct(currentIndex);
            }
        }

        function hideCurateInputs() {
            // Hide canonical tag delete buttons and add inputs
            document.querySelectorAll('.canonical-tag-delete-btn').forEach(btn => {
                btn.style.display = 'none';
            });
            document.querySelectorAll('.canonical-tag-add-input').forEach(input => {
                input.style.display = 'none';
            });
        }

        async function selectCurator(curator) {
            if (!curator) {
                currentCurator = null;
                document.getElementById('curatorBadge').style.display = 'none';
                return;
            }

            currentCurator = curator;
            const badge = document.getElementById('curatorBadge');
            const colorInfo = curatorColors[curator];

            badge.textContent = `Curating as: ${curator}`;
            badge.className = `curator-badge ${colorInfo.class}`;
            badge.style.display = 'inline-block';

            // Re-render the product to show curate inputs (await since displayProduct is async)
            await displayProduct(currentIndex);

            // Show the curate input after render
            showCurateInputs();
        }

        function showCurateInputs() {
            if (!currentCurator) return;

            const colorInfo = curatorColors[currentCurator];

            // Show/hide canonical tag delete buttons, add inputs, and restore buttons
            document.querySelectorAll('.canonical-tag-delete-btn').forEach(btn => {
                btn.style.display = 'inline';
            });
            document.querySelectorAll('.canonical-tag-add-input').forEach(input => {
                input.style.display = 'inline-block';
            });
            document.querySelectorAll('.canonical-tag-restore-btn').forEach(btn => {
                btn.style.display = 'inline';
            });

            // Style Tags input
            const styleInputContainer = document.getElementById('curateStyleTagInput');
            if (styleInputContainer) {
                styleInputContainer.innerHTML = `
                    <div class="curate-input-wrapper">
                        <input type="text"
                               class="curate-input"
                               id="newStyleTagInput"
                               placeholder="Add new style tag... (press Enter)"
                               onkeypress="handleCurateKeypress(event, 'style_tag', 'styleTagsList')"
                               style="border-color: ${colorInfo.bg};">
                    </div>
                `;
            }

            // Fit input
            const fitInputContainer = document.getElementById('curateFitInput');
            if (fitInputContainer) {
                fitInputContainer.innerHTML = `
                    <div class="curate-input-wrapper">
                        <input type="text"
                               class="curate-input"
                               id="newFitInput"
                               placeholder="Add fit value... (e.g., slim, relaxed, oversized)"
                               onkeypress="handleCurateKeypress(event, 'fit', 'fitTagsList')"
                               style="border-color: ${colorInfo.bg};">
                    </div>
                `;
            }

            // Weight input
            const weightInputContainer = document.getElementById('curateWeightInput');
            if (weightInputContainer) {
                weightInputContainer.innerHTML = `
                    <div class="curate-input-wrapper">
                        <input type="text"
                               class="curate-input"
                               id="newWeightInput"
                               placeholder="Add weight value... (e.g., light, medium, heavy)"
                               onkeypress="handleCurateKeypress(event, 'weight', 'weightTagsList')"
                               style="border-color: ${colorInfo.bg};">
                    </div>
                `;
            }

            // Curation form (notes, error types, confidence, include in training) and Mark Complete button
            const curationFormArea = document.getElementById('curationFormArea');
            const curationButtonArea = document.getElementById('curationButtonArea');
            const curationStatus = window.currentCurationStatus;
            if (curationButtonArea) {
                if (curationStatus && curationStatus.status === 'complete') {
                    if (curationFormArea) curationFormArea.innerHTML = '';
                    curationButtonArea.innerHTML = `
                        <button class="complete-btn undo" onclick="unmarkProductComplete()">↩ Undo Completion</button>
                    `;
                } else {
                    if (curationFormArea && curateMode) {
                        populateCurationForm(curationFormArea);
                    } else if (curationFormArea) {
                        curationFormArea.innerHTML = '';
                    }
                    curationButtonArea.innerHTML = `
                        <button class="complete-btn" onclick="markProductComplete()">✓ Mark as Complete</button>
                    `;
                }
            }
        }

        const ERROR_TYPE_OPTIONS = [
            { value: 'overtagging', label: 'Overtagging (too many tags)' },
            { value: 'undertagging', label: 'Undertagging (missing important tags)' },
            { value: 'wrong_style_identity', label: 'Wrong style identity' },
            { value: 'wrong_construction', label: 'Wrong construction details' },
            { value: 'wrong_fit', label: 'Wrong fit/silhouette' },
            { value: 'wrong_formality', label: 'Wrong formality' },
            { value: 'low_confidence', label: 'Low confidence in original tags' }
        ];

        const CURATION_NOTES_DELIMITER = '\n\n[Add additional notes below]\n\n';

        function buildTagChangesSummary(tagsFinal) {
            if (!tagsFinal || typeof tagsFinal !== 'object') return '';
            const lines = [];
            const dt = tagsFinal.deleted_tags || {};
            for (const [field, entries] of Object.entries(dt)) {
                const items = Array.isArray(entries) ? entries : [entries];
                for (const item of items) {
                    const val = typeof item === 'object' ? (item.value || item.tag) : item;
                    const reason = typeof item === 'object' && item.reason ? `: ${item.reason}` : '';
                    if (val) lines.push(`- Removed '${val}' from ${field}${reason}`);
                }
            }
            const at = tagsFinal.added_tags || {};
            for (const [field, entries] of Object.entries(at)) {
                const items = Array.isArray(entries) ? entries : [entries];
                for (const item of items) {
                    const val = typeof item === 'object' ? (item.value || item.tag) : item;
                    const reason = typeof item === 'object' && item.reason ? `: ${item.reason}` : '';
                    if (val) lines.push(`- Added '${val}' to ${field}${reason}`);
                }
            }
            const mt = tagsFinal.modified_tags || {};
            for (const [field, entry] of Object.entries(mt)) {
                if (typeof entry !== 'object') continue;
                const fromVal = entry.from;
                const toVal = entry.to;
                const reason = entry.reason ? `: ${entry.reason}` : '';
                if (fromVal != null && toVal != null) lines.push(`- Changed ${field} from '${fromVal}' to '${toVal}'${reason}`);
            }
            if (lines.length === 0) return '';
            return 'Tag Changes:\n' + lines.join('\n');
        }

        function inferErrorTypesFromChanges(tagsFinal) {
            if (!tagsFinal || typeof tagsFinal !== 'object') return [];
            const types = [];
            const dt = tagsFinal.deleted_tags || {};
            const mt = tagsFinal.modified_tags || {};
            const hasIn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
            let removedCount = 0;
            for (const entries of Object.values(dt)) {
                const n = Array.isArray(entries) ? entries.length : (entries ? 1 : 0);
                removedCount += n;
            }
            if (removedCount >= 2) types.push('overtagging');
            const at = tagsFinal.added_tags || {};
            const addedCount = Object.values(at).reduce((n, entries) => n + (Array.isArray(entries) ? entries.length : (entries ? 1 : 0)), 0);
            if (addedCount >= 1) types.push('undertagging');
            if (hasIn(dt, 'construction_details') || hasIn(mt, 'construction_details')) types.push('wrong_construction');
            if (hasIn(dt, 'style_identity') || hasIn(mt, 'style_identity')) types.push('wrong_style_identity');
            if (hasIn(dt, 'fit') || hasIn(dt, 'silhouette') || hasIn(mt, 'fit') || hasIn(mt, 'silhouette')) types.push('wrong_fit');
            if (hasIn(dt, 'formality') || hasIn(mt, 'formality')) types.push('wrong_formality');
            return types;
        }

        function preserveCurationNotesUserContent() {
            const product = products[currentIndex];
            if (!product) return;
            const el = document.getElementById('curationNotesInput');
            if (!el) return;
            const val = el.value || '';
            const idx = val.indexOf(CURATION_NOTES_DELIMITER);
            const userPart = idx >= 0 ? val.slice(idx + CURATION_NOTES_DELIMITER.length) : '';
            product._curationNotesUserContent = userPart.trim();
        }

        async function populateCurationForm(container) {
            const product = products[currentIndex];
            if (!product) return;
            const originalTags = (window.originalTagsForCuration || {})[product.product_id];
            const correctedTags = product.tags_final || {};
            let inferredErrorTypes = [];
            if (originalTags != null && useSupabase) {
                try {
                    const res = await fetch('/api/curation_preview', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ original_tags: originalTags, corrected_tags: correctedTags })
                    });
                    const data = await res.json();
                    if (data.error_types) inferredErrorTypes = data.error_types;
                } catch (e) { console.warn('Could not fetch curation preview:', e); }
            }
            const autoErrorTypes = inferErrorTypesFromChanges(correctedTags);
            const mergedErrorTypes = [...new Set([...inferredErrorTypes, ...autoErrorTypes])];
            const summary = buildTagChangesSummary(correctedTags);
            const userNotes = product._curationNotesUserContent || '';
            const notesValue = summary
                ? summary + CURATION_NOTES_DELIMITER + userNotes
                : userNotes;
            const checks = ERROR_TYPE_OPTIONS.map(o =>
                `<label style="display:block;margin:4px 0;font-size:13px;cursor:pointer;">
                    <input type="checkbox" class="curation-error-type" value="${o.value}" ${mergedErrorTypes.indexOf(o.value) >= 0 ? 'checked' : ''}> ${o.label}
                </label>`
            ).join('');
            container.innerHTML = `
                <div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; background: #fafafa;">
                    <p style="font-weight: 600; margin: 0 0 12px; font-size: 14px;">Curation Feedback</p>
                    <div style="margin-bottom: 14px;">
                        <label style="display:block;font-size: 12px; color: #666; margin-bottom: 4px;">General Curation Notes</label>
                        <textarea id="curationNotesInput" placeholder="Tag changes auto-summarized above; add extra notes below." rows="4" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;font-size:13px;resize:vertical;"></textarea>
                    </div>
                    <div style="margin-bottom: 14px;">
                        <label style="display:block;font-size: 12px; color: #666; margin-bottom: 6px;">What was wrong with the AI tags?</label>
                        <div style="max-height: 140px; overflow-y: auto;">${checks}</div>
                    </div>
                    <div style="margin-bottom: 14px;">
                        <label style="display:block;font-size: 12px; color: #666; margin-bottom: 6px;">Confidence in corrections</label>
                        <div style="display:flex;flex-wrap:wrap;gap:12px 16px;">
                            ${[5,4,3,2,1].map(n => {
                                const labels = { 5: 'Very confident', 4: 'Confident', 3: 'Somewhat confident', 2: 'Unsure', 1: 'Guessing' };
                                return `<label style="font-size:13px;cursor:pointer;"><input type="radio" name="curationConfidence" value="${n}" ${n === 4 ? 'checked' : ''}> ${n} - ${labels[n]}</label>`;
                            }).join('')}
                        </div>
                    </div>
                    <div style="margin-bottom: 0;">
                        <label style="display:block;font-size: 13px; cursor: pointer;">
                            <input type="checkbox" id="curationIncludeTraining" checked> Include this correction in AI training data
                        </label>
                    </div>
                </div>
            `;
            const notesEl = container.querySelector('#curationNotesInput');
            if (notesEl && notesValue) notesEl.value = notesValue;
        }

        function handleCurateKeypress(event, fieldName, tagsListId) {
            if (event.key === 'Enter') {
                const input = event.target;
                const tagValue = input.value.trim();

                if (tagValue && currentCurator) {
                    addCuratedField(tagValue, fieldName, tagsListId);
                    input.value = '';
                }
            }
        }

        async function addCuratedField(tagValue, fieldName, tagsListId) {
            const product = products[currentIndex];
            const colorInfo = curatorColors[currentCurator];

            // Add the tag to the display immediately
            const tagsList = document.getElementById(tagsListId);

            // Remove "Not specified" placeholder if present
            const placeholder = tagsList.querySelector('span[style*="color:#999"]');
            if (placeholder) {
                placeholder.remove();
            }

            // Create the new curated tag element
            const newTag = document.createElement('span');
            newTag.className = 'curated-tag';
            newTag.style.background = colorInfo.bg;
            newTag.innerHTML = `${tagValue} <span class="curator-name">(${currentCurator})</span>`;
            tagsList.appendChild(newTag);

            // Save to database
            try {
                const response = await fetch('/api/curated', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_id: product.product_id,
                        field_name: fieldName,
                        field_value: tagValue,
                        curator: currentCurator
                    })
                });
                const result = await response.json();
                if (result.success) {
                    console.log(`✓ Saved curated ${fieldName}: "${tagValue}" by ${currentCurator}`);
                } else {
                    console.error('Failed to save:', result.error);
                }
            } catch (error) {
                console.error('Error saving curated field:', error);
            }
        }

        // ============================================
        // TAG DELETION FUNCTIONALITY
        // ============================================

        function handleTagDeleteClick(button) {
            const fieldName = button.dataset.field;
            const fieldValue = button.dataset.value;
            const isRejected = button.dataset.rejected === 'true';
            const reasoning = button.dataset.reasoning || '';

            handleInferredTagDelete(fieldName, fieldValue, reasoning, isRejected);
        }

        // ============================================
        // TAG FEEDBACK MODAL (removal, addition, change)
        // ============================================

        let tagFeedbackModalResolve = null;

        function showTagFeedbackModal(mode, fieldName, value, oldValue) {
            return new Promise((resolve) => {
                tagFeedbackModalResolve = resolve;
                const titleEl = document.getElementById('tagFeedbackTitle');
                const descEl = document.getElementById('tagFeedbackDescription');
                const labelEl = document.getElementById('tagFeedbackReasonLabel');
                const categoryWrap = document.getElementById('tagFeedbackCategoryWrap');
                const confirmBtn = document.getElementById('tagFeedbackConfirm');
                const fn = fieldName.replace(/_/g, ' ');

                if (mode === 'remove') {
                    titleEl.textContent = 'Tag Removal Feedback';
                    descEl.innerHTML = `You're removing <strong style="color: #e74c3c;">"${value}"</strong> from <strong>${fn}</strong>. Please provide feedback to help improve AI tagging.`;
                    labelEl.textContent = 'Why is this tag incorrect?';
                    document.getElementById('tagFeedbackReason').placeholder = "e.g., 'This is casual, not work-appropriate' or 'The fit is actually slim, not regular'";
                    categoryWrap.style.display = 'block';
                    document.getElementById('tagFeedbackCategory').value = 'incorrect_value';
                    confirmBtn.textContent = 'Remove Tag';
                    confirmBtn.style.background = '#e74c3c';
                } else if (mode === 'add') {
                    titleEl.textContent = 'Tag Addition Feedback';
                    descEl.innerHTML = `You're adding <strong style="color: #4CAF50;">"${value}"</strong> to <strong>${fn}</strong>.`;
                    labelEl.textContent = 'Why are you adding this tag? (optional)';
                    document.getElementById('tagFeedbackReason').placeholder = "e.g., 'Clear preppy details in the design'";
                    categoryWrap.style.display = 'none';
                    confirmBtn.textContent = 'Add Tag';
                    confirmBtn.style.background = '#4CAF50';
                } else if (mode === 'change') {
                    titleEl.textContent = 'Tag Change Feedback';
                    descEl.innerHTML = `Changing <strong>${fn}</strong> from <strong style="color: #e74c3c;">"${oldValue}"</strong> to <strong style="color: #2196F3;">"${value}"</strong>.`;
                    labelEl.textContent = 'Why did you change this? (optional)';
                    document.getElementById('tagFeedbackReason').placeholder = "e.g., 'Item fits more loosely than slim suggests'";
                    categoryWrap.style.display = 'none';
                    confirmBtn.textContent = 'Save Change';
                    confirmBtn.style.background = '#2196F3';
                }

                document.getElementById('tagFeedbackReason').value = '';
                document.getElementById('tagFeedbackModal').style.display = 'flex';
                setTimeout(() => document.getElementById('tagFeedbackReason').focus(), 100);
            });
        }

        function closeTagFeedbackModal(confirmed) {
            document.getElementById('tagFeedbackModal').style.display = 'none';
            if (tagFeedbackModalResolve) {
                if (confirmed) {
                    const reason = document.getElementById('tagFeedbackReason').value.trim();
                    const category = document.getElementById('tagFeedbackCategory').value;
                    tagFeedbackModalResolve({ confirmed: true, reason: reason || null, category });
                } else {
                    tagFeedbackModalResolve({ confirmed: false });
                }
                tagFeedbackModalResolve = null;
            }
        }

        function showTagRemovalModal(fieldName, value) {
            return showTagFeedbackModal('remove', fieldName, value);
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && document.getElementById('tagFeedbackModal').style.display === 'flex') {
                closeTagFeedbackModal(false);
            }
        });

        // ============================================
        // CANONICAL TAG CURATION FUNCTIONS
        // ============================================

        async function handleCanonicalTagAdd(fieldName, value) {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first to add tags.');
                return;
            }

            const product = products[currentIndex];
            if (!value || !value.trim()) {
                return;
            }

            const feedback = await showTagFeedbackModal('add', fieldName, value.trim());
            if (!feedback.confirmed) return;

            try {
                const response = await fetch(`/api/canonical_tags/${product.product_id}/field`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        field_name: fieldName,
                        action: 'add',
                        value: value.trim(),
                        curator: currentCurator,
                        feedback_reason: feedback.reason
                    })
                });

                const result = await response.json();
                if (result.success) {
                    console.log(`✓ Added canonical tag: "${value}" to ${fieldName}`);
                    if (product.tags_final) product.tags_final = result.tags_final;
                    preserveCurationNotesUserContent();
                    await displayProduct(currentIndex);
                    showCurateInputs();
                } else {
                    console.error('Failed to add:', result.error);
                    alert('Failed to add tag: ' + result.error);
                }
            } catch (error) {
                console.error('Error adding canonical tag:', error);
                alert('Error adding tag');
            }
        }

        async function handleCanonicalTagRemove(fieldName, value) {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first to remove tags.');
                return;
            }

            const product = products[currentIndex];

            // Show feedback modal instead of simple confirm
            const feedback = await showTagRemovalModal(fieldName, value);
            if (!feedback.confirmed) {
                return;
            }

            try {
                const response = await fetch(`/api/canonical_tags/${product.product_id}/field`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        field_name: fieldName,
                        action: 'remove',
                        value: value,
                        curator: currentCurator,
                        feedback_reason: feedback.reason,
                        feedback_category: feedback.category
                    })
                });

                const result = await response.json();
                if (result.success) {
                    console.log(`✓ Removed canonical tag: "${value}" from ${fieldName} (reason: ${feedback.reason || 'none provided'})`);
                    if (product.tags_final) product.tags_final = result.tags_final;
                    preserveCurationNotesUserContent();
                    await displayProduct(currentIndex);
                    showCurateInputs();
                } else {
                    console.error('Failed to remove:', result.error);
                    alert('Failed to remove tag: ' + result.error);
                }
            } catch (error) {
                console.error('Error removing canonical tag:', error);
                alert('Error removing tag');
            }
        }

        async function handleCanonicalTagSet(fieldName, value) {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first to edit tags.');
                return;
            }

            const product = products[currentIndex];
            const currentValue = product.tags_final?.[fieldName];

            // If removing a tag (value is null), show removal feedback modal
            let feedback = { reason: null, category: null };
            if (value === null && currentValue) {
                const modalResult = await showTagRemovalModal(fieldName, currentValue);
                if (!modalResult.confirmed) return;
                feedback = { reason: modalResult.reason, category: modalResult.category };
            } else if (value !== null && value !== '' && currentValue && String(currentValue).trim() !== String(value).trim()) {
                // Changing from one value to another - show change feedback modal
                const modalResult = await showTagFeedbackModal('change', fieldName, value.trim(), currentValue);
                if (!modalResult.confirmed) return;
                feedback = { reason: modalResult.reason, category: modalResult.category };
            }

            try {
                const response = await fetch(`/api/canonical_tags/${product.product_id}/field`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        field_name: fieldName,
                        action: 'set',
                        value: value ? value.trim() : null,
                        curator: currentCurator,
                        feedback_reason: feedback.reason,
                        feedback_category: feedback.category
                    })
                });

                const result = await response.json();
                if (result.success) {
                    console.log(`✓ Set canonical tag: ${fieldName} = "${value}"${feedback.reason ? ` (reason: ${feedback.reason})` : ''}`);
                    if (product.tags_final) product.tags_final = result.tags_final;
                    preserveCurationNotesUserContent();
                    await displayProduct(currentIndex);
                    showCurateInputs();
                } else {
                    console.error('Failed to set:', result.error);
                    alert('Failed to set tag: ' + result.error);
                }
            } catch (error) {
                console.error('Error setting canonical tag:', error);
                alert('Error setting tag');
            }
        }

        async function handleCuratedTagDelete(fieldName, fieldValue, curator) {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first to delete tags.');
                return;
            }

            const product = products[currentIndex];

            if (!confirm(`Delete curated tag "${fieldValue}" added by ${curator}?`)) {
                return;
            }

            try {
                const response = await fetch('/api/curated', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_id: product.product_id,
                        field_name: fieldName,
                        field_value: fieldValue,
                        curator: curator
                    })
                });

                const result = await response.json();
                if (result.success || result.error === undefined) {
                    console.log(`✓ Deleted curated tag: "${fieldValue}" by ${curator}`);
                    // Refresh the display
                    await displayProduct(currentIndex);
                    showCurateInputs();
                } else {
                    console.error('Failed to delete:', result.error);
                    alert('Failed to delete tag: ' + result.error);
                }
            } catch (error) {
                console.error('Error deleting curated tag:', error);
                alert('Error deleting tag');
            }
        }

        // Handle deletion of AI-generated tags
        async function handleAITagDelete(fieldName, fieldValue) {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first to delete tags.');
                return;
            }

            const product = products[currentIndex];

            if (!confirm(`Delete AI-generated tag "${fieldValue}"?`)) {
                return;
            }

            try {
                const response = await fetch('/api/ai_tags', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_id: product.product_id,
                        field_name: fieldName,
                        field_value: fieldValue
                    })
                });

                const result = await response.json();
                if (result.success || result.error === undefined) {
                    console.log(`✓ Deleted AI-generated tag: "${fieldValue}"`);
                    // Refresh the display
                    await displayProduct(currentIndex);
                    showCurateInputs();
                } else {
                    console.error('Failed to delete:', result.error);
                    alert('Failed to delete AI tag: ' + result.error);
                }
            } catch (error) {
                console.error('Error deleting AI-generated tag:', error);
                alert('Error deleting AI tag');
            }
        }

        async function handleInferredTagDelete(fieldName, fieldValue, reasoning, isCurrentlyRejected) {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first to manage tags.');
                return;
            }

            const product = products[currentIndex];

            if (isCurrentlyRejected) {
                // Undo rejection - restore the tag
                if (!confirm(`Restore tag "${fieldValue}"? This will undo the rejection.`)) {
                    return;
                }

                try {
                    const response = await fetch('/api/rejected_tags', {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            product_id: product.product_id,
                            field_name: fieldName,
                            field_value: fieldValue
                        })
                    });

                    const result = await response.json();
                    if (result.success || result.error === undefined) {
                        console.log(`✓ Restored inferred tag: "${fieldValue}"`);
                        // Refresh the display
                        await displayProduct(currentIndex);
                        showCurateInputs();
                    } else {
                        console.error('Failed to restore:', result.error);
                        alert('Failed to restore tag: ' + result.error);
                    }
                } catch (error) {
                    console.error('Error restoring tag:', error);
                    alert('Error restoring tag');
                }
            } else {
                // Mark as rejected
                const rejectionReason = prompt(`Mark "${fieldValue}" as incorrect?\n\nOptionally, enter why this tag is wrong (for ML training):`, '');

                if (rejectionReason === null) {
                    return; // User cancelled
                }

                try {
                    const response = await fetch('/api/rejected_tags', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            product_id: product.product_id,
                            field_name: fieldName,
                            field_value: fieldValue,
                            original_reasoning: reasoning,
                            curator: currentCurator,
                            rejection_reason: rejectionReason || null
                        })
                    });

                    const result = await response.json();
                    if (result.success) {
                        console.log(`✓ Marked inferred tag as rejected: "${fieldValue}" (reason: ${rejectionReason || 'not provided'})`);
                        // Refresh the display
                        await displayProduct(currentIndex);
                        showCurateInputs();
                    } else {
                        console.error('Failed to reject:', result.error);
                        alert('Failed to mark as incorrect: ' + result.error);
                    }
                } catch (error) {
                    console.error('Error rejecting tag:', error);
                    alert('Error marking tag as incorrect');
                }
            }
        }

        // Load products on page load - defer so UI is interactive first (avoids blocking clicks if fetch hangs)
        (function init() {
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', function onReady() {
                    document.removeEventListener('DOMContentLoaded', onReady);
                    setTimeout(function() { loadProducts().catch(function(e) { console.error('Load products failed:', e); }); }, 0);
                });
            } else {
                setTimeout(function() { loadProducts().catch(function(e) { console.error('Load products failed:', e); }); }, 0);
            }
        })();

        // ============================================
        // TAB NAVIGATION
        // ============================================

        function switchTab(tab) {
            // Update tab buttons
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            if (tab === 'products') {
                document.getElementById('tabProducts').classList.add('active');
            } else if (tab === 'ai') {
                document.getElementById('tabAI').classList.add('active');
            } else {
                document.getElementById('tabDashboard').classList.add('active');
            }

            // Update tab content
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            if (tab === 'products') {
                document.getElementById('productsTab').classList.add('active');
            } else if (tab === 'ai') {
                document.getElementById('aiTab').classList.add('active');
                checkAIStatus();
            } else {
                document.getElementById('dashboardTab').classList.add('active');
                loadDashboard();
            }
        }

        // ============================================
        // AI FUNCTIONALITY
        // ============================================

        let chatHistory = [];

        async function checkAIStatus() {
            const statusEl = document.getElementById('aiStatus');
            const statusText = document.getElementById('aiStatusText');

            try {
                const response = await fetch('/api/ai/status');
                const data = await response.json();

                if (data.available) {
                    statusEl.classList.remove('offline');
                    statusEl.classList.add('online');
                    statusText.textContent = 'Online';
                } else {
                    statusEl.classList.remove('online');
                    statusEl.classList.add('offline');
                    statusText.textContent = 'Offline';
                }
            } catch (error) {
                statusEl.classList.remove('online');
                statusEl.classList.add('offline');
                statusText.textContent = 'Error';
            }
        }

        function handleAISearchKeypress(event) {
            if (event.key === 'Enter') {
                performAISearch();
            }
        }

        async function performAISearch() {
            const input = document.getElementById('aiSearchInput');
            const query = input.value.trim();

            if (!query) {
                alert('Please enter a search query');
                return;
            }

            const searchBtn = document.getElementById('aiSearchBtn');
            const progress = document.getElementById('searchProgress');
            const results = document.getElementById('aiSearchResults');

            searchBtn.disabled = true;
            progress.classList.add('visible');
            results.innerHTML = '';

            try {
                const response = await fetch('/api/ai/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query, limit: 12 })
                });

                const data = await response.json();

                if (data.error) {
                    results.innerHTML = `<div class="no-results"><p>❌ ${data.error}</p></div>`;
                } else if (data.results && data.results.length > 0) {
                    renderSearchResults(data.results);
                } else {
                    results.innerHTML = `<div class="no-results"><p>No matching products found. Try a different description.</p></div>`;
                }
            } catch (error) {
                results.innerHTML = `<div class="no-results"><p>❌ Error: ${error.message}</p></div>`;
            } finally {
                searchBtn.disabled = false;
                progress.classList.remove('visible');
            }
        }

        function renderSearchResults(results) {
            const container = document.getElementById('aiSearchResults');
            const supabaseUrl = '{{ supabase_url }}';

            const html = `
                <p style="color: #666; margin-bottom: 15px;">Found ${results.length} matching products:</p>
                <div class="ai-results">
                    ${results.map(product => {
                        let imageUrl = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="250" height="200" fill="%23ccc"><rect width="100%" height="100%"/><text x="50%" y="50%" text-anchor="middle" fill="%23999">No Image</text></svg>';

                        if (product.image_urls && product.image_urls[0]) {
                            imageUrl = product.image_urls[0];
                        } else if (product.primary_image) {
                            imageUrl = product.primary_image;
                        }

                        const similarity = product.similarity ? Math.round(product.similarity * 100) : '';

                        return `
                            <div class="ai-result-card" onclick="goToProduct('${product.product_id}')">
                                <img src="${imageUrl}" alt="${product.name}" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22250%22 height=%22200%22 fill=%22%23ccc%22><rect width=%22100%25%22 height=%22100%25%22/><text x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 fill=%22%23999%22>No Image</text></svg>'">
                                <div class="card-content">
                                    <div class="card-title">${product.name || 'Unknown'}</div>
                                    <div class="card-price">${product.price || ''}</div>
                                    ${similarity ? `<div class="card-similarity">${similarity}% match</div>` : ''}
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>
            `;

            container.innerHTML = html;
        }

        function goToProduct(productId) {
            const index = products.findIndex(p => p.product_id === productId);
            if (index !== -1) {
                switchTab('products');
                displayProduct(index);
            } else {
                alert('Product not found in current view. Try refreshing the page.');
            }
        }

        // Reset product metadata to original scraped state
        async function resetProductMetadata(productId) {
            if (!confirm('This will remove all curated tags, AI-generated tags, and manual changes for this product. The product will be restored to its original scraped state. Continue?')) {
                return;
            }

            const statusDiv = document.getElementById('aiTagsStatus');
            if (statusDiv) {
                statusDiv.innerHTML = '<span style="color: #ef5350;">🔄 Resetting to original state...</span>';
            }

            try {
                const response = await fetch('/api/reset-metadata/' + productId, {
                    method: 'DELETE'
                });

                const data = await response.json();

                if (data.error) {
                    if (statusDiv) {
                        statusDiv.innerHTML = '<span style="color: #c62828;">❌ ' + data.error + '</span>';
                    }
                } else {
                    if (statusDiv) {
                        statusDiv.innerHTML = '<span style="color: #4caf50;">✅ Reset complete! Removed ' + (data.curated_deleted || 0) + ' curated and ' + (data.ai_deleted || 0) + ' AI tags</span>';
                    }
                    // Refresh the product display
                    await displayProduct(currentIndex);
                }
            } catch (error) {
                console.error('Error resetting metadata:', error);
                if (statusDiv) {
                    statusDiv.innerHTML = '<span style="color: #c62828;">❌ Error: ' + error.message + '</span>';
                }
            }
        }

        function handleChatKeypress(event) {
            if (event.key === 'Enter') {
                sendChatMessage();
            }
        }

        async function sendChatMessage() {
            const input = document.getElementById('chatInput');
            const message = input.value.trim();

            if (!message) return;

            input.value = '';

            const messagesContainer = document.getElementById('chatMessages');

            // Add user message to UI
            messagesContainer.innerHTML += `
                <div class="ai-chat-message user">
                    <div class="role">You</div>
                    <div>${escapeHtml(message)}</div>
                </div>
            `;

            // Add to history
            chatHistory.push({ role: 'user', content: message });

            // Add loading indicator
            messagesContainer.innerHTML += `
                <div class="ai-chat-message assistant" id="chatLoading">
                    <div class="role">Assistant</div>
                    <div><em>Thinking...</em></div>
                </div>
            `;

            messagesContainer.scrollTop = messagesContainer.scrollHeight;

            try {
                const response = await fetch('/api/ai/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ messages: chatHistory })
                });

                const data = await response.json();

                // Remove loading indicator
                document.getElementById('chatLoading')?.remove();

                if (data.error) {
                    messagesContainer.innerHTML += `
                        <div class="ai-chat-message assistant">
                            <div class="role">Assistant</div>
                            <div style="color: #c62828;">Error: ${data.error}</div>
                        </div>
                    `;
                } else {
                    const assistantMessage = data.response || 'No response';
                    chatHistory.push({ role: 'assistant', content: assistantMessage });

                    messagesContainer.innerHTML += `
                        <div class="ai-chat-message assistant">
                            <div class="role">Assistant</div>
                            <div>${formatChatResponse(assistantMessage)}</div>
                        </div>
                    `;
                }
            } catch (error) {
                document.getElementById('chatLoading')?.remove();
                messagesContainer.innerHTML += `
                    <div class="ai-chat-message assistant">
                        <div class="role">Assistant</div>
                        <div style="color: #c62828;">Error: ${error.message}</div>
                    </div>
                `;
            }

            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function formatChatResponse(text) {
            // Basic markdown-like formatting
            return text
                .replace(/\\n/g, '<br>')
                .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
                .replace(/\\*(.+?)\\*/g, '<em>$1</em>');
        }

        // ============================================
        // DASHBOARD FUNCTIONALITY
        // ============================================

        async function loadDashboard() {
            const dashboardContent = document.getElementById('dashboardContent');

            if (!useSupabase) {
                dashboardContent.innerHTML = `
                    <div class="no-data">
                        <h2>Dashboard requires Supabase</h2>
                        <p>Run the viewer with <code>--supabase</code> flag to enable dashboard features.</p>
                    </div>
                `;
                return;
            }

            dashboardContent.innerHTML = '<div class="no-data"><h2>Loading dashboard...</h2></div>';

            try {
                const response = await fetch('/api/dashboard/stats');
                const stats = await response.json();

                if (stats.error) {
                    dashboardContent.innerHTML = `
                        <div class="no-data">
                            <h2>Error loading dashboard</h2>
                            <p>${stats.error}</p>
                        </div>
                    `;
                    return;
                }

                renderDashboard(stats);
            } catch (error) {
                console.error('Error loading dashboard:', error);
                dashboardContent.innerHTML = `
                    <div class="no-data">
                        <h2>Error loading dashboard</h2>
                        <p>${error.message}</p>
                    </div>
                `;
            }
        }

        function renderDashboard(stats) {
            const overview = stats.overview;
            const byCategory = stats.by_category;
            const byCurator = stats.by_curator;
            const recentActivity = stats.recent_activity;

            // Build scraper section
            const scraperHtml = `
                <div class="scraper-section">
                    <h3>🔍 Want to Scrape?</h3>
                    <p style="color: #666; margin-bottom: 15px;">Scrape new products from Zara. Already-scraped products will be skipped automatically.</p>

                    <div class="scraper-controls">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <label style="margin: 0;">Categories:</label>
                            <div style="display: flex; gap: 8px;">
                                <button type="button" onclick="selectAllCategories()" style="padding: 4px 8px; font-size: 11px; cursor: pointer; background: #4CAF50; color: white; border: none; border-radius: 4px;">Select All</button>
                                <button type="button" onclick="deselectAllCategories()" style="padding: 4px 8px; font-size: 11px; cursor: pointer; background: #f44336; color: white; border: none; border-radius: 4px;">Deselect All</button>
                            </div>
                        </div>
                        <select id="scraperCategories" multiple style="height: 150px;">
                            <optgroup label="Clothing">
                                <option value="tshirts" selected>T-Shirts</option>
                                <option value="shirts" selected>Shirts</option>
                                <option value="trousers" selected>Trousers</option>
                                <option value="jeans" selected>Jeans</option>
                                <option value="shorts" selected>Shorts</option>
                                <option value="jackets" selected>Jackets</option>
                                <option value="blazers" selected>Blazers</option>
                                <option value="suits" selected>Suits</option>
                            </optgroup>
                            <optgroup label="Footwear">
                                <option value="shoes" selected>Shoes</option>
                            </optgroup>
                            <optgroup label="Discovery">
                                <option value="new-in" selected>New In</option>
                            </optgroup>
                        </select>

                        <div style="margin-top: 15px; padding: 12px; background: #f8f9fa; border-radius: 8px; border: 1px solid #e0e0e0;">
                            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                                <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; margin: 0; font-weight: 500;">
                                    <input type="checkbox" id="scrapeAllProducts" onchange="toggleAllProducts()" style="width: 18px; height: 18px; cursor: pointer;">
                                    <span>Scrape ALL products per category</span>
                                </label>
                            </div>
                            <div id="productCountContainer" style="display: flex; align-items: center; gap: 10px;">
                                <label style="margin: 0; color: #666;">Products per category:</label>
                                <input type="number" id="scraperProductCount" value="2" min="1" max="100" style="width: 70px; padding: 6px; border: 1px solid #ccc; border-radius: 4px;">
                            </div>
                            <p id="allProductsNote" style="display: none; margin: 8px 0 0 0; font-size: 12px; color: #4CAF50;">
                                ✓ Will scrape all available products (this may take a while)
                            </p>
                        </div>
                    </div>

                    <button class="go-btn" id="scraperGoBtn" onclick="startScraper()">🚀 GO</button>

                    <div class="progress-container" id="scraperProgress">
                        <div class="progress-status" id="progressStatus">Starting scraper...</div>
                        <div class="progress-bar-wrapper">
                            <div class="progress-bar" id="progressBar"></div>
                        </div>
                        <div class="progress-text" id="progressText">0 products processed</div>
                        <div class="progress-details" id="progressDetails"></div>

                        <button class="log-toggle" id="logToggle" onclick="toggleLogViewer()">📋 Show Logs</button>
                        <div class="log-viewer" id="logViewer"></div>
                    </div>
                </div>
            `;

            // Build stat cards
            const statCardsHtml = `
                <div class="dashboard-grid">
                    <div class="stat-card">
                        <div class="stat-value">${overview.total_products}</div>
                        <div class="stat-label">Total Products</div>
                    </div>
                    <div class="stat-card success">
                        <div class="stat-value">${overview.curated_products}</div>
                        <div class="stat-label">Curated Complete</div>
                    </div>
                    <div class="stat-card warning">
                        <div class="stat-value">${overview.pending_products}</div>
                        <div class="stat-label">Pending Curation</div>
                    </div>
                    <div class="stat-card info">
                        <div class="stat-value">${overview.percent_complete}%</div>
                        <div class="stat-label">Progress</div>
                    </div>
                </div>

                <div class="dashboard-grid">
                    <div class="stat-card">
                        <div class="stat-value">${overview.total_curated_tags}</div>
                        <div class="stat-label">Tags Added by Curators</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${overview.total_rejected_tags}</div>
                        <div class="stat-label">Inferred Tags Rejected</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${Object.keys(byCategory).length}</div>
                        <div class="stat-label">Categories</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${Object.keys(byCurator).length}</div>
                        <div class="stat-label">Active Curators</div>
                    </div>
                </div>
            `;

            // Build recent activity
            let activityHtml = '';
            if (recentActivity.length > 0) {
                const activityItems = recentActivity.map(item => {
                    const colorInfo = curatorColors[item.curator] || { bg: '#999' };
                    const date = new Date(item.created_at).toLocaleDateString();
                    return `
                        <div class="activity-item">
                            <span class="activity-product">${item.product_id}</span>
                            <span class="activity-curator" style="background: ${colorInfo.bg};">${item.curator}</span>
                            <span class="activity-time">${date}</span>
                        </div>
                    `;
                }).join('');

                activityHtml = `
                    <div class="activity-list">
                        <h3 class="chart-title">Recent Curation Activity</h3>
                        ${activityItems}
                    </div>
                `;
            }

            // Render HTML
            document.getElementById('dashboardContent').innerHTML = `
                ${scraperHtml}

                ${statCardsHtml}

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                    <div class="chart-container">
                        <h3 class="chart-title">Curation Progress by Category</h3>
                        <div id="categoryChart"></div>
                    </div>
                    <div class="chart-container">
                        <h3 class="chart-title">Curator Activity</h3>
                        <div id="curatorChart"></div>
                    </div>
                </div>

                <div class="chart-container">
                    <h3 class="chart-title">Overall Progress</h3>
                    <div id="progressChart"></div>
                </div>

                ${activityHtml}
            `;

            // Render Plotly charts
            renderCategoryChart(byCategory);
            renderCuratorChart(byCurator);
            renderProgressChart(overview);

            // Check if scraper is already running
            checkScraperStatus();
        }

        // ============================================
        // SCRAPER FUNCTIONALITY
        // ============================================

        let scraperPollingInterval = null;

        // Select all categories
        function selectAllCategories() {
            const select = document.getElementById('scraperCategories');
            for (let option of select.options) {
                option.selected = true;
            }
        }

        // Deselect all categories
        function deselectAllCategories() {
            const select = document.getElementById('scraperCategories');
            for (let option of select.options) {
                option.selected = false;
            }
        }

        // Toggle "all products" mode
        function toggleAllProducts() {
            const checkbox = document.getElementById('scrapeAllProducts');
            const countContainer = document.getElementById('productCountContainer');
            const allNote = document.getElementById('allProductsNote');
            const countInput = document.getElementById('scraperProductCount');

            if (checkbox.checked) {
                countContainer.style.opacity = '0.5';
                countInput.disabled = true;
                allNote.style.display = 'block';
            } else {
                countContainer.style.opacity = '1';
                countInput.disabled = false;
                allNote.style.display = 'none';
            }
        }

        async function startScraper() {
            const categoriesSelect = document.getElementById('scraperCategories');
            const productCountInput = document.getElementById('scraperProductCount');
            const scrapeAllCheckbox = document.getElementById('scrapeAllProducts');
            const goBtn = document.getElementById('scraperGoBtn');

            // Get selected categories
            const selectedCategories = Array.from(categoriesSelect.selectedOptions).map(opt => opt.value);

            // If "scrape all" is checked, use a high number (9999), otherwise use the input value
            const productsPerCategory = scrapeAllCheckbox.checked ? 9999 : (parseInt(productCountInput.value) || 2);

            if (selectedCategories.length === 0) {
                alert('Please select at least one category');
                return;
            }

            // Disable button
            goBtn.disabled = true;
            goBtn.textContent = '⏳ Starting...';

            try {
                const response = await fetch('/api/scraper/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        categories: selectedCategories,
                        products_per_category: productsPerCategory
                    })
                });

                const result = await response.json();
                if (result.success) {
                    // Show progress container
                    document.getElementById('scraperProgress').classList.add('visible');
                    goBtn.textContent = '🔄 Scraping...';

                    // Start polling for status
                    startScraperPolling();
                } else {
                    alert('Failed to start scraper: ' + result.error);
                    goBtn.disabled = false;
                    goBtn.textContent = '🚀 GO';
                }
            } catch (error) {
                console.error('Error starting scraper:', error);
                alert('Error starting scraper');
                goBtn.disabled = false;
                goBtn.textContent = '🚀 GO';
            }
        }

        function startScraperPolling() {
            // Clear any existing interval
            if (scraperPollingInterval) {
                clearInterval(scraperPollingInterval);
            }

            // Poll every second
            scraperPollingInterval = setInterval(checkScraperStatus, 1000);
        }

        async function checkScraperStatus() {
            try {
                const response = await fetch('/api/scraper/status');
                const status = await response.json();

                const progressContainer = document.getElementById('scraperProgress');
                const progressBar = document.getElementById('progressBar');
                const progressStatus = document.getElementById('progressStatus');
                const progressText = document.getElementById('progressText');
                const progressDetails = document.getElementById('progressDetails');
                const goBtn = document.getElementById('scraperGoBtn');
                const logViewer = document.getElementById('logViewer');

                // Don't update if elements don't exist (not on dashboard tab)
                if (!progressContainer) return;

                // Update log viewer
                if (logViewer && status.logs && status.logs.length > 0) {
                    logViewer.innerHTML = status.logs.map(line => {
                        let lineClass = 'log-line';
                        if (line.startsWith('$')) lineClass += ' command';
                        else if (line.includes('❌') || line.includes('Error') || line.includes('error')) lineClass += ' error';
                        else if (line.includes('✅') || line.includes('✓')) lineClass += ' success';
                        else if (line.includes('⏭️') || line.includes('Skipping')) lineClass += ' warning';
                        else if (line.includes('Processing') || line.includes('Extracting')) lineClass += ' info';
                        return `<div class="${lineClass}">${line}</div>`;
                    }).join('');
                    // Auto-scroll to bottom
                    logViewer.scrollTop = logViewer.scrollHeight;
                }

                if (status.running) {
                    progressContainer.classList.add('visible');
                    goBtn.disabled = true;
                    goBtn.textContent = '🔄 Scraping...';

                    // Update progress bar
                    const total = status.total || 1;
                    const progress = status.progress || 0;
                    const percent = Math.min((progress / total) * 100, 100);
                    progressBar.style.width = percent + '%';

                    // Update status text
                    progressStatus.textContent = `Category: ${status.current_category || 'Starting...'}`;
                    progressText.textContent = `${status.products_scraped} scraped, ${status.products_skipped} skipped`;
                    if (status.current_product) {
                        progressDetails.textContent = `Current: ${status.current_product}`;
                    }
                } else if (status.completed && !status.refresh_handled) {
                    // Scraping completed - only refresh once
                    clearInterval(scraperPollingInterval);
                    scraperPollingInterval = null;

                    progressBar.style.width = '100%';
                    progressStatus.textContent = '✅ Scraping Complete!';
                    progressText.textContent = `${status.products_scraped} new products scraped, ${status.products_skipped} skipped`;
                    progressDetails.textContent = 'Refreshing dashboard...';

                    goBtn.disabled = false;
                    goBtn.textContent = '🚀 GO';

                    // Mark refresh as handled to prevent loops
                    fetch('/api/scraper/reset', { method: 'POST' });

                    // Auto-refresh after 2 seconds
                    setTimeout(() => {
                        // Reload products
                        loadProducts();
                        // Reload dashboard stats only (not full reload to avoid loop)
                        refreshDashboardStats();
                        progressDetails.textContent = 'Dashboard updated!';
                    }, 2000);
                } else if (status.completed && status.refresh_handled) {
                    // Already handled, just show completed state
                    progressContainer.classList.add('visible');
                    progressBar.style.width = '100%';
                    progressStatus.textContent = '✅ Scraping Complete!';
                    progressText.textContent = `${status.products_scraped} new products scraped, ${status.products_skipped} skipped`;
                    progressDetails.textContent = 'Dashboard updated!';
                    goBtn.disabled = false;
                    goBtn.textContent = '🚀 GO';
                } else if (status.error) {
                    // Error occurred
                    clearInterval(scraperPollingInterval);
                    scraperPollingInterval = null;

                    progressStatus.textContent = '❌ Error';
                    progressText.textContent = status.error;
                    progressDetails.textContent = 'Check logs for details';

                    // Auto-show logs on error
                    if (logViewer) {
                        logViewer.classList.add('visible');
                        const logToggle = document.getElementById('logToggle');
                        if (logToggle) logToggle.textContent = '📋 Hide Logs';
                    }

                    goBtn.disabled = false;
                    goBtn.textContent = '🚀 GO';
                } else {
                    // Not running, hide progress
                    progressContainer.classList.remove('visible');
                    goBtn.disabled = false;
                    goBtn.textContent = '🚀 GO';
                }
            } catch (error) {
                console.error('Error checking scraper status:', error);
            }
        }

        function toggleLogViewer() {
            const logViewer = document.getElementById('logViewer');
            const logToggle = document.getElementById('logToggle');

            if (logViewer.classList.contains('visible')) {
                logViewer.classList.remove('visible');
                logToggle.textContent = '📋 Show Logs';
            } else {
                logViewer.classList.add('visible');
                logToggle.textContent = '📋 Hide Logs';
            }
        }

        async function refreshDashboardStats() {
            // Refresh only the statistics without re-rendering the scraper section
            // This prevents the refresh loop issue
            try {
                const response = await fetch('/api/dashboard/stats');
                const stats = await response.json();

                if (stats.error) {
                    console.error('Error refreshing stats:', stats.error);
                    return;
                }

                // Just update the stat card values
                const overview = stats.overview;

                // Find and update stat cards by their labels
                const statCards = document.querySelectorAll('.stat-card');
                statCards.forEach(card => {
                    const label = card.querySelector('.stat-label');
                    const value = card.querySelector('.stat-value');
                    if (!label || !value) return;

                    const labelText = label.textContent.toLowerCase();
                    if (labelText.includes('total products')) {
                        value.textContent = overview.total_products;
                    } else if (labelText.includes('curated complete')) {
                        value.textContent = overview.curated_products;
                    } else if (labelText.includes('pending')) {
                        value.textContent = overview.pending_products;
                    } else if (labelText.includes('progress')) {
                        value.textContent = overview.percent_complete + '%';
                    } else if (labelText.includes('tags added')) {
                        value.textContent = overview.total_curated_tags;
                    } else if (labelText.includes('tags rejected')) {
                        value.textContent = overview.total_rejected_tags;
                    }
                });

                // Update charts
                renderCategoryChart(stats.by_category);
                renderCuratorChart(stats.by_curator);
                renderProgressChart(overview);

                console.log('Dashboard stats refreshed');
            } catch (error) {
                console.error('Error refreshing dashboard stats:', error);
            }
        }

        function renderCategoryChart(byCategory) {
            const categories = Object.keys(byCategory);

            // Build stacked bars by curator instead of generic "Curated"
            const allCurators = ['Reed', 'Gigi', 'Kiki'];
            const curatorTraces = [];

            // Create a trace for each curator
            allCurators.forEach(curator => {
                const colorInfo = curatorColors[curator];
                const values = categories.map(cat => {
                    const byCurator = byCategory[cat].by_curator || {};
                    return byCurator[curator] || 0;
                });

                // Only add trace if this curator has any data
                if (values.some(v => v > 0)) {
                    curatorTraces.push({
                        x: categories,
                        y: values,
                        name: curator,
                        type: 'bar',
                        marker: { color: colorInfo ? colorInfo.bg : '#999' }
                    });
                }
            });

            // Add pending trace
            const pending = categories.map(c => byCategory[c].pending);
            curatorTraces.push({
                x: categories,
                y: pending,
                name: 'Pending',
                type: 'bar',
                marker: { color: '#ff9800' }
            });

            const layout = {
                barmode: 'stack',
                margin: { t: 20, r: 20, b: 60, l: 40 },
                legend: { orientation: 'h', y: -0.2 },
                xaxis: { tickangle: -45 }
            };

            Plotly.newPlot('categoryChart', curatorTraces, layout, { responsive: true });
        }

        function renderCuratorChart(byCurator) {
            const curators = Object.keys(byCurator);

            if (curators.length === 0) {
                document.getElementById('curatorChart').innerHTML = '<p style="color:#999;text-align:center;padding:40px;">No curator activity yet</p>';
                return;
            }

            // Get curator colors to match the rest of the UI
            const curatorBarColors = curators.map(c => {
                const colorInfo = curatorColors[c];
                return colorInfo ? colorInfo.bg : '#999';
            });

            const data = [
                {
                    x: curators,
                    y: curators.map(c => byCurator[c].completed),
                    name: 'Products Completed',
                    type: 'bar',
                    marker: { color: curatorBarColors }
                },
                {
                    x: curators,
                    y: curators.map(c => byCurator[c].tags_added),
                    name: 'Tags Added',
                    type: 'bar',
                    marker: { color: curatorBarColors.map(c => c + '99') }  // Slightly transparent
                },
                {
                    x: curators,
                    y: curators.map(c => byCurator[c].tags_rejected),
                    name: 'Tags Rejected',
                    type: 'bar',
                    marker: { color: curatorBarColors.map(c => c + '66') }  // More transparent
                }
            ];

            const layout = {
                barmode: 'group',
                margin: { t: 20, r: 20, b: 40, l: 40 },
                legend: { orientation: 'h', y: -0.15 }
            };

            Plotly.newPlot('curatorChart', data, layout, { responsive: true });
        }

        function renderProgressChart(overview) {
            // Build values, labels, and colors arrays based on curated_by_curator
            const values = [];
            const labels = [];
            const colors = [];

            // Add curator-specific slices if curated_by_curator data is available
            if (overview.curated_by_curator) {
                const allCurators = ['Reed', 'Gigi', 'Kiki'];
                allCurators.forEach(curator => {
                    const count = overview.curated_by_curator[curator] || 0;
                    if (count > 0) {
                        values.push(count);
                        labels.push(`${curator} Curated`);
                        const colorInfo = curatorColors[curator];
                        colors.push(colorInfo ? colorInfo.bg : '#999');
                    }
                });
            } else if (overview.curated_products > 0) {
                // Fallback if no curator breakdown available
                values.push(overview.curated_products);
                labels.push('Curated');
                colors.push('#4CAF50');
            }

            // Add pending products
            if (overview.pending_products > 0) {
                values.push(overview.pending_products);
                labels.push('Pending');
                colors.push('#ff9800');
            }

            const data = [{
                values: values,
                labels: labels,
                type: 'pie',
                hole: 0.6,
                marker: {
                    colors: colors
                },
                textinfo: 'label+percent',
                textposition: 'outside'
            }];

            const layout = {
                margin: { t: 20, r: 20, b: 20, l: 20 },
                showlegend: false,
                annotations: [{
                    text: `${overview.percent_complete}%`,
                    showarrow: false,
                    font: { size: 32, weight: 'bold' }
                }]
            };

            Plotly.newPlot('progressChart', data, layout, { responsive: true });
        }

        // ============================================
        // MARK AS COMPLETE FUNCTIONALITY
        // ============================================

        async function markProductComplete() {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first.');
                return;
            }

            const product = products[currentIndex];
            const notesEl = document.getElementById('curationNotesInput');
            const notes = notesEl ? notesEl.value.trim() || null : null;
            const confidenceEl = document.querySelector('input[name="curationConfidence"]:checked');
            const confidence = confidenceEl ? parseInt(confidenceEl.value, 10) : 4;
            const includeEl = document.getElementById('curationIncludeTraining');
            const includeInTraining = includeEl ? includeEl.checked : true;
            const errorTypeEls = document.querySelectorAll('.curation-error-type:checked');
            const errorTypes = Array.from(errorTypeEls).map(el => el.value);

            const originalTags = (window.originalTagsForCuration || {})[product.product_id];
            const correctedTags = product.tags_final;

            if (originalTags != null && correctedTags != null && useSupabase) {
                try {
                    const historyResponse = await fetch('/api/save_curation_history', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            product_id: product.product_id,
                            original_tags: originalTags,
                            corrected_tags: correctedTags,
                            curator_notes: notes,
                            confidence: confidence,
                            curator_id: currentCurator,
                            error_types: errorTypes,
                            include_in_training: includeInTraining
                        })
                    });
                    const historyResult = await historyResponse.json();
                    if (!historyResult.success) {
                        console.warn('Curation history save failed:', historyResult.error);
                    }
                } catch (err) {
                    console.warn('Could not save curation history:', err);
                }
            }

            try {
                const response = await fetch('/api/curation_status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_id: product.product_id,
                        curator: currentCurator,
                        notes: notes
                    })
                });

                const result = await response.json();
                if (result.success) {
                    console.log(`✓ Marked product ${product.product_id} as complete`);
                    await displayProduct(currentIndex);
                    showCurateInputs();
                } else {
                    alert('Failed to mark as complete: ' + result.error);
                }
            } catch (error) {
                console.error('Error marking complete:', error);
                alert('Error marking product as complete');
            }
        }

        async function unmarkProductComplete() {
            if (!curateMode || !currentCurator) {
                alert('Please enter curate mode first.');
                return;
            }

            const product = products[currentIndex];

            if (!confirm('Remove completion status from this product?')) {
                return;
            }

            try {
                const response = await fetch('/api/curation_status', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        product_id: product.product_id
                    })
                });

                const result = await response.json();
                if (result.success) {
                    console.log(`✓ Unmarked product ${product.product_id}`);
                    await displayProduct(currentIndex);
                    showCurateInputs();
                } else {
                    alert('Failed to unmark: ' + result.error);
                }
            } catch (error) {
                console.error('Error unmarking:', error);
                alert('Error removing completion status');
            }
        }

        // ============================================
        // VOCABULARY MANAGEMENT
        // ============================================

        async function loadCustomVocabulary() {
            try {
                const response = await fetch('/api/vocabulary');
                const result = await response.json();

                if (result.success) {
                    displayCustomVocabulary(result.vocabulary);
                    updateCategoryDropdown(result.vocabulary);
                } else {
                    document.getElementById('customVocabList').innerHTML = '<em style="color: #666;">No custom vocabulary yet</em>';
                }
            } catch (error) {
                console.error('Error loading vocabulary:', error);
                document.getElementById('customVocabList').innerHTML = '<em style="color: #ff6b6b;">Error loading vocabulary</em>';
            }
        }

        function displayCustomVocabulary(vocabulary) {
            const container = document.getElementById('customVocabList');

            if (!vocabulary || Object.keys(vocabulary).length === 0) {
                container.innerHTML = '<em style="color: #666;">No custom vocabulary yet. Add tags above to extend the AI vocabulary.</em>';
                return;
            }

            let html = '<div style="display: flex; flex-wrap: wrap; gap: 15px;">';

            for (const [category, tags] of Object.entries(vocabulary)) {
                if (tags && tags.length > 0) {
                    html += `
                        <div style="background: rgba(100, 181, 246, 0.1); padding: 10px 15px; border-radius: 8px; border-left: 3px solid #64b5f6;">
                            <strong style="color: #64b5f6; text-transform: capitalize;">${category.replace('_', ' ')}:</strong>
                            <div style="margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px;">
                                ${tags.map(tag => `
                                    <span style="background: rgba(0,212,170,0.2); color: #00d4aa; padding: 3px 8px; border-radius: 4px; font-size: 12px; display: inline-flex; align-items: center; gap: 5px;">
                                        ${tag}
                                        <span onclick="deleteVocabTag('${category}', '${tag}')" style="cursor: pointer; opacity: 0.7; font-size: 14px;" title="Remove tag">×</span>
                                    </span>
                                `).join('')}
                            </div>
                        </div>
                    `;
                }
            }

            html += '</div>';
            container.innerHTML = html;
        }

        function updateCategoryDropdown(vocabulary) {
            const dropdown = document.getElementById('vocabCategory');
            if (!dropdown) return;

            // Add custom categories to dropdown
            if (vocabulary) {
                for (const category of Object.keys(vocabulary)) {
                    // Check if category already exists in dropdown
                    const exists = Array.from(dropdown.options).some(opt => opt.value === category);
                    if (!exists) {
                        const option = document.createElement('option');
                        option.value = category;
                        // Capitalize first letter of each word
                        option.textContent = category.replace(/_/g, ' ').split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                        dropdown.appendChild(option);
                    }
                }
            }
        }

        async function addVocabTag() {
            const category = document.getElementById('vocabCategory').value;
            const tag = document.getElementById('vocabNewTag').value.trim().toLowerCase();

            if (!tag) {
                alert('Please enter a tag');
                return;
            }

            if (!/^[a-z][a-z0-9-]*$/.test(tag)) {
                alert('Tags must start with a letter and contain only lowercase letters, numbers, and hyphens');
                return;
            }

            try {
                const response = await fetch('/api/vocabulary/tag', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category, tag })
                });

                const result = await response.json();

                if (result.success) {
                    document.getElementById('vocabNewTag').value = '';
                    loadCustomVocabulary();
                    alert(`Tag "${tag}" added to ${category}!`);
                } else {
                    alert('Error: ' + result.error);
                }
            } catch (error) {
                console.error('Error adding tag:', error);
                alert('Error adding tag');
            }
        }

        async function createVocabCategory() {
            const categoryName = document.getElementById('newCategoryName').value.trim().toLowerCase().replace(/\\s+/g, '_');
            const tagsInput = document.getElementById('newCategoryTags').value.trim();

            if (!categoryName) {
                alert('Please enter a category name');
                return;
            }

            if (!/^[a-z][a-z0-9_]*$/.test(categoryName)) {
                alert('Category name must start with a letter and contain only lowercase letters, numbers, and underscores');
                return;
            }

            const tags = tagsInput.split(',').map(t => t.trim().toLowerCase()).filter(t => t);

            if (tags.length === 0) {
                alert('Please enter at least one initial tag');
                return;
            }

            try {
                const response = await fetch('/api/vocabulary/category', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category: categoryName, tags })
                });

                const result = await response.json();

                if (result.success) {
                    document.getElementById('newCategoryName').value = '';
                    document.getElementById('newCategoryTags').value = '';
                    loadCustomVocabulary();
                    alert(`Category "${categoryName}" created with ${tags.length} tags!`);
                } else {
                    alert('Error: ' + result.error);
                }
            } catch (error) {
                console.error('Error creating category:', error);
                alert('Error creating category');
            }
        }

        async function deleteVocabTag(category, tag) {
            if (!confirm(`Remove "${tag}" from ${category}?`)) {
                return;
            }

            try {
                const response = await fetch('/api/vocabulary/tag', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category, tag })
                });

                const result = await response.json();

                if (result.success) {
                    loadCustomVocabulary();
                } else {
                    alert('Error: ' + result.error);
                }
            } catch (error) {
                console.error('Error deleting tag:', error);
                alert('Error deleting tag');
            }
        }

        // Load custom vocabulary when page loads
        document.addEventListener('DOMContentLoaded', function() {
            // Delay loading to ensure other elements are ready
            setTimeout(loadCustomVocabulary, 1000);
        });
    </script>

    <!-- AI Technology Documentation Section -->
    <div id="aiDocumentation" style="
        max-width: 900px;
        margin: 60px auto 40px auto;
        padding: 30px;
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border-radius: 16px;
        border: 1px solid rgba(255,255,255,0.1);
        color: #e0e0e0;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    ">
        <h2 style="
            color: #00d4aa;
            margin-bottom: 25px;
            font-size: 24px;
            display: flex;
            align-items: center;
            gap: 12px;
        ">
            🤖 AI Technology Documentation
        </h2>

        <!-- ReFitd Canonical Tagging Overview -->
        <div style="margin-bottom: 25px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                🏷️ ReFitd Canonical Tagging System
            </h3>
            <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 10px;">
                The ReFitd tagging system uses a <strong style="color: #fff;">three-layer architecture</strong> for structured,
                machine-readable fashion tags that power outfit generation.
            </p>
            <div style="background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px; margin-top: 15px;">
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; text-align: center;">
                    <div style="background: rgba(100, 181, 246, 0.2); padding: 15px; border-radius: 8px; border: 1px solid #64b5f6;">
                        <div style="font-size: 24px; margin-bottom: 8px;">🔵</div>
                        <strong style="color: #64b5f6;">AI Sensor</strong>
                        <p style="color: #b0b0b0; font-size: 12px; margin-top: 5px;">GPT-5.2 Vision analyzes images → tags with confidence</p>
                    </div>
                    <div style="background: rgba(255, 183, 77, 0.2); padding: 15px; border-radius: 8px; border: 1px solid #ffb74d;">
                        <div style="font-size: 24px; margin-bottom: 8px;">⚙️</div>
                        <strong style="color: #ffb74d;">Tag Policy</strong>
                        <p style="color: #b0b0b0; font-size: 12px; margin-top: 5px;">Applies thresholds & rules → filtered tags</p>
                    </div>
                    <div style="background: rgba(129, 199, 132, 0.2); padding: 15px; border-radius: 8px; border: 1px solid #81c784;">
                        <div style="font-size: 24px; margin-bottom: 8px;">🏷️</div>
                        <strong style="color: #81c784;">Canonical Tags</strong>
                        <p style="color: #b0b0b0; font-size: 12px; margin-top: 5px;">Clean, confidence-free tags for generator</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- GPT-5.2 Model Overview -->
        <div style="margin-bottom: 25px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                📦 Primary AI Model: GPT-5.2
            </h3>
            <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 10px;">
                ReFitd uses <strong style="color: #fff;">GPT-5.2</strong> (OpenAI's latest multimodal model) for generating
                canonical tags. GPT-5.2 excels at visual understanding and produces consistent, structured JSON output.
            </p>
            <ul style="line-height: 1.8; color: #b0b0b0; padding-left: 20px;">
                <li><strong style="color: #fff;">Vision + Language:</strong> Analyzes product images alongside title and description for comprehensive understanding</li>
                <li><strong style="color: #fff;">Structured Output:</strong> Returns JSON with confidence scores for each tag</li>
                <li><strong style="color: #fff;">Controlled Vocabulary:</strong> Prompted to use ONLY predefined tag values</li>
                <li><strong style="color: #fff;">Low Temperature (0.3):</strong> Ensures reproducible, consistent results</li>
            </ul>
        </div>

        <!-- Canonical Tag Categories -->
        <div style="margin-bottom: 25px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                🏷️ Canonical Tag Categories
            </h3>
            <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 15px;">
                The AI generates tags from these predefined categories with strict vocabulary control:
            </p>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px;">
                <div style="background: rgba(26, 26, 46, 0.8); padding: 14px; border-radius: 8px; border-left: 3px solid #1a1a1a;">
                    <strong style="color: #fff;">Style Identity (1-2, required):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"> minimal, classic, preppy, workwear, streetwear, rugged, tailoring, elevated-basics, normcore, sporty, outdoorsy, western, vintage, grunge, punk, utilitarian</span>
                </div>
                <div style="background: rgba(100, 181, 246, 0.1); padding: 14px; border-radius: 8px; border-left: 3px solid #64b5f6;">
                    <strong style="color: #64b5f6;">Silhouette (1, required):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"><br/>• Bottoms: straight, tapered, wide<br/>• Tops/Outerwear: boxy, structured, relaxed, longline, tailored</span>
                </div>
                <div style="background: rgba(129, 199, 132, 0.1); padding: 14px; border-radius: 8px; border-left: 3px solid #81c784;">
                    <strong style="color: #81c784;">Formality (1, AI-generated):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"><br/>Scale 1→5: athletic, casual, smart-casual, business-casual, formal<br/><em style="color: #666;">(Compared with rule-based formality)</em></span>
                </div>
                <div style="background: rgba(255, 183, 77, 0.1); padding: 14px; border-radius: 8px; border-left: 3px solid #ffb74d;">
                    <strong style="color: #ffb74d;">Context (0-2, optional):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"> everyday, work-appropriate, travel, evening, weekend</span>
                </div>
                <div style="background: rgba(186, 104, 200, 0.1); padding: 14px; border-radius: 8px; border-left: 3px solid #ba68c8;">
                    <strong style="color: #ba68c8;">Pattern (0-1, optional):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"> solid, stripe, check, textured</span>
                </div>
                <div style="background: rgba(255, 138, 128, 0.1); padding: 14px; border-radius: 8px; border-left: 3px solid #ff8a80;">
                    <strong style="color: #ff8a80;">Construction Details (0-2):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"><br/>• Bottoms: pleated, flat-front, cargo, drawstring, elastic-waist<br/>• Tops: structured-shoulder, dropped-shoulder</span>
                </div>
                <div style="background: rgba(79, 195, 247, 0.1); padding: 14px; border-radius: 8px; border-left: 3px solid #4fc3f7;">
                    <strong style="color: #4fc3f7;">Pairing Tags (0-3, scoring):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"> neutral-base, statement-piece, easy-dress-up, easy-dress-down, high-versatility</span>
                </div>
                <div style="background: rgba(255, 213, 79, 0.1); padding: 14px; border-radius: 8px; border-left: 3px solid #ffd54f;">
                    <strong style="color: #ffd54f;">Top Layer Role (tops only):</strong>
                    <span style="color: #b0b0b0; font-size: 13px;"><br/>• Base: T-shirts, shirts, polos, tanks, henleys<br/>• Mid: Sweaters, cardigans, hoodies, sweatshirts</span>
                </div>
            </div>
        </div>

        <!-- Tag Policy Layer -->
        <div style="margin-bottom: 25px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                ⚙️ Tag Policy Layer
            </h3>
            <div style="background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px; margin-bottom: 15px;">
                <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 15px;">
                    The Policy Layer applies confidence thresholds and business rules to AI output:
                </p>
                <ul style="line-height: 1.8; color: #b0b0b0; padding-left: 20px;">
                    <li><strong style="color: #fff;">Confidence Thresholds:</strong> Tags below threshold are suppressed (e.g., style_identity_auto: 0.75)</li>
                    <li><strong style="color: #fff;">Vocabulary Validation:</strong> Only allowed tag values pass through</li>
                    <li><strong style="color: #fff;">Category-Aware Rules:</strong> Different silhouettes for tops vs bottoms, shoe-specific fields</li>
                    <li><strong style="color: #fff;">Default Fallbacks:</strong> Missing required tags get sensible defaults (e.g., formality → "casual")</li>
                    <li><strong style="color: #fff;">Curation Status:</strong> Products flagged as "approved", "needs_review", or "needs_fix"</li>
                </ul>
            </div>
        </div>

        <!-- AI Formality vs Scraped Formality -->
        <div style="margin-bottom: 25px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                📊 AI Formality vs Rule-Based Formality
            </h3>
            <div style="background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px; margin-bottom: 15px;">
                <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 15px;">
                    The system now generates <strong style="color: #81c784;">AI formality</strong> (in ReFitd Canonical Tags)
                    alongside the original <strong style="color: #ffb74d;">rule-based formality</strong> (in the Formality section above)
                    so you can compare approaches:
                </p>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                    <div style="background: rgba(129, 199, 132, 0.15); padding: 15px; border-radius: 8px; border: 1px solid #81c784;">
                        <strong style="color: #81c784;">🤖 AI Formality</strong>
                        <p style="color: #b0b0b0; font-size: 12px; margin-top: 8px;">GPT-5.2 analyzes the image and assigns formality based on visual appearance and product context. Uses confidence scoring.</p>
                    </div>
                    <div style="background: rgba(255, 183, 77, 0.15); padding: 15px; border-radius: 8px; border: 1px solid #ffb74d;">
                        <strong style="color: #ffb74d;">📐 Rule-Based Formality</strong>
                        <p style="color: #b0b0b0; font-size: 12px; margin-top: 8px;">Calculated from garment type, color, material, and structure using predefined formality modifiers. Deterministic scoring.</p>
                    </div>
                </div>
            </div>
        </div>

        <!-- Tagging at Ingestion -->
        <div style="margin-bottom: 25px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                🏷️ Canonical Tags at Ingestion
            </h3>
            <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 10px;">
                ReFitd canonical tags (style identity, fit, silhouette, formality, etc.) are generated during the pipeline
                after scrape, using GPT vision. No in-viewer tag generation — tags are already present when products load.
            </p>
        </div>

        <!-- Product Categories -->
        <div style="margin-bottom: 15px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                📂 Product Category Structure
            </h3>
            <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 15px;">
                Products are organized into hierarchical categories with subcategories:
            </p>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;">
                <div style="background: rgba(76, 175, 80, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid #4caf50;">
                    <strong style="color: #4caf50;">👕 Base Layer:</strong>
                    <span style="color: #b0b0b0; font-size: 12px; display: block; margin-top: 4px;">T-Shirts, Long Sleeve, Shirts, Polos, Tanks & Henleys</span>
                </div>
                <div style="background: rgba(33, 150, 243, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid #2196f3;">
                    <strong style="color: #2196f3;">🧶 Mid Layer:</strong>
                    <span style="color: #b0b0b0; font-size: 12px; display: block; margin-top: 4px;">Sweaters, Cardigans, Hoodies, Sweatshirts</span>
                </div>
                <div style="background: rgba(156, 39, 176, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid #9c27b0;">
                    <strong style="color: #9c27b0;">👖 Bottoms:</strong>
                    <span style="color: #b0b0b0; font-size: 12px; display: block; margin-top: 4px;">Pants, Shorts</span>
                </div>
                <div style="background: rgba(255, 152, 0, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid #ff9800;">
                    <strong style="color: #ff9800;">🧥 Outerwear:</strong>
                    <span style="color: #b0b0b0; font-size: 12px; display: block; margin-top: 4px;">Jackets, Coats, Blazers, Vests</span>
                </div>
                <div style="background: rgba(121, 85, 72, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid #795548;">
                    <strong style="color: #795548;">👞 Shoes:</strong>
                    <span style="color: #b0b0b0; font-size: 12px; display: block; margin-top: 4px;">Sneakers, Boots, Loafers, Derbies, Oxfords, Sandals</span>
                </div>
            </div>
        </div>

        <!-- Vocabulary Manager -->
        <div style="margin-bottom: 25px;">
            <h3 style="color: #64b5f6; font-size: 18px; margin-bottom: 12px;">
                🛠️ Vocabulary Manager (Legacy)
            </h3>
            <p style="line-height: 1.7; color: #b0b0b0; margin-bottom: 15px;">
                Extend the legacy AI vocabulary by adding new tags to existing categories or creating entirely new categories.
                Custom vocabulary is stored in Supabase and merged with the default tags.
            </p>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                <!-- Add Tag to Existing Category -->
                <div style="background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px;">
                    <h4 style="color: #81c784; margin-bottom: 15px; font-size: 15px;">➕ Add Tag to Category</h4>
                    <div style="margin-bottom: 12px;">
                        <label style="color: #b0b0b0; font-size: 13px; display: block; margin-bottom: 5px;">Category:</label>
                        <select id="vocabCategory" style="
                            width: 100%;
                            padding: 10px;
                            border-radius: 6px;
                            border: 1px solid rgba(255,255,255,0.2);
                            background: #1a1a2e;
                            color: #fff;
                            font-size: 14px;
                        ">
                            <option value="aesthetic">Aesthetic</option>
                            <option value="fit">Fit</option>
                            <option value="pattern">Pattern</option>
                            <option value="material_feel">Material</option>
                            <option value="season">Season</option>
                            <option value="occasion">Occasion</option>
                            <option value="color_mood">Color Mood</option>
                            <option value="details">Details</option>
                        </select>
                    </div>
                    <div style="margin-bottom: 12px;">
                        <label style="color: #b0b0b0; font-size: 13px; display: block; margin-bottom: 5px;">New Tag:</label>
                        <input type="text" id="vocabNewTag" placeholder="e.g., cyberpunk" style="
                            width: 100%;
                            padding: 10px;
                            border-radius: 6px;
                            border: 1px solid rgba(255,255,255,0.2);
                            background: #1a1a2e;
                            color: #fff;
                            font-size: 14px;
                            box-sizing: border-box;
                        ">
                    </div>
                    <button onclick="addVocabTag()" style="
                        background: linear-gradient(135deg, #81c784, #4caf50);
                        color: #000;
                        border: none;
                        padding: 10px 20px;
                        border-radius: 6px;
                        cursor: pointer;
                        font-weight: 500;
                        width: 100%;
                    ">Add Tag</button>
                </div>

                <!-- Create New Category -->
                <div style="background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px;">
                    <h4 style="color: #ba68c8; margin-bottom: 15px; font-size: 15px;">📁 Create New Category</h4>
                    <div style="margin-bottom: 12px;">
                        <label style="color: #b0b0b0; font-size: 13px; display: block; margin-bottom: 5px;">Category Name:</label>
                        <input type="text" id="newCategoryName" placeholder="e.g., vibe" style="
                            width: 100%;
                            padding: 10px;
                            border-radius: 6px;
                            border: 1px solid rgba(255,255,255,0.2);
                            background: #1a1a2e;
                            color: #fff;
                            font-size: 14px;
                            box-sizing: border-box;
                        ">
                    </div>
                    <div style="margin-bottom: 12px;">
                        <label style="color: #b0b0b0; font-size: 13px; display: block; margin-bottom: 5px;">Initial Tags (comma-separated):</label>
                        <input type="text" id="newCategoryTags" placeholder="e.g., cozy, edgy, playful" style="
                            width: 100%;
                            padding: 10px;
                            border-radius: 6px;
                            border: 1px solid rgba(255,255,255,0.2);
                            background: #1a1a2e;
                            color: #fff;
                            font-size: 14px;
                            box-sizing: border-box;
                        ">
                    </div>
                    <button onclick="createVocabCategory()" style="
                        background: linear-gradient(135deg, #ba68c8, #9c27b0);
                        color: #fff;
                        border: none;
                        padding: 10px 20px;
                        border-radius: 6px;
                        cursor: pointer;
                        font-weight: 500;
                        width: 100%;
                    ">Create Category</button>
                </div>
            </div>

            <!-- Current Custom Vocabulary Display -->
            <div id="customVocabDisplay" style="margin-top: 20px; background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px;">
                <h4 style="color: #64b5f6; margin-bottom: 15px; font-size: 15px;">📋 Custom Vocabulary</h4>
                <div id="customVocabList" style="color: #b0b0b0; font-size: 13px;">
                    <em>Loading custom vocabulary...</em>
                </div>
            </div>
        </div>

        <!-- Technical Stack Footer -->
        <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.1); text-align: center;">
            <p style="color: #666; font-size: 13px;">
                <strong>Tech Stack:</strong> GPT-5.2 (canonical tagging at ingestion) • Supabase (storage) • Python/Flask (backend)
            </p>
            <p style="color: #555; font-size: 11px; margin-top: 8px;">
                Policy Version: tag_policy_v2.3 • Formality: AI-generated with rule-based comparison
            </p>
        </div>
    </div>

    <!-- Tag Feedback Modal (removal, addition, change) -->
    <div id="tagFeedbackModal" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 10000; justify-content: center; align-items: center;">
        <div style="background: #1a1a1a; border-radius: 12px; padding: 24px; max-width: 480px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.5); border: 1px solid #333;">
            <h3 id="tagFeedbackTitle" style="margin: 0 0 16px 0; color: #fff; font-size: 18px;">Tag Feedback</h3>
            <p id="tagFeedbackDescription" style="color: #aaa; margin-bottom: 16px; font-size: 14px;"></p>
            <div style="margin-bottom: 16px;">
                <label id="tagFeedbackReasonLabel" style="color: #888; font-size: 12px; display: block; margin-bottom: 6px;">Why is this tag incorrect?</label>
                <textarea id="tagFeedbackReason" placeholder="Optional feedback for AI training" style="width: 100%; height: 80px; padding: 12px; border: 1px solid #444; border-radius: 8px; background: #222; color: #fff; font-size: 14px; resize: vertical; box-sizing: border-box;"></textarea>
            </div>
            <div id="tagFeedbackCategoryWrap" style="margin-bottom: 20px;">
                <label style="color: #888; font-size: 12px; display: block; margin-bottom: 6px;">Feedback Category</label>
                <select id="tagFeedbackCategory" style="width: 100%; padding: 10px 12px; border: 1px solid #444; border-radius: 8px; background: #222; color: #fff; font-size: 14px;">
                    <option value="incorrect_value">Incorrect value (wrong tag)</option>
                    <option value="not_applicable">Not applicable to this item</option>
                    <option value="ambiguous">Ambiguous/subjective</option>
                    <option value="missing_context">AI lacked context</option>
                    <option value="other">Other</option>
                </select>
            </div>
            <div style="display: flex; gap: 12px; justify-content: flex-end;">
                <button id="tagFeedbackCancel" onclick="closeTagFeedbackModal(false)" style="padding: 10px 20px; border: 1px solid #444; border-radius: 8px; background: transparent; color: #aaa; cursor: pointer; font-size: 14px;">Cancel</button>
                <button id="tagFeedbackConfirm" onclick="closeTagFeedbackModal(true)" style="padding: 10px 20px; border: none; border-radius: 8px; color: white; cursor: pointer; font-size: 14px; font-weight: 500;">Confirm</button>
            </div>
        </div>
    </div>
</body>
</html>
"""


@app.route("/")
def index():
    """Serve the main viewer page."""
    supabase_url = os.getenv("SUPABASE_URL", "")
    return render_template_string(
        HTML_TEMPLATE, use_supabase=USE_SUPABASE, supabase_url=supabase_url
    )


CURATION_STATS_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Curation Stats - Training Data</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js" async></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f8f9fa;
            min-height: 100vh;
            color: #1a1a1a;
        }
        header {
            background: #1a1a1a;
            color: #fff;
            padding: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        header h1 { font-size: 22px; font-weight: 600; }
        .back-link {
            color: #fff;
            text-decoration: none;
            font-size: 14px;
            opacity: 0.9;
        }
        .back-link:hover { opacity: 1; }
        main {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px 24px;
        }
        .section {
            background: #fff;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        }
        .section h2 {
            font-size: 16px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #555;
            margin-bottom: 20px;
            padding-bottom: 8px;
            border-bottom: 2px solid #eee;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 16px;
        }
        .metric {
            padding: 16px;
            background: #f8f9fa;
            border-radius: 8px;
        }
        .metric-value { font-size: 28px; font-weight: 700; color: #1a1a1a; }
        .metric-label { font-size: 12px; color: #666; margin-top: 4px; }
        .progress-bar-wrap {
            height: 12px;
            background: #e9ecef;
            border-radius: 6px;
            overflow: hidden;
            margin: 16px 0;
        }
        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, #28a745, #20c997);
            border-radius: 6px;
            transition: width 0.3s ease;
        }
        .chart-container { height: 280px; margin-top: 16px; }
        .export-btn {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: #1a1a1a;
            color: #fff;
            border: none;
            padding: 12px 24px;
            font-size: 14px;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .export-btn:hover { background: #333; }
        .export-btn:disabled { background: #999; cursor: not-allowed; }
        .tag-list { list-style: none; }
        .tag-list li {
            padding: 8px 12px;
            margin: 4px 0;
            background: #f8f9fa;
            border-radius: 6px;
            display: flex;
            justify-content: space-between;
            font-size: 13px;
        }
        .tag-list .count { font-weight: 600; color: #333; }
        .error { color: #dc3545; padding: 16px; }
        .loading { color: #666; padding: 24px; text-align: center; }
    </style>
</head>
<body>
    <header>
        <h1>Curation & Training Data Stats</h1>
        <a href="/" class="back-link">← Back to Viewer</a>
    </header>
    <main>
        <div id="loading" class="section loading">Loading statistics…</div>
        <div id="content" style="display:none;">
            <section class="section">
                <h2>Curation Progress</h2>
                <div class="metrics-grid">
                    <div class="metric">
                        <div class="metric-value" id="total-products">0</div>
                        <div class="metric-label">Total Products</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value" id="curated-count">0</div>
                        <div class="metric-label">Products Curated</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value" id="remaining">0</div>
                        <div class="metric-label">Remaining</div>
                    </div>
                </div>
                <div class="progress-bar-wrap">
                    <div class="progress-bar-fill" id="progress-fill" style="width: 0%;"></div>
                </div>
                <div style="font-size: 13px; color: #666;" id="progress-pct">0% complete</div>
            </section>

            <section class="section">
                <h2>Training Data Quality</h2>
                <div class="metrics-grid">
                    <div class="metric">
                        <div class="metric-value" id="total-records">0</div>
                        <div class="metric-label">Total Curation Records</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value" id="approved-count">0</div>
                        <div class="metric-label">Approved for Training</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value" id="excluded-count">0</div>
                        <div class="metric-label">Excluded</div>
                    </div>
                </div>
                <div id="confidence-chart" class="chart-container"></div>
                <div id="error-type-chart" class="chart-container"></div>
                <div id="category-chart" class="chart-container"></div>
            </section>

            <section class="section">
                <h2>Common Issues</h2>
                <p style="font-size: 13px; color: #666; margin-bottom: 12px;">Top 10 most corrected tags (removed during curation)</p>
                <ul class="tag-list" id="top-tags"></ul>
                <p style="font-size: 13px; color: #666; margin: 16px 0 8px;">Most common error types</p>
                <ul class="tag-list" id="top-errors"></ul>
                <div class="metric" style="margin-top: 16px;">
                    <div class="metric-value" id="products-corrected-multiple">0</div>
                    <div class="metric-label">Products corrected multiple times</div>
                </div>
            </section>

            <section class="section">
                <h2>Export Status</h2>
                <div class="metrics-grid">
                    <div class="metric">
                        <div class="metric-value" id="last-export-date">—</div>
                        <div class="metric-label">Last Export Date</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value" id="last-export-count">0</div>
                        <div class="metric-label">Examples in Last Export</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value" id="last-export-size">—</div>
                        <div class="metric-label">File Size</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value" id="estimated-cost">—</div>
                        <div class="metric-label">Est. Training Cost</div>
                    </div>
                </div>
                <button class="export-btn" id="export-btn">
                    Export Training Data
                </button>
            </section>
        </div>
        <div id="error" class="section error" style="display:none;"></div>
    </main>
    <script>
        async function loadStats() {
            const loading = document.getElementById('loading');
            const content = document.getElementById('content');
            const errEl = document.getElementById('error');
            try {
                const r = await fetch('/api/curation_stats');
                const data = await r.json();
                if (!r.ok) throw new Error(data.error || 'Failed to load stats');
                loading.style.display = 'none';
                content.style.display = 'block';
                render(data);
            } catch (e) {
                loading.style.display = 'none';
                errEl.style.display = 'block';
                errEl.textContent = 'Error: ' + e.message;
            }
        }

        function fmt(n) { return n != null ? n.toLocaleString() : '0'; }
        function fmtBytes(b) {
            if (b == null || b === 0) return '—';
            if (b < 1024) return b + ' B';
            if (b < 1024*1024) return (b/1024).toFixed(1) + ' KB';
            return (b/(1024*1024)).toFixed(2) + ' MB';
        }
        function fmtDate(iso) {
            if (!iso) return '—';
            try {
                const d = new Date(iso);
                return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:2,minute:2});
            } catch (_) { return iso; }
        }

        function render(data) {
            const cp = data.curation_progress || {};
            document.getElementById('total-products').textContent = fmt(cp.total_products);
            document.getElementById('curated-count').textContent = fmt(cp.curated_count);
            document.getElementById('remaining').textContent = fmt(cp.remaining);
            const pct = cp.progress_pct || 0;
            document.getElementById('progress-fill').style.width = pct + '%';
            document.getElementById('progress-pct').textContent = pct + '% complete';

            const tq = data.training_quality || {};
            document.getElementById('total-records').textContent = fmt(tq.total_records);
            document.getElementById('approved-count').textContent = fmt(tq.approved_count);
            document.getElementById('excluded-count').textContent = fmt(tq.excluded_count);

            const bc = tq.by_confidence || {};
            Plotly.newPlot('confidence-chart', [{
                x: ['1★', '2★', '3★', '4★', '5★'],
                y: [bc[1]||0, bc[2]||0, bc[3]||0, bc[4]||0, bc[5]||0],
                type: 'bar',
                marker: { color: '#6c757d' }
            }], {
                title: 'By Confidence Level',
                margin: { t: 40, r: 20, b: 40, l: 50 },
                xaxis: { title: '' },
                yaxis: { title: 'Count' }
            }, { responsive: true });

            const be = tq.by_error_type || {};
            const errKeys = Object.keys(be);
            const errVals = Object.values(be);
            Plotly.newPlot('error-type-chart', [{
                x: errKeys.length ? errKeys : ['(none)'],
                y: errVals.length ? errVals : [0],
                type: 'bar',
                marker: { color: '#0d6efd' }
            }], {
                title: 'By Error Type',
                margin: { t: 40, r: 20, b: 80, l: 50 },
                xaxis: { tickangle: -35 },
                yaxis: { title: 'Count' }
            }, { responsive: true });

            const bc2 = tq.by_category || {};
            const catKeys = Object.keys(bc2).filter(k => bc2[k] > 0);
            Plotly.newPlot('category-chart', [{
                labels: catKeys.length ? catKeys : ['other'],
                values: catKeys.length ? catKeys.map(k => bc2[k]) : [0],
                type: 'pie',
                hole: 0.4,
                marker: { colors: ['#28a745','#17a2b8','#ffc107','#6f42c1','#6c757d'] }
            }], {
                title: 'By Category (tops, bottoms, outerwear)',
                margin: { t: 40, r: 20, b: 20, l: 20 },
                showlegend: true
            }, { responsive: true });

            const ci = data.common_issues || {};
            const topTags = document.getElementById('top-tags');
            topTags.innerHTML = (ci.top_corrected_tags || []).map(x =>
                '<li><span>' + x.tag + '</span><span class="count">' + x.count + ' removed</span></li>'
            ).join('') || '<li style="color:#999;">No data</li>';

            const topErrors = document.getElementById('top-errors');
            topErrors.innerHTML = (ci.most_common_error_types || []).map(([k,v]) =>
                '<li><span>' + k + '</span><span class="count">' + v + '</span></li>'
            ).join('') || '<li style="color:#999;">No data</li>';

            document.getElementById('products-corrected-multiple').textContent =
                fmt(ci.products_corrected_multiple);

            const ex = data.export || {};
            document.getElementById('last-export-date').textContent = fmtDate(ex.last_export_at);
            document.getElementById('last-export-count').textContent = fmt(ex.example_count);
            document.getElementById('last-export-size').textContent = fmtBytes(ex.file_size_bytes);
            document.getElementById('estimated-cost').textContent =
                ex.estimated_cost_usd != null ? '$' + ex.estimated_cost_usd.toFixed(2) : '—';
        }

        document.getElementById('export-btn').addEventListener('click', async function() {
            const btn = this;
            btn.disabled = true;
            btn.textContent = 'Exporting…';
            try {
                const r = await fetch('/api/export_training_data');
                if (!r.ok) {
                    const j = await r.json().catch(() => ({}));
                    throw new Error(j.error || 'Export failed');
                }
                const blob = await r.blob();
                const a = document.createElement('a');
                a.href = URL.createObjectURL(blob);
                a.download = 'training.jsonl';
                a.click();
                URL.revokeObjectURL(a.href);
                loadStats();
            } catch (e) {
                alert('Export failed: ' + e.message);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Export Training Data';
            }
        });

        loadStats();
    </script>
</body>
</html>
"""


@app.route("/curation/stats")
def curation_stats_page():
    """Serve curation statistics and training data dashboard."""
    return render_template_string(CURATION_STATS_TEMPLATE)


@app.route("/api/products")
def api_products():
    """API endpoint to get all products."""
    products = get_all_products()
    return jsonify(products)


def _get_image_extension(url: str, content_type: str = "image/jpeg") -> str:
    """Return file extension for image from URL or content-type."""
    url_lower = url.lower()
    if ".jpg" in url_lower or ".jpeg" in url_lower:
        return ".jpg"
    if ".png" in url_lower:
        return ".png"
    if ".webp" in url_lower:
        return ".webp"
    if ".gif" in url_lower:
        return ".gif"
    if "png" in (content_type or ""):
        return ".png"
    if "webp" in (content_type or ""):
        return ".webp"
    if "gif" in (content_type or ""):
        return ".gif"
    return ".jpg"


@app.route("/api/products/<product_id>/stored-images", methods=["POST"])
def set_stored_images(product_id):
    """Reselect which 2 images from image_urls_all are stored in DB and Supabase Storage.

    Body: {"stored_indices": [i, j]} — 0-based indices into image_urls_all.
    Downloads those 2 images, uploads to storage (replacing existing), updates product row.
    """
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json() or {}
    indices = data.get("stored_indices")
    if not isinstance(indices, list) or len(indices) != 2:
        return (
            jsonify(
                {
                    "error": "Exactly 2 indices required: stored_indices: [i, j] (0-based into full image list)"
                }
            ),
            400,
        )
    i, j = int(indices[0]), int(indices[1])

    try:
        row = (
            supabase_client.table("products")
            .select("product_id, category, image_urls_all")
            .eq("product_id", product_id)
            .execute()
        )
        if not row.data:
            return jsonify({"error": "Product not found"}), 404
        product = row.data[0]
        all_urls = product.get("image_urls_all") or []
        if len(all_urls) < 2:
            return (
                jsonify(
                    {
                        "error": "Product has no image_urls_all (or fewer than 2). Re-scrape to enable reselecting stored images."
                    }
                ),
                400,
            )
        for idx in (i, j):
            if idx < 0 or idx >= len(all_urls):
                return jsonify({"error": f"Index {idx} out of range (0–{len(all_urls) - 1})"}), 400

        two_urls = [all_urls[i], all_urls[j]]
        category = product.get("category") or "other"
        storage_category = "footwear" if category in ("shoes", "footwear") else category

        # Download and upload (sync) — same path pattern as pipeline so we overwrite
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://www.zara.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        storage_paths = []
        for pos, url in enumerate(two_urls):
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                image_data = resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                ext = _get_image_extension(url, content_type)
                path = f"{storage_category}/{product_id}/image_{pos}{ext}"
                supabase_client.storage.from_(BUCKET_NAME).upload(
                    path,
                    image_data,
                    {"content-type": content_type, "upsert": "true"},
                )
                storage_paths.append(path)

        # Update product: image_paths, image_urls (the 2 we store), image_urls_stored_indices
        update = {
            "image_paths": storage_paths,
            "image_urls": two_urls,
            "image_urls_stored_indices": [i, j],
            "image_count": 2,
        }
        supabase_client.table("products").update(update).eq(
            "product_id", product_id
        ).execute()

        return jsonify(
            {
                "success": True,
                "message": "Stored images updated",
                "image_paths": storage_paths,
                "stored_indices": [i, j],
            }
        )
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"Failed to download image: {e.code} {e.reason}"}), 400
    except urllib.error.URLError as e:
        return jsonify({"error": f"Failed to download image: {e.reason}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/products/<product_id>", methods=["DELETE"])
def delete_product(product_id):
    """Delete a product from the database and storage."""
    if not USE_SUPABASE or not supabase_client:
        return (
            jsonify(
                {"error": "Supabase not configured. Deletion only works with Supabase."}
            ),
            400,
        )

    try:
        # First, get the product to find its image paths
        product_result = (
            supabase_client.table("products")
            .select("image_paths, name")
            .eq("product_id", product_id)
            .execute()
        )

        if not product_result.data:
            return jsonify({"error": "Product not found"}), 404

        product = product_result.data[0]
        image_paths = product.get("image_paths", [])
        images_deleted = 0

        # Delete images from storage
        if image_paths:
            try:
                supabase_client.storage.from_(BUCKET_NAME).remove(image_paths)
                images_deleted = len(image_paths)
            except Exception as e:
                print(f"Warning: Could not delete some images: {e}")

        # Delete from curated_metadata table (if exists)
        try:
            supabase_client.table("curated_metadata").delete().eq(
                "product_id", product_id
            ).execute()
        except Exception:
            pass  # Table may not exist

        # Delete from curation_status table (if exists)
        try:
            supabase_client.table("curation_status").delete().eq(
                "product_id", product_id
            ).execute()
        except Exception:
            pass  # Table may not exist

        # Delete from rejected_tags table (if exists)
        try:
            supabase_client.table("rejected_tags").delete().eq(
                "product_id", product_id
            ).execute()
        except Exception:
            pass  # Table may not exist

        # Delete from ai_generated_tags table (if exists)
        try:
            supabase_client.table("ai_generated_tags").delete().eq(
                "product_id", product_id
            ).execute()
        except Exception:
            pass  # Table may not exist

        # Delete the product itself
        supabase_client.table("products").delete().eq(
            "product_id", product_id
        ).execute()

        # Also remove from local tracking database
        try:
            from src.tracking import ProductTracker

            tracker = ProductTracker()
            tracker.remove_product(product_id)
        except Exception as e:
            print(f"Warning: Could not remove from tracking DB: {e}")

        return jsonify(
            {
            "success": True,
            "message": f"Product {product_id} deleted successfully",
                "images_deleted": images_deleted,
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset-metadata/<product_id>", methods=["DELETE"])
def reset_product_metadata(product_id):
    """Reset a product's metadata to its original scraped state.

    This removes:
    - All curated_metadata entries for this product
    - All ai_generated_tags entries for this product
    - All rejected_tags entries for this product
    - The curation_status entry for this product

    The original scraped product data (including inferred style_tags) remains unchanged.
    """
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    try:
        curated_deleted = 0
        ai_deleted = 0
        rejected_deleted = 0
        status_deleted = 0

        # Delete curated metadata
        try:
            result = (
                supabase_client.table("curated_metadata")
                .delete()
                .eq("product_id", product_id)
                .execute()
            )
            curated_deleted = len(result.data) if result.data else 0
        except Exception as e:
            print(f"Note: Could not delete curated_metadata: {e}")

        # Delete AI-generated tags
        try:
            result = (
                supabase_client.table("ai_generated_tags")
                .delete()
                .eq("product_id", product_id)
                .execute()
            )
            ai_deleted = len(result.data) if result.data else 0
        except Exception as e:
            print(f"Note: Could not delete ai_generated_tags: {e}")

        # Delete rejected tags
        try:
            result = (
                supabase_client.table("rejected_tags")
                .delete()
                .eq("product_id", product_id)
                .execute()
            )
            rejected_deleted = len(result.data) if result.data else 0
        except Exception as e:
            print(f"Note: Could not delete rejected_tags: {e}")

        # Delete curation status
        try:
            result = (
                supabase_client.table("curation_status")
                .delete()
                .eq("product_id", product_id)
                .execute()
            )
            status_deleted = len(result.data) if result.data else 0
        except Exception as e:
            print(f"Note: Could not delete curation_status: {e}")

        return jsonify(
            {
                "success": True,
                "message": f"Product {product_id} reset to original state",
                "curated_deleted": curated_deleted,
                "ai_deleted": ai_deleted,
                "rejected_deleted": rejected_deleted,
                "status_deleted": status_deleted,
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# PRODUCT CATEGORY UPDATE API
# ============================================


@app.route("/api/update_product_category", methods=["POST"])
def update_product_category():
    """Update a product's category for reclassification.

    This allows curators to move products between subcategories.
    For example, moving a sweater to cardigans, or a t-shirt to polos.
    """
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    try:
        data = request.get_json()
        product_id = data.get("product_id")
        new_category = data.get("new_category")

        if not product_id or not new_category:
            return jsonify({"error": "product_id and new_category required"}), 400

        # Update the product's category in Supabase
        result = (
            supabase_client.table("products")
            .update({"category": new_category})
            .eq("product_id", product_id)
            .execute()
        )

        if result.data:
            return jsonify(
                {
                    "success": True,
                    "product_id": product_id,
                    "new_category": new_category,
                    "message": f"Category updated to {new_category}",
                }
            )
        else:
            return jsonify({"error": "Product not found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# CANONICAL TAGS CURATION API
# ============================================


@app.route("/api/canonical_tags/<product_id>", methods=["GET"])
def get_canonical_tags(product_id):
    """Get the current canonical tags for a product.
    Returns tags_final (for editing), tags_ai_raw (original prediction for curation history).
    """
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    try:
        result = (
            supabase_client.table("products")
            .select(
                "tags_final, tags_ai_raw, curation_status_refitd, tag_policy_version"
            )
            .eq("product_id", product_id)
            .execute()
        )
        if result.data:
            return jsonify(result.data[0])
        return jsonify({"error": "Product not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/curation_preview", methods=["POST"])
def curation_preview():
    """Compute changes and inferred error types for curation form (pre-populate checkboxes)."""
    data = request.get_json() or {}
    original_tags = data.get("original_tags") or {}
    corrected_tags = data.get("corrected_tags") or {}

    try:
        from src.utils.tag_comparison import compute_tag_changes, infer_error_types

        changes = compute_tag_changes(original_tags, corrected_tags)
        error_types = infer_error_types(changes)
        return jsonify({"changes": changes, "error_types": error_types})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/save_curation_history", methods=["POST"])
def save_curation_history():
    """Save curation history and update product (curated_at, curated_by, training_eligible).
    Called when a curator completes editing and saves.
    """
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json() or {}
    product_id = data.get("product_id")
    original_tags = data.get("original_tags")
    corrected_tags = data.get("corrected_tags")
    curator_notes = data.get("curator_notes")
    confidence = data.get("confidence")
    curator_id = data.get("curator_id", "unknown")
    error_types = data.get("error_types")
    include_in_training = data.get("include_in_training")

    if not product_id or original_tags is None or corrected_tags is None:
        return (
            jsonify({"error": "Missing product_id, original_tags, or corrected_tags"}),
            400,
        )

    try:
        from src.services.curation_history_service import CurationHistoryService

        service = CurationHistoryService()
        service.save_curation(
            product_id=product_id,
            original_tags=original_tags or {},
            corrected_tags=corrected_tags or {},
            curator_notes=curator_notes,
            confidence=confidence,
            curator_id=curator_id,
            model_version="gpt-4o-2024-11-20",
            prompt_version="v2.1",
            error_types=error_types,
            include_in_training=include_in_training,
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/canonical_tags/<product_id>", methods=["PUT"])
def update_canonical_tags(product_id):
    """Update canonical tags for a product (full replacement of tags_final).
    When original_tags is provided, saves curation history before updating.
    """
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    tags_final = data.get("tags_final")
    curator = data.get("curator")
    original_tags = data.get("original_tags")
    curator_notes = data.get("curator_notes")
    confidence = data.get("confidence")

    if tags_final is None:
        return jsonify({"error": "Missing tags_final"}), 400

    try:
        if original_tags is not None and curator:
            try:
                from src.services.curation_history_service import CurationHistoryService

                service = CurationHistoryService()
                service.save_curation(
                    product_id=product_id,
                    original_tags=original_tags,
                    corrected_tags=tags_final,
                    curator_notes=curator_notes,
                    confidence=confidence,
                    curator_id=curator,
                    model_version="gpt-4o-2024-11-20",
                    prompt_version="v2.1",
                )
            except Exception as e:
                print(f"Warning: Could not save curation history: {e}")

        result = (
            supabase_client.table("products")
            .update(
                {
                    "tags_final": tags_final,
                    "curation_status_refitd": "approved" if curator else "needs_review",
                }
            )
            .eq("product_id", product_id)
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/canonical_tags/<product_id>/field", methods=["PATCH"])
def patch_canonical_tag_field(product_id):
    """Add or remove a value from a specific canonical tag field.

    This is more granular than full replacement - it modifies one field at a time.
    For array fields (style_identity, context, etc.), you can add/remove items.
    For single-value fields (silhouette, pattern), you replace the value.

    When removing tags, optional feedback can be provided for AI learning:
    - feedback_reason: Free-text explanation of why the tag was incorrect
    - feedback_category: Category of the correction (incorrect_value, not_applicable, etc.)
    """
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    field_name = data.get("field_name")  # e.g., "style_identity", "silhouette"
    action = data.get("action")  # "add", "remove", or "set"
    value = data.get("value")  # The tag value to add/remove/set
    curator = data.get("curator")

    # Optional feedback fields for tag removal learning
    feedback_reason = data.get("feedback_reason")
    feedback_category = data.get("feedback_category")

    if not all([field_name, action, curator]):
        return (
            jsonify({"error": "Missing required fields (field_name, action, curator)"}),
            400,
        )

    # Value is required for "add" action only
    # "set" can have null value (to clear a field), "remove" doesn't need value
    if action == "add" and value is None:
        return (
            jsonify({"error": "Missing required field 'value' for add action"}),
            400,
        )

    # Array fields vs single-value fields
    array_fields = ["style_identity", "context", "construction_details", "pairing_tags"]
    single_fields = [
        "silhouette",
        "pattern",
        "formality",
        "fit",
        "length",
        "shoe_type",
        "profile",
        "closure",
        "top_layer_role",
    ]

    try:
        # First, get current tags_final
        result = (
            supabase_client.table("products")
            .select("tags_final")
            .eq("product_id", product_id)
            .execute()
        )

        if not result.data:
            return jsonify({"error": "Product not found"}), 404

        tags_final = result.data[0].get("tags_final") or {}

        # Track values for feedback (deleted_tags, added_tags, modified_tags)
        removed_value = None
        added_value = None
        modified_from = None
        modified_to = None

        for key in ("deleted_tags", "added_tags", "modified_tags"):
            if key not in tags_final:
                tags_final[key] = {}

        # Apply the modification
        if field_name in array_fields:
            current_list = tags_final.get(field_name, []) or []
            if action == "add":
                if value not in current_list:
                    current_list.append(value)
                    added_value = value
                    if feedback_reason:
                        if field_name not in tags_final["added_tags"]:
                            tags_final["added_tags"][field_name] = []
                        tags_final["added_tags"][field_name].append({
                            "value": value,
                            "reason": feedback_reason,
                            "curator": curator,
                        })
                # Remove from deleted_tags if re-adding
                if field_name in tags_final["deleted_tags"]:
                    deleted_list = tags_final["deleted_tags"][field_name]
                    tags_final["deleted_tags"][field_name] = [
                        v
                        for v in deleted_list
                        if (isinstance(v, dict) and v.get("value") != value)
                        or (isinstance(v, str) and v != value)
                    ]
            elif action == "remove":
                removed_value = value
                current_list = [v for v in current_list if v != value]
                # Track as deleted with rejection context
                if field_name not in tags_final["deleted_tags"]:
                    tags_final["deleted_tags"][field_name] = []
                # Store as object with value and rejection reason
                deletion_entry = {
                    "value": value,
                    "reason": feedback_reason,
                    "category": feedback_category,
                    "curator": curator,
                }
                # Check if already exists (by value)
                existing = [
                    v
                    for v in tags_final["deleted_tags"][field_name]
                    if (isinstance(v, dict) and v.get("value") == value)
                    or (isinstance(v, str) and v == value)
                ]
                if not existing:
                    tags_final["deleted_tags"][field_name].append(deletion_entry)
            elif action == "set":
                current_list = value if isinstance(value, list) else [value]
            tags_final[field_name] = current_list
        elif field_name in single_fields:
            if action == "remove" or value == "" or value is None:
                removed_value = tags_final.get(field_name)
                if removed_value:
                    tags_final["deleted_tags"][field_name] = {
                        "value": removed_value,
                        "reason": feedback_reason,
                        "category": feedback_category,
                        "curator": curator,
                    }
                tags_final[field_name] = None
            else:
                prev_value = tags_final.get(field_name)
                if prev_value and str(prev_value) != str(value):
                    modified_from = prev_value
                    modified_to = value
                    if feedback_reason:
                        tags_final["modified_tags"][field_name] = {
                            "from": prev_value,
                            "to": value,
                            "reason": feedback_reason,
                            "curator": curator,
                        }
                if field_name in tags_final["deleted_tags"]:
                    del tags_final["deleted_tags"][field_name]
                tags_final[field_name] = value
        else:
            return jsonify({"error": f"Unknown field: {field_name}"}), 400

        # Save back to database
        update_result = (
            supabase_client.table("products")
            .update(
                {
                    "tags_final": tags_final,
                    "curation_status_refitd": "approved",
                }
            )
            .eq("product_id", product_id)
            .execute()
        )

        # Store feedback for AI learning if provided during tag removal
        if removed_value and (feedback_reason or feedback_category):
            try:
                supabase_client.table("tag_correction_feedback").insert(
                    {
                        "product_id": product_id,
                        "field_name": field_name,
                        "removed_value": str(removed_value),
                        "feedback_reason": feedback_reason,
                        "feedback_category": feedback_category,
                        "curator": curator,
                    }
                ).execute()
            except Exception as feedback_error:
                # Log but don't fail the main operation if feedback storage fails
                print(
                    f"Warning: Failed to store tag correction feedback: {feedback_error}"
                )

        return jsonify(
            {"success": True, "tags_final": tags_final, "data": update_result.data}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/curated", methods=["POST"])
def save_curated_metadata():
    """Save a curated metadata entry to the database."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    from flask import request

    data = request.get_json()
    product_id = data.get("product_id")
    field_name = data.get("field_name")
    field_value = data.get("field_value")
    curator = data.get("curator")

    if not all([product_id, field_name, field_value, curator]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        result = (
            supabase_client.table("curated_metadata")
            .insert(
                {
                    "product_id": product_id,
                    "field_name": field_name,
                    "field_value": field_value,
                    "curator": curator,
                }
            )
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        # Handle duplicate entries gracefully
        if "duplicate" in str(e).lower():
            return jsonify({"success": True, "message": "Already exists"})
        return jsonify({"error": str(e)}), 500


@app.route("/api/curated/<product_id>")
def get_curated_metadata(product_id):
    """Get all curated metadata for a product."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify([])

    try:
        result = (
            supabase_client.table("curated_metadata")
            .select("*")
            .eq("product_id", product_id)
            .execute()
        )
        return jsonify(result.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/curated", methods=["DELETE"])
def delete_curated_metadata():
    """Delete a curated metadata entry from the database."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")
    field_name = data.get("field_name")
    field_value = data.get("field_value")
    curator = data.get("curator")

    if not all([product_id, field_name, field_value, curator]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        result = (
            supabase_client.table("curated_metadata")
            .delete()
            .eq("product_id", product_id)
            .eq("field_name", field_name)
            .eq("field_value", field_value)
            .eq("curator", curator)
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rejected_tags", methods=["POST"])
def reject_inferred_tag():
    """Mark an inferred tag as rejected (incorrect). Saved for ML training."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")
    field_name = data.get("field_name")
    field_value = data.get("field_value")
    original_reasoning = data.get("original_reasoning")
    curator = data.get("curator")
    rejection_reason = data.get("rejection_reason")

    if not all([product_id, field_name, field_value, curator]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        result = (
            supabase_client.table("rejected_inferred_tags")
            .insert(
                {
                    "product_id": product_id,
                    "field_name": field_name,
                    "field_value": field_value,
                    "original_reasoning": original_reasoning,
                    "curator": curator,
                    "rejection_reason": rejection_reason,
                }
            )
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        # Handle duplicate entries gracefully
        if "duplicate" in str(e).lower():
            return jsonify({"success": True, "message": "Already rejected"})
        return jsonify({"error": str(e)}), 500


@app.route("/api/rejected_tags/<product_id>")
def get_rejected_tags(product_id):
    """Get all rejected inferred tags for a product."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify([])

    try:
        result = (
            supabase_client.table("rejected_inferred_tags")
            .select("*")
            .eq("product_id", product_id)
            .execute()
        )
        return jsonify(result.data or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rejected_tags", methods=["DELETE"])
def unreject_inferred_tag():
    """Remove a tag from the rejected list (undo rejection)."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")
    field_name = data.get("field_name")
    field_value = data.get("field_value")

    if not all([product_id, field_name, field_value]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        result = (
            supabase_client.table("rejected_inferred_tags")
            .delete()
            .eq("product_id", product_id)
            .eq("field_name", field_name)
            .eq("field_value", field_value)
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# AI GENERATED TAGS ENDPOINTS
# ============================================


@app.route("/api/ai_tags/<product_id>")
def get_ai_generated_tags(product_id):
    """Get all AI-generated tags for a product."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify([])

    try:
        result = (
            supabase_client.table("ai_generated_tags")
            .select("*")
            .eq("product_id", product_id)
            .execute()
        )
        return jsonify(result.data or [])
    except Exception as e:
        # Table might not exist yet
        print(f"Error fetching AI tags: {e}")
        return jsonify([])


@app.route("/api/ai_tags", methods=["POST"])
def save_ai_generated_tag():
    """Save an AI-generated tag to the database."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")
    field_name = data.get("field_name", "style_tag")
    field_value = data.get("field_value")
    model_name = data.get("model_name", "moondream")
    reasoning = data.get("reasoning")

    if not all([product_id, field_value]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        result = (
            supabase_client.table("ai_generated_tags")
            .upsert(
                {
                    "product_id": product_id,
                    "field_name": field_name,
                    "field_value": field_value,
                    "model_name": model_name,
                    "reasoning": reasoning,
                },
                on_conflict="product_id,field_name,field_value",
            )
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        # Handle duplicate entries gracefully
        if "duplicate" in str(e).lower():
            return jsonify({"success": True, "message": "Already exists"})
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai_tags", methods=["DELETE"])
def delete_ai_generated_tag():
    """Delete an AI-generated tag from the database."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")
    field_name = data.get("field_name")
    field_value = data.get("field_value")

    if not all([product_id, field_name, field_value]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        result = (
            supabase_client.table("ai_generated_tags")
            .delete()
            .eq("product_id", product_id)
            .eq("field_name", field_name)
            .eq("field_value", field_value)
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai_tags/batch", methods=["POST"])
def save_ai_generated_tags_batch():
    """Save multiple AI-generated tags for a product at once."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")
    tags = data.get("tags", [])
    model_name = data.get("model_name", "moondream")

    if not product_id or not tags:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        # Prepare batch insert data
        records = [
            {
                "product_id": product_id,
                "field_name": "style_tag",
                "field_value": tag,
                "model_name": model_name,
            }
            for tag in tags
        ]

        result = (
            supabase_client.table("ai_generated_tags")
            .upsert(records, on_conflict="product_id,field_name,field_value")
            .execute()
        )
        return jsonify({"success": True, "count": len(tags), "data": result.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# CURATION STATUS ENDPOINTS
# ============================================


@app.route("/api/curation_status/<product_id>")
def get_curation_status(product_id):
    """Get curation status for a product."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify(None)

    try:
        result = (
            supabase_client.table("curation_status")
            .select("*")
            .eq("product_id", product_id)
            .execute()
        )
        if result.data and len(result.data) > 0:
            return jsonify(result.data[0])
        return jsonify(None)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/curation_status", methods=["POST"])
def mark_product_curated():
    """Mark a product as fully curated/complete."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")
    curator = data.get("curator")
    notes = data.get("notes")

    if not all([product_id, curator]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        # Upsert - insert or update if exists
        result = (
            supabase_client.table("curation_status")
            .upsert(
                {
                    "product_id": product_id,
                    "curator": curator,
                    "status": "complete",
                    "notes": notes,
                    "updated_at": "now()",
                },
                on_conflict="product_id",
            )
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/curation_status", methods=["DELETE"])
def unmark_product_curated():
    """Remove curation status from a product (mark as incomplete)."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json()
    product_id = data.get("product_id")

    if not product_id:
        return jsonify({"error": "Missing product_id"}), 400

    try:
        result = (
            supabase_client.table("curation_status")
            .delete()
            .eq("product_id", product_id)
            .execute()
        )
        return jsonify({"success": True, "data": result.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# DASHBOARD STATISTICS ENDPOINTS
# ============================================


@app.route("/api/dashboard/stats")
def get_dashboard_stats():
    """Get comprehensive dashboard statistics."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    try:
        # Get all products
        products_result = supabase_client.table("products").select("*").execute()
        products = products_result.data or []

        # Get curation statuses
        curation_result = supabase_client.table("curation_status").select("*").execute()
        curation_data = curation_result.data or []
        curated_ids = {c["product_id"]: c["curator"] for c in curation_data}

        # Get curated metadata counts
        curated_meta_result = (
            supabase_client.table("curated_metadata").select("*").execute()
        )
        curated_metadata = curated_meta_result.data or []

        # Get rejected tags counts
        rejected_result = (
            supabase_client.table("rejected_inferred_tags").select("*").execute()
        )
        rejected_tags = rejected_result.data or []

        # Calculate statistics
        total_products = len(products)
        curated_products = len(curated_ids)
        pending_products = total_products - curated_products

        # Category breakdown with curator info
        category_stats = {}
        for p in products:
            cat = p.get("category", "Unknown")
            if cat not in category_stats:
                category_stats[cat] = {
                    "total": 0,
                    "curated": 0,
                    "pending": 0,
                    "by_curator": {},
                }
            category_stats[cat]["total"] += 1
            if p["product_id"] in curated_ids:
                category_stats[cat]["curated"] += 1
                curator = curated_ids[p["product_id"]]
                if curator not in category_stats[cat]["by_curator"]:
                    category_stats[cat]["by_curator"][curator] = 0
                category_stats[cat]["by_curator"][curator] += 1
            else:
                category_stats[cat]["pending"] += 1

        # Curator activity
        curator_stats = {}
        for c in curation_data:
            curator = c.get("curator", "Unknown")
            if curator not in curator_stats:
                curator_stats[curator] = {
                    "completed": 0,
                    "tags_added": 0,
                    "tags_rejected": 0,
                }
            curator_stats[curator]["completed"] += 1

        for cm in curated_metadata:
            curator = cm.get("curator", "Unknown")
            if curator not in curator_stats:
                curator_stats[curator] = {
                    "completed": 0,
                    "tags_added": 0,
                    "tags_rejected": 0,
                }
            curator_stats[curator]["tags_added"] += 1

        for rt in rejected_tags:
            curator = rt.get("curator", "Unknown")
            if curator not in curator_stats:
                curator_stats[curator] = {
                    "completed": 0,
                    "tags_added": 0,
                    "tags_rejected": 0,
                }
            curator_stats[curator]["tags_rejected"] += 1

        # Curated by curator breakdown for pie chart
        curated_by_curator = {}
        for c in curation_data:
            curator = c.get("curator", "Unknown")
            if curator not in curated_by_curator:
                curated_by_curator[curator] = 0
            curated_by_curator[curator] += 1

        # Recent activity (last 10 curated products)
        recent_curation = sorted(
            curation_data,
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )[:10]

        return jsonify(
            {
                "overview": {
                    "total_products": total_products,
                    "curated_products": curated_products,
                    "pending_products": pending_products,
                    "percent_complete": (
                        round(curated_products / total_products * 100, 1)
                        if total_products > 0
                        else 0
                    ),
                    "total_curated_tags": len(curated_metadata),
                    "total_rejected_tags": len(rejected_tags),
                    "curated_by_curator": curated_by_curator,
                },
                "by_category": category_stats,
                "by_curator": curator_stats,
                "recent_activity": recent_curation,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# CURATION STATS & EXPORT
# ============================================

EXPORT_META_PATH = Path(__file__).parent / "data" / "exports" / "last_export_meta.json"


def _parse_change_summary_for_removed(summary: str) -> list[str]:
    """Extract removed tags from change_summary like 'Removed: a, b; Added: c'."""
    if not summary:
        return []
    removed = []
    m = re.search(r"Removed:\s*([^;]+?)(?:;|$)", summary, re.IGNORECASE)
    if m:
        for part in m.group(1).split(","):
            tag = part.strip()
            if tag:
                removed.append(tag)
    return removed


@app.route("/api/curation_stats")
def get_curation_stats():
    """Return stats for curation progress, training quality, common issues, and export."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    try:
        # --- Curation Progress (products) ---
        products_result = supabase_client.table("products").select(
            "product_id, training_eligible"
        ).execute()
        products = products_result.data or []
        total_products = len(products)
        curated_count = sum(1 for p in products if p.get("training_eligible") is True)
        remaining = max(0, total_products - curated_count)
        progress_pct = (
            round(curated_count / total_products * 100, 1) if total_products > 0 else 0
        )

        # --- Curation History (may not exist) ---
        total_records = 0
        by_confidence = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        by_error_type = {}
        by_category = {"top": 0, "bottom": 0, "outerwear": 0, "footwear": 0, "other": 0}
        approved_count = 0
        excluded_count = 0
        top_corrected_tags = []
        products_corrected_multiple = 0
        records_raw = []

        try:
            ch_result = supabase_client.table("curation_history").select(
                "id, product_id, original_ai_tags, corrected_tags, change_summary, "
                "error_types, confidence_in_correction, include_in_training"
            ).execute()
            records_raw = ch_result.data or []
        except Exception:
            records_raw = []

        # Join with products for category_refitd
        product_categories = {}
        if records_raw:
            ids = list({r["product_id"] for r in records_raw})
            for i in range(0, len(ids), 100):
                batch = ids[i : i + 100]
                p_result = (
                    supabase_client.table("products")
                    .select("product_id, category_refitd, category")
                    .in_("product_id", batch)
                    .execute()
                )
                for p in p_result.data or []:
                    product_categories[p["product_id"]] = p

        for r in records_raw:
            total_records += 1
            conf = r.get("confidence_in_correction")
            if conf is not None and 1 <= conf <= 5:
                by_confidence[conf] += 1
            for et in r.get("error_types") or []:
                by_error_type[et] = by_error_type.get(et, 0) + 1
            if r.get("include_in_training") is True:
                approved_count += 1
            else:
                excluded_count += 1

            pc = product_categories.get(r["product_id"], {})
            cr = pc.get("category_refitd")
            if cr and cr in by_category:
                by_category[cr] += 1
            else:
                cat_str = (pc.get("category") or "").lower()
                if any(x in cat_str for x in ["shirt", "top", "t-shirt", "sweater", "hoodie"]):
                    by_category["top"] += 1
                elif any(x in cat_str for x in ["trouser", "pant", "jean", "short"]):
                    by_category["bottom"] += 1
                elif any(x in cat_str for x in ["jacket", "coat", "blazer", "vest"]):
                    by_category["outerwear"] += 1
                elif any(x in cat_str for x in ["shoe", "boot", "sneaker"]):
                    by_category["footwear"] += 1
                else:
                    by_category["other"] += 1

        # Top 10 most corrected tags (from change_summary or compute_tag_changes)
        tag_removed_counts = {}
        try:
            from src.utils.tag_comparison import compute_tag_changes

            for r in records_raw:
                orig = r.get("original_ai_tags") or {}
                corr = r.get("corrected_tags") or {}
                changes = compute_tag_changes(orig, corr)
                for t in changes.get("removed", []):
                    tag_removed_counts[t] = tag_removed_counts.get(t, 0) + 1
        except ImportError:
            for r in records_raw:
                for t in _parse_change_summary_for_removed(r.get("change_summary") or ""):
                    tag_removed_counts[t] = tag_removed_counts.get(t, 0) + 1

        top_corrected_tags = sorted(
            tag_removed_counts.items(), key=lambda x: -x[1]
        )[:10]

        # Products corrected multiple times
        product_edit_counts = {}
        for r in records_raw:
            pid = r.get("product_id")
            if pid:
                product_edit_counts[pid] = product_edit_counts.get(pid, 0) + 1
        products_corrected_multiple = sum(1 for c in product_edit_counts.values() if c > 1)

        # --- Export metadata ---
        export_meta = {
            "last_export_at": None,
            "example_count": 0,
            "file_size_bytes": 0,
            "estimated_cost_usd": None,
        }
        if EXPORT_META_PATH.exists():
            try:
                export_meta = json.loads(EXPORT_META_PATH.read_text())
            except Exception:
                pass

        return jsonify(
            {
                "curation_progress": {
                    "total_products": total_products,
                    "curated_count": curated_count,
                    "remaining": remaining,
                    "progress_pct": progress_pct,
                },
                "training_quality": {
                    "total_records": total_records,
                    "by_confidence": by_confidence,
                    "by_error_type": by_error_type,
                    "by_category": by_category,
                    "approved_count": approved_count,
                    "excluded_count": excluded_count,
                },
                "common_issues": {
                    "top_corrected_tags": [{"tag": t, "count": c} for t, c in top_corrected_tags],
                    "most_common_error_types": sorted(
                        by_error_type.items(), key=lambda x: -x[1]
                    )[:5],
                    "products_corrected_multiple": products_corrected_multiple,
                },
                "export": export_meta,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export_training_data")
def export_training_data():
    """Generate JSONL training file and return as download. Updates last export metadata."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    try:
        from src.ai.refitd_tagger import SYSTEM_PROMPT
        from src.services.curation_history_service import CurationHistoryService

        service = CurationHistoryService()
        records = service.get_training_data(min_confidence=3, approved_only=True)
        if not records:
            return jsonify({"error": "No training examples available"}), 404

        # Reuse export logic from script
        from scripts.export_training_data import build_example

        buf = io.BytesIO()
        for r in records:
            ex = build_example(r, SYSTEM_PROMPT)
            buf.write((json.dumps(ex, ensure_ascii=False) + "\n").encode("utf-8"))

        buf.seek(0)
        file_size = buf.tell()
        buf.seek(0)

        # Cost estimate
        CHARS_PER_TOKEN = 4
        est_tokens = file_size // CHARS_PER_TOKEN
        cost_per_m = 25.0
        est_cost = (est_tokens / 1_000_000) * cost_per_m

        # Persist export metadata
        meta = {
            "last_export_at": datetime.utcnow().isoformat() + "Z",
            "example_count": len(records),
            "file_size_bytes": file_size,
            "estimated_cost_usd": round(est_cost, 2),
        }
        EXPORT_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_META_PATH.write_text(json.dumps(meta, indent=2))

        return Response(
            buf.getvalue(),
            mimetype="application/jsonl",
            headers={
                "Content-Disposition": "attachment; filename=training.jsonl",
                "Content-Length": str(file_size),
            },
        )
    except ImportError as e:
        return jsonify({"error": f"Import error: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# AI ENDPOINTS
# ============================================

@app.route("/api/ai/status")
def ai_status():
    """Check if AI service (OpenAI) is available."""
    try:
        from src.ai import OPENAI_AVAILABLE

        return jsonify({"available": OPENAI_AVAILABLE, "provider": "openai"})
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@app.route("/api/ai/search", methods=["POST"])
def ai_search():
    """Semantic search for products using AI embeddings."""
    import asyncio

    if not USE_SUPABASE or not supabase_client:
        return jsonify({"error": "Supabase not configured"}), 400

    data = request.get_json() or {}
    query = data.get("query", "")
    limit = data.get("limit", 10)

    if not query:
        return jsonify({"error": "Query is required"}), 400

    try:
        from src.ai import EmbeddingsService

        async def search():
            async with EmbeddingsService(
                supabase_client=supabase_client,
                use_openai=True,
            ) as embeddings_service:
                # Generate query embedding
                query_embedding = await embeddings_service.embed_text(query)

                if not query_embedding:
                    return {"error": "Failed to generate query embedding"}

                # Get all products and calculate similarity in memory
                # (until pgvector is set up in Supabase)
                products_result = (
                    supabase_client.table("products").select("*").execute()
                )
                products = products_result.data or []

                if not products:
                    return {"results": [], "message": "No products in database"}

                # Generate embeddings for products without them and calculate similarity
                results = []
                for product in products:
                    # Build text for embedding
                    text_parts = [product.get("name", "")]
                    if product.get("description"):
                        text_parts.append(product["description"][:300])
                    if product.get("category"):
                        text_parts.append(product["category"])
                    if product.get("colors"):
                        colors = product["colors"]
                        if isinstance(colors, list):
                            text_parts.append(" ".join(colors))

                    product_text = " ".join(text_parts)
                    product_embedding = await embeddings_service.embed_text(
                        product_text
                    )

                    if product_embedding:
                        similarity = embeddings_service._cosine_similarity(
                            query_embedding, product_embedding
                        )

                        if similarity > 0.3:  # Minimum threshold
                            # Build image URLs
                            image_paths = product.get("image_paths", [])
                            supabase_url = (
                                os.getenv("SUPABASE_URL") or DEFAULT_SUPABASE_URL
                            )
                            image_urls = (
                                [
                                    f"{supabase_url}/storage/v1/object/public/{BUCKET_NAME}/{path}"
                                    for path in image_paths
                                ]
                                if image_paths
                                else []
                            )

                            results.append(
                                {
                                    "product_id": product.get("product_id"),
                                    "name": product.get("name"),
                                    "price": f"${product.get('price_current', 'N/A')}",
                                    "category": product.get("category"),
                                    "image_urls": image_urls,
                                    "primary_image": (
                                        image_urls[0] if image_urls else None
                                    ),
                                    "similarity": similarity,
                                }
                            )

                # Sort by similarity and limit
                results.sort(key=lambda x: x["similarity"], reverse=True)
                return {"results": results[:limit]}

        result = asyncio.run(search())
        return jsonify(result)

    except ImportError as e:
        return jsonify({"error": f"AI modules not available: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    """Chat with the AI fashion assistant."""
    import asyncio

    data = request.get_json() or {}
    messages = data.get("messages", [])

    if not messages:
        return jsonify({"error": "Messages are required"}), 400

    try:
        from src.ai import ChatAssistant

        async def chat():
            async with ChatAssistant(
                supabase_client=supabase_client if USE_SUPABASE else None,
                use_openai=True,
            ) as assistant:
                response = await assistant.chat(
                    messages=messages,
                    include_context=USE_SUPABASE,
                )
                return {"response": response}

        result = asyncio.run(chat())
        return jsonify(result)

    except ImportError as e:
        return jsonify({"error": f"AI modules not available: {e}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# VOCABULARY MANAGEMENT ENDPOINTS
# ============================================


@app.route("/api/vocabulary", methods=["GET"])
def get_vocabulary():
    """Get all custom vocabulary from the database."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"success": False, "error": "Supabase not configured"}), 400

    try:
        result = supabase_client.table("custom_vocabulary").select("*").execute()

        # Group by category
        vocabulary = {}
        for item in result.data or []:
            category = item.get("category")
            tag = item.get("tag")
            if category and tag:
                if category not in vocabulary:
                    vocabulary[category] = []
                vocabulary[category].append(tag)

        return jsonify({"success": True, "vocabulary": vocabulary})

    except Exception as e:
        # Table might not exist yet
        if "relation" in str(e).lower() and "does not exist" in str(e).lower():
            return jsonify({"success": True, "vocabulary": {}})
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vocabulary/tag", methods=["POST"])
def add_vocabulary_tag():
    """Add a new tag to an existing or new category."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"success": False, "error": "Supabase not configured"}), 400

    data = request.get_json() or {}
    category = data.get("category", "").strip().lower()
    tag = data.get("tag", "").strip().lower()

    if not category or not tag:
        return (
            jsonify({"success": False, "error": "Category and tag are required"}),
            400,
        )

    try:
        # Insert the new tag
        supabase_client.table("custom_vocabulary").upsert(
            {"category": category, "tag": tag}, on_conflict="category,tag"
        ).execute()

        return jsonify(
            {"success": True, "message": f"Tag '{tag}' added to '{category}'"}
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vocabulary/tag", methods=["DELETE"])
def delete_vocabulary_tag():
    """Delete a tag from a category."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"success": False, "error": "Supabase not configured"}), 400

    data = request.get_json() or {}
    category = data.get("category", "").strip().lower()
    tag = data.get("tag", "").strip().lower()

    if not category or not tag:
        return (
            jsonify({"success": False, "error": "Category and tag are required"}),
            400,
        )

    try:
        supabase_client.table("custom_vocabulary").delete().eq("category", category).eq(
            "tag", tag
        ).execute()

        return jsonify(
            {"success": True, "message": f"Tag '{tag}' removed from '{category}'"}
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vocabulary/category", methods=["POST"])
def create_vocabulary_category():
    """Create a new category with initial tags."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"success": False, "error": "Supabase not configured"}), 400

    data = request.get_json() or {}
    category = data.get("category", "").strip().lower()
    tags = data.get("tags", [])

    if not category:
        return jsonify({"success": False, "error": "Category name is required"}), 400

    if not tags or not isinstance(tags, list):
        return jsonify({"success": False, "error": "At least one tag is required"}), 400

    try:
        # Insert all tags for the new category
        records = [
            {"category": category, "tag": tag.strip().lower()}
            for tag in tags
            if tag.strip()
        ]

        if records:
            supabase_client.table("custom_vocabulary").upsert(
                records, on_conflict="category,tag"
            ).execute()

        return jsonify(
            {
                "success": True,
                "message": f"Category '{category}' created with {len(records)} tags",
            }
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vocabulary/category/<category>", methods=["DELETE"])
def delete_vocabulary_category(category):
    """Delete an entire custom category."""
    if not USE_SUPABASE or not supabase_client:
        return jsonify({"success": False, "error": "Supabase not configured"}), 400

    try:
        supabase_client.table("custom_vocabulary").delete().eq(
            "category", category.lower()
        ).execute()

        return jsonify({"success": True, "message": f"Category '{category}' deleted"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/images/<category>/<product_id>/<filename>")
def serve_image(category, product_id, filename):
    """Serve product images from local files."""
    image_dir = DATA_DIR / category / product_id
    return send_from_directory(image_dir, filename)


# ============================================
# SCRAPER ENDPOINTS
# ============================================


def run_scraper_process(categories, products_per_category):
    """Run the scraper in a background thread."""
    global scraper_status
    import time

    scraper_status["running"] = True
    scraper_status["completed"] = False
    scraper_status["error"] = None
    scraper_status["progress"] = 0
    scraper_status["products_scraped"] = 0
    scraper_status["products_skipped"] = 0
    scraper_status["start_time"] = time.time()
    scraper_status["total"] = len(categories) * products_per_category
    scraper_status["logs"] = []  # Clear previous logs

    try:
        # Build the command
        cmd = [
            "python",
            str(Path(__file__).parent / "main.py"),
            "--products",
            str(products_per_category),
            "--categories",
        ] + categories

        # Add supabase flag if we're using it
        if not USE_SUPABASE:
            cmd.append("--no-supabase")

        # Run the scraper process
        scraper_status["current_category"] = "Starting..."
        scraper_status["logs"].append(f"$ {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(Path(__file__).parent),
        )

        # Read output line by line to track progress
        for line in iter(process.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue

            # Add to logs (keep last 100 lines)
            scraper_status["logs"].append(line)
            if len(scraper_status["logs"]) > 100:
                scraper_status["logs"] = scraper_status["logs"][-100:]

            # Parse progress from output
            if "Processing category:" in line:
                cat = line.split("Processing category:")[-1].strip()
                scraper_status["current_category"] = cat
            elif "Extracting product:" in line or "Scraping:" in line:
                scraper_status["current_product"] = line.split(":")[-1].strip()[:50]
            elif "Skipping already scraped" in line:
                scraper_status["products_skipped"] += 1
                scraper_status["progress"] = (
                    scraper_status["products_scraped"]
                    + scraper_status["products_skipped"]
                )
            elif "Saved to Supabase" in line or "Saved product" in line:
                scraper_status["products_scraped"] += 1
                scraper_status["progress"] = (
                    scraper_status["products_scraped"]
                    + scraper_status["products_skipped"]
                )
            elif "Extracted" in line and "new products" in line:
                # Extract count from "Extracted X new products"
                try:
                    count = int(line.split("Extracted")[1].split("new")[0].strip())
                    scraper_status["products_scraped"] = count
                except (ValueError, IndexError):
                    pass

        process.wait()

        if process.returncode == 0:
            scraper_status["completed"] = True
            scraper_status["current_category"] = "Complete!"
            scraper_status["current_product"] = ""
            scraper_status["logs"].append("✅ Scraping completed successfully!")
        else:
            scraper_status["error"] = (
                f"Process exited with code {process.returncode}. Check logs for details."
            )
            scraper_status["logs"].append(
                f"❌ Process exited with code {process.returncode}"
            )

    except Exception as e:
        scraper_status["error"] = str(e)
        scraper_status["logs"].append(f"❌ Error: {str(e)}")
    finally:
        scraper_status["running"] = False
        scraper_status["end_time"] = time.time()


@app.route("/api/scraper/start", methods=["POST"])
def start_scraper():
    """Start the web scraper process."""
    global scraper_status

    if scraper_status["running"]:
        return jsonify({"error": "Scraper is already running"}), 400

    # Reset status for new scrape
    scraper_status["refresh_handled"] = False
    scraper_status["completed"] = False
    scraper_status["error"] = None

    data = request.get_json() or {}
    categories = data.get(
        "categories",
        [
            "tshirts",
            "shirts",
            "trousers",
            "jeans",
            "shorts",
            "swimwear",
            "jackets",
            "blazers",
            "suits",
            "shoes",
            "bags",
            "accessories",
            "underwear",
            "new-in",
        ],
    )
    products_per_category = data.get("products_per_category", 2)

    # Start scraper in background thread
    thread = threading.Thread(
        target=run_scraper_process,
        args=(categories, products_per_category),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True, "message": "Scraper started"})


@app.route("/api/scraper/status")
def get_scraper_status():
    """Get the current scraper status."""
    return jsonify(scraper_status)


@app.route("/api/scraper/stop", methods=["POST"])
def stop_scraper():
    """Stop the scraper (not fully implemented - would need process tracking)."""
    global scraper_status
    # Note: This is a soft stop - sets a flag but doesn't kill the process
    scraper_status["running"] = False
    scraper_status["error"] = "Stopped by user"
    return jsonify({"success": True, "message": "Stop requested"})


@app.route("/api/scraper/reset", methods=["POST"])
def reset_scraper_status():
    """Reset scraper status after refresh has been handled."""
    global scraper_status
    scraper_status["refresh_handled"] = True
    return jsonify({"success": True})


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Zara Product Viewer - Browse scraped products"
    )
    parser.add_argument(
        "--supabase",
        action="store_true",
        help="Load products from Supabase database instead of local files",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port to run the server on (default: 5000)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    USE_SUPABASE = args.supabase

    # ANSI color codes for terminal styling
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BLUE = "\033[34m"
    UNDERLINE = "\033[4m"

    print()
    print(f"{BOLD}╔══════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}║            🛍️  ZARA PRODUCT VIEWER  🛍️              ║{RESET}")
    print(f"{BOLD}╚══════════════════════════════════════════════════════╝{RESET}")

    if USE_SUPABASE:
        print(f"\n{DIM}Data Source:{RESET} Supabase Database")
        try:
            init_supabase()
            print(f"{GREEN}✓ Connected{RESET}")
        except Exception as e:
            print(f"{RED}✗ Connection failed: {e}{RESET}")
            print(f"{YELLOW}  Falling back to local files...{RESET}")
            USE_SUPABASE = False

    if not USE_SUPABASE:
        print(f"\n{DIM}Data Source:{RESET} Local Files")
        print(f"{DIM}Directory:{RESET}   {DATA_DIR}")

    products = get_all_products()
    print(f"\n{DIM}Products:{RESET}    {BOLD}{len(products)}{RESET} items loaded")

    if products and len(products) > 0:
        print(f"\n{DIM}Sample products:{RESET}")
        for p in products[:5]:  # Show first 5 only
            name = p.get("name", "Unknown")
            if len(name) > 40:
                name = name[:37] + "..."
            print(f"  {DIM}•{RESET} {name}")
        if len(products) > 5:
            print(f"  {DIM}  ... and {len(products) - 5} more{RESET}")

    print()
    print(f"{BOLD}╔══════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}║                                                      ║{RESET}")
    print(
        f"{BOLD}║   🌐  {UNDERLINE}{CYAN}http://localhost:{args.port}{RESET}{BOLD}                         ║{RESET}"
    )
    print(f"{BOLD}║                                                      ║{RESET}")
    print(f"{BOLD}╚══════════════════════════════════════════════════════╝{RESET}")
    print(f"{DIM}Press CTRL+C to stop the server{RESET}")
    print()

    app.run(debug=True, port=args.port)
