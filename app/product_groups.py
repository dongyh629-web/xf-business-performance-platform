from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


PRODUCT_GROUP_COLUMN = "Product Group"


def _match_key(value: object) -> str:
    return "".join(str(value).strip().casefold().split())


_PRODUCT_GROUP_ALIASES = {
    _match_key("Shrimp Paste(虾滑)"): "8.0虾滑Shrimp Paste",
    _match_key("8.0虾滑Shrimp Paste"): "8.0虾滑Shrimp Paste",
    _match_key("7.0干货Ambient Pack/空酱"): "7.0 空酱部队Ambient Pack",
    _match_key("7.0 空酱部队Ambient Pack"): "7.0 空酱部队Ambient Pack",
    _match_key("Sausage Series/烤肠"): "香肠系列 Sausage Series",
    _match_key("香肠系列 Sausage Series"): "香肠系列 Sausage Series",
}


def canonical_product_group(value: object) -> object:
    """Return the canonical reporting name without mutating source data."""
    if value is None or pd.isna(value):
        return value
    text = str(value).strip()
    return _PRODUCT_GROUP_ALIASES.get(_match_key(text), text)


def normalize_product_group_copy(df: pd.DataFrame) -> pd.DataFrame:
    """Return a calculation copy with canonical Product Group names."""
    normalized = df.copy()
    if PRODUCT_GROUP_COLUMN in normalized.columns:
        normalized[PRODUCT_GROUP_COLUMN] = normalized[PRODUCT_GROUP_COLUMN].map(canonical_product_group)
    return normalized


def unmatched_product_groups(target_groups: Iterable[object], sales_groups: Iterable[object]) -> list[str]:
    """List canonical target groups that have no corresponding sales group."""
    sales = {
        str(canonical).strip()
        for value in sales_groups
        if value is not None and not pd.isna(value)
        for canonical in [canonical_product_group(value)]
        if str(canonical).strip()
    }
    targets = {
        str(canonical).strip()
        for value in target_groups
        if value is not None and not pd.isna(value)
        for canonical in [canonical_product_group(value)]
        if str(canonical).strip() and str(canonical).strip() != "公司整体"
    }
    return sorted(targets - sales)
