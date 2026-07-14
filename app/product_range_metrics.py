from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


RANGE_COLUMN = "Product Group"


@dataclass(frozen=True)
class RangeContext:
    analysis_date: pd.Timestamp
    year: int
    month: int
    month_start: pd.Timestamp
    month_end: pd.Timestamp
    previous_year_start: pd.Timestamp
    previous_year_end: pd.Timestamp
    previous_month_start: pd.Timestamp
    previous_month_end: pd.Timestamp


def safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def analysis_context(df: pd.DataFrame, year: int | None = None, month: int | None = None) -> RangeContext:
    dates = pd.to_datetime(df["Performance Date"], errors="coerce").dropna()
    if dates.empty:
        raise ValueError("当前数据没有有效 Performance Date。")
    anchor = dates.max().normalize()
    target_year = int(year or anchor.year)
    target_month = int(month or anchor.month)
    month_start = pd.Timestamp(year=target_year, month=target_month, day=1)
    month_end = month_start + pd.offsets.MonthEnd(0)
    if target_year == anchor.year and target_month == anchor.month:
        month_end = min(month_end, anchor)
    previous_year_start = pd.Timestamp(year=target_year - 1, month=target_month, day=1)
    previous_year_month_end = previous_year_start + pd.offsets.MonthEnd(0)
    previous_year_end = pd.Timestamp(
        year=target_year - 1,
        month=target_month,
        day=min(month_end.day, int(previous_year_month_end.day)),
    )
    previous_month_anchor = month_start - pd.DateOffset(months=1)
    previous_month_start = pd.Timestamp(year=previous_month_anchor.year, month=previous_month_anchor.month, day=1)
    previous_month_end_full = previous_month_start + pd.offsets.MonthEnd(0)
    previous_month_end = pd.Timestamp(
        year=previous_month_anchor.year,
        month=previous_month_anchor.month,
        day=min(month_end.day, int(previous_month_end_full.day)),
    )
    return RangeContext(
        analysis_date=anchor,
        year=target_year,
        month=target_month,
        month_start=month_start,
        month_end=month_end,
        previous_year_start=previous_year_start,
        previous_year_end=previous_year_end,
        previous_month_start=previous_month_start,
        previous_month_end=previous_month_end,
    )


def _range_series(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    dates = pd.to_datetime(df["Performance Date"], errors="coerce")
    mask = dates.between(start, end, inclusive="both")
    return df.loc[mask].groupby(RANGE_COLUMN, dropna=False)["Sales Amount"].sum()


def _total_between(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    dates = pd.to_datetime(df["Performance Date"], errors="coerce")
    mask = dates.between(start, end, inclusive="both")
    return float(df.loc[mask, "Sales Amount"].sum())


def _target_table(amount_targets: pd.DataFrame | None, year: int, month: int) -> pd.DataFrame:
    if amount_targets is None or amount_targets.empty:
        return pd.DataFrame(columns=[RANGE_COLUMN, "Monthly Target"])
    required = {"Year", "Month", RANGE_COLUMN}
    if not required.issubset(amount_targets.columns):
        return pd.DataFrame(columns=[RANGE_COLUMN, "Monthly Target"])
    targets = amount_targets[
        amount_targets["Year"].astype("Int64").eq(year)
        & amount_targets["Month"].astype("Int64").eq(month)
        & ~amount_targets[RANGE_COLUMN].astype(str).eq("公司整体")
    ].copy()
    if targets.empty:
        return pd.DataFrame(columns=[RANGE_COLUMN, "Monthly Target"])
    value_col = "Revised Target" if "Revised Target" in targets.columns else "Original Target"
    targets["Monthly Target"] = pd.to_numeric(targets[value_col], errors="coerce")
    return targets[[RANGE_COLUMN, "Monthly Target"]].dropna(subset=["Monthly Target"])


def _annual_target_table(amount_targets: pd.DataFrame | None, year: int) -> pd.DataFrame:
    if amount_targets is None or amount_targets.empty:
        return pd.DataFrame(columns=[RANGE_COLUMN, "Annual Target"])
    required = {"Year", RANGE_COLUMN}
    if not required.issubset(amount_targets.columns):
        return pd.DataFrame(columns=[RANGE_COLUMN, "Annual Target"])
    targets = amount_targets[
        amount_targets["Year"].astype("Int64").eq(year)
        & ~amount_targets[RANGE_COLUMN].astype(str).eq("公司整体")
    ].copy()
    if targets.empty:
        return pd.DataFrame(columns=[RANGE_COLUMN, "Annual Target"])
    value_col = "Revised Target" if "Revised Target" in targets.columns else "Original Target"
    targets["Target Value"] = pd.to_numeric(targets[value_col], errors="coerce").fillna(0)
    return targets.groupby(RANGE_COLUMN, dropna=False)["Target Value"].sum().rename("Annual Target").reset_index()


def range_status(row: pd.Series) -> str:
    yoy = row.get("YoY Rate")
    mom = row.get("MoM Rate")
    completion = row.get("Target Completion")
    has_target = pd.notna(completion)
    has_yoy = pd.notna(yoy)
    if not has_yoy and not has_target:
        return "数据不足"
    positive = 0
    negative = 0
    if has_yoy:
        if yoy >= 0.10:
            positive += 1
        elif yoy <= -0.20:
            negative += 2
        elif yoy < 0:
            negative += 1
    if pd.notna(mom):
        if mom >= 0.10:
            positive += 1
        elif mom <= -0.15:
            negative += 1
    if has_target:
        if completion >= 1:
            positive += 2
        elif completion >= 0.85:
            positive += 1
        elif completion < 0.60:
            negative += 2
        elif completion < 0.85:
            negative += 1
    if negative >= 3:
        return "明显落后"
    if positive >= 2 and negative == 0:
        return "表现良好"
    if negative:
        return "需要关注"
    return "稳定"


def build_range_overview(df: pd.DataFrame, amount_targets: pd.DataFrame | None, year: int | None = None, month: int | None = None) -> tuple[pd.DataFrame, RangeContext]:
    if RANGE_COLUMN not in df.columns:
        return pd.DataFrame(), analysis_context(df, year, month)
    ctx = analysis_context(df, year, month)
    work = df.copy()
    work[RANGE_COLUMN] = work[RANGE_COLUMN].fillna("未分类").astype(str)

    current = _range_series(work, ctx.month_start, ctx.month_end).rename("Current Month Sales")
    previous_year = _range_series(work, ctx.previous_year_start, ctx.previous_year_end).rename("Previous Year Same Period")
    previous_month = _range_series(work, ctx.previous_month_start, ctx.previous_month_end).rename("Previous Month Same Period")
    ytd = _range_series(work, pd.Timestamp(year=ctx.year, month=1, day=1), ctx.month_end).rename("YTD Sales")
    previous_ytd = _range_series(
        work,
        pd.Timestamp(year=ctx.year - 1, month=1, day=1),
        pd.Timestamp(year=ctx.year - 1, month=ctx.month_end.month, day=ctx.month_end.day),
    ).rename("Previous YTD Sales")
    table = pd.concat([current, previous_year, previous_month, ytd, previous_ytd], axis=1).fillna(0).reset_index()
    monthly_targets = _target_table(amount_targets, ctx.year, ctx.month)
    annual_targets = _annual_target_table(amount_targets, ctx.year)
    table = table.merge(monthly_targets, on=RANGE_COLUMN, how="left").merge(annual_targets, on=RANGE_COLUMN, how="left")
    table["YoY Change"] = table["Current Month Sales"] - table["Previous Year Same Period"]
    table["YoY Rate"] = table.apply(lambda row: safe_ratio(row["YoY Change"], row["Previous Year Same Period"]), axis=1)
    table["MoM Rate"] = table.apply(lambda row: safe_ratio(row["Current Month Sales"] - row["Previous Month Same Period"], row["Previous Month Same Period"]), axis=1)
    table["YTD YoY"] = table.apply(lambda row: safe_ratio(row["YTD Sales"] - row["Previous YTD Sales"], row["Previous YTD Sales"]), axis=1)
    table["Target Completion"] = table.apply(lambda row: safe_ratio(row["Current Month Sales"], row["Monthly Target"]) if pd.notna(row.get("Monthly Target")) else None, axis=1)
    table["Target Gap"] = table.apply(lambda row: row["Current Month Sales"] - row["Monthly Target"] if pd.notna(row.get("Monthly Target")) else None, axis=1)
    table["Status"] = table.apply(range_status, axis=1)
    return table.sort_values("Current Month Sales", ascending=False).reset_index(drop=True), ctx


def build_core_kpis(overview: pd.DataFrame) -> dict[str, object]:
    if overview.empty:
        return {}
    total = float(overview["Current Month Sales"].sum())
    previous = float(overview["Previous Year Same Period"].sum())
    previous_month = float(overview["Previous Month Same Period"].sum())
    target = pd.to_numeric(overview.get("Monthly Target"), errors="coerce").sum(min_count=1)
    return {
        "current_sales": total,
        "previous_year_sales": previous,
        "yoy": safe_ratio(total - previous, previous),
        "previous_month_sales": previous_month,
        "mom": safe_ratio(total - previous_month, previous_month),
        "monthly_target": None if pd.isna(target) else float(target),
        "completion": None if pd.isna(target) else safe_ratio(total, float(target)),
        "target_gap": None if pd.isna(target) else total - float(target),
    }


def build_monthly_trend(df: pd.DataFrame, amount_targets: pd.DataFrame | None, year: int, product_range: str | None = None) -> pd.DataFrame:
    work = df.copy()
    work[RANGE_COLUMN] = work[RANGE_COLUMN].fillna("未分类").astype(str)
    if product_range and product_range != "全部":
        work = work[work[RANGE_COLUMN].eq(product_range)]
    rows = []
    anchor = analysis_context(df).analysis_date
    for month in range(1, 13):
        start = pd.Timestamp(year=year, month=month, day=1)
        end = start + pd.offsets.MonthEnd(0)
        if year == anchor.year and month == anchor.month:
            end = min(end, anchor)
        previous_start = pd.Timestamp(year=year - 1, month=month, day=1)
        previous_end = pd.Timestamp(year=year - 1, month=month, day=min(end.day, int((previous_start + pd.offsets.MonthEnd(0)).day)))
        actual = _total_between(work, start, end)
        previous = _total_between(work, previous_start, previous_end)
        target = None
        if amount_targets is not None and product_range and product_range != "全部":
            target_rows = _target_table(amount_targets, year, month)
            match = target_rows[target_rows[RANGE_COLUMN].astype(str).eq(product_range)]
            if not match.empty:
                target = float(match.iloc[-1]["Monthly Target"])
        rows.append(
            {
                "Month": month,
                "Month Label": f"{month}月",
                "Sales": actual,
                "Previous Year": previous,
                "YoY": safe_ratio(actual - previous, previous),
                "Target": target,
                "Completion": None if target is None else safe_ratio(actual, target),
            }
        )
    trend = pd.DataFrame.from_records(rows)
    trend["YTD Sales"] = trend["Sales"].cumsum()
    trend["Previous YTD"] = trend["Previous Year"].cumsum()
    trend["YTD YoY"] = trend.apply(lambda row: safe_ratio(row["YTD Sales"] - row["Previous YTD"], row["Previous YTD"]), axis=1)
    return trend


def build_week_progress(df: pd.DataFrame, amount_targets: pd.DataFrame | None, ctx: RangeContext, product_range: str | None = None) -> pd.DataFrame:
    work = df.copy()
    work[RANGE_COLUMN] = work[RANGE_COLUMN].fillna("未分类").astype(str)
    if product_range and product_range != "全部":
        work = work[work[RANGE_COLUMN].eq(product_range)]
    month_rows = work[pd.to_datetime(work["Performance Date"], errors="coerce").between(ctx.month_start, ctx.month_end, inclusive="both")].copy()
    if month_rows.empty:
        return pd.DataFrame()
    month_rows["Week"] = ((pd.to_datetime(month_rows["Performance Date"]).dt.day - 1) // 7 + 1).clip(upper=5)
    weekly = month_rows.groupby("Week", dropna=False)["Sales Amount"].sum().rename("Weekly Sales").reset_index()
    all_weeks = pd.DataFrame({"Week": range(1, 6)})
    weekly = all_weeks.merge(weekly, on="Week", how="left").fillna({"Weekly Sales": 0})
    weekly["Week Label"] = weekly["Week"].map(lambda week: f"Week {int(week)}")
    weekly["Month Cumulative Sales"] = weekly["Weekly Sales"].cumsum()
    previous_end_day = min(ctx.month_end.day, int((ctx.previous_year_start + pd.offsets.MonthEnd(0)).day))
    previous = work[
        pd.to_datetime(work["Performance Date"], errors="coerce").between(
            ctx.previous_year_start,
            pd.Timestamp(year=ctx.year - 1, month=ctx.month, day=previous_end_day),
            inclusive="both",
        )
    ].copy()
    if previous.empty:
        weekly["Previous Year Cumulative"] = 0.0
    else:
        previous["Week"] = ((pd.to_datetime(previous["Performance Date"]).dt.day - 1) // 7 + 1).clip(upper=5)
        previous_weekly = previous.groupby("Week", dropna=False)["Sales Amount"].sum().rename("Previous Weekly").reset_index()
        weekly = weekly.merge(previous_weekly, on="Week", how="left").fillna({"Previous Weekly": 0})
        weekly["Previous Year Cumulative"] = weekly["Previous Weekly"].cumsum()
    weekly["Cumulative YoY"] = weekly.apply(lambda row: safe_ratio(row["Month Cumulative Sales"] - row["Previous Year Cumulative"], row["Previous Year Cumulative"]), axis=1)
    weekly["Change vs Previous Week"] = weekly["Weekly Sales"].diff()
    days_elapsed = max((ctx.month_end - ctx.month_start).days + 1, 1)
    days_in_month = int((ctx.month_start + pd.offsets.MonthEnd(0)).day)
    weekly["Time Progress"] = min(days_elapsed / days_in_month, 1.0)
    target = None
    if amount_targets is not None and product_range and product_range != "全部":
        target_rows = _target_table(amount_targets, ctx.year, ctx.month)
        match = target_rows[target_rows[RANGE_COLUMN].astype(str).eq(product_range)]
        if not match.empty:
            target = float(match.iloc[-1]["Monthly Target"])
    weekly["Target"] = target
    weekly["Target Completion"] = weekly["Month Cumulative Sales"].map(lambda value: safe_ratio(value, target) if target else None)
    return weekly


def top_contributors(df: pd.DataFrame, dimension: str, ctx: RangeContext, product_range: str | None, limit: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = df.copy()
    work[RANGE_COLUMN] = work[RANGE_COLUMN].fillna("未分类").astype(str)
    if product_range and product_range != "全部":
        work = work[work[RANGE_COLUMN].eq(product_range)]
    current = work[pd.to_datetime(work["Performance Date"], errors="coerce").between(ctx.month_start, ctx.month_end, inclusive="both")]
    previous = work[pd.to_datetime(work["Performance Date"], errors="coerce").between(ctx.previous_year_start, ctx.previous_year_end, inclusive="both")]
    current_series = current.groupby(dimension, dropna=False)["Sales Amount"].sum().rename("Current")
    previous_series = previous.groupby(dimension, dropna=False)["Sales Amount"].sum().rename("Previous")
    table = pd.concat([current_series, previous_series], axis=1).fillna(0).reset_index()
    table["Change"] = table["Current"] - table["Previous"]
    growth = table[table["Change"].gt(0)].sort_values("Change", ascending=False).head(limit)
    decline = table[table["Change"].lt(0)].sort_values("Change", ascending=True).head(limit)
    return growth, decline
