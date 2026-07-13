from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app import config as app_config


ABC_A_THRESHOLD = getattr(app_config, "ABC_A_THRESHOLD", 0.70)
ABC_B_THRESHOLD = getattr(app_config, "ABC_B_THRESHOLD", 0.90)
NEW_CUSTOMER_START_DATE = getattr(app_config, "NEW_CUSTOMER_START_DATE", "2026-07-01")
CUSTOMER_VALUE_DECLINE_THRESHOLD = getattr(app_config, "CUSTOMER_VALUE_DECLINE_THRESHOLD", 0.30)
CUSTOMER_VALUE_DECLINE_MIN_AMOUNT = getattr(app_config, "CUSTOMER_VALUE_DECLINE_MIN_AMOUNT", 500)
CUSTOMER_FREQUENCY_DECLINE_THRESHOLD = getattr(app_config, "CUSTOMER_FREQUENCY_DECLINE_THRESHOLD", 0.30)
CUSTOMER_FREQUENCY_MIN_BASE_ORDERS = getattr(app_config, "CUSTOMER_FREQUENCY_MIN_BASE_ORDERS", 4)


PRIORITY_ORDER = {"高优先级": 0, "中优先级": 1, "低优先级": 2}
ABC_ORDER = {"A": 0, "B": 1, "C": 2, "未分类": 3}


@dataclass(frozen=True)
class ActiveCustomerMetrics:
    anchor_date: pd.Timestamp
    current_active: int
    previous_year_active: int | None
    previous_month_active: int | None
    yoy: float | None
    mom: float | None


@dataclass(frozen=True)
class CustomerHealthResult:
    active_metrics: ActiveCustomerMetrics
    new_customers: pd.DataFrame
    dormant_30: pd.DataFrame
    dormant_90: pd.DataFrame
    value_decline: pd.DataFrame
    frequency_decline: pd.DataFrame
    risk_customers: pd.DataFrame
    follow_up: pd.DataFrame
    excluded_missing_customer_code: int
    abc_fallback_used: bool


def _valid_customer_data(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    required = {"Customer Code", "Performance Date", "Sales Amount", "Order No."}
    if not required.issubset(df.columns):
        return pd.DataFrame()
    result = df.copy()
    result["Performance Date"] = pd.to_datetime(result["Performance Date"], errors="coerce")
    result["Customer Code"] = result["Customer Code"].astype("string").str.strip()
    result = result.dropna(subset=["Performance Date", "Customer Code"])
    result = result[result["Customer Code"].ne("")]
    result["Sales Amount"] = pd.to_numeric(result["Sales Amount"], errors="coerce").fillna(0.0)
    return result


def analysis_cutoff(df: pd.DataFrame) -> pd.Timestamp | None:
    valid = _valid_customer_data(df)
    if valid.empty:
        return None
    return valid["Performance Date"].max().normalize()


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _latest_value(group: pd.DataFrame, column: str) -> object:
    if column not in group.columns:
        return pd.NA
    values = group.sort_values("Performance Date").dropna(subset=[column])
    return pd.NA if values.empty else values.iloc[-1][column]


def _month_to_date_bounds(anchor: pd.Timestamp, month_offset: int = 0, year_offset: int = 0) -> tuple[pd.Timestamp, pd.Timestamp]:
    comparable = anchor + pd.DateOffset(months=month_offset, years=year_offset)
    start = pd.Timestamp(year=comparable.year, month=comparable.month, day=1)
    month_end = start + pd.offsets.MonthEnd(0)
    day = min(anchor.day, int(month_end.day))
    end = pd.Timestamp(year=comparable.year, month=comparable.month, day=day)
    return start, end


def _active_count_between(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> int:
    mask = df["Performance Date"].between(start, end, inclusive="both")
    return int(df.loc[mask, "Customer Code"].nunique())


def active_customer_metrics(df: pd.DataFrame, anchor: pd.Timestamp) -> ActiveCustomerMetrics:
    valid = _valid_customer_data(df)
    current_start, current_end = _month_to_date_bounds(anchor)
    previous_year_start, previous_year_end = _month_to_date_bounds(anchor, year_offset=-1)
    previous_month_start, previous_month_end = _month_to_date_bounds(anchor, month_offset=-1)

    current = _active_count_between(valid, current_start, current_end)
    previous_year = _active_count_between(valid, previous_year_start, previous_year_end)
    previous_month = _active_count_between(valid, previous_month_start, previous_month_end)
    return ActiveCustomerMetrics(
        anchor_date=anchor,
        current_active=current,
        previous_year_active=previous_year if previous_year > 0 else None,
        previous_month_active=previous_month if previous_month > 0 else None,
        yoy=None if previous_year <= 0 else _safe_ratio(current - previous_year, previous_year),
        mom=None if previous_month <= 0 else _safe_ratio(current - previous_month, previous_month),
    )


def _order_table(df: pd.DataFrame) -> pd.DataFrame:
    valid = _valid_customer_data(df)
    if valid.empty:
        return pd.DataFrame()
    return (
        valid.groupby(["Customer Code", "Order No."], dropna=False)
        .agg(
            OrderDate=("Performance Date", "min"),
            OrderSales=("Sales Amount", "sum"),
            CustomerName=("Customer Name", "last"),
            CustomerType=("Customer Type", "last"),
        )
        .reset_index()
    )


def _customer_profile(history_df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    valid = _valid_customer_data(history_df)
    if valid.empty:
        return pd.DataFrame(), False
    orders = _order_table(valid)
    last_orders = orders.sort_values("OrderDate").groupby("Customer Code", dropna=False).tail(1)
    total_sales = valid.groupby("Customer Code", dropna=False)["Sales Amount"].sum().rename("历史累计销售额")
    order_counts = orders.groupby("Customer Code", dropna=False)["Order No."].nunique().rename("历史订单数")
    avg_order = orders.groupby("Customer Code", dropna=False)["OrderSales"].mean().rename("历史平均订单金额")
    profile = (
        total_sales.to_frame()
        .join(order_counts)
        .join(avg_order)
        .reset_index()
        .merge(
            last_orders[["Customer Code", "OrderDate", "OrderSales"]].rename(
                columns={"OrderDate": "最近下单日期", "OrderSales": "最近一次订单金额"}
            ),
            on="Customer Code",
            how="left",
        )
    )

    latest = (
        valid.sort_values("Performance Date")
        .groupby("Customer Code", dropna=False)
        .apply(
            lambda group: pd.Series(
                {
                    "Customer Name": _latest_value(group, "Customer Name"),
                    "Customer Type": _latest_value(group, "Customer Type"),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    profile = profile.merge(latest, on="Customer Code", how="left")

    if "ABC Class" in valid.columns and valid["ABC Class"].notna().any():
        abc = valid.sort_values("Performance Date").groupby("Customer Code", dropna=False)["ABC Class"].last().rename("ABC Class").reset_index()
        fallback_used = False
    else:
        ranked = profile.sort_values("历史累计销售额", ascending=False).copy()
        total = float(ranked["历史累计销售额"].sum())
        ranked["ABC Class"] = "C"
        if total > 0:
            previous_cumulative = ranked["历史累计销售额"].cumsum().shift(fill_value=0) / total
            ranked.loc[previous_cumulative < ABC_A_THRESHOLD, "ABC Class"] = "A"
            ranked.loc[(previous_cumulative >= ABC_A_THRESHOLD) & (previous_cumulative < ABC_B_THRESHOLD), "ABC Class"] = "B"
        abc = ranked[["Customer Code", "ABC Class"]]
        fallback_used = True
    profile = profile.merge(abc, on="Customer Code", how="left")
    profile["ABC Class"] = profile["ABC Class"].fillna("未分类")
    return profile, fallback_used


def new_customer_table(current_df: pd.DataFrame, history_df: pd.DataFrame, anchor: pd.Timestamp) -> pd.DataFrame:
    if anchor.to_period("M") < pd.Timestamp(NEW_CUSTOMER_START_DATE).to_period("M"):
        return pd.DataFrame()
    current = _valid_customer_data(current_df)
    history = _valid_customer_data(history_df)
    if current.empty or history.empty:
        return pd.DataFrame()

    month_start, month_end = _month_to_date_bounds(anchor)
    first_dates = history.groupby("Customer Code", dropna=False)["Performance Date"].min().rename("首次下单日期").reset_index()
    new_start = pd.Timestamp(NEW_CUSTOMER_START_DATE)
    new_codes = first_dates[
        first_dates["首次下单日期"].ge(new_start)
        & first_dates["首次下单日期"].between(month_start, month_end, inclusive="both")
    ]
    if new_codes.empty:
        return pd.DataFrame()

    month_rows = current[current["Performance Date"].between(month_start, month_end, inclusive="both")].copy()
    first_order_sales = (
        _order_table(history)
        .sort_values("OrderDate")
        .groupby("Customer Code", dropna=False)
        .head(1)[["Customer Code", "OrderSales"]]
        .rename(columns={"OrderSales": "首单金额"})
    )
    month_summary = (
        month_rows.groupby("Customer Code", dropna=False)
        .agg(当月累计销售额=("Sales Amount", "sum"), 当月订单数=("Order No.", "nunique"))
        .reset_index()
    )
    profile, _ = _customer_profile(history)
    return (
        new_codes.merge(profile[["Customer Code", "Customer Name", "Customer Type"]], on="Customer Code", how="left")
        .merge(first_order_sales, on="Customer Code", how="left")
        .merge(month_summary, on="Customer Code", how="left")
        .sort_values("首次下单日期")
        .reset_index(drop=True)
    )[
        ["Customer Code", "Customer Name", "Customer Type", "首次下单日期", "首单金额", "当月累计销售额", "当月订单数"]
    ]


def dormant_customers(history_df: pd.DataFrame, anchor: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    profile, _ = _customer_profile(history_df)
    if profile.empty:
        return pd.DataFrame(), pd.DataFrame()
    result = profile.copy()
    result["未下单天数"] = (anchor - pd.to_datetime(result["最近下单日期"], errors="coerce")).dt.days
    base_columns = [
        "Customer Code",
        "Customer Name",
        "Customer Type",
        "ABC Class",
        "最近下单日期",
        "未下单天数",
        "历史累计销售额",
        "最近一次订单金额",
        "历史平均订单金额",
        "风险类型",
    ]
    dormant_30 = result[result["未下单天数"].gt(30) & result["未下单天数"].le(90)].copy()
    dormant_30["风险类型"] = "30天未下单"
    dormant_90 = result[result["未下单天数"].gt(90)].copy()
    dormant_90["风险类型"] = "90天未下单"
    return dormant_30[base_columns], dormant_90[base_columns]


def _last_complete_week_end(anchor: pd.Timestamp) -> pd.Timestamp:
    monday = anchor.normalize() - pd.Timedelta(days=anchor.weekday())
    return monday - pd.Timedelta(days=1)


def _window_sales(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    mask = df["Performance Date"].between(start, end, inclusive="both")
    return df.loc[mask].groupby("Customer Code", dropna=False)["Sales Amount"].sum()


def _window_orders(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    mask = df["Performance Date"].between(start, end, inclusive="both")
    return df.loc[mask].groupby("Customer Code", dropna=False)["Order No."].nunique()


def value_decline_customers(history_df: pd.DataFrame, anchor: pd.Timestamp) -> pd.DataFrame:
    valid = _valid_customer_data(history_df)
    if valid.empty:
        return pd.DataFrame()
    recent_end = _last_complete_week_end(anchor)
    recent_start = recent_end - pd.Timedelta(weeks=4) + pd.Timedelta(days=1)
    previous_end = recent_start - pd.Timedelta(days=1)
    previous_start = previous_end - pd.Timedelta(weeks=4) + pd.Timedelta(days=1)

    recent = _window_sales(valid, recent_start, recent_end).rename("最近4周销售额")
    previous = _window_sales(valid, previous_start, previous_end).rename("前4周销售额")
    table = pd.concat([recent, previous], axis=1).fillna(0.0).reset_index()
    table["绝对下降金额"] = table["前4周销售额"] - table["最近4周销售额"]
    table["下降比例"] = table.apply(lambda row: _safe_ratio(row["绝对下降金额"], row["前4周销售额"]), axis=1)
    table = table[
        table["前4周销售额"].gt(0)
        & table["绝对下降金额"].ge(CUSTOMER_VALUE_DECLINE_MIN_AMOUNT)
        & table["下降比例"].ge(CUSTOMER_VALUE_DECLINE_THRESHOLD)
    ].copy()
    if table.empty:
        return pd.DataFrame()
    profile, _ = _customer_profile(valid)
    table = table.merge(profile[["Customer Code", "Customer Name", "Customer Type", "ABC Class", "最近下单日期", "历史累计销售额"]], on="Customer Code", how="left")
    table["风险类型"] = "采购金额下降"
    return table[
        [
            "Customer Code",
            "Customer Name",
            "Customer Type",
            "ABC Class",
            "最近4周销售额",
            "前4周销售额",
            "绝对下降金额",
            "下降比例",
            "最近下单日期",
            "历史累计销售额",
            "风险类型",
        ]
    ]


def frequency_decline_customers(history_df: pd.DataFrame, anchor: pd.Timestamp) -> pd.DataFrame:
    valid = _valid_customer_data(history_df)
    if valid.empty:
        return pd.DataFrame()
    recent_end = _last_complete_week_end(anchor)
    recent_start = recent_end - pd.Timedelta(weeks=8) + pd.Timedelta(days=1)
    previous_end = recent_start - pd.Timedelta(days=1)
    previous_start = previous_end - pd.Timedelta(weeks=8) + pd.Timedelta(days=1)

    recent = _window_orders(valid, recent_start, recent_end).rename("最近8周订单数")
    previous = _window_orders(valid, previous_start, previous_end).rename("前8周订单数")
    table = pd.concat([recent, previous], axis=1).fillna(0).reset_index()
    table["订单数变化"] = table["最近8周订单数"] - table["前8周订单数"]
    table["频次变化率"] = table.apply(lambda row: _safe_ratio(row["订单数变化"], row["前8周订单数"]), axis=1)
    table = table[
        table["前8周订单数"].ge(CUSTOMER_FREQUENCY_MIN_BASE_ORDERS)
        & table["频次变化率"].le(-CUSTOMER_FREQUENCY_DECLINE_THRESHOLD)
    ].copy()
    if table.empty:
        return pd.DataFrame()
    profile, _ = _customer_profile(valid)
    table = table.merge(profile[["Customer Code", "Customer Name", "Customer Type", "ABC Class", "最近下单日期", "历史累计销售额"]], on="Customer Code", how="left")
    table["风险类型"] = "下单频次下降"
    return table[
        [
            "Customer Code",
            "Customer Name",
            "Customer Type",
            "ABC Class",
            "最近8周订单数",
            "前8周订单数",
            "订单数变化",
            "频次变化率",
            "最近下单日期",
            "历史累计销售额",
            "风险类型",
        ]
    ]


def _priority_for_row(row: pd.Series) -> str:
    abc = str(row.get("ABC Class", "未分类"))
    risks = set(str(row.get("风险类型", "")).split("；"))
    value_decline_rate = row.get("金额变化率")
    history_sales = float(row.get("历史累计销售额", 0.0) or 0.0)
    high_contribution = bool(row.get("_high_contribution", False))

    if abc == "A" and ("30天未下单" in risks or "90天未下单" in risks):
        return "高优先级"
    if abc in {"A", "B"} and pd.notna(value_decline_rate) and value_decline_rate <= -0.40:
        return "高优先级"
    if abc == "A" and {"采购金额下降", "下单频次下降"}.issubset(risks):
        return "高优先级"
    if "90天未下单" in risks and high_contribution and history_sales > 0:
        return "高优先级"
    if abc == "B" and "30天未下单" in risks:
        return "中优先级"
    if abc in {"A", "B"} and "下单频次下降" in risks:
        return "中优先级"
    if "采购金额下降" in risks:
        return "中优先级"
    if abc == "C" and "90天未下单" in risks:
        return "中优先级"
    return "低优先级"


def merge_risk_customers(
    history_df: pd.DataFrame,
    anchor: pd.Timestamp,
    dormant_30: pd.DataFrame,
    dormant_90: pd.DataFrame,
    value_decline: pd.DataFrame,
    frequency_decline: pd.DataFrame,
) -> pd.DataFrame:
    profile, _ = _customer_profile(history_df)
    if profile.empty:
        return pd.DataFrame()
    risk_frames = []
    for frame in [dormant_30, dormant_90, value_decline, frequency_decline]:
        if frame is not None and not frame.empty:
            risk_frames.append(frame[["Customer Code", "风险类型"]])
    if not risk_frames:
        return pd.DataFrame()
    risk_types = pd.concat(risk_frames, ignore_index=True).groupby("Customer Code", dropna=False)["风险类型"].agg(lambda values: "；".join(sorted(set(values)))).reset_index()
    result = risk_types.merge(profile, on="Customer Code", how="left")

    value_cols = ["Customer Code", "最近4周销售额", "前4周销售额", "绝对下降金额", "下降比例"]
    if not value_decline.empty:
        result = result.merge(value_decline[value_cols], on="Customer Code", how="left")
    else:
        for col in value_cols[1:]:
            result[col] = pd.NA
    freq_cols = ["Customer Code", "最近8周订单数", "前8周订单数", "订单数变化", "频次变化率"]
    if not frequency_decline.empty:
        result = result.merge(frequency_decline[freq_cols], on="Customer Code", how="left")
    else:
        for col in freq_cols[1:]:
            result[col] = pd.NA

    result["未下单天数"] = (anchor.normalize() - pd.to_datetime(result["最近下单日期"], errors="coerce")).dt.days
    result["金额变化率"] = -pd.to_numeric(result["下降比例"], errors="coerce")
    result["频次变化率"] = pd.to_numeric(result["频次变化率"], errors="coerce")
    contribution_threshold = result["历史累计销售额"].quantile(0.20) if result["历史累计销售额"].notna().any() else 0.0
    result["_high_contribution"] = result["历史累计销售额"].ge(contribution_threshold)
    result["风险等级"] = result.apply(_priority_for_row, axis=1)
    result["主要风险原因"] = result["风险类型"].str.split("；").str[0]
    result["_priority_order"] = result["风险等级"].map(PRIORITY_ORDER).fillna(9)
    result["_abc_order"] = result["ABC Class"].map(ABC_ORDER).fillna(9)
    result["_sort_impact"] = pd.to_numeric(result["未下单天数"], errors="coerce").fillna(0) + pd.to_numeric(result["绝对下降金额"], errors="coerce").fillna(0) / 1000
    ordered = result.sort_values(["_priority_order", "_abc_order", "历史累计销售额", "_sort_impact"], ascending=[True, True, False, False])
    return ordered[
        [
            "风险等级",
            "风险类型",
            "Customer Code",
            "Customer Name",
            "Customer Type",
            "ABC Class",
            "最近下单日期",
            "未下单天数",
            "最近4周销售额",
            "前4周销售额",
            "金额变化率",
            "最近8周订单数",
            "前8周订单数",
            "频次变化率",
            "历史累计销售额",
            "最近一次订单金额",
            "历史平均订单金额",
            "主要风险原因",
        ]
    ].reset_index(drop=True)


def follow_up_customers(risk_customers: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    if risk_customers is None or risk_customers.empty:
        return pd.DataFrame()
    result = risk_customers.copy()
    result["_priority_order"] = result["风险等级"].map(PRIORITY_ORDER).fillna(9)
    result["_abc_order"] = result["ABC Class"].map(ABC_ORDER).fillna(9)
    result["_impact"] = pd.to_numeric(result["未下单天数"], errors="coerce").fillna(0) + pd.to_numeric(result["历史累计销售额"], errors="coerce").fillna(0) / 1000
    result = result.sort_values(["_priority_order", "_abc_order", "历史累计销售额", "_impact"], ascending=[True, True, False, False])
    return result.head(limit).drop(columns=["_priority_order", "_abc_order", "_impact"], errors="ignore")


def build_customer_health(current_df: pd.DataFrame, history_df: pd.DataFrame) -> CustomerHealthResult:
    valid_current = _valid_customer_data(current_df)
    valid_history = _valid_customer_data(history_df)
    excluded = 0 if current_df is None or current_df.empty or "Customer Code" not in current_df.columns else int(current_df["Customer Code"].isna().sum())
    anchor = analysis_cutoff(valid_current)
    if anchor is None:
        empty_active = ActiveCustomerMetrics(pd.Timestamp.today().normalize(), 0, None, None, None, None)
        return CustomerHealthResult(empty_active, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), excluded, False)

    active = active_customer_metrics(valid_history, anchor)
    new_customers = new_customer_table(valid_current, valid_history, anchor)
    dormant_30, dormant_90 = dormant_customers(valid_history, anchor)
    value_decline = value_decline_customers(valid_history, anchor)
    frequency_decline = frequency_decline_customers(valid_history, anchor)
    risk = merge_risk_customers(valid_history, anchor, dormant_30, dormant_90, value_decline, frequency_decline)
    follow_up = follow_up_customers(risk)
    _, abc_fallback = _customer_profile(valid_history)
    return CustomerHealthResult(
        active_metrics=active,
        new_customers=new_customers,
        dormant_30=dormant_30,
        dormant_90=dormant_90,
        value_decline=value_decline,
        frequency_decline=frequency_decline,
        risk_customers=risk,
        follow_up=follow_up,
        excluded_missing_customer_code=excluded,
        abc_fallback_used=abc_fallback,
    )
