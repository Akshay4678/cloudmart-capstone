import csv
import io
import os
from datetime import datetime

import boto3
import pymysql
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for

app = Flask(__name__)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "")
DB_HOST = os.environ.get("DB_HOST", "")
DB_NAME = os.environ.get("DB_NAME", "cloudmart")
DB_USER = os.environ.get("DB_USER", "cloudmartadmin")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_PASSWORD_PARAMETER = os.environ.get(
    "DB_PASSWORD_PARAMETER",
    "/cloudmart/dev/database/password",
)

s3 = boto3.client("s3", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)


def get_db_password():
    response = ssm.get_parameter(
        Name=DB_PASSWORD_PARAMETER,
        WithDecryption=True,
    )
    return response["Parameter"]["Value"]


def get_db_connection():
    if not DB_HOST:
        raise RuntimeError("DB_HOST is not configured")

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=get_db_password(),
        database=DB_NAME,
        port=DB_PORT,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


def fetch_dashboard_data():
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            # KPI cards
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS active_products,
                    COALESCE(SUM(stock_count), 0) AS total_stock,
                    SUM(CASE WHEN stock_count <= 5 THEN 1 ELSE 0 END)
                        AS low_stock_products
                FROM products
                WHERE status = 'ACTIVE'
                """
            )
            summary = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS orders_7d,
                    COALESCE(SUM(total_amount), 0) AS sales_7d
                FROM orders
                WHERE status = 'CONFIRMED'
                  AND created_at >= NOW() - INTERVAL 7 DAY
                """
            )
            sales_summary = cursor.fetchone()

            cursor.execute(
                """
                SELECT COUNT(*) AS audit_events
                FROM audit_logs
                """
            )
            audit_events = cursor.fetchone()["audit_events"]

            # Actual products table: no invented category/inventory columns.
            cursor.execute(
                """
                SELECT
                    product_id,
                    name,
                    description,
                    price,
                    stock_count,
                    status,
                    created_at,
                    updated_at
                FROM products
                ORDER BY created_at DESC
                LIMIT 12
                """
            )
            products = cursor.fetchall()

            # "Sold in the week" is based on confirmed orders created
            # during the last 7 days.
            cursor.execute(
                """
                SELECT
                    p.product_id,
                    p.name,
                    SUM(oi.quantity) AS units_sold,
                    COALESCE(SUM(oi.quantity * oi.price), 0) AS revenue
                FROM orders o
                INNER JOIN order_items oi
                    ON o.order_id = oi.order_id
                INNER JOIN products p
                    ON oi.product_id = p.product_id
                WHERE o.status = 'CONFIRMED'
                  AND o.created_at >= NOW() - INTERVAL 7 DAY
                GROUP BY p.product_id, p.name
                ORDER BY units_sold DESC, revenue DESC
                LIMIT 5
                """
            )
            top_products = cursor.fetchall()

            cursor.execute(
                """
                SELECT
                    o.order_id,
                    o.customer_id,
                    COALESCE(c.name, 'Customer #' + CAST(o.customer_id AS CHAR))
                        AS customer_name,
                    o.status,
                    o.total_amount,
                    o.created_at,
                    o.updated_at
                FROM orders o
                LEFT JOIN customers c
                    ON o.customer_id = c.customer_id
                ORDER BY o.created_at DESC
                LIMIT 10
                """
            )
            recent_orders = cursor.fetchall()

            return {
                "summary": {
                    "active_products": summary["active_products"] or 0,
                    "total_stock": summary["total_stock"] or 0,
                    "low_stock_products": summary["low_stock_products"] or 0,
                    "orders_7d": sales_summary["orders_7d"] or 0,
                    "sales_7d": sales_summary["sales_7d"] or 0,
                    "audit_events": audit_events or 0,
                },
                "products": products,
                "top_products": top_products,
                "recent_orders": recent_orders,
            }
    finally:
        connection.close()


def get_report_objects():
    if not REPORTS_BUCKET:
        return []

    reports = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=REPORTS_BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith(".csv"):
                reports.append(
                    {
                        "key": key,
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    }
                )

    reports.sort(key=lambda item: item["last_modified"], reverse=True)
    return reports


def read_report_csv(key):
    response = s3.get_object(Bucket=REPORTS_BUCKET, Key=key)
    content = response["Body"].read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    columns = reader.fieldnames or []
    rows = list(reader)
    return columns, rows


@app.route("/health")
def health():
    return jsonify(
        {
            "status": "healthy",
            "service": "cloudmart-dashboard",
        }
    )


@app.route("/reports/download")
def download_report():
    key = request.args.get("key", "").strip()

    if not key:
        return "Report key is required.", 400

    try:
        response = s3.get_object(Bucket=REPORTS_BUCKET, Key=key)
        body = response["Body"].read()

        filename = key.rsplit("/", 1)[-1] or "cloudmart-report.csv"

        return Response(
            body,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    except (BotoCoreError, ClientError) as exc:
        return f"Unable to download report: {exc}", 500


@app.route("/")
def dashboard():
    data = {
        "summary": {
            "active_products": "—",
            "total_stock": "—",
            "low_stock_products": "—",
            "orders_7d": "—",
            "sales_7d": "—",
            "audit_events": "—",
        },
        "products": [],
        "top_products": [],
        "recent_orders": [],
    }
    db_error = None

    try:
        data = fetch_dashboard_data()
    except Exception as exc:
        db_error = str(exc)

    reports = []
    report_error = None

    try:
        reports = get_report_objects()
    except Exception as exc:
        report_error = str(exc)

    selected_key = request.args.get("report", "").strip()
    selected_columns = []
    selected_rows = []
    selected_error = None

    if selected_key:
        try:
            selected_columns, selected_rows = read_report_csv(selected_key)
        except Exception as exc:
            selected_error = str(exc)

    return render_template_string(
        DASHBOARD_HTML,
        db=data,
        db_error=db_error,
        reports=reports,
        report_error=report_error,
        selected_key=selected_key,
        selected_columns=selected_columns,
        selected_rows=selected_rows,
        selected_error=selected_error,
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CloudMart Operations Console</title>
<style>
    :root {
        --bg: #f5f7fb;
        --surface: #ffffff;
        --surface-2: #f8fafc;
        --text: #101828;
        --muted: #667085;
        --line: #e4e7ec;
        --nav: #111827;
        --nav-hover: #1f2937;
        --blue: #2563eb;
        --blue-soft: #eff6ff;
        --green: #039855;
        --green-soft: #ecfdf3;
        --orange: #d97706;
        --orange-soft: #fffbeb;
        --red: #d92d20;
        --red-soft: #fef3f2;
        --shadow: 0 8px 30px rgba(16, 24, 40, 0.06);
        --radius: 14px;
    }

    * { box-sizing: border-box; }

    html { scroll-behavior: smooth; }

    body {
        margin: 0;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
                     "Segoe UI", sans-serif;
        background: var(--bg);
        color: var(--text);
    }

    a { color: inherit; text-decoration: none; }

    .layout {
        display: flex;
        min-height: 100vh;
    }

    .sidebar {
        width: 240px;
        background: var(--nav);
        color: #d1d5db;
        padding: 22px 14px;
        position: fixed;
        inset: 0 auto 0 0;
        z-index: 10;
    }

    .brand {
        display: flex;
        align-items: center;
        gap: 11px;
        padding: 6px 12px 25px;
        color: #fff;
    }

    .brand-logo {
        width: 38px;
        height: 38px;
        border-radius: 11px;
        background: #fff;
        color: #111827;
        display: grid;
        place-items: center;
        font-weight: 800;
        font-size: 18px;
    }

    .brand strong { display: block; font-size: 16px; }
    .brand span { display: block; font-size: 11px; color: #9ca3af; margin-top: 2px; }

    .nav {
        display: grid;
        gap: 5px;
    }

    .nav a {
        padding: 11px 12px;
        border-radius: 9px;
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 13px;
    }

    .nav a:hover,
    .nav a.active {
        background: var(--nav-hover);
        color: #fff;
    }

    .nav-icon {
        width: 20px;
        text-align: center;
        opacity: .9;
    }

    .main {
        margin-left: 240px;
        width: calc(100% - 240px);
        min-width: 0;
    }

    .topbar {
        background: var(--surface);
        border-bottom: 1px solid var(--line);
        padding: 18px 32px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 20px;
        position: sticky;
        top: 0;
        z-index: 5;
    }

    .topbar h1 {
        margin: 0;
        font-size: 22px;
        letter-spacing: -.02em;
    }

    .topbar p {
        margin: 5px 0 0;
        color: var(--muted);
        font-size: 13px;
    }

    .env {
        background: var(--green-soft);
        color: var(--green);
        border: 1px solid #abefc6;
        border-radius: 999px;
        padding: 7px 12px;
        font-size: 12px;
        font-weight: 700;
    }

    .content {
        max-width: 1450px;
        margin: 0 auto;
        padding: 28px 32px 45px;
    }

    .notice {
        padding: 13px 16px;
        border-radius: 10px;
        margin-bottom: 20px;
        font-size: 13px;
        background: var(--green-soft);
        color: #027a48;
        border: 1px solid #abefc6;
    }

    .error {
        background: var(--red-soft);
        color: var(--red);
        border: 1px solid #fecdca;
        padding: 12px 14px;
        border-radius: 10px;
        margin-bottom: 18px;
        font-size: 13px;
    }

    .section-title {
        display: flex;
        justify-content: space-between;
        align-items: end;
        gap: 15px;
        margin: 30px 0 13px;
    }

    .section-title h2 {
        margin: 0;
        font-size: 17px;
        letter-spacing: -.01em;
    }

    .section-title p {
        margin: 4px 0 0;
        color: var(--muted);
        font-size: 12px;
    }

    .cards {
        display: grid;
        grid-template-columns: repeat(6, minmax(150px, 1fr));
        gap: 14px;
    }

    .card {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        padding: 17px;
        box-shadow: var(--shadow);
    }

    .card-label {
        color: var(--muted);
        font-size: 12px;
        font-weight: 600;
    }

    .card-value {
        margin-top: 9px;
        font-size: 25px;
        font-weight: 800;
        letter-spacing: -.03em;
    }

    .card-sub {
        margin-top: 5px;
        font-size: 11px;
        color: var(--muted);
    }

    .grid-2 {
        display: grid;
        grid-template-columns: minmax(0, 1.25fr) minmax(330px, .75fr);
        gap: 18px;
    }

    .panel {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        overflow: hidden;
    }

    .panel-head {
        padding: 17px 18px;
        border-bottom: 1px solid var(--line);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
    }

    .panel-head h3 {
        margin: 0;
        font-size: 15px;
    }

    .panel-head span {
        color: var(--muted);
        font-size: 11px;
    }

    .table-wrap {
        overflow-x: auto;
    }

    table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
    }

    th, td {
        padding: 12px 14px;
        border-bottom: 1px solid #eef0f3;
        text-align: left;
        white-space: nowrap;
    }

    th {
        background: var(--surface-2);
        color: #475467;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: .04em;
    }

    tbody tr:hover { background: #fafcff; }

    .product-name {
        font-weight: 700;
        color: #1d2939;
    }

    .description {
        color: var(--muted);
        max-width: 250px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .money { font-weight: 700; }

    .badge {
        display: inline-flex;
        align-items: center;
        padding: 4px 8px;
        border-radius: 999px;
        font-size: 10px;
        font-weight: 800;
    }

    .badge-green { color: #027a48; background: var(--green-soft); }
    .badge-orange { color: #b54708; background: var(--orange-soft); }
    .badge-red { color: #b42318; background: var(--red-soft); }
    .badge-blue { color: #175cd3; background: var(--blue-soft); }

    .stock-low {
        color: var(--red);
        font-weight: 800;
    }

    .top-products {
        padding: 17px;
    }

    .rank-row {
        display: grid;
        grid-template-columns: 30px minmax(0, 1fr) 55px;
        gap: 10px;
        align-items: center;
        margin-bottom: 18px;
    }

    .rank {
        width: 28px;
        height: 28px;
        border-radius: 8px;
        display: grid;
        place-items: center;
        background: var(--blue-soft);
        color: var(--blue);
        font-weight: 800;
        font-size: 12px;
    }

    .rank-info { min-width: 0; }

    .rank-name {
        font-weight: 700;
        font-size: 12px;
        margin-bottom: 6px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .bar-bg {
        height: 7px;
        background: #edf2f7;
        border-radius: 999px;
        overflow: hidden;
    }

    .bar {
        height: 100%;
        background: var(--blue);
        border-radius: 999px;
    }

    .units {
        text-align: right;
        font-weight: 800;
        font-size: 12px;
    }

    .empty {
        padding: 28px 18px;
        color: var(--muted);
        text-align: center;
        font-size: 13px;
    }

    .report-list {
        display: grid;
        gap: 10px;
        padding: 15px;
    }

    .report-row {
        border: 1px solid var(--line);
        border-radius: 11px;
        padding: 12px 13px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
    }

    .report-name {
        font-weight: 700;
        font-size: 12px;
        word-break: break-all;
    }

    .report-meta {
        color: var(--muted);
        font-size: 10px;
        margin-top: 4px;
    }

    .actions {
        display: flex;
        gap: 7px;
        flex-wrap: wrap;
    }

    .btn {
        border: 1px solid #d0d5dd;
        background: #fff;
        color: #344054;
        border-radius: 8px;
        padding: 7px 10px;
        font-size: 11px;
        font-weight: 700;
        cursor: pointer;
    }

    .btn:hover { background: #f9fafb; }

    .btn-primary {
        background: var(--blue);
        color: #fff;
        border-color: var(--blue);
    }

    .btn-primary:hover { background: #1d4ed8; }

    .download-top {
        display: inline-flex;
        align-items: center;
        gap: 7px;
    }

    .report-view {
        margin-top: 18px;
    }

    footer {
        color: var(--muted);
        text-align: center;
        padding: 30px 0 0;
        font-size: 11px;
    }

    @media (max-width: 1200px) {
        .cards { grid-template-columns: repeat(3, 1fr); }
        .grid-2 { grid-template-columns: 1fr; }
    }

    @media (max-width: 800px) {
        .sidebar {
            position: static;
            width: 100%;
            min-height: auto;
        }

        .layout { display: block; }
        .main { margin-left: 0; width: 100%; }
        .nav { grid-template-columns: repeat(3, 1fr); }
        .content { padding: 20px 15px 35px; }
        .topbar { padding: 15px; }
        .cards { grid-template-columns: repeat(2, 1fr); }
    }

    @media (max-width: 520px) {
        .cards { grid-template-columns: 1fr; }
        .nav { grid-template-columns: 1fr 1fr; }
        .topbar { align-items: flex-start; }
    }
</style>
</head>

<body>
<div class="layout">

    <aside class="sidebar">
        <div class="brand">
            <div class="brand-logo">C</div>
            <div>
                <strong>CloudMart</strong>
                <span>Operations Console</span>
            </div>
        </div>

        <nav class="nav">
            <a class="active" href="#dashboard">
                <span class="nav-icon">▦</span> Dashboard
            </a>
            <a href="#products">
                <span class="nav-icon">□</span> Products
            </a>
            <a href="#sales">
                <span class="nav-icon">↗</span> Sales
            </a>
            <a href="#orders">
                <span class="nav-icon">≡</span> Orders
            </a>
            <a href="#reports">
                <span class="nav-icon">▤</span> Reports
            </a>
        </nav>
    </aside>

    <div class="main">
        <header class="topbar" id="dashboard">
            <div>
                <h1>CloudMart Operations Dashboard</h1>
                <p>Products, stock, orders, sales and generated reports</p>
            </div>
            <div class="env">● DEV</div>
        </header>

        <main class="content">

            {% if db_error %}
            <div class="error">
                Database unavailable: {{ db_error }}
            </div>
            {% else %}
            <div class="notice">
                ✓ Dashboard connected to CloudMart RDS successfully.
            </div>
            {% endif %}

            <div class="cards">
                <div class="card">
                    <div class="card-label">Active Products</div>
                    <div class="card-value">{{ db.summary.active_products }}</div>
                    <div class="card-sub">Products with ACTIVE status</div>
                </div>

                <div class="card">
                    <div class="card-label">Total Stock</div>
                    <div class="card-value">{{ db.summary.total_stock }}</div>
                    <div class="card-sub">Current stock_count</div>
                </div>

                <div class="card">
                    <div class="card-label">Low Stock</div>
                    <div class="card-value">{{ db.summary.low_stock_products }}</div>
                    <div class="card-sub">Stock ≤ 5</div>
                </div>

                <div class="card">
                    <div class="card-label">Orders — 7 Days</div>
                    <div class="card-value">{{ db.summary.orders_7d }}</div>
                    <div class="card-sub">Confirmed orders</div>
                </div>

                <div class="card">
                    <div class="card-label">Sales — 7 Days</div>
                    <div class="card-value">₹{{ "%.2f"|format(db.summary.sales_7d|float) }}</div>
                    <div class="card-sub">Confirmed order value</div>
                </div>

                <div class="card">
                    <div class="card-label">Audit Events</div>
                    <div class="card-value">{{ db.summary.audit_events }}</div>
                    <div class="card-sub">audit_logs rows</div>
                </div>
            </div>

            <div class="section-title" id="sales">
                <div>
                    <h2>Sales Overview</h2>
                    <p>Top 5 products sold in the last 7 days from CONFIRMED orders</p>
                </div>
            </div>

            <div class="grid-2">
                <section class="panel">
                    <div class="panel-head">
                        <div>
                            <h3>Top 5 Sold Products</h3>
                            <span>Units sold • Last 7 days</span>
                        </div>
                    </div>

                    {% if db.top_products %}
                    <div class="top-products">
                        {% set max_units = db.top_products[0].units_sold|int %}
                        {% for product in db.top_products %}
                        {% set width = ((product.units_sold|int / max_units) * 100) if max_units else 0 %}
                        <div class="rank-row">
                            <div class="rank">{{ loop.index }}</div>
                            <div class="rank-info">
                                <div class="rank-name">{{ product.name }}</div>
                                <div class="bar-bg">
                                    <div class="bar" style="width: {{ width }}%"></div>
                                </div>
                            </div>
                            <div class="units">{{ product.units_sold }}</div>
                        </div>
                        {% endfor %}
                    </div>
                    {% else %}
                    <div class="empty">No confirmed product sales were found in the last 7 days.</div>
                    {% endif %}
                </section>

                <section class="panel">
                    <div class="panel-head">
                        <div>
                            <h3>Recent Orders</h3>
                            <span>Latest 10 orders</span>
                        </div>
                    </div>

                    {% if db.recent_orders %}
                    <div class="table-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>Order</th>
                                    <th>Customer</th>
                                    <th>Status</th>
                                    <th>Total</th>
                                </tr>
                            </thead>
                            <tbody>
                            {% for order in db.recent_orders %}
                                <tr>
                                    <td class="product-name">{{ order.order_id }}</td>
                                    <td>{{ order.customer_name }}</td>
                                    <td>
                                        {% if order.status == 'CONFIRMED' %}
                                        <span class="badge badge-green">CONFIRMED</span>
                                        {% elif order.status == 'FAILED' %}
                                        <span class="badge badge-red">FAILED</span>
                                        {% else %}
                                        <span class="badge badge-orange">{{ order.status }}</span>
                                        {% endif %}
                                    </td>
                                    <td class="money">₹{{ "%.2f"|format(order.total_amount|float) }}</td>
                                </tr>
                            {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% else %}
                    <div class="empty">No orders found.</div>
                    {% endif %}
                </section>
            </div>

            <div class="section-title" id="products">
                <div>
                    <h2>Products</h2>
                    <p>Live product data from the CloudMart products table</p>
                </div>
            </div>

            <section class="panel">
                <div class="panel-head">
                    <div>
                        <h3>Product & Stock List</h3>
                        <span>Latest 12 products</span>
                    </div>
                </div>

                {% if db.products %}
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Product</th>
                                <th>Description</th>
                                <th>Price</th>
                                <th>Stock</th>
                                <th>Status</th>
                                <th>Created</th>
                            </tr>
                        </thead>
                        <tbody>
                        {% for product in db.products %}
                            <tr>
                                <td>{{ product.product_id }}</td>
                                <td class="product-name">{{ product.name }}</td>
                                <td class="description">{{ product.description or "—" }}</td>
                                <td class="money">₹{{ "%.2f"|format(product.price|float) }}</td>
                                <td class="{% if product.stock_count|int <= 5 %}stock-low{% endif %}">
                                    {{ product.stock_count }}
                                </td>
                                <td>
                                    {% if product.status == 'ACTIVE' %}
                                    <span class="badge badge-green">ACTIVE</span>
                                    {% else %}
                                    <span class="badge badge-orange">{{ product.status }}</span>
                                    {% endif %}
                                </td>
                                <td>{{ product.created_at }}</td>
                            </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="empty">No products found.</div>
                {% endif %}
            </section>

            <div class="section-title" id="orders">
                <div>
                    <h2>Orders</h2>
                    <p>Recent orders from the orders table</p>
                </div>
            </div>

            <section class="panel">
                <div class="panel-head">
                    <div>
                        <h3>Order History</h3>
                        <span>Latest 10 orders</span>
                    </div>
                </div>

                {% if db.recent_orders %}
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Order ID</th>
                                <th>Customer ID</th>
                                <th>Customer</th>
                                <th>Status</th>
                                <th>Total Amount</th>
                                <th>Created</th>
                                <th>Updated</th>
                            </tr>
                        </thead>
                        <tbody>
                        {% for order in db.recent_orders %}
                            <tr>
                                <td class="product-name">{{ order.order_id }}</td>
                                <td>{{ order.customer_id }}</td>
                                <td>{{ order.customer_name }}</td>
                                <td>
                                    {% if order.status == 'CONFIRMED' %}
                                    <span class="badge badge-green">CONFIRMED</span>
                                    {% elif order.status == 'FAILED' %}
                                    <span class="badge badge-red">FAILED</span>
                                    {% else %}
                                    <span class="badge badge-orange">{{ order.status }}</span>
                                    {% endif %}
                                </td>
                                <td class="money">₹{{ "%.2f"|format(order.total_amount|float) }}</td>
                                <td>{{ order.created_at }}</td>
                                <td>{{ order.updated_at }}</td>
                            </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="empty">No orders found.</div>
                {% endif %}
            </section>

            <div class="section-title" id="reports">
                <div>
                    <h2>Reports</h2>
                    <p>CSV reports stored in the CloudMart S3 reports bucket</p>
                </div>
            </div>

            <section class="panel">
                <div class="panel-head">
                    <div>
                        <h3>Generated Reports</h3>
                        <span>View or download without exposing the S3 bucket publicly</span>
                    </div>
                </div>

                {% if report_error %}
                <div class="error" style="margin:15px;">
                    Unable to read reports from S3: {{ report_error }}
                </div>
                {% elif reports %}
                <div class="report-list">
                    {% for report in reports %}
                    <div class="report-row">
                        <div>
                            <div class="report-name">{{ report.key }}</div>
                            <div class="report-meta">
                                {{ report.size }} bytes • {{ report.last_modified }}
                            </div>
                        </div>
                        <div class="actions">
                            <a class="btn" href="/?report={{ report.key|urlencode }}#reports">
                                View
                            </a>
                            <a class="btn btn-primary download-top"
                               href="{{ url_for('download_report', key=report.key) }}">
                                ↓ Download CSV
                            </a>
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <div class="empty">
                    No CSV reports are currently available.
                </div>
                {% endif %}
            </section>

            {% if selected_key %}
            <section class="panel report-view">
                <div class="panel-head">
                    <div>
                        <h3>Report Preview</h3>
                        <span>{{ selected_key }}</span>
                    </div>
                    <a class="btn btn-primary"
                       href="{{ url_for('download_report', key=selected_key) }}">
                        ↓ Download CSV
                    </a>
                </div>

                {% if selected_error %}
                <div class="error" style="margin:15px;">
                    Unable to read this report: {{ selected_error }}
                </div>
                {% elif selected_columns %}
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                            {% for column in selected_columns %}
                                <th>{{ column }}</th>
                            {% endfor %}
                            </tr>
                        </thead>
                        <tbody>
                        {% for row in selected_rows %}
                            <tr>
                            {% for column in selected_columns %}
                                <td>{{ row.get(column, "") }}</td>
                            {% endfor %}
                            </tr>
                        {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="empty">The selected report is empty.</div>
                {% endif %}
            </section>
            {% endif %}

            <footer>
                CloudMart • Generated {{ generated_at }}
            </footer>

        </main>
    </div>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
