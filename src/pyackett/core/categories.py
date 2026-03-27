"""Torznab standard category definitions.

Category IDs follow the Newznab/Torznab specification.
"""

from __future__ import annotations

# Main categories
CATEGORIES: dict[str, int] = {
    "Console": 1000,
    "Console/NDS": 1010,
    "Console/PSP": 1020,
    "Console/Wii": 1030,
    "Console/XBox": 1040,
    "Console/XBox 360": 1050,
    "Console/Wiiware": 1060,
    "Console/XBox 360 DLC": 1070,
    "Console/PS3": 1080,
    "Console/Other": 1090,
    "Console/3DS": 1110,
    "Console/PS Vita": 1120,
    "Console/WiiU": 1130,
    "Console/XBox One": 1140,
    "Console/PS4": 1180,
    "Movies": 2000,
    "Movies/Foreign": 2010,
    "Movies/Other": 2020,
    "Movies/SD": 2030,
    "Movies/HD": 2040,
    "Movies/UHD": 2045,
    "Movies/BluRay": 2050,
    "Movies/3D": 2060,
    "Movies/DVD": 2070,
    "Movies/WEB-DL": 2080,
    "Audio": 3000,
    "Audio/MP3": 3010,
    "Audio/Video": 3020,
    "Audio/Audiobook": 3030,
    "Audio/Lossless": 3040,
    "Audio/Other": 3050,
    "Audio/Foreign": 3060,
    "PC": 4000,
    "PC/0day": 4010,
    "PC/ISO": 4020,
    "PC/Mac": 4030,
    "PC/Mobile-Other": 4040,
    "PC/Games": 4050,
    "PC/Mobile-iOS": 4060,
    "PC/Mobile-Android": 4070,
    "TV": 5000,
    "TV/WEB-DL": 5010,
    "TV/Foreign": 5020,
    "TV/SD": 5030,
    "TV/HD": 5040,
    "TV/UHD": 5045,
    "TV/Other": 5050,
    "TV/Sport": 5060,
    "TV/Anime": 5070,
    "TV/Documentary": 5080,
    "XXX": 6000,
    "XXX/DVD": 6010,
    "XXX/WMV": 6020,
    "XXX/XviD": 6030,
    "XXX/x264": 6040,
    "XXX/UHD": 6045,
    "XXX/Pack": 6050,
    "XXX/ImageSet": 6060,
    "XXX/Other": 6070,
    "XXX/SD": 6080,
    "XXX/WEB-DL": 6090,
    "Books": 7000,
    "Books/Mags": 7010,
    "Books/EBook": 7020,
    "Books/Comics": 7030,
    "Books/Technical": 7040,
    "Books/Other": 7050,
    "Books/Foreign": 7060,
    "Other": 8000,
    "Other/Misc": 8010,
    "Other/Hashed": 8020,
}

# Reverse mapping: int -> name
CATEGORY_NAMES: dict[int, str] = {v: k for k, v in CATEGORIES.items()}


def resolve_category(cat_str: str) -> int | None:
    """Resolve a category string like 'Movies/HD' to its numeric ID."""
    return CATEGORIES.get(cat_str)


def get_parent_category(cat_id: int) -> int:
    """Get the parent (main) category ID for a subcategory."""
    return (cat_id // 1000) * 1000


def category_matches(cat_id: int, filter_cats: list[int]) -> bool:
    """Check if a category ID matches any of the filter categories.

    Matches both exact IDs and parent categories (e.g. 2000 matches 2040).
    """
    if not filter_cats:
        return True
    for fc in filter_cats:
        if cat_id == fc:
            return True
        if fc == get_parent_category(cat_id):
            return True
    return False
