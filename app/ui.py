from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import date
from pathlib import Path

from app.config import DATE_BASIS_DESCRIPTIONS, DATE_BASIS_LABELS, DATE_BASIS_OPTIONS
from app.data import apply_date_basis


def safe_page_link(page: str, label: str, **kwargs) -> None:
    """Render a page link only when Streamlit can resolve the target page."""
    target = Path(page)
    if not target.exists():
        return
    try:
        st.page_link(page, label=label, **kwargs)
    except Exception:
        return


STATUS_COLORS = {
    "green": ("#166534", "#e8f5ee", "#22c55e"),
    "red": ("#991b1b", "#fde8e8", "#ef4444"),
    "orange": ("#9a4b00", "#fff4e5", "#f59e0b"),
    "yellow": ("#854d0e", "#fff9db", "#eab308"),
    "gray": ("#4b5563", "#f3f4f6", "#9ca3af"),
    "blue": ("#1d4ed8", "#eff6ff", "#2563eb"),
}


def inject_global_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --xf-brand-primary: #FFC72C;
            --xf-brand-soft: #FFF7D6;
            --xf-brand-soft-hover: #FFF1B8;
            --xf-bg-page: #F7F8FA;
            --xf-bg-sidebar: #F3F4F6;
            --xf-bg-card: #FFFFFF;
            --xf-bg-hover: #F5F6F8;
            --xf-bg-active: #FFF8E1;
            --xf-text-primary: #202124;
            --xf-text-secondary: #5F6368;
            --xf-text-muted: #9AA0A6;
            --xf-border: #E5E7EB;
            --xf-border-strong: #D6D9DE;
            --xf-success: #2E8B57;
            --xf-warning: #C98500;
            --xf-error: #C23B3B;
            --xf-radius-sm: 6px;
            --xf-radius-md: 10px;
            --xf-radius-lg: 12px;
            --xf-shadow-card: 0 1px 2px rgba(0,0,0,0.04);
            --xf-space-1: 4px;
            --xf-space-2: 8px;
            --xf-space-3: 12px;
            --xf-space-4: 16px;
            --xf-space-5: 24px;
            --xf-space-6: 32px;
        }
        .stApp {
            background: var(--xf-bg-page);
            color: var(--xf-text-primary);
        }
        .block-container {max-width: 1240px; padding-top: 1.15rem; padding-bottom: 3rem;}
        section[data-testid="stSidebar"][aria-expanded="true"] {
            width: 242px !important;
            min-width: 242px !important;
        }
        section[data-testid="stSidebar"][aria-expanded="true"] > div {
            width: 242px !important;
            min-width: 242px !important;
        }
        section[data-testid="stSidebar"] {
            background: var(--xf-bg-sidebar);
        }
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            padding: 0.9rem 0.95rem 1.1rem 0.95rem;
        }
        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        button[kind="header"] {
            display: inline-flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            pointer-events: auto !important;
        }
        section[data-testid="stSidebar"] button[kind="headerNoPadding"] {
            display: inline-flex !important;
            visibility: visible !important;
            opacity: 1 !important;
            color: var(--xf-text-secondary) !important;
            background: var(--xf-bg-card) !important;
            border: 1px solid var(--xf-border-strong) !important;
            border-radius: var(--xf-radius-sm) !important;
            z-index: 20;
        }
        section[data-testid="stSidebar"] button[kind="headerNoPadding"] [data-testid="stIconMaterial"] {
            color: var(--xf-text-secondary) !important;
            font-size: 20px !important;
        }
        .xf-sidebar-brand {
            margin: 0.04rem 0 var(--xf-space-3) 0;
        }
        .xf-sidebar-brand-title {
            color: var(--xf-text-primary);
            font-size: 27px;
            font-weight: 700;
            line-height: 1.15;
            white-space: nowrap;
            letter-spacing: 0;
        }
        .xf-sidebar-brand-subtitle {
            color: var(--xf-text-secondary);
            font-size: 12.5px;
            line-height: 1.2;
            margin-top: var(--xf-space-1);
        }
        .xf-sidebar-brand-line {
            width: 34px;
            height: 3px;
            border-radius: 999px;
            background: var(--xf-brand-primary);
            margin-top: var(--xf-space-2);
        }
        .xf-sidebar-user {
            display: flex;
            align-items: center;
            gap: var(--xf-space-3);
            min-height: 50px;
            padding: var(--xf-space-2) 0 var(--xf-space-3) 0;
            border-bottom: 1px solid var(--xf-border);
            margin-bottom: var(--xf-space-2);
        }
        .xf-sidebar-avatar {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 32px;
            height: 32px;
            border-radius: 999px;
            background: var(--xf-brand-soft);
            border: 1px solid var(--xf-brand-soft-hover);
            color: var(--xf-text-primary);
            font-size: 13px;
            font-weight: 700;
        }
        .xf-sidebar-user-text {
            min-width: 0;
        }
        .xf-sidebar-user-name {
            color: var(--xf-text-primary);
            font-size: 15px;
            font-weight: 600;
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .xf-sidebar-user-role {
            color: var(--xf-text-secondary);
            font-size: 12px;
            line-height: 1.25;
            margin-top: 2px;
        }
        .xf-nav-group {
            color: #6b7280;
            font-size: 12.5px;
            font-weight: 600;
            line-height: 1.2;
            margin: 0.85rem 0 0.22rem 0;
        }
        .xf-native-nav-group {
            display: flex;
            align-items: baseline;
            gap: 5px;
            color: #1f2937;
            font-size: 17px;
            font-weight: 600;
            line-height: 1.2;
            margin: 0.56rem 0 0.18rem 0;
            padding-left: 0.08rem;
        }
        .xf-native-nav-subtitle {
            color: #8b93a1;
            font-size: 13px;
            font-weight: 500;
        }
        section[data-testid="stSidebar"] div[data-testid="stButton"] {
            margin: 0.04rem 0;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] {
            display: block !important;
            width: 100% !important;
            margin: 0 0 0.26rem 0;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] {
            width: 100% !important;
            margin: 0 !important;
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] details {
            width: 100% !important;
            border: 0 !important;
            background: transparent !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] details summary {
            min-height: 36px !important;
            padding: 0.22rem 0.5rem !important;
            border-radius: var(--xf-radius-sm) !important;
            background: transparent !important;
            color: var(--xf-text-primary) !important;
            line-height: 1.15 !important;
            text-align: left !important;
            justify-content: flex-start !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] details[open] summary,
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] details summary:hover {
            background: var(--xf-bg-hover) !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] details summary p {
            color: var(--xf-text-primary) !important;
            font-size: 14.5px !important;
            font-weight: 600 !important;
            line-height: 1.15 !important;
            margin: 0 !important;
            text-align: left !important;
            white-space: nowrap !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] details summary svg {
            color: var(--xf-text-muted) !important;
            margin-left: auto !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_group_row_"] [data-testid="stExpander"] details > div {
            padding: 0.1rem 0 0.06rem 0 !important;
        }
        .xf-nav-divider {
            height: 1px;
            background: var(--xf-border);
            margin: var(--xf-space-2) 0 var(--xf-space-2) 0;
        }
        .xf-nav-group-gap {
            height: 5px;
        }
        section[data-testid="stSidebar"] summary::marker {
            content: "";
        }
        section[data-testid="stSidebar"] summary::-webkit-details-marker {
            display: none;
        }
        .xf-nav-group-details {
            margin: 0;
        }
        .xf-nav-group-details > summary {
            list-style: none;
        }
        .xf-nav-group-details > summary::marker {
            content: "";
        }
        .xf-nav-group-details > summary::-webkit-details-marker {
            display: none;
        }
        .xf-nav-toggle {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
            box-sizing: border-box;
            color: #1f2937 !important;
            text-decoration: none !important;
            border-radius: 8px;
            padding: 8px 10px 8px 8px;
            margin: 1px 0;
            line-height: 1.2;
            background: transparent;
            cursor: pointer;
            user-select: none;
        }
        .xf-nav-toggle:hover {
            background: #f3f6fb;
            text-decoration: none !important;
        }
        .xf-nav-toggle-text {
            display: inline-flex;
            align-items: baseline;
            min-width: 0;
        }
        .xf-nav-toggle-label {
            color: #1f2937;
            font-size: 17px;
            font-weight: 600;
            white-space: nowrap;
        }
        .xf-nav-toggle-subtitle {
            color: #8b93a1;
            font-size: 13px;
            font-weight: 500;
            margin-left: 5px;
            white-space: nowrap;
        }
        .xf-nav-chevron {
            color: #8b93a1;
            flex: 0 0 auto;
            font-size: 14px;
            font-weight: 400;
            line-height: 1;
            margin-left: 10px;
            transition: color 160ms ease, transform 160ms ease;
        }
        .xf-nav-chevron::before {
            content: "›";
            display: inline-block;
        }
        .xf-nav-group-details[open] > .xf-nav-toggle .xf-nav-chevron::before {
            content: "⌄";
        }
        .xf-nav-toggle:hover .xf-nav-chevron {
            color: #64748b;
        }
        section[data-testid="stSidebar"] button[kind="tertiary"] {
            color: var(--xf-text-primary) !important;
            font-size: 14px !important;
            font-weight: 500 !important;
            line-height: 1.25 !important;
            min-height: 36px !important;
            padding: 0.24rem 0.5rem !important;
            margin: 0.7rem 0 0.35rem 0 !important;
            justify-content: flex-start !important;
            background: var(--xf-bg-card) !important;
            border: 1px solid var(--xf-border-strong) !important;
            border-radius: var(--xf-radius-sm) !important;
        }
        section[data-testid="stSidebar"] button[kind="tertiary"] p {
            font-size: 14px !important;
            font-weight: 500 !important;
            line-height: 1.25 !important;
        }
        section[data-testid="stSidebar"] button[kind="tertiary"]:hover {
            background: var(--xf-brand-soft) !important;
            border-color: var(--xf-brand-soft-hover) !important;
        }
        .xf-nav-children {
            margin: 0.18rem 0 0.28rem 1.12rem;
        }
        .xf-nav-link {
            display: block;
            color: #1f2937 !important;
            text-decoration: none !important;
            border-radius: 8px;
            padding: 0.36rem 0.58rem;
            margin: 0.16rem 0;
            line-height: 1.18;
        }
        .xf-nav-link:hover {
            background: #f3f4f6;
            text-decoration: none !important;
        }
        .xf-nav-link.active {
            background: #e8eef9;
        }
        .xf-nav-link.home {
            margin-top: 0.25rem;
        }
        .xf-nav-link-title {
            display: block;
            color: #1f2937;
            font-size: 15px;
            font-weight: 400;
        }
        .xf-nav-link.home .xf-nav-link-title {
            font-size: 16px;
            font-weight: 600;
        }
        .xf-nav-link-subtitle {
            display: block;
            color: #8a94a6;
            font-size: 12.5px;
            margin-top: 2px;
        }
        section[data-testid="stSidebar"] [data-testid="stPageLink"] {
            display: block !important;
            width: 100% !important;
            margin: 1px 0 1px 1.08rem !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_home_link"] [data-testid="stPageLink"] {
            margin-left: 0 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stPageLink"] a {
            width: 100% !important;
            min-height: 34px !important;
            padding: 0.22rem 0.46rem !important;
            border-radius: var(--xf-radius-sm) !important;
            font-size: 13.5px !important;
            font-weight: 400 !important;
            line-height: 1.25 !important;
            border-left: 3px solid transparent !important;
            justify-content: flex-start !important;
            text-align: left !important;
        }
        section[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"] {
            background: var(--xf-bg-active) !important;
            font-weight: 500 !important;
            border-left-color: var(--xf-brand-primary) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover {
            background: var(--xf-bg-hover) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stPageLink"] p {
            font-size: 13.5px !important;
            font-weight: inherit !important;
            line-height: 1.25 !important;
            margin: 0 !important;
            text-align: left !important;
        }
        section[data-testid="stSidebar"] div[class*="st-key-sidebar_home_link"] [data-testid="stPageLink"] a {
            min-height: 36px !important;
            font-weight: 500 !important;
        }
        section[data-testid="stSidebar"] h3 {
            color: var(--xf-text-primary);
            font-size: 0.88rem;
            font-weight: 650;
            margin-top: 0.8rem;
            margin-bottom: 0.28rem;
        }
        section[data-testid="stSidebar"] [data-testid="stExpander"] details summary {
            min-height: 34px;
            padding-top: 0.25rem;
            padding-bottom: 0.25rem;
        }
        section[data-testid="stSidebar"] [data-testid="stExpander"] {
            margin-bottom: 0.28rem;
        }
        section[data-testid="stSidebar"] p {
            color: var(--xf-text-secondary);
            margin-bottom: 0.22rem;
        }
        div[data-testid="stMetric"] {
            border: 1px solid var(--xf-border);
            border-top: 2px solid var(--xf-brand-primary);
            border-radius: var(--xf-radius-md);
            padding: 16px 16px 14px 16px;
            background: var(--xf-bg-card);
            box-shadow: var(--xf-shadow-card);
            min-height: 112px;
        }
        div[data-testid="stMetric"] label {color: var(--xf-text-secondary); font-size: 0.82rem; font-weight: 500;}
        div[data-testid="stMetricValue"] {color: var(--xf-text-primary); font-size: 1.62rem; font-weight: 700;}
        div[data-testid="stDataFrame"] {border: 1px solid var(--xf-border); border-radius: var(--xf-radius-md);}
        div[data-testid="stPlotlyChart"] {
            background: var(--xf-bg-card);
            border: 1px solid var(--xf-border);
            border-radius: var(--xf-radius-md);
            box-shadow: var(--xf-shadow-card);
            padding: var(--xf-space-3);
        }
        div[data-testid="stAlert"] {
            border-radius: var(--xf-radius-md);
        }
        .xf-page-title {
            margin: 0 0 var(--xf-space-4) 0;
        }
        .xf-page-title h1 {
            color: var(--xf-text-primary);
            font-size: 27px;
            font-weight: 700;
            line-height: 1.15;
            margin: 0;
            letter-spacing: 0;
        }
        .xf-page-title p {
            color: var(--xf-text-secondary);
            font-size: 13px;
            line-height: 1.35;
            margin: var(--xf-space-1) 0 0 0;
        }
        .xf-dashboard-header {
            display: flex;
            align-items: flex-start;
            justify-content: flex-start;
            gap: var(--xf-space-5);
            padding: var(--xf-space-4) 0 var(--xf-space-3) 0;
            margin-bottom: var(--xf-space-3);
            border-bottom: 1px solid var(--xf-border);
        }
        .xf-dashboard-title {
            width: 100%;
        }
        .xf-dashboard-title h1 {
            color: var(--xf-text-primary);
            font-size: 28px;
            font-weight: 700;
            line-height: 1.12;
            margin: 0;
            letter-spacing: 0;
        }
        .xf-dashboard-title p {
            color: var(--xf-text-secondary);
            font-size: 13px;
            line-height: 1.35;
            margin: var(--xf-space-1) 0 0 0;
        }
        .xf-dashboard-meta {
            display: flex;
            flex-wrap: wrap;
            gap: var(--xf-space-2);
            margin-top: var(--xf-space-3);
        }
        .xf-dashboard-meta-item {
            color: var(--xf-text-secondary);
            font-size: 12.5px;
            line-height: 1.2;
            padding: 5px 8px;
            border: 1px solid var(--xf-border);
            border-radius: var(--xf-radius-sm);
            background: rgba(255,255,255,0.65);
        }
        .xf-dashboard-meta-item strong {
            color: var(--xf-text-primary);
            font-weight: 600;
        }
        .xf-dashboard-meta-status {
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .xf-dashboard-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 999px;
            background: var(--xf-success);
        }
        .xf-dashboard-meta-status.warning .xf-dashboard-status-dot {
            background: var(--xf-warning);
        }
        .xf-dashboard-scope {
            color: var(--xf-text-muted);
            font-size: 12.5px;
            margin: -4px 0 var(--xf-space-3) 0;
        }
        .xf-alert {
            display: flex;
            gap: var(--xf-space-3);
            padding: 12px 14px;
            margin: var(--xf-space-3) 0 var(--xf-space-5) 0;
            border: 1px solid var(--xf-border);
            border-left: 4px solid var(--xf-info-color, #64748b);
            border-radius: var(--xf-radius-md);
            background: var(--xf-alert-bg, #ffffff);
            box-shadow: var(--xf-shadow-card);
        }
        .xf-alert.info {--xf-info-color: #64748b; --xf-alert-bg: #f8fafc;}
        .xf-alert.success {--xf-info-color: var(--xf-success); --xf-alert-bg: #f3faf6;}
        .xf-alert.warning {--xf-info-color: var(--xf-warning); --xf-alert-bg: #fffaf0;}
        .xf-alert.error {--xf-info-color: var(--xf-error); --xf-alert-bg: #fff5f5;}
        .xf-alert-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 24px;
            width: 22px;
            height: 22px;
            border-radius: 999px;
            color: var(--xf-info-color);
            border: 1px solid currentColor;
            font-size: 12px;
            font-weight: 700;
            line-height: 1;
            margin-top: 1px;
        }
        .xf-alert-content {
            min-width: 0;
        }
        .xf-alert-title {
            color: var(--xf-text-primary);
            font-size: 15px;
            font-weight: 700;
            line-height: 1.25;
            margin: 0 0 4px 0;
        }
        .xf-alert-body {
            color: var(--xf-text-secondary);
            font-size: 14px;
            line-height: 1.45;
            margin: 0;
        }
        .xf-executive-kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: var(--xf-space-4);
            margin: var(--xf-space-3) 0 var(--xf-space-5) 0;
        }
        .xf-executive-kpi {
            position: relative;
            min-height: 142px;
            padding: var(--xf-space-4);
            border: 1px solid var(--xf-border);
            border-radius: var(--xf-radius-md);
            background: var(--xf-bg-card);
            box-shadow: var(--xf-shadow-card);
            overflow: hidden;
        }
        .xf-executive-kpi.featured {
            border-color: #ead37a;
            box-shadow: 0 0 0 1px rgba(255,199,44,0.25), var(--xf-shadow-card);
        }
        .xf-executive-kpi.featured::before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: var(--xf-brand-primary);
        }
        .xf-executive-kpi-top {
            display: flex;
            align-items: center;
            justify-content: flex-start;
            gap: var(--xf-space-2);
            margin-bottom: var(--xf-space-3);
        }
        .xf-executive-kpi-label {
            color: var(--xf-text-secondary);
            font-size: 12.5px;
            font-weight: 600;
            line-height: 1.2;
        }
        .xf-executive-kpi-value {
            color: var(--xf-text-primary);
            font-size: 27px;
            font-weight: 750;
            line-height: 1.08;
            margin-bottom: var(--xf-space-2);
            overflow-wrap: anywhere;
        }
        .xf-executive-kpi.featured .xf-executive-kpi-value {
            font-size: 30px;
        }
        .xf-trend {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            color: var(--xf-text-muted);
            font-size: 13px;
            font-weight: 650;
            line-height: 1.2;
            margin-bottom: var(--xf-space-2);
        }
        .xf-trend.up {color: var(--xf-success);}
        .xf-trend.down {color: var(--xf-error);}
        .xf-trend.neutral {color: var(--xf-text-muted);}
        .xf-executive-kpi-caption {
            color: var(--xf-text-secondary);
            font-size: 12.5px;
            line-height: 1.35;
        }
        .xf-summary-card-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: var(--xf-space-4);
            margin: var(--xf-space-3) 0 var(--xf-space-5) 0;
        }
        .xf-summary-card {
            min-height: 102px;
            padding: 15px 16px 14px 16px;
            border: 1px solid var(--xf-border);
            border-radius: var(--xf-radius-md);
            background: var(--xf-bg-card);
            box-shadow: var(--xf-shadow-card);
        }
        .xf-summary-card.featured {
            border-top: 3px solid var(--xf-brand-primary);
            border-color: #ead37a;
        }
        .xf-summary-card-label {
            color: var(--xf-text-secondary);
            font-size: 13px;
            font-weight: 500;
            line-height: 1.25;
            margin-bottom: var(--xf-space-2);
        }
        .xf-summary-card-value {
            color: var(--xf-text-primary);
            font-size: 24px;
            font-weight: 730;
            line-height: 1.12;
            overflow-wrap: anywhere;
        }
        .xf-section {margin-top: var(--xf-space-5); margin-bottom: var(--xf-space-3);}
        .xf-section h3 {
            color: var(--xf-text-primary);
            font-size: 1.08rem;
            font-weight: 600;
            margin-bottom: 0.12rem;
        }
        .xf-section p {color: var(--xf-text-muted); font-size: 12.5px; margin-top: 0;}
        .xf-badge {display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 0.78rem; font-weight: 600;}
        div[data-testid="column"] {min-width: 0;}
        div[data-testid="stDataFrame"] * {overflow-wrap: anywhere;}
        @media (max-width: 1100px) {
            div[data-testid="stMetricValue"] {font-size: 1.25rem;}
            div[data-testid="stMetric"] {min-height: 104px; padding: 12px;}
            .xf-executive-kpi-grid {grid-template-columns: repeat(2, minmax(0, 1fr));}
            .xf-summary-card-grid {grid-template-columns: repeat(2, minmax(0, 1fr));}
            .xf-dashboard-header {flex-direction: column; gap: var(--xf-space-3);}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def section_header(title: str, caption: str | None = None) -> None:
    caption_html = f"<p>{caption}</p>" if caption else ""
    st.markdown(f'<div class="xf-section"><h3>{title}</h3>{caption_html}</div>', unsafe_allow_html=True)


def status_badge(label: str, tone: str = "gray") -> str:
    color, background, _ = STATUS_COLORS.get(tone, STATUS_COLORS["gray"])
    return f'<span class="xf-badge" style="color:{color}; background:{background};">{label}</span>'


def status_style(value: object) -> str:
    text = "" if pd.isna(value) else str(value)
    if any(token in text for token in ["领先", "达标", "增长", "改善", "良好", "已连接", "绿色"]):
        color, background, _ = STATUS_COLORS["green"]
    elif any(token in text for token in ["高风险", "严重", "明显下降", "落后", "红色"]):
        color, background, _ = STATUS_COLORS["red"]
    elif any(token in text for token in ["关注", "轻度", "接近", "黄色", "橙色"]):
        color, background, _ = STATUS_COLORS["orange"]
    elif any(token in text for token in ["不足", "无同期", "未配置", "暂无", "灰色"]):
        color, background, _ = STATUS_COLORS["gray"]
    else:
        return ""
    return f"background-color: {background}; color: {color}; font-weight: 600;"


def kpi_grid(metrics: list[dict[str, object]], columns: int = 4) -> None:
    if not metrics:
        return
    for start in range(0, len(metrics), columns):
        cols = st.columns(columns)
        for idx, col in enumerate(cols):
            item_index = start + idx
            if item_index >= len(metrics):
                col.empty()
                continue
            item = metrics[item_index]
            col.metric(
                str(item.get("label", "")),
                str(item.get("value", "")),
                delta=item.get("delta"),
                help=item.get("help"),
            )
            if item.get("caption"):
                col.caption(str(item["caption"]))


def metric_delta(value: float | int | None, suffix: str = "", precision: int = 1) -> str | None:
    if value is None or pd.isna(value):
        return None
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value):.{precision}f}{suffix}"


def metric_card(label: str, value: object, delta: object | None = None, help: str | None = None) -> None:
    st.metric(label, value, delta=delta, help=help)


def kpi_card(label: str, value: object, delta: object | None = None, help: str | None = None) -> None:
    metric_card(label, value, delta=delta, help=help)


def data_status(label: str, loaded: bool, detail: str | None = None) -> None:
    tone = "green" if loaded else "gray"
    state = "已加载" if loaded else "未加载"
    st.markdown(f"**{label}** {status_badge(state, tone)}", unsafe_allow_html=True)
    if detail:
        st.caption(detail)


def empty_state(message: str, caption: str | None = None) -> None:
    st.info(message)
    if caption:
        st.caption(caption)


def formatted_table(
    data: pd.DataFrame,
    *,
    height: int | None = None,
    use_container_width: bool = True,
    hide_index: bool = True,
    **kwargs,
) -> None:
    st.dataframe(
        data,
        height=height,
        use_container_width=use_container_width,
        hide_index=hide_index,
        **kwargs,
    )


def money(value: float) -> str:
    return f"£{value:,.0f}"


def number(value: float) -> str:
    return f"{value:,.0f}"


def percent(value: float) -> str:
    return f"{value:.1%}"


def days(value: float | None) -> str:
    if pd.isna(value):
        return "仅1单"
    return f"约 {value:.1f} 天"


def date_text(value) -> str:
    if pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def compact_name(value: object, max_len: int = 34) -> str:
    text = "未分类" if pd.isna(value) else str(value)
    if " — " in text:
        text = text.split(" — ", 1)[1]
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _first_valid_date(df: pd.DataFrame, col: str):
    if col not in df.columns:
        return None
    values = pd.to_datetime(df[col], errors="coerce").dropna()
    if values.empty:
        return None
    return values.min().date(), values.max().date()


def _reset_filter_state(key_prefix: str, min_date, max_date) -> None:
    st.session_state["date_basis"] = "Completed Date"
    st.session_state[f"{key_prefix}_date_range"] = (min_date, max_date) if min_date and max_date else None
    st.session_state[f"{key_prefix}_all_customer_types"] = True
    st.session_state[f"{key_prefix}_all_product_groups"] = True
    st.session_state[f"{key_prefix}_selected_customer_types"] = []
    st.session_state[f"{key_prefix}_selected_product_groups"] = []


def show_filters(df: pd.DataFrame, key_prefix: str = "main") -> pd.DataFrame:
    with st.sidebar:
        st.markdown("### Date Basis")
        current_basis = st.session_state.get("date_basis", "Completed Date")
        if current_basis not in DATE_BASIS_OPTIONS:
            current_basis = "Completed Date"
        selected_basis = st.selectbox(
            "分析日期口径\nDate Basis",
            DATE_BASIS_OPTIONS,
            index=DATE_BASIS_OPTIONS.index(current_basis),
            key="date_basis",
            format_func=lambda value: DATE_BASIS_LABELS.get(value, value),
        )
        st.caption(DATE_BASIS_DESCRIPTIONS[selected_basis])

    basis_df = apply_date_basis(df, selected_basis)
    if basis_df["Performance Date"].isna().any():
        st.warning(f"当前选择的日期字段 `{selected_basis}` 存在缺失或无效值，请到数据质量中心查看明细。")

    min_date = basis_df["Performance Date"].dropna().min()
    max_date = basis_df["Performance Date"].dropna().max()
    month_values = basis_df["Month"].fillna("未分类").astype(str)
    customer_type_values = basis_df["Customer Type"].fillna("未分类").astype(str)
    product_group_values = basis_df["Product Group"].fillna("未分类").astype(str)

    with st.sidebar:
        st.markdown("### 筛选")
        if pd.notna(min_date) and pd.notna(max_date):
            selected_range = st.date_input(
                "日期范围",
                value=(min_date.date(), max_date.date()),
                min_value=min_date.date(),
                max_value=max_date.date(),
                key=f"{key_prefix}_date_range",
            )
        else:
            selected_range = None
        customer_types = sorted(customer_type_values.unique().tolist())
        product_groups = sorted(product_group_values.unique().tolist())

        customer_expanded = not st.session_state.get(f"{key_prefix}_all_customer_types", True)
        customer_title = "客户类型：已筛选" if customer_expanded else "客户类型：全部"
        with st.expander(customer_title, expanded=customer_expanded):
            all_customer_types = st.checkbox("全部客户类型", value=True, key=f"{key_prefix}_all_customer_types")
            if all_customer_types:
                selected_customer_types = customer_types
                st.caption("当前选择全部客户类型")
            else:
                selected_customer_types = st.multiselect(
                    "客户类型",
                    customer_types,
                    default=st.session_state.get(f"{key_prefix}_selected_customer_types", []),
                    key=f"{key_prefix}_selected_customer_types",
                )
                st.caption(f"已选 {len(selected_customer_types)} 项")

        product_expanded = not st.session_state.get(f"{key_prefix}_all_product_groups", True)
        product_title = "产品组：已筛选" if product_expanded else "产品组：全部"
        with st.expander(product_title, expanded=product_expanded):
            all_product_groups = st.checkbox("全部产品组", value=True, key=f"{key_prefix}_all_product_groups")
            if all_product_groups:
                selected_product_groups = product_groups
                st.caption("当前选择全部产品组")
            else:
                selected_product_groups = st.multiselect(
                    "产品组",
                    product_groups,
                    default=st.session_state.get(f"{key_prefix}_selected_product_groups", []),
                    key=f"{key_prefix}_selected_product_groups",
                )
                st.caption(f"已选 {len(selected_product_groups)} 项")

        st.divider()
        if st.button("重置全部筛选", key=f"{key_prefix}_reset_filters", use_container_width=True):
            _reset_filter_state(key_prefix, min_date.date() if pd.notna(min_date) else None, max_date.date() if pd.notna(max_date) else None)
            st.rerun()

    date_mask = pd.Series(True, index=basis_df.index)
    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        start, end = selected_range
        date_values = basis_df["Performance Date"].dt.date
        date_mask = date_values.ge(start) & date_values.le(end)

    filtered = basis_df[date_mask & customer_type_values.isin(selected_customer_types) & product_group_values.isin(selected_product_groups)].copy()
    filtered.attrs["date_basis"] = selected_basis
    if isinstance(selected_range, tuple) and len(selected_range) == 2:
        filtered.attrs["date_range"] = (str(selected_range[0]), str(selected_range[1]))
    else:
        filtered.attrs["date_range"] = None
    filtered.attrs["customer_types"] = selected_customer_types
    filtered.attrs["product_groups"] = selected_product_groups
    filtered.attrs["all_customer_type_count"] = len(customer_types)
    filtered.attrs["all_product_group_count"] = len(product_groups)
    return filtered


def show_code_warning(df: pd.DataFrame) -> None:
    missing_customer_code = "Customer Code" not in df.columns or df["Customer Code"].isna().any()
    missing_product_code = "Product Code" not in df.columns or df["Product Code"].isna().any()
    if missing_customer_code or missing_product_code:
        st.warning("当前源文件缺少唯一代码，分析暂按名称统计，可能存在重名、改名或空格导致的误差。")


def show_context_summary(df: pd.DataFrame) -> None:
    basis = df.attrs.get("date_basis", st.session_state.get("date_basis", "Completed Date"))
    basis_label = DATE_BASIS_LABELS.get(basis, basis)
    desc = DATE_BASIS_DESCRIPTIONS.get(basis, "")
    valid_dates = df["Performance Date"].dropna() if "Performance Date" in df.columns else pd.Series(dtype="datetime64[ns]")
    if valid_dates.empty:
        date_text = "无有效日期"
    else:
        date_text = f"{valid_dates.min().date()} 至 {valid_dates.max().date()}"
    customer_types = df.attrs.get("customer_types", [])
    product_groups = df.attrs.get("product_groups", [])
    all_customer_count = df.attrs.get("all_customer_type_count")
    all_product_count = df.attrs.get("all_product_group_count")
    customer_text = "全部" if all_customer_count is None or len(customer_types) == all_customer_count else f"已选 {len(customer_types)} 项"
    product_text = "全部" if all_product_count is None or len(product_groups) == all_product_count else f"已选 {len(product_groups)} 项"
    cols = st.columns(4)
    cols[0].caption(f"日期范围\n\n**{date_text}**")
    cols[1].caption(f"日期口径\n\n**{basis_label}**")
    cols[2].caption(f"客户类型\n\n**{customer_text}**")
    cols[3].caption(f"产品组\n\n**{product_text}**")
    st.caption(desc)
    if not valid_dates.empty:
        latest_date = valid_dates.max().date()
        today = date.today()
        if latest_date.year == today.year and latest_date.month == today.month:
            st.warning(f"{latest_date:%Y-%m} 为截至 {today:%Y-%m-%d} 的部分月份数据，避免与完整月份直接比较。")


def metric_row(df: pd.DataFrame) -> None:
    sales = df["Sales Amount"].sum()
    orders = df["Order No."].nunique()
    customer_dim = "Customer Key" if "Customer Key" in df.columns else "Customer"
    product_dim = "Product Key" if "Product Key" in df.columns else "Product"
    customers = df[customer_dim].nunique()
    products = df[product_dim].nunique()
    avg_order = sales / orders if orders else 0

    metrics = [
        ("销售额", money(sales)),
        ("订单数", number(orders)),
        ("客户数", number(customers)),
        ("产品数", number(products)),
        ("平均订单金额", money(avg_order)),
    ]
    cols = st.columns(len(metrics))
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)


def metric_cards(metrics: list[tuple[str, str]]) -> None:
    if not metrics:
        return
    cols = st.columns(len(metrics))
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)


def style_plotly(fig):
    fig.update_layout(
        font=dict(size=12, color="#5F6368"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=12, r=12, t=44, b=18),
        title=dict(font=dict(size=15, color="#202124")),
        legend=dict(font=dict(size=11), orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        colorway=["#FFC72C", "#AEB6C2", "#374151", "#8B93A1", "#D9A600", "#64748B"],
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor="#E5E7EB", title_font=dict(size=11), tickfont=dict(size=11))
    fig.update_yaxes(showgrid=True, gridcolor="#EEF1F4", zeroline=False, title_font=dict(size=11), tickfont=dict(size=11))
    return fig


def line_chart(data: pd.DataFrame, x: str, y: str, title: str):
    fig = px.line(data, x=x, y=y, markers=True, title=title)
    fig.update_traces(line=dict(color="#FFC72C", width=2.6), marker=dict(size=6, color="#FFC72C"))
    fig.update_yaxes(tickprefix="£", separatethousands=True)
    fig.update_layout(height=340)
    return style_plotly(fig)


def bar_chart(data: pd.DataFrame, x: str, y: str, title: str, orientation: str = "v"):
    plot_data = data.copy()
    if orientation == "h":
        label_col = y
        if label_col in plot_data.columns:
            plot_data["_Display Label"] = plot_data[label_col].map(compact_name)
            plot_data = plot_data.sort_values(x, ascending=True)
            fig = px.bar(
                plot_data,
                x=x,
                y="_Display Label",
                title=title,
                orientation="h",
                hover_data={label_col: True, x: ":,.2f", "_Display Label": False},
            )
            fig.update_yaxes(title="")
        else:
            fig = px.bar(plot_data, x=x, y=y, title=title, orientation=orientation)
    else:
        fig = px.bar(plot_data, x=x, y=y, title=title, orientation=orientation)
    fig.update_traces(marker_color="#FFC72C", hovertemplate="%{y}<br>销售额：£%{x:,.2f}<extra></extra>" if orientation == "h" else None)
    fig.update_xaxes(tickprefix="£", separatethousands=True, title="销售额" if orientation == "h" else None)
    fig.update_layout(height=max(320, min(560, 26 * len(plot_data) + 120)))
    return style_plotly(fig)


def donut_chart(data: pd.DataFrame, names: str, values: str, title: str):
    plot_data = data.copy()
    plot_data[names] = plot_data[names].fillna("未分类").astype(str).replace({"<NA>": "未分类", "nan": "未分类"})
    fig = px.pie(plot_data, names=names, values=values, title=title, hole=0.55)
    fig.update_traces(textposition="inside", textinfo="percent", hovertemplate="%{label}<br>销售额：£%{value:,.2f}<br>占比：%{percent}<extra></extra>")
    fig.update_layout(height=360)
    return style_plotly(fig)
