import csv
import io
import os
from datetime import datetime

import boto3
import pymysql
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, render_template_string, request

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


def get_database_summary():
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS total_products
                FROM products
                WHERE status = 'ACTIVE'
                """
            )
            products = cursor.fetchone()["total_products"]

            cursor.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS total_stock
                FROM inventory
                """
            )
            stock = cursor.fetchone()["total_stock"]

            cursor.execute(
                """
                SELECT COUNT(*) AS low_stock_products
                FROM inventory i
                INNER JOIN products p
                    ON p.product_id = i.product_id
                WHERE p.status = 'ACTIVE'
                  AND i.quantity <= 5
                """
            )
            low_stock = cursor.fetchone()["low_stock_products"]

            cursor.execute(
                """
                SELECT COUNT(*) AS total_history
                FROM product_history
                """
            )
            history = cursor.fetchone()["total_history"]

            return {
                "total_products": products,
                "total_stock": stock,
                "low_stock_products": low_stock,
                "product_history_events": history,
            }
    finally:
        connection.close()


def get_report_objects():
    if not REPORTS_BUCKET:
        return []

    response = s3.list_objects_v2(Bucket=REPORTS_BUCKET)

    reports = []
    for obj in response.get("Contents", []):
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
    response = s3.get_object(
        Bucket=REPORTS_BUCKET,
        Key=key,
    )

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


@app.route("/")
def dashboard():
    db_summary = {
        "total_products": "Unavailable",
        "total_stock": "Unavailable",
        "low_stock_products": "Unavailable",
        "product_history_events": "Unavailable",
    }
    db_error = None

    try:
        db_summary = get_database_summary()
    except Exception as exc:
        db_error = str(exc)

    reports = []
    report_error = None

    try:
        reports = get_report_objects()
    except (BotoCoreError, ClientError, Exception) as exc:
        report_error = str(exc)

    selected_key = request.args.get("report")
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
        db=db_summary,
        db_error=db_error,
        reports=reports,
        report_error=report_error,
        selected_key=selected_key,
        selected_columns=selected_columns,
        selected_rows=selected_rows,
        selected_error=selected_error,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CloudMart Operations Dashboard</title>
    <style>
        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f4f6f8;
            color: #17202a;
        }

        header {
            background: #17202a;
            color: white;
            padding: 24px 32px;
        }

        header h1 {
            margin: 0 0 6px 0;
        }

        header p {
            margin: 0;
            opacity: 0.8;
        }

        main {
            max-width: 1200px;
            margin: 28px auto;
            padding: 0 20px;
        }

        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
        }

        .card {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }

        .card h3 {
            margin-top: 0;
            font-size: 14px;
            color: #667085;
        }

        .value {
            font-size: 30px;
            font-weight: bold;
        }

        section {
            background: white;
            margin-top: 24px;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
        }

        th, td {
            padding: 10px;
            border-bottom: 1px solid #e5e7eb;
            text-align: left;
            vertical-align: top;
        }

        th {
            background: #f8fafc;
        }

        a {
            color: #1565c0;
            text-decoration: none;
        }

        .error {
            background: #fff1f2;
            color: #b42318;
            padding: 12px;
            border-radius: 6px;
            margin-top: 12px;
        }

        .ok {
            background: #ecfdf3;
            color: #027a48;
            padding: 12px;
            border-radius: 6px;
        }

        .table-wrapper {
            overflow-x: auto;
        }

        footer {
            text-align: center;
            color: #667085;
            padding: 30px;
        }
    </style>
</head>

<body>
<header>
    <h1>CloudMart Operations Dashboard</h1>
    <p>AWS EC2 Flask Dashboard • Reports + Product/Inventory Summary</p>
</header>

<main>

    <section>
        <div class="ok">
            Dashboard is running successfully.
        </div>

        {% if db_error %}
        <div class="error">
            Database unavailable: {{ db_error }}
        </div>
        {% endif %}
    </section>

    <div class="cards">
        <div class="card">
            <h3>Active Products</h3>
            <div class="value">{{ db.total_products }}</div>
        </div>

        <div class="card">
            <h3>Total Stock</h3>
            <div class="value">{{ db.total_stock }}</div>
        </div>

        <div class="card">
            <h3>Low Stock Products</h3>
            <div class="value">{{ db.low_stock_products }}</div>
        </div>

        <div class="card">
            <h3>Product History Events</h3>
            <div class="value">{{ db.product_history_events }}</div>
        </div>
    </div>

    <section>
        <h2>Generated Reports</h2>

        {% if report_error %}
        <div class="error">
            Unable to read reports from S3: {{ report_error }}
        </div>
        {% elif not reports %}
        <p>No CSV reports are currently available.</p>
        {% else %}

        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Report</th>
                        <th>Size</th>
                        <th>Last Modified</th>
                    </tr>
                </thead>

                <tbody>
                {% for report in reports %}
                    <tr>
                        <td>
                            <a href="/?report={{ report.key | urlencode }}">
                                {{ report.key }}
                            </a>
                        </td>
                        <td>{{ report.size }} bytes</td>
                        <td>{{ report.last_modified }}</td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>

        {% endif %}
    </section>

    {% if selected_key %}
    <section>
        <h2>Report: {{ selected_key }}</h2>

        {% if selected_error %}
        <div class="error">
            Unable to read this report: {{ selected_error }}
        </div>
        {% elif selected_columns %}

        <div class="table-wrapper">
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
        <p>The selected report is empty.</p>
        {% endif %}
    </section>
    {% endif %}

</main>

<footer>
    CloudMart Dashboard • Generated {{ generated_at }}
</footer>

</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
