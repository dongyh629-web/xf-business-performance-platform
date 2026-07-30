from __future__ import annotations

from datetime import date
import unittest

from app.business_dashboard import (
    BusinessDashboardMetrics,
    DashboardTargetSelection,
    _remaining_caption,
    _target_caption,
    _target_money,
    _target_percent,
    business_status,
    generate_business_summary,
)


def _metrics() -> BusinessDashboardMetrics:
    return BusinessDashboardMetrics(
        anchor_date=date(2026, 7, 17),
        month_start=None,
        month_end=None,
        year_start=None,
        year_end=None,
        monthly_sales=1000.0,
        annual_sales=1000.0,
        today_sales=0.0,
        week_sales=0.0,
        previous_business_day_sales=0.0,
        previous_week_same_progress_sales=0.0,
        week_mom=None,
        today_orders=0,
        week_orders=0,
        month_orders=0,
        monthly_active_customers=0,
        week_active_customers=0,
        previous_month_sales=0.0,
        monthly_mom=None,
        monthly_target=0.0,
        annual_target=0.0,
        monthly_completion=None,
        annual_completion=None,
        previous_year_month_sales=0.0,
        monthly_yoy=None,
        previous_year_ytd_sales=0.0,
        annual_ytd_yoy=None,
        elapsed_workdays=0,
        total_workdays=0,
        remaining_workdays=10,
        workday_progress=None,
        pace_ratio=None,
        pace_gap=None,
        monthly_remaining_target=0.0,
        annual_remaining_target=0.0,
        required_daily_sales=None,
        week=None,
    )


class DashboardTargetDisplayTests(unittest.TestCase):
    def test_missing_target_display_does_not_render_zero_target(self) -> None:
        targets = DashboardTargetSelection(0.0, 0.0, False, False, None, "missing")
        metrics = _metrics()

        self.assertEqual("目标：未读取到目标", _target_caption(targets))
        self.assertEqual("剩余目标：N/A", _remaining_caption(metrics, targets))
        self.assertEqual("无基准", _target_percent(None, targets.has_monthly_target))
        self.assertEqual("N/A", _target_money(0.0, targets.has_annual_target))
        self.assertEqual(("未读取到目标数据", "info"), business_status(metrics, targets))
        self.assertIn("未读取到目标数据", generate_business_summary(metrics, targets))

    def test_loaded_zero_target_is_distinct_from_missing_target(self) -> None:
        targets = DashboardTargetSelection(0.0, 0.0, True, True, "Target Excel", None)
        metrics = _metrics()

        self.assertEqual("目标：£0", _target_caption(targets))
        self.assertEqual("剩余目标：£0", _remaining_caption(metrics, targets))
        self.assertEqual("尚未设置目标", business_status(metrics, targets)[0])


if __name__ == "__main__":
    unittest.main()
