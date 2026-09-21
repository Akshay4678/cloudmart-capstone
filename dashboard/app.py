import csv
import io
import os
from datetime import datetime, timedelta
import hashlib
import secrets
from zoneinfo import ZoneInfo

import boto3
import pymysql
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, Response, jsonify, redirect, render_template_string, request, session, url_for

app = Flask(__name__)

# ============================================================
# ADMIN AUTHENTICATION
# ============================================================

# Flask uses this key to sign the admin session cookie.
# A persistent FLASK_SECRET_KEY should be supplied by the EC2
# environment later through CloudFormation/IaC.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)
app.config["SESSION_REFRESH_EACH_REQUEST"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# The current dashboard is served over HTTP. Set this to True
# when the dashboard is moved to HTTPS.
app.config["SESSION_COOKIE_SECURE"] = False

# ============================================================
# CONFIGURATION
# ============================================================

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
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
cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
rds = boto3.client("rds", region_name=AWS_REGION)


# ============================================================
# DATABASE
# ============================================================

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
        read_timeout=15,
        write_timeout=15,
        autocommit=True,
    )


def query_db(sql, params=None):
    connection = None
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        if connection:
            connection.close()


# ============================================================
# ADMIN AUTHENTICATION
# Uses the existing customers table in RDS.
# No new database table is required.
# ============================================================

def hash_admin_token(token):
    """Create the same SHA-256 hash used by CloudMart auth."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_admin_credentials(admin_id, admin_token):
    """
    Verify an admin ID and token against the existing customers table.

    The dashboard only accepts a customer whose role is ADMIN.
    The database stores auth_token_hash, never the plain token.
    """
    admin_id = (admin_id or "").strip()
    admin_token = (admin_token or "").strip()

    if not admin_id or not admin_token:
        return None

    rows = query_db(
        """
        SELECT customer_id, name, role, auth_token_hash
        FROM customers
        WHERE customer_id = %s
          AND role = 'ADMIN'
        LIMIT 1
        """,
        (admin_id,),
    )

    if not rows:
        return None

    admin = rows[0]
    supplied_hash = hash_admin_token(admin_token)
    stored_hash = str(admin.get("auth_token_hash") or "")

    # compare_digest avoids a simple string comparison for the secret hash.
    if not secrets.compare_digest(supplied_hash, stored_hash):
        return None

    return admin


@app.before_request
def require_admin_login():
    """Protect every dashboard route except login/logout/health."""
    public_endpoints = {"login", "health", "static"}

    if request.endpoint in public_endpoints:
        return None

    if not session.get("admin_authenticated"):
        return redirect(url_for("login"))

    # The session is permanent and has a fixed 24-hour lifetime.
    # SESSION_REFRESH_EACH_REQUEST=False prevents activity from extending it.
    return None


# ============================================================
# ADMIN LOGIN
# ============================================================

LOGIN_BODY = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CloudMart · Admin Login</title>
<style>
:root {
    --bg: #f5f7fb;
    --surface: #ffffff;
    --text: #101828;
    --muted: #667085;
    --line: #d0d5dd;
    --blue: #2563eb;
    --red: #d92d20;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 20px;
    background: var(--bg);
    font-family: Inter, ui-sans-serif, system-ui, -apple-system,
                 BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: var(--text);
}
.login-card {
    width: min(420px, 100%);
    background: var(--surface);
    border: 1px solid #e4e7ec;
    border-radius: 16px;
    padding: 32px;
    box-shadow: 0 12px 40px rgba(16,24,40,.08);
}
.logo {
    width: 48px; height: 48px; border-radius: 12px;
    display: grid; place-items: center;
    background: #101828; color: #fff;
    font-weight: 900; font-size: 22px;
    margin-bottom: 18px;
}
h1 { margin: 0; font-size: 25px; }
p { color: var(--muted); font-size: 13px; margin: 7px 0 25px; }
label { display: block; margin: 15px 0 7px; font-size: 12px; font-weight: 750; }
input {
    width: 100%; padding: 11px 12px;
    border: 1px solid var(--line); border-radius: 9px;
    font: inherit; outline: none;
}
input:focus { border-color: var(--blue); }
button {
    width: 100%; margin-top: 22px; padding: 11px;
    border: 0; border-radius: 9px;
    background: var(--blue); color: #fff;
    font: inherit; font-weight: 750; cursor: pointer;
}
button:hover { background: #1d4ed8; }
.error {
    margin-bottom: 16px; padding: 11px 13px;
    border-radius: 9px; background: #fef3f2;
    border: 1px solid #fecdca; color: var(--red);
    font-size: 12px;
}
.note { margin-top: 18px; color: var(--muted); font-size: 11px; line-height: 1.5; }
</style>
</head>
<body>
<div class="login-card">
    <div class="logo">C</div>
    <h1>CloudMart Admin</h1>
    <p>Sign in to access the CloudMart Operations Console.</p>

    {% if error %}
        <div class="error">{{ error }}</div>
    {% endif %}

    <form method="post" action="{{ url_for('login') }}">
        <label for="admin_id">Admin ID</label>
        <input id="admin_id" name="admin_id" type="text"
               autocomplete="username" required autofocus
               value="{{ admin_id }}">

        <label for="admin_token">Admin Token</label>
        <input id="admin_token" name="admin_token" type="password"
               autocomplete="current-password" required>

        <button type="submit">Login</button>
    </form>

    <div class="note">
        Only accounts with the <strong>ADMIN</strong> role can access this dashboard.
        Your session expires automatically after 24 hours.
    </div>
</div>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_authenticated"):
        return redirect(url_for("dashboard"))

    error = None
    admin_id = ""

    if request.method == "POST":
        admin_id = request.form.get("admin_id", "").strip()
        admin_token = request.form.get("admin_token", "")

        try:
            admin = verify_admin_credentials(admin_id, admin_token)
        except Exception:
            # Do not expose database/SSM errors to the login page.
            admin = None
            error = "Unable to verify credentials. Please try again."

        if admin:
            session.clear()
            session.permanent = True
            session["admin_authenticated"] = True
            session["admin_id"] = admin["customer_id"]
            session["admin_role"] = str(admin["role"]).upper()
            session["admin_name"] = admin.get("name") or admin["customer_id"]
            return redirect(url_for("dashboard"))

        if error is None:
            error = "Invalid Admin ID or Admin Token."

    return render_template_string(
        LOGIN_BODY,
        error=error,
        admin_id=admin_id,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# DASHBOARD DATA
# Uses ONLY the actual CloudMart tables:
# products, customers, orders, order_items, audit_logs
# ============================================================

def fetch_dashboard_data():
    summary_rows = query_db(
        """
        SELECT
            COUNT(*) AS total_products,
            SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END)
                AS active_products,
            COALESCE(SUM(stock_count), 0) AS total_stock,
            SUM(
                CASE
                    WHEN status = 'ACTIVE' AND stock_count <= 5
                    THEN 1 ELSE 0
                END
            ) AS low_stock
        FROM products
        """
    )
    summary = summary_rows[0]

    customer_rows = query_db(
        "SELECT COUNT(*) AS total_customers FROM customers"
    )

    order_rows = query_db(
        """
        SELECT
            COUNT(*) AS total_orders,
            SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END)
                AS failed_orders,
            COALESCE(
                SUM(
                    CASE
                        WHEN status = 'CONFIRMED' THEN total_amount
                        ELSE 0
                    END
                ),
                0
            ) AS total_revenue
        FROM orders
        """
    )

    audit_rows = query_db(
        "SELECT COUNT(*) AS audit_events FROM audit_logs"
    )

    week_rows = query_db(
        """
        SELECT
            COUNT(*) AS orders_7d,
            COALESCE(SUM(total_amount), 0) AS sales_7d
        FROM orders
        WHERE status = 'CONFIRMED'
          AND created_at >= NOW() - INTERVAL 7 DAY
        """
    )

    # Top 5 products sold during the last 7 days.
    # order_items uses quantity + price in the actual schema.
    top_products = query_db(
        """
        SELECT
            p.product_id,
            p.name,
            COALESCE(SUM(oi.quantity), 0) AS units_sold,
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

    recent_orders = query_db(
        """
        SELECT
            o.order_id,
            o.customer_id,
            COALESCE(c.name, CONCAT('Customer #', o.customer_id))
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

    return {
        "summary": {
            "total_products": summary["total_products"] or 0,
            "active_products": summary["active_products"] or 0,
            "total_stock": summary["total_stock"] or 0,
            "low_stock": summary["low_stock"] or 0,
            "total_customers": customer_rows[0]["total_customers"] or 0,
            "total_orders": order_rows[0]["total_orders"] or 0,
            "failed_orders": order_rows[0]["failed_orders"] or 0,
            "total_revenue": order_rows[0]["total_revenue"] or 0,
            "orders_7d": week_rows[0]["orders_7d"] or 0,
            "sales_7d": week_rows[0]["sales_7d"] or 0,
            "audit_events": audit_rows[0]["audit_events"] or 0,
        },
        "top_products": top_products,
        "recent_orders": recent_orders,
    }


# ============================================================
# REPORTS
# ============================================================

def get_report_objects():
    if not REPORTS_BUCKET:
        return []

    reports = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=REPORTS_BUCKET,
        Prefix="reports/",
    ):
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

    reports.sort(
        key=lambda item: item["last_modified"],
        reverse=True,
    )
    return reports


def get_todays_report(reports):
    """Return today's daily_report CSV, or None if it has not been generated yet."""
    today = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
    expected_name = f"daily_report_{today}.csv"

    for report in reports:
        if report["key"].split("/")[-1] == expected_name:
            return report

    return None


def read_report_csv(key):
    if not key.startswith("reports/"):
        raise ValueError("Invalid report path")

    response = s3.get_object(
        Bucket=REPORTS_BUCKET,
        Key=key,
    )

    content = response["Body"].read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))

    return reader.fieldnames or [], list(reader)


# ============================================================
# AWS MONITORING
# ============================================================

LAMBDA_FUNCTIONS = [
    f"cloudmart-product-{ENVIRONMENT}",
    f"cloudmart-order-{ENVIRONMENT}",
    f"cloudmart-order-processor-{ENVIRONMENT}",
    f"cloudmart-report-{ENVIRONMENT}",
]

API_NAME = os.environ.get("API_NAME", f"cloudmart-api-{ENVIRONMENT}")
API_STAGE = os.environ.get("API_STAGE", ENVIRONMENT)


def get_metric_statistics(
    namespace,
    metric_name,
    dimensions,
    statistic,
    period=300,
    hours=1,
):
    end_time = datetime.now().astimezone()
    start_time = end_time.timestamp() - (hours * 3600)

    response = cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=datetime.fromtimestamp(start_time, tz=end_time.tzinfo),
        EndTime=end_time,
        Period=period,
        Statistics=[statistic],
    )

    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return None

    datapoints.sort(key=lambda item: item["Timestamp"], reverse=True)
    return datapoints[0].get(statistic)


def sum_metric_over_hour(namespace, metric_name, dimensions):
    end_time = datetime.now().astimezone()
    start_time = end_time.timestamp() - 3600

    response = cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=datetime.fromtimestamp(start_time, tz=end_time.tzinfo),
        EndTime=end_time,
        Period=300,
        Statistics=["Sum"],
    )

    return sum(point.get("Sum", 0) for point in response.get("Datapoints", []))


def get_rds_identifier():
    if not DB_HOST:
        raise RuntimeError("DB_HOST is not configured")

    response = rds.describe_db_instances()

    for instance in response.get("DBInstances", []):
        endpoint = (instance.get("Endpoint") or {}).get("Address")
        if endpoint == DB_HOST:
            return instance["DBInstanceIdentifier"]

    raise RuntimeError("Could not find RDS instance for configured DB_HOST")


def fetch_monitoring_data():
    lambda_rows = []

    for function_name in LAMBDA_FUNCTIONS:
        dimensions = [
            {"Name": "FunctionName", "Value": function_name}
        ]

        errors = sum_metric_over_hour(
            "AWS/Lambda", "Errors", dimensions
        )
        invocations = sum_metric_over_hour(
            "AWS/Lambda", "Invocations", dimensions
        )
        duration = get_metric_statistics(
            "AWS/Lambda",
            "Duration",
            dimensions,
            "Average",
            period=300,
            hours=1,
        )

        lambda_rows.append(
            {
                "name": function_name,
                "errors": int(errors),
                "invocations": int(invocations),
                "duration": round(duration, 2) if duration is not None else 0,
            }
        )

    db_identifier = get_rds_identifier()
    rds_dimensions = [
        {"Name": "DBInstanceIdentifier", "Value": db_identifier}
    ]

    rds_cpu = get_metric_statistics(
        "AWS/RDS", "CPUUtilization", rds_dimensions, "Average"
    )
    rds_connections = get_metric_statistics(
        "AWS/RDS", "DatabaseConnections", rds_dimensions, "Average"
    )
    rds_free_storage = get_metric_statistics(
        "AWS/RDS", "FreeStorageSpace", rds_dimensions, "Average"
    )

    api_dimensions = [
        {"Name": "ApiName", "Value": API_NAME},
        {"Name": "Stage", "Value": API_STAGE},
    ]

    api_requests = sum_metric_over_hour(
        "AWS/ApiGateway", "Count", api_dimensions
    )
    api_4xx = sum_metric_over_hour(
        "AWS/ApiGateway", "4XXError", api_dimensions
    )
    api_5xx = sum_metric_over_hour(
        "AWS/ApiGateway", "5XXError", api_dimensions
    )
    api_latency = get_metric_statistics(
        "AWS/ApiGateway", "Latency", api_dimensions, "Average"
    )

    return {
        "lambda_rows": lambda_rows,
        "lambda_errors": sum(row["errors"] for row in lambda_rows),
        "lambda_invocations": sum(row["invocations"] for row in lambda_rows),
        "lambda_duration": round(
            sum(row["duration"] for row in lambda_rows) / len(lambda_rows),
            2,
        ) if lambda_rows else 0,
        "rds_identifier": db_identifier,
        "rds_cpu": round(rds_cpu, 2) if rds_cpu is not None else 0,
        "rds_connections": round(rds_connections, 2) if rds_connections is not None else 0,
        "rds_free_storage_gb": round((rds_free_storage or 0) / (1024 ** 3), 2),
        "api_name": API_NAME,
        "api_stage": API_STAGE,
        "api_requests": int(api_requests),
        "api_4xx": int(api_4xx),
        "api_5xx": int(api_5xx),
        "api_latency": round(api_latency, 2) if api_latency is not None else 0,
    }


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():
    return jsonify(
        {
            "status": "healthy",
            "service": "cloudmart-dashboard",
        }
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
def dashboard():
    data = None
    db_error = None
    monitoring_data = None
    monitoring_error = None

    try:
        data = fetch_dashboard_data()
    except Exception as exc:
        db_error = str(exc)
        data = {
            "summary": {
                "total_products": "—",
                "active_products": "—",
                "total_stock": "—",
                "low_stock": "—",
                "total_customers": "—",
                "total_orders": "—",
                "failed_orders": "—",
                "total_revenue": "—",
                "orders_7d": "—",
                "sales_7d": "—",
                "audit_events": "—",
            },
            "top_products": [],
            "recent_orders": [],
        }

    try:
        monitoring_data = fetch_monitoring_data()
    except Exception as exc:
        monitoring_error = str(exc)
        monitoring_data = {
            "lambda_rows": [],
            "lambda_errors": "—",
            "lambda_invocations": "—",
            "lambda_duration": "—",
            "rds_identifier": "—",
            "rds_cpu": "—",
            "rds_connections": "—",
            "rds_free_storage_gb": "—",
            "api_name": API_NAME,
            "api_stage": API_STAGE,
            "api_requests": "—",
            "api_4xx": "—",
            "api_5xx": "—",
            "api_latency": "—",
        }

    return render_template_string(
        PAGE_HTML,
        page_title="Dashboard",
        page_subtitle="CloudMart operations overview",
        active="dashboard",
        content=render_template_string(
            DASHBOARD_BODY,
            db=data,
            db_error=db_error,
            monitoring=monitoring_data,
            monitoring_error=monitoring_error,
        ),
        generated_at=datetime.now().strftime(
            "%d %b %Y, %I:%M %p"
        ),
    )


# ============================================================
# MONITORING
# ============================================================

@app.route("/monitoring")
def monitoring():
    monitoring_data = None
    monitoring_error = None

    try:
        monitoring_data = fetch_monitoring_data()
    except Exception as exc:
        monitoring_error = str(exc)
        monitoring_data = {
            "lambda_rows": [],
            "lambda_errors": "—",
            "lambda_invocations": "—",
            "lambda_duration": "—",
            "rds_identifier": "—",
            "rds_cpu": "—",
            "rds_connections": "—",
            "rds_free_storage_gb": "—",
            "api_name": API_NAME,
            "api_stage": API_STAGE,
            "api_requests": "—",
            "api_4xx": "—",
            "api_5xx": "—",
            "api_latency": "—",
        }

    try:
        db_data = fetch_dashboard_data()
        orders_count = db_data["summary"]["total_orders"]
        inventory_count = db_data["summary"]["total_stock"]
    except Exception:
        orders_count = "—"
        inventory_count = "—"

    body = render_template_string(
        MONITORING_BODY,
        monitoring=monitoring_data,
        monitoring_error=monitoring_error,
        orders_count=orders_count,
        inventory_count=inventory_count,
    )

    return render_template_string(
        PAGE_HTML,
        page_title="Monitoring",
        page_subtitle="CloudMart AWS and application health metrics",
        active="monitoring",
        content=body,
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


# ============================================================
# PRODUCTS
# ============================================================

@app.route("/products")
def products():
    search = request.args.get("search", "").strip()

    if search:
        rows = query_db(
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
            WHERE name LIKE %s
               OR description LIKE %s
               OR CAST(product_id AS CHAR) LIKE %s
            ORDER BY created_at DESC
            """,
            (
                f"%{search}%",
                f"%{search}%",
                f"%{search}%",
            ),
        )
    else:
        rows = query_db(
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
            """
        )

    body = render_template_string(
        PRODUCTS_BODY,
        products=rows,
        search=search,
    )

    return render_template_string(
        PAGE_HTML,
        page_title="Products",
        page_subtitle="Products, prices and current stock",
        active="products",
        content=body,
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


# ============================================================
# CUSTOMERS
# ============================================================

@app.route("/customers")
def customers():
    rows = query_db(
        """
        SELECT
            c.customer_id,
            c.name,
            c.email,
            c.phone,
            c.role,
            c.created_at,
            COUNT(o.order_id) AS total_orders,
            COALESCE(
                SUM(
                    CASE
                        WHEN o.status = 'CONFIRMED'
                        THEN o.total_amount
                        ELSE 0
                    END
                ),
                0
            ) AS total_spent
        FROM customers c
        LEFT JOIN orders o
            ON c.customer_id = o.customer_id
        GROUP BY
            c.customer_id,
            c.name,
            c.email,
            c.phone,
            c.role,
            c.created_at
        ORDER BY c.created_at DESC
        """
    )

    return render_template_string(
        PAGE_HTML,
        page_title="Customers",
        page_subtitle="Customer accounts and purchase summary",
        active="customers",
        content=render_template_string(
            CUSTOMERS_BODY,
            customers=rows,
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


@app.route("/customers/<customer_id>")
def customer_details(customer_id):
    customer_rows = query_db(
        """
        SELECT
            customer_id,
            name,
            email,
            phone,
            role,
            created_at
        FROM customers
        WHERE customer_id = %s
        """,
        (customer_id,),
    )

    if not customer_rows:
        return render_template_string(
            PAGE_HTML,
            page_title="Customer",
            page_subtitle="Customer details",
            active="customers",
            content=render_template_string(
                ERROR_BODY,
                error="Customer not found",
            ),
            generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
        ), 404

    orders_rows = query_db(
        """
        SELECT
            order_id,
            status,
            total_amount,
            created_at,
            updated_at
        FROM orders
        WHERE customer_id = %s
        ORDER BY created_at DESC
        """,
        (customer_id,),
    )

    return render_template_string(
        PAGE_HTML,
        page_title=customer_rows[0]["name"],
        page_subtitle="Customer details and order history",
        active="customers",
        content=render_template_string(
            CUSTOMER_DETAIL_BODY,
            customer=customer_rows[0],
            orders=orders_rows,
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


# ============================================================
# ORDERS
# ============================================================

@app.route("/orders")
def orders():
    rows = query_db(
        """
        SELECT
            o.order_id,
            o.customer_id,
            COALESCE(c.name, CONCAT('Customer #', o.customer_id))
                AS customer_name,
            COALESCE(c.email, '—') AS customer_email,
            o.status,
            o.total_amount,
            o.created_at,
            o.updated_at
        FROM orders o
        LEFT JOIN customers c
            ON o.customer_id = c.customer_id
        ORDER BY o.created_at DESC
        """
    )

    return render_template_string(
        PAGE_HTML,
        page_title="Orders",
        page_subtitle="All CloudMart orders and their current status",
        active="orders",
        content=render_template_string(
            ORDERS_BODY,
            orders=rows,
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


@app.route("/orders/<path:order_id>")
def order_details(order_id):
    order_rows = query_db(
        """
        SELECT
            o.order_id,
            o.customer_id,
            COALESCE(c.name, CONCAT('Customer #', o.customer_id))
                AS customer_name,
            COALESCE(c.email, '—') AS customer_email,
            o.status,
            o.total_amount,
            o.created_at,
            o.updated_at
        FROM orders o
        LEFT JOIN customers c
            ON o.customer_id = c.customer_id
        WHERE o.order_id = %s
        """,
        (order_id,),
    )

    if not order_rows:
        return render_template_string(
            PAGE_HTML,
            page_title="Order",
            page_subtitle="Order details",
            active="orders",
            content=render_template_string(
                ERROR_BODY,
                error="Order not found",
            ),
            generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
        ), 404

    items = query_db(
        """
        SELECT
            oi.product_id,
            p.name AS product_name,
            oi.quantity,
            oi.price AS unit_price,
            (oi.quantity * oi.price) AS subtotal
        FROM order_items oi
        INNER JOIN products p
            ON oi.product_id = p.product_id
        WHERE oi.order_id = %s
        ORDER BY oi.created_at
        """,
        (order_id,),
    )

    return render_template_string(
        PAGE_HTML,
        page_title=f"Order {order_id}",
        page_subtitle="Order details and purchased products",
        active="orders",
        content=render_template_string(
            ORDER_DETAIL_BODY,
            order=order_rows[0],
            items=items,
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


# ============================================================
# ORDER ITEMS
# ============================================================

@app.route("/order-items")
def order_items():
    rows = query_db(
        """
        SELECT
            oi.order_id,
            oi.product_id,
            p.name AS product_name,
            oi.quantity,
            oi.price AS unit_price,
            (oi.quantity * oi.price) AS subtotal,
            oi.created_at
        FROM order_items oi
        INNER JOIN products p
            ON oi.product_id = p.product_id
        ORDER BY oi.created_at DESC
        """
    )

    return render_template_string(
        PAGE_HTML,
        page_title="Order Items",
        page_subtitle="Products included in CloudMart orders",
        active="order-items",
        content=render_template_string(
            ORDER_ITEMS_BODY,
            items=rows,
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


# ============================================================
# AUDIT LOGS
# ============================================================

@app.route("/audit-logs")
def audit_logs():
    rows = query_db(
        """
        SELECT
            log_id,
            entity_type,
            entity_id,
            action,
            old_value,
            new_value,
            performed_by,
            created_at
        FROM audit_logs
        ORDER BY created_at DESC
        LIMIT 200
        """
    )

    return render_template_string(
        PAGE_HTML,
        page_title="Audit Logs",
        page_subtitle="Recent application activity recorded in audit_logs",
        active="audit",
        content=render_template_string(
            AUDIT_BODY,
            logs=rows,
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


# ============================================================
# REPORTS
# ============================================================

# Direct URL: http://<EC2-public-ip>:5000/reports
# Do not use /#reports; URL fragments are browser-only and are not sent to Flask.
@app.route("/reports")
def reports():
    report_rows = []
    todays_report = None
    previous_reports = []
    report_error = None

    try:
        report_rows = get_report_objects()
        todays_report = get_todays_report(report_rows)

        if todays_report:
            previous_reports = [
                report for report in report_rows
                if report["key"] != todays_report["key"]
            ]
        else:
            previous_reports = report_rows

    except Exception as exc:
        report_error = str(exc)

    return render_template_string(
        PAGE_HTML,
        page_title="Reports",
        page_subtitle="Daily CSV reports stored privately in S3",
        active="reports",
        content=render_template_string(
            REPORTS_BODY,
            todays_report=todays_report,
            previous_reports=previous_reports,
            report_error=report_error,
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


@app.route("/reports/view")
def view_report():
    key = request.args.get("key", "").strip()

    if not key or not key.startswith("reports/"):
        return "Invalid report", 400

    try:
        columns, rows = read_report_csv(key)

        return render_template_string(
            PAGE_HTML,
            page_title="Report Preview",
            page_subtitle=key.split("/")[-1],
            active="reports",
            content=render_template_string(
                REPORT_VIEW_BODY,
                report_name=key.split("/")[-1],
                key=key,
                columns=columns,
                rows=rows,
            ),
            generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
        )
    except Exception as exc:
        return render_template_string(
            PAGE_HTML,
            page_title="Report Preview",
            page_subtitle="Unable to read report",
            active="reports",
            content=render_template_string(
                ERROR_BODY,
                error=str(exc),
            ),
            generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
        ), 500


@app.route("/reports/download")
def download_report():
    key = request.args.get("key", "").strip()

    if not key or not key.startswith("reports/"):
        return "Invalid report", 400

    filename = key.rsplit("/", 1)[-1] or "cloudmart-report.csv"

    try:
        # The S3 bucket remains private. The dashboard creates a
        # short-lived signed URL only when the user clicks Download.
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": REPORTS_BUCKET,
                "Key": key,
                "ResponseContentType": "text/csv",
                "ResponseContentDisposition": (
                    f'attachment; filename="{filename}"'
                ),
            },
            ExpiresIn=300,
        )
        return redirect(url)
    except (BotoCoreError, ClientError) as exc:
        return f"Unable to create download link: {exc}", 500


# ============================================================
# SHARED PAGE
# ============================================================

PAGE_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CloudMart · {{ page_title }}</title>

<style>
:root {
    --bg: #f5f7fb;
    --surface: #ffffff;
    --surface-soft: #f8fafc;
    --text: #101828;
    --muted: #667085;
    --line: #e4e7ec;
    --nav: #101828;
    --nav2: #1d2939;
    --blue: #2563eb;
    --blue-soft: #eff6ff;
    --green: #039855;
    --green-soft: #ecfdf3;
    --orange: #d97706;
    --orange-soft: #fffaeb;
    --red: #d92d20;
    --red-soft: #fef3f2;
    --shadow: 0 8px 30px rgba(16,24,40,.06);
    --radius: 14px;
}

* { box-sizing: border-box; }

html { scroll-behavior: smooth; }

body {
    margin: 0;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system,
                 BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg);
    color: var(--text);
}

a { color: inherit; text-decoration: none; }

.layout {
    min-height: 100vh;
    display: flex;
}

.sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    width: 245px;
    padding: 22px 14px;
    background: var(--nav);
    color: #d0d5dd;
    z-index: 20;
}

.brand {
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 5px 12px 25px;
    color: #fff;
}

.logo {
    width: 40px;
    height: 40px;
    border-radius: 11px;
    background: #fff;
    color: #101828;
    display: grid;
    place-items: center;
    font-weight: 900;
    font-size: 19px;
}

.brand strong {
    display: block;
    font-size: 16px;
}

.brand span {
    display: block;
    margin-top: 2px;
    color: #98a2b3;
    font-size: 11px;
}

.nav {
    display: grid;
    gap: 5px;
}

.nav a {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 12px;
    border-radius: 9px;
    font-size: 13px;
}

.nav a:hover,
.nav a.active {
    background: var(--nav2);
    color: #fff;
}

.icon {
    width: 20px;
    text-align: center;
    opacity: .9;
}

.main {
    margin-left: 245px;
    width: calc(100% - 245px);
    min-width: 0;
}

.topbar {
    position: sticky;
    top: 0;
    z-index: 10;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
    padding: 18px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
}

.topbar h1 {
    margin: 0;
    font-size: 23px;
    letter-spacing: -.025em;
}

.topbar p {
    margin: 5px 0 0;
    color: var(--muted);
    font-size: 13px;
}

.env {
    background: var(--green-soft);
    border: 1px solid #abefc6;
    color: var(--green);
    border-radius: 999px;
    padding: 7px 12px;
    font-size: 12px;
    font-weight: 800;
}

.content {
    max-width: 1500px;
    margin: 0 auto;
    padding: 28px 32px 50px;
}

.notice,
.error {
    border-radius: 11px;
    padding: 13px 16px;
    margin-bottom: 20px;
    font-size: 13px;
}

.notice {
    background: var(--green-soft);
    color: #027a48;
    border: 1px solid #abefc6;
}

.error {
    background: var(--red-soft);
    color: var(--red);
    border: 1px solid #fecdca;
}

.cards {
    display: grid;
    grid-template-columns: repeat(6, minmax(140px, 1fr));
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
    font-weight: 650;
}

.card-value {
    margin-top: 9px;
    font-size: 25px;
    font-weight: 850;
    letter-spacing: -.03em;
}

.card-sub {
    margin-top: 5px;
    color: var(--muted);
    font-size: 11px;
}

.section-title {
    display: flex;
    align-items: end;
    justify-content: space-between;
    gap: 15px;
    margin: 31px 0 13px;
}

.section-title h2 {
    margin: 0;
    font-size: 18px;
}

.section-title p {
    margin: 4px 0 0;
    color: var(--muted);
    font-size: 12px;
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
    overflow: hidden;
    box-shadow: var(--shadow);
}

.panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 17px 18px;
    border-bottom: 1px solid var(--line);
}

.panel-head h3 {
    margin: 0;
    font-size: 15px;
}

.panel-head span {
    display: block;
    margin-top: 3px;
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

th,
td {
    padding: 12px 14px;
    border-bottom: 1px solid #eef0f3;
    text-align: left;
    white-space: nowrap;
}

th {
    background: var(--surface-soft);
    color: #475467;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: .045em;
}

tbody tr:hover {
    background: #fafcff;
}

.product-name {
    color: #1d2939;
    font-weight: 750;
}

.description {
    max-width: 270px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--muted);
}

.money {
    font-weight: 750;
}

.stock-low {
    color: var(--red);
    font-weight: 850;
}

.badge {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    padding: 4px 8px;
    font-size: 10px;
    font-weight: 850;
}

.badge-green {
    color: #027a48;
    background: var(--green-soft);
}

.badge-orange {
    color: #b54708;
    background: var(--orange-soft);
}

.badge-red {
    color: #b42318;
    background: var(--red-soft);
}

.badge-blue {
    color: #175cd3;
    background: var(--blue-soft);
}

.top-products {
    padding: 18px;
}

.rank-row {
    display: grid;
    grid-template-columns: 30px minmax(0,1fr) 65px;
    gap: 10px;
    align-items: center;
    margin-bottom: 19px;
}

.rank {
    width: 28px;
    height: 28px;
    display: grid;
    place-items: center;
    border-radius: 8px;
    background: var(--blue-soft);
    color: var(--blue);
    font-size: 12px;
    font-weight: 850;
}

.rank-name {
    margin-bottom: 6px;
    font-size: 12px;
    font-weight: 750;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.bar-bg {
    height: 7px;
    overflow: hidden;
    border-radius: 999px;
    background: #edf2f7;
}

.bar {
    height: 100%;
    border-radius: 999px;
    background: var(--blue);
}

.units {
    text-align: right;
    font-size: 12px;
    font-weight: 850;
}

.empty {
    padding: 30px 18px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
}

.actions {
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
}

.btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    border: 1px solid #d0d5dd;
    border-radius: 8px;
    background: #fff;
    color: #344054;
    padding: 8px 11px;
    font-size: 11px;
    font-weight: 750;
}

.btn:hover {
    background: #f9fafb;
}

.btn-primary {
    background: var(--blue);
    border-color: var(--blue);
    color: #fff;
}

.btn-primary:hover {
    background: #1d4ed8;
}

.btn-green {
    background: #039855;
    border-color: #039855;
    color: #fff;
}

.btn-green:hover {
    background: #027a48;
}

.search {
    display: flex;
    gap: 8px;
    margin-bottom: 15px;
}

.search input {
    width: min(430px, 100%);
    border: 1px solid #d0d5dd;
    border-radius: 9px;
    padding: 10px 12px;
    outline: none;
    font: inherit;
}

.search input:focus {
    border-color: var(--blue);
}

.report-list {
    display: grid;
    gap: 10px;
    padding: 15px;
}

.report-highlight {
    display: flex;
    align-items: center;
    gap: 18px;
    padding: 20px;
    border: 1px solid #dbe5f5;
    border-radius: 12px;
    background: #f8fbff;
}

.report-icon {
    width: 48px;
    height: 48px;
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #eef4ff;
    color: #2563eb;
    font-size: 22px;
    flex: 0 0 48px;
}

.report-highlight-info {
    flex: 1;
    min-width: 0;
}

@media (max-width: 760px) {
    .report-highlight {
        align-items: flex-start;
        flex-direction: column;
    }
}

.report-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    border: 1px solid var(--line);
    border-radius: 11px;
    padding: 13px;
}

.report-name {
    font-size: 12px;
    font-weight: 750;
    word-break: break-all;
}

.report-meta {
    margin-top: 4px;
    color: var(--muted);
    font-size: 10px;
}

.detail-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    padding: 18px;
}

.detail-card {
    background: var(--surface-soft);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 13px;
}

.detail-label {
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: .04em;
}

.detail-value {
    margin-top: 6px;
    font-weight: 750;
    font-size: 13px;
    word-break: break-word;
}

footer {
    padding: 32px 0 0;
    text-align: center;
    color: var(--muted);
    font-size: 11px;
}

@media (max-width: 1250px) {
    .cards { grid-template-columns: repeat(3, 1fr); }
    .grid-2 { grid-template-columns: 1fr; }
    .detail-grid { grid-template-columns: repeat(2, 1fr); }
}

@media (max-width: 800px) {
    .sidebar {
        position: static;
        width: 100%;
        min-height: auto;
    }

    .layout { display: block; }

    .main {
        width: 100%;
        margin-left: 0;
    }

    .nav {
        grid-template-columns: repeat(3, 1fr);
    }

    .content { padding: 20px 15px 35px; }

    .topbar {
        position: static;
        padding: 15px;
    }
}

@media (max-width: 520px) {
    .cards { grid-template-columns: 1fr; }
    .nav { grid-template-columns: 1fr 1fr; }
    .detail-grid { grid-template-columns: 1fr; }
    .report-row { align-items: flex-start; flex-direction: column; }
}
</style>
</head>

<body>
<div class="layout">

<aside class="sidebar">
    <div class="brand">
        <div class="logo">C</div>
        <div>
            <strong>CloudMart</strong>
            <span>Operations Console</span>
        </div>
    </div>

    <nav class="nav">
        <a class="{% if active == 'dashboard' %}active{% endif %}" href="{{ url_for('dashboard') }}">
            <span class="icon">▦</span> Dashboard
        </a>

        <a class="{% if active == 'products' %}active{% endif %}" href="{{ url_for('products') }}">
            <span class="icon">□</span> Products
        </a>

        <a class="{% if active == 'customers' %}active{% endif %}" href="{{ url_for('customers') }}">
            <span class="icon">♙</span> Customers
        </a>

        <a class="{% if active == 'orders' %}active{% endif %}" href="{{ url_for('orders') }}">
            <span class="icon">≡</span> Orders
        </a>

        <a class="{% if active == 'order-items' %}active{% endif %}" href="{{ url_for('order_items') }}">
            <span class="icon">⊞</span> Order Items
        </a>

        <a class="{% if active == 'audit' %}active{% endif %}" href="{{ url_for('audit_logs') }}">
            <span class="icon">◷</span> Audit Logs
        </a>

        <a class="{% if active == 'monitoring' %}active{% endif %}" href="{{ url_for('monitoring') }}">
            <span class="icon">◉</span> Monitoring
        </a>

        <a class="{% if active == 'reports' %}active{% endif %}" href="{{ url_for('reports') }}">
            <span class="icon">▤</span> Reports
        </a>
    </nav>
</aside>

<div class="main">

<header class="topbar">
    <div>
        <h1>CloudMart · {{ page_title }}</h1>
        <p>{{ page_subtitle }}</p>
    </div>
    <div style="display:flex;align-items:center;gap:10px;">
        <div class="env">● DEV</div>
        {% if session.get("admin_authenticated") %}
            <span style="font-size:12px;color:#667085;">{{ session.get("admin_id") }}</span>
            <a class="btn" href="{{ url_for('logout') }}">Logout</a>
        {% endif %}
    </div>
</header>

<main class="content" id="page-content">
    {{ content | safe }}

    <footer>
        CloudMart · Generated {{ generated_at }}
    </footer>
</main>

</div>
</div>
</body>
</html>
"""


# ============================================================
# DASHBOARD BODY
# ============================================================

DASHBOARD_BODY = r"""
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
        <div class="card-sub">Products currently ACTIVE</div>
    </div>

    <div class="card">
        <div class="card-label">Total Stock</div>
        <div class="card-value">{{ db.summary.total_stock }}</div>
        <div class="card-sub">Current stock_count</div>
    </div>

    <div class="card">
        <div class="card-label">Low Stock</div>
        <div class="card-value">{{ db.summary.low_stock }}</div>
        <div class="card-sub">ACTIVE stock ≤ 5</div>
    </div>

    <div class="card">
        <div class="card-label">Customers</div>
        <div class="card-value">{{ db.summary.total_customers }}</div>
        <div class="card-sub">Customer accounts</div>
    </div>

    <div class="card">
        <div class="card-label">Orders</div>
        <div class="card-value">{{ db.summary.total_orders }}</div>
        <div class="card-sub">{{ db.summary.failed_orders }} failed</div>
    </div>

    <div class="card">
        <div class="card-label">Total Revenue</div>
        <div class="card-value">₹{{ "%.2f"|format(db.summary.total_revenue|float) }}</div>
        <div class="card-sub">Confirmed orders</div>
    </div>

</div>

<div class="section-title">
    <div>
        <h2>Sales Overview</h2>
        <p>Top 5 products sold during the last 7 days from CONFIRMED orders</p>
    </div>
</div>

<div class="grid-2">

<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Top 5 Sold Products</h3>
            <span>Units sold · Last 7 days</span>
        </div>
        <span class="badge badge-blue">{{ db.summary.orders_7d }} orders</span>
    </div>

    {% if db.top_products %}
    <div class="top-products">

        {% set max_units = db.top_products[0].units_sold|int %}

        {% for product in db.top_products %}
            {% set width = ((product.units_sold|int / max_units) * 100) if max_units else 0 %}

            <div class="rank-row">
                <div class="rank">{{ loop.index }}</div>

                <div>
                    <div class="rank-name">
                        {{ product.name }}
                    </div>

                    <div class="bar-bg">
                        <div class="bar" style="width: {{ width }}%"></div>
                    </div>
                </div>

                <div class="units">
                    {{ product.units_sold }} units
                </div>
            </div>
        {% endfor %}

    </div>
    {% else %}
        <div class="empty">
            No confirmed product sales were found during the last 7 days.
        </div>
    {% endif %}
</section>

<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Recent Orders</h3>
            <span>Latest 10 orders</span>
        </div>

        <a class="btn" href="{{ url_for('orders') }}">View all</a>
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
                    <td>
                        <a class="product-name"
                           href="{{ url_for('order_details', order_id=order.order_id) }}">
                            {{ order.order_id }}
                        </a>
                    </td>

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

                    <td class="money">
                        ₹{{ "%.2f"|format(order.total_amount|float) }}
                    </td>
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

<div class="section-title">
    <div>
        <h2>Weekly Performance</h2>
        <p>Confirmed order activity from the last 7 days</p>
    </div>
</div>

<div class="cards">
    <div class="card">
        <div class="card-label">Orders · 7 Days</div>
        <div class="card-value">{{ db.summary.orders_7d }}</div>
        <div class="card-sub">Confirmed orders</div>
    </div>

    <div class="card">
        <div class="card-label">Sales · 7 Days</div>
        <div class="card-value">₹{{ "%.2f"|format(db.summary.sales_7d|float) }}</div>
        <div class="card-sub">Confirmed order value</div>
    </div>

    <div class="card">
        <div class="card-label">Failed Orders</div>
        <div class="card-value">{{ db.summary.failed_orders }}</div>
        <div class="card-sub">All recorded failures</div>
    </div>

    <div class="card">
        <div class="card-label">Audit Events</div>
        <div class="card-value">{{ db.summary.audit_events }}</div>
        <div class="card-sub">audit_logs rows</div>
    </div>
</div>

<div class="section-title">
    <div>
        <h2>AWS Monitoring</h2>
        <p>CloudWatch health metrics for Lambda, RDS and API Gateway</p>
    </div>
    <a class="btn" href="{{ url_for('monitoring') }}">Detailed Monitoring</a>
</div>

{% if monitoring_error %}
<div class="error">
    CloudWatch monitoring unavailable: {{ monitoring_error }}
</div>
{% endif %}

<div class="cards">
    <div class="card">
        <div class="card-label">Lambda Errors</div>
        <div class="card-value">{{ monitoring.lambda_errors }}</div>
        <div class="card-sub">Last 1 hour · all CloudMart Lambdas</div>
    </div>

    <div class="card">
        <div class="card-label">Lambda Duration</div>
        <div class="card-value">{{ monitoring.lambda_duration }} ms</div>
        <div class="card-sub">Latest average across functions</div>
    </div>

    <div class="card">
        <div class="card-label">Lambda Invocations</div>
        <div class="card-value">{{ monitoring.lambda_invocations }}</div>
        <div class="card-sub">Last 1 hour</div>
    </div>

    <div class="card">
        <div class="card-label">RDS CPU</div>
        <div class="card-value">{{ monitoring.rds_cpu }}%</div>
        <div class="card-sub">Latest 5-minute average</div>
    </div>

    <div class="card">
        <div class="card-label">RDS Connections</div>
        <div class="card-value">{{ monitoring.rds_connections }}</div>
        <div class="card-sub">Latest 5-minute average</div>
    </div>

    <div class="card">
        <div class="card-label">RDS Free Storage</div>
        <div class="card-value">{{ monitoring.rds_free_storage_gb }} GB</div>
        <div class="card-sub">Latest 5-minute average</div>
    </div>

    <div class="card">
        <div class="card-label">API Requests</div>
        <div class="card-value">{{ monitoring.api_requests }}</div>
        <div class="card-sub">Last 1 hour</div>
    </div>

    <div class="card">
        <div class="card-label">API Errors</div>
        <div class="card-value">{{ monitoring.api_4xx + monitoring.api_5xx }}</div>
        <div class="card-sub">4XX: {{ monitoring.api_4xx }} · 5XX: {{ monitoring.api_5xx }}</div>
    </div>
</div>
"""


# ============================================================
# PRODUCTS BODY
# ============================================================

PRODUCTS_BODY = r"""
<div class="section-title" style="margin-top:0;">
    <div>
        <h2>Product Catalog</h2>
        <p>Live data from the CloudMart products table</p>
    </div>
</div>

<form class="search" method="get" action="{{ url_for('products') }}">
    <input
        name="search"
        value="{{ search }}"
        placeholder="Search by product name, description or ID..."
    >
    <button class="btn btn-primary" type="submit">Search</button>

    {% if search %}
        <a class="btn" href="{{ url_for('products') }}">Clear</a>
    {% endif %}
</form>

<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Products</h3>
            <span>{{ products|length }} product records shown</span>
        </div>
    </div>

    {% if products %}
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
                    <th>Updated</th>
                </tr>
            </thead>

            <tbody>
            {% for product in products %}
                <tr>
                    <td>{{ product.product_id }}</td>

                    <td class="product-name">
                        {{ product.name }}
                    </td>

                    <td class="description">
                        {{ product.description or "—" }}
                    </td>

                    <td class="money">
                        ₹{{ "%.2f"|format(product.price|float) }}
                    </td>

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
                    <td>{{ product.updated_at }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
        <div class="empty">No products found.</div>
    {% endif %}
</section>
"""


# ============================================================
# CUSTOMERS BODY
# ============================================================

CUSTOMERS_BODY = r"""
<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Customers</h3>
            <span>Customer information and confirmed spending</span>
        </div>
    </div>

    {% if customers %}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Customer ID</th>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Phone</th>
                    <th>Role</th>
                    <th>Orders</th>
                    <th>Total Spent</th>
                    <th>Created</th>
                </tr>
            </thead>

            <tbody>
            {% for customer in customers %}
                <tr>
                    <td>
                        <a class="product-name"
                           href="{{ url_for('customer_details', customer_id=customer.customer_id) }}">
                            {{ customer.customer_id }}
                        </a>
                    </td>

                    <td class="product-name">{{ customer.name }}</td>
                    <td>{{ customer.email }}</td>
                    <td>{{ customer.phone or "—" }}</td>
                    <td>
                        <span class="badge badge-blue">{{ customer.role or "CUSTOMER" }}</span>
                    </td>
                    <td>{{ customer.total_orders }}</td>
                    <td class="money">
                        ₹{{ "%.2f"|format(customer.total_spent|float) }}
                    </td>
                    <td>{{ customer.created_at }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
        <div class="empty">No customers found.</div>
    {% endif %}
</section>
"""


CUSTOMER_DETAIL_BODY = r"""
<section class="panel">
    <div class="panel-head">
        <div>
            <h3>{{ customer.name }}</h3>
            <span>Customer profile</span>
        </div>

        <a class="btn" href="{{ url_for('customers') }}">← Customers</a>
    </div>

    <div class="detail-grid">
        <div class="detail-card">
            <div class="detail-label">Customer ID</div>
            <div class="detail-value">{{ customer.customer_id }}</div>
        </div>

        <div class="detail-card">
            <div class="detail-label">Email</div>
            <div class="detail-value">{{ customer.email }}</div>
        </div>

        <div class="detail-card">
            <div class="detail-label">Phone</div>
            <div class="detail-value">{{ customer.phone or "—" }}</div>
        </div>

        <div class="detail-card">
            <div class="detail-label">Role</div>
            <div class="detail-value">{{ customer.role or "CUSTOMER" }}</div>
        </div>
    </div>
</section>

<div class="section-title">
    <div>
        <h2>Customer Orders</h2>
        <p>All orders placed by this customer</p>
    </div>
</div>

<section class="panel">
    {% if orders %}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Order ID</th>
                    <th>Status</th>
                    <th>Total</th>
                    <th>Created</th>
                    <th>Updated</th>
                </tr>
            </thead>

            <tbody>
            {% for order in orders %}
                <tr>
                    <td>
                        <a class="product-name"
                           href="{{ url_for('order_details', order_id=order.order_id) }}">
                            {{ order.order_id }}
                        </a>
                    </td>

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
        <div class="empty">No orders found for this customer.</div>
    {% endif %}
</section>
"""


# ============================================================
# ORDERS BODY
# ============================================================

ORDERS_BODY = r"""
<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Order History</h3>
            <span>{{ orders|length }} order records</span>
        </div>
    </div>

    {% if orders %}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Order ID</th>
                    <th>Customer ID</th>
                    <th>Customer</th>
                    <th>Email</th>
                    <th>Status</th>
                    <th>Total Amount</th>
                    <th>Created</th>
                    <th>Updated</th>
                </tr>
            </thead>

            <tbody>
            {% for order in orders %}
                <tr>
                    <td>
                        <a class="product-name"
                           href="{{ url_for('order_details', order_id=order.order_id) }}">
                            {{ order.order_id }}
                        </a>
                    </td>

                    <td>{{ order.customer_id }}</td>
                    <td>{{ order.customer_name }}</td>
                    <td>{{ order.customer_email }}</td>

                    <td>
                        {% if order.status == 'CONFIRMED' %}
                            <span class="badge badge-green">CONFIRMED</span>
                        {% elif order.status == 'FAILED' %}
                            <span class="badge badge-red">FAILED</span>
                        {% elif order.status == 'PROCESSING' %}
                            <span class="badge badge-blue">PROCESSING</span>
                        {% else %}
                            <span class="badge badge-orange">{{ order.status }}</span>
                        {% endif %}
                    </td>

                    <td class="money">
                        ₹{{ "%.2f"|format(order.total_amount|float) }}
                    </td>

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
"""


ORDER_DETAIL_BODY = r"""
<section class="panel">
    <div class="panel-head">
        <div>
            <h3>{{ order.order_id }}</h3>
            <span>Order and customer details</span>
        </div>

        <a class="btn" href="{{ url_for('orders') }}">← Orders</a>
    </div>

    <div class="detail-grid">
        <div class="detail-card">
            <div class="detail-label">Customer</div>
            <div class="detail-value">{{ order.customer_name }}</div>
        </div>

        <div class="detail-card">
            <div class="detail-label">Customer ID</div>
            <div class="detail-value">{{ order.customer_id }}</div>
        </div>

        <div class="detail-card">
            <div class="detail-label">Status</div>
            <div class="detail-value">{{ order.status }}</div>
        </div>

        <div class="detail-card">
            <div class="detail-label">Total</div>
            <div class="detail-value">₹{{ "%.2f"|format(order.total_amount|float) }}</div>
        </div>
    </div>
</section>

<div class="section-title">
    <div>
        <h2>Order Items</h2>
        <p>Products included in this order</p>
    </div>
</div>

<section class="panel">
    {% if items %}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Product ID</th>
                    <th>Product</th>
                    <th>Quantity</th>
                    <th>Unit Price</th>
                    <th>Subtotal</th>
                </tr>
            </thead>

            <tbody>
            {% for item in items %}
                <tr>
                    <td>{{ item.product_id }}</td>
                    <td class="product-name">{{ item.product_name }}</td>
                    <td>{{ item.quantity }}</td>
                    <td class="money">₹{{ "%.2f"|format(item.unit_price|float) }}</td>
                    <td class="money">₹{{ "%.2f"|format(item.subtotal|float) }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
        <div class="empty">No order items found.</div>
    {% endif %}
</section>
"""


# ============================================================
# ORDER ITEMS BODY
# ============================================================

ORDER_ITEMS_BODY = r"""
<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Order Items</h3>
            <span>Detailed product-level order records</span>
        </div>
    </div>

    {% if items %}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Order ID</th>
                    <th>Product ID</th>
                    <th>Product</th>
                    <th>Quantity</th>
                    <th>Unit Price</th>
                    <th>Subtotal</th>
                    <th>Created</th>
                </tr>
            </thead>

            <tbody>
            {% for item in items %}
                <tr>
                    <td>
                        <a class="product-name"
                           href="{{ url_for('order_details', order_id=item.order_id) }}">
                            {{ item.order_id }}
                        </a>
                    </td>

                    <td>{{ item.product_id }}</td>
                    <td class="product-name">{{ item.product_name }}</td>
                    <td>{{ item.quantity }}</td>
                    <td class="money">₹{{ "%.2f"|format(item.unit_price|float) }}</td>
                    <td class="money">₹{{ "%.2f"|format(item.subtotal|float) }}</td>
                    <td>{{ item.created_at }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
        <div class="empty">No order items found.</div>
    {% endif %}
</section>
"""


# ============================================================
# AUDIT LOG BODY
# ============================================================

AUDIT_BODY = r"""
<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Audit Logs</h3>
            <span>Latest 200 records from audit_logs</span>
        </div>
    </div>

    {% if logs %}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Log ID</th>
                    <th>Entity Type</th>
                    <th>Entity ID</th>
                    <th>Action</th>
                    <th>Old Value</th>
                    <th>New Value</th>
                    <th>Performed By</th>
                    <th>Created</th>
                </tr>
            </thead>

            <tbody>
            {% for log in logs %}
                <tr>
                    <td>{{ log.log_id }}</td>
                    <td>{{ log.entity_type or "—" }}</td>
                    <td>{{ log.entity_id or "—" }}</td>
                    <td>
                        <span class="badge badge-blue">{{ log.action }}</span>
                    </td>
                    <td class="description">{{ log.old_value or "—" }}</td>
                    <td class="description">{{ log.new_value or "—" }}</td>
                    <td>{{ log.performed_by or "—" }}</td>
                    <td>{{ log.created_at }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
        <div class="empty">No audit logs found.</div>
    {% endif %}
</section>
"""


# ============================================================
# REPORTS BODY
# ============================================================

REPORTS_BODY = r"""
<div class="section-title" style="margin-top:0;">
    <div>
        <h2>Reports</h2>
        <p>Daily CloudMart CSV reports generated by the Report Lambda and stored privately in S3.</p>
    </div>
</div>

{% if report_error %}
<div class="error">
    Unable to read reports from S3: {{ report_error }}
</div>
{% endif %}

<section class="panel">
    <div class="panel-head">
        <div>
            <h3>Today's Daily Report</h3>

            {% if todays_report %}
                <span>
                    {{ todays_report.key.split("/")[-1] }}
                    · {{ todays_report.size }} bytes
                    · {{ todays_report.last_modified }}
                </span>
            {% else %}
                <span>
                    Today's CSV has not been generated yet.
                </span>
            {% endif %}
        </div>
    </div>

    {% if todays_report %}
    <div class="report-highlight">
        <div class="report-icon">▤</div>

        <div class="report-highlight-info">
            <div class="report-name">
                {{ todays_report.key.split("/")[-1] }}
            </div>

            <div class="report-meta">
                Generated daily by EventBridge → Report Lambda → S3
            </div>
        </div>

        <div class="actions">
            <a class="btn"
               href="{{ url_for('view_report', key=todays_report.key) }}">
                View Today's CSV
            </a>

            <a class="btn btn-green"
               href="{{ url_for('download_report', key=todays_report.key) }}">
                ↓ Download Today's CSV
            </a>
        </div>
    </div>
    {% else %}
    <div class="empty">
        The daily report will appear here after the scheduled Report Lambda
        generates today's CSV.
    </div>
    {% endif %}
</section>

<section class="panel" style="margin-top:24px;">
    <div class="panel-head">
        <div>
            <h3>Previous Reports</h3>
            <span>Previously generated CloudMart CSV reports</span>
        </div>
    </div>

    {% if previous_reports %}
    <div class="report-list">
        {% for report in previous_reports %}
        <div class="report-row">
            <div>
                <div class="report-name">
                    {{ report.key.split("/")[-1] }}
                </div>

                <div class="report-meta">
                    {{ report.last_modified }}
                    · {{ report.size }} bytes
                </div>
            </div>

            <div class="actions">
                <a class="btn"
                   href="{{ url_for('view_report', key=report.key) }}">
                    View
                </a>

                <a class="btn btn-green"
                   href="{{ url_for('download_report', key=report.key) }}">
                    Download
                </a>
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div class="empty">
        No previous CSV reports are currently available.
    </div>
    {% endif %}
</section>
"""



# ============================================================
# MONITORING BODY
# ============================================================

MONITORING_BODY = r"""
{% if monitoring_error %}
<div class="error">
    CloudWatch monitoring unavailable: {{ monitoring_error }}
</div>
{% else %}
<div class="notice">
    ✓ CloudWatch metrics loaded successfully. Lambda metrics cover the last 1 hour; RDS and API Gateway cards show the latest 5-minute data point.
</div>
{% endif %}

<div class="cards">
    <div class="card">
        <div class="card-label">Orders</div>
        <div class="card-value">{{ orders_count }}</div>
        <div class="card-sub">Total orders in RDS</div>
    </div>

    <div class="card">
        <div class="card-label">Inventory Count</div>
        <div class="card-value">{{ inventory_count }}</div>
        <div class="card-sub">Total stock_count in products</div>
    </div>

    <div class="card">
        <div class="card-label">Lambda Errors</div>
        <div class="card-value">{{ monitoring.lambda_errors }}</div>
        <div class="card-sub">Last 1 hour · all CloudMart Lambdas</div>
    </div>

    <div class="card">
        <div class="card-label">Lambda Duration</div>
        <div class="card-value">{{ monitoring.lambda_duration }} ms</div>
        <div class="card-sub">Latest average across functions</div>
    </div>

    <div class="card">
        <div class="card-label">Lambda Invocations</div>
        <div class="card-value">{{ monitoring.lambda_invocations }}</div>
        <div class="card-sub">Last 1 hour · all CloudMart Lambdas</div>
    </div>

    <div class="card">
        <div class="card-label">RDS CPU Utilization</div>
        <div class="card-value">{{ monitoring.rds_cpu }}%</div>
        <div class="card-sub">Latest 5-minute average</div>
    </div>

    <div class="card">
        <div class="card-label">RDS Connections</div>
        <div class="card-value">{{ monitoring.rds_connections }}</div>
        <div class="card-sub">Latest 5-minute average</div>
    </div>

    <div class="card">
        <div class="card-label">RDS Free Storage</div>
        <div class="card-value">{{ monitoring.rds_free_storage_gb }} GB</div>
        <div class="card-sub">Latest 5-minute average</div>
    </div>
</div>

<div class="section-title">
    <div>
        <h2>API Gateway</h2>
        <p>{{ monitoring.api_name }} · stage {{ monitoring.api_stage }} · CloudWatch</p>
    </div>
</div>

<div class="cards">
    <div class="card">
        <div class="card-label">API Requests</div>
        <div class="card-value">{{ monitoring.api_requests }}</div>
        <div class="card-sub">Last 1 hour</div>
    </div>

    <div class="card">
        <div class="card-label">API 4XX Errors</div>
        <div class="card-value">{{ monitoring.api_4xx }}</div>
        <div class="card-sub">Last 1 hour</div>
    </div>

    <div class="card">
        <div class="card-label">API 5XX Errors</div>
        <div class="card-value">{{ monitoring.api_5xx }}</div>
        <div class="card-sub">Last 1 hour</div>
    </div>

    <div class="card">
        <div class="card-label">API Latency</div>
        <div class="card-value">{{ monitoring.api_latency }} ms</div>
        <div class="card-sub">Latest 5-minute average</div>
    </div>
</div>

<section class="panel" style="margin-top:18px;">
    <div class="panel-head">
        <div>
            <h3>Lambda Details</h3>
            <span>Errors and invocations are totals from the last 1 hour</span>
        </div>
    </div>

    {% if monitoring.lambda_rows %}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>Lambda Function</th>
                    <th>Errors</th>
                    <th>Invocations</th>
                    <th>Average Duration</th>
                </tr>
            </thead>
            <tbody>
            {% for row in monitoring.lambda_rows %}
                <tr>
                    <td class="product-name">{{ row.name }}</td>
                    <td>{{ row.errors }}</td>
                    <td>{{ row.invocations }}</td>
                    <td>{{ row.duration }} ms</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <div class="empty">No Lambda metric data is currently available.</div>
    {% endif %}
</section>
"""


# ============================================================
# REPORT VIEW BODY
# ============================================================

REPORT_VIEW_BODY = r"""
<div class="section-title" style="margin-top:0;">
    <div>
        <h2>{{ report_name }}</h2>
        <p>Report preview</p>
    </div>

    <div class="actions">
        <a class="btn" href="{{ url_for('reports') }}">← Reports</a>

        <a class="btn btn-green"
           href="{{ url_for('download_report', key=key) }}">
            ↓ Download CSV
        </a>
    </div>
</div>

<section class="panel">

    <div class="panel-head">
        <div>
            <h3>Report Data</h3>
            <span>{{ rows|length }} rows</span>
        </div>
    </div>

    {% if columns %}

    <div class="table-wrap">
        <table>

            <thead>
                <tr>
                {% for column in columns %}
                    <th>{{ column }}</th>
                {% endfor %}
                </tr>
            </thead>

            <tbody>
            {% for row in rows %}
                <tr>
                {% for column in columns %}
                    <td>{{ row.get(column, "") }}</td>
                {% endfor %}
                </tr>
            {% endfor %}
            </tbody>

        </table>
    </div>

    {% else %}

    <div class="empty">
        This report does not contain any rows.
    </div>

    {% endif %}

</section>
"""


ERROR_BODY = r"""
<div class="error">
    {{ error }}
</div>
"""


# ============================================================
# ERROR HANDLER
# ============================================================

@app.errorhandler(Exception)
def handle_error(error):
    return render_template_string(
        PAGE_HTML,
        page_title="Error",
        page_subtitle="CloudMart dashboard error",
        active="dashboard",
        content=render_template_string(
            ERROR_BODY,
            error=str(error),
        ),
        generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    ), 500


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )
