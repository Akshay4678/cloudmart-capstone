import csv
import io
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
import pymysql


# ================================================================
# AWS CLIENTS
# ================================================================

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

s3 = boto3.client("s3", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)
cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)


# ================================================================
# ENVIRONMENT VARIABLES
# ================================================================

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ.get("DB_NAME", "cloudmart")
DB_USER = os.environ.get("DB_USER", "cloudmartadmin")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

DB_PASSWORD_PARAMETER = os.environ.get(
    "DB_PASSWORD_PARAMETER",
    f"/cloudmart/{ENVIRONMENT}/database/password",
)

BUSINESS_TIMEZONE = ZoneInfo("Asia/Kolkata")


# ================================================================
# SSM PASSWORD
# ================================================================

_db_password = None


def get_db_password():
    global _db_password

    if _db_password is None:
        response = ssm.get_parameter(
            Name=DB_PASSWORD_PARAMETER,
            WithDecryption=True,
        )
        _db_password = response["Parameter"]["Value"]

    return _db_password


# ================================================================
# DATABASE CONNECTION
# ================================================================

def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=get_db_password(),
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=5,
        read_timeout=20,
        write_timeout=20,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ================================================================
# CLOUDWATCH
# ================================================================

def put_metric(metric_name, value=1):
    try:
        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count",
                    "Dimensions": [
                        {
                            "Name": "Environment",
                            "Value": ENVIRONMENT,
                        },
                        {
                            "Name": "Service",
                            "Value": "Report",
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        print(f"CloudWatch metric error: {exc}")


# ================================================================
# CSV
# ================================================================

def csv_value(value):
    if value is None:
        return ""

    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ")

    return str(value)


def make_csv(row):
    fieldnames = [
        "report_date",
        "orders_total",
        "orders_confirmed",
        "orders_processing",
        "orders_failed",
        "orders_cancelled",
        "total_revenue",
        "items_sold",
        "active_products",
        "low_stock_products",
        "audit_events",
        "generated_at",
    ]

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
    )

    writer.writeheader()
    writer.writerow(
        {
            field: csv_value(row.get(field))
            for field in fieldnames
        }
    )

    return output.getvalue().encode("utf-8")


# ================================================================
# GENERATE DAILY REPORT
# ================================================================

def generate_daily_report(report_date):

    start_local = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        0,
        0,
        0,
        tzinfo=BUSINESS_TIMEZONE,
    )

    end_local = start_local + timedelta(days=1)

    # CloudMart DATETIME values are stored without timezone.
    # Convert IST boundaries to UTC before querying MySQL.
    start_utc = start_local.astimezone(
        ZoneInfo("UTC")
    ).replace(tzinfo=None)

    end_utc = end_local.astimezone(
        ZoneInfo("UTC")
    ).replace(tzinfo=None)

    print(
        f"Report date: {report_date.isoformat()}"
    )
    print(
        f"Database window: {start_utc} -> {end_utc}"
    )

    connection = None

    try:
        connection = get_connection()

        with connection.cursor() as cursor:

            # --------------------------------------------------------
            # ORDERS
            # --------------------------------------------------------

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS orders_total,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'CONFIRMED'
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS orders_confirmed,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'PROCESSING'
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS orders_processing,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'FAILED'
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS orders_failed,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'CANCELLED'
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS orders_cancelled,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'CONFIRMED'
                                THEN total_amount
                                ELSE 0
                            END
                        ),
                        0
                    ) AS total_revenue

                FROM orders

                WHERE created_at >= %s
                  AND created_at < %s
                """,
                (
                    start_utc,
                    end_utc,
                ),
            )

            order_summary = cursor.fetchone() or {}

            # --------------------------------------------------------
            # ORDER ITEMS
            # --------------------------------------------------------

            cursor.execute(
                """
                SELECT
                    COALESCE(
                        SUM(oi.quantity),
                        0
                    ) AS items_sold

                FROM orders o

                INNER JOIN order_items oi
                    ON o.order_id = oi.order_id

                WHERE o.created_at >= %s
                  AND o.created_at < %s
                  AND o.status = 'CONFIRMED'
                """,
                (
                    start_utc,
                    end_utc,
                ),
            )

            item_summary = cursor.fetchone() or {}

            # --------------------------------------------------------
            # PRODUCTS
            # --------------------------------------------------------

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS active_products,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN stock_count <= 5
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS low_stock_products

                FROM products

                WHERE status = 'ACTIVE'
                """
            )

            product_summary = cursor.fetchone() or {}

            # --------------------------------------------------------
            # AUDIT LOGS
            # --------------------------------------------------------

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS audit_events

                FROM audit_logs

                WHERE created_at >= %s
                  AND created_at < %s
                """,
                (
                    start_utc,
                    end_utc,
                ),
            )

            audit_summary = cursor.fetchone() or {}

        row = {
            "report_date": report_date.isoformat(),

            "orders_total": int(
                order_summary.get("orders_total") or 0
            ),

            "orders_confirmed": int(
                order_summary.get("orders_confirmed") or 0
            ),

            "orders_processing": int(
                order_summary.get("orders_processing") or 0
            ),

            "orders_failed": int(
                order_summary.get("orders_failed") or 0
            ),

            "orders_cancelled": int(
                order_summary.get("orders_cancelled") or 0
            ),

            "total_revenue": (
                order_summary.get("total_revenue") or 0
            ),

            "items_sold": int(
                item_summary.get("items_sold") or 0
            ),

            "active_products": int(
                product_summary.get("active_products") or 0
            ),

            "low_stock_products": int(
                product_summary.get("low_stock_products") or 0
            ),

            "audit_events": int(
                audit_summary.get("audit_events") or 0
            ),

            "generated_at": datetime.now(
                BUSINESS_TIMEZONE
            ).strftime("%Y-%m-%d %H:%M:%S %Z"),
        }

        print(
            "Report values: "
            f"orders={row['orders_total']}, "
            f"confirmed={row['orders_confirmed']}, "
            f"revenue={row['total_revenue']}, "
            f"items={row['items_sold']}"
        )

        return row

    finally:
        if connection:
            connection.close()


# ================================================================
# S3 UPLOAD
# ================================================================

def upload_report(report_date, csv_bytes):

    key = (
        f"reports/"
        f"daily_report_{report_date.isoformat()}.csv"
    )

    print(
        f"Uploading: s3://{REPORTS_BUCKET}/{key}"
    )

    s3.put_object(
        Bucket=REPORTS_BUCKET,
        Key=key,
        Body=csv_bytes,
        ContentType="text/csv",
        ServerSideEncryption="AES256",
    )

    print(
        f"Upload successful: s3://{REPORTS_BUCKET}/{key}"
    )

    return key


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    print("CloudMart Report Lambda started")
    print(f"Event: {event}")
    print(f"Environment: {ENVIRONMENT}")
    print(f"Reports bucket: {REPORTS_BUCKET}")

    try:

        # IMPORTANT:
        # Use India business date, not UTC date.
        #
        # Your EventBridge rule runs at 18:00 UTC,
        # which is 23:30 IST.
        #
        # Therefore the report must be named using the
        # Asia/Kolkata date so the dashboard can find it.

        now_local = datetime.now(
            BUSINESS_TIMEZONE
        )

        report_date = now_local.date()

        print(
            f"Business time: {now_local.isoformat()}"
        )
        print(
            f"Generating report for: "
            f"{report_date.isoformat()}"
        )

        row = generate_daily_report(
            report_date
        )

        csv_bytes = make_csv(row)

        key = upload_report(
            report_date,
            csv_bytes
        )

        put_metric(
            "ReportsGenerated"
        )

        print(
            "CloudMart Report Lambda completed successfully."
        )

        return {
            "statusCode": 200,
            "message": (
                "Daily report generated successfully"
            ),
            "report_date": (
                report_date.isoformat()
            ),
            "bucket": REPORTS_BUCKET,
            "key": key,
            "orders_total": row[
                "orders_total"
            ],
            "orders_confirmed": row[
                "orders_confirmed"
            ],
            "total_revenue": csv_value(
                row["total_revenue"]
            ),
        }

    except Exception as exc:

        print(
            "CloudMart Report Lambda FAILED"
        )

        print(
            f"{type(exc).__name__}: {exc}"
        )

        put_metric(
            "ReportsGenerationFailures"
        )

        # Re-raise so Lambda is marked FAILED
        # and CloudWatch shows the real error.
        raise
