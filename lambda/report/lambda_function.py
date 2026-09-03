import csv
import io
import os
from datetime import datetime, timezone

import boto3
import pymysql


# ================================================================
# AWS CLIENTS
# ================================================================

s3 = boto3.client("s3")
cloudwatch = boto3.client("cloudwatch")


# ================================================================
# ENVIRONMENT
# ================================================================

REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")


# ================================================================
# DATABASE
# ================================================================

def get_connection():

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=5,
        read_timeout=20,
        write_timeout=20,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )


# ================================================================
# METRICS
# ================================================================

def put_metric():

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": "ReportsGenerated",
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [
                        {
                            "Name": "Environment",
                            "Value": ENVIRONMENT
                        },
                        {
                            "Name": "Service",
                            "Value": "Report"
                        }
                    ]
                }
            ]
        )

    except Exception as exc:

        print(f"Metric error: {exc}")


# ================================================================
# GENERATE REPORT
# ================================================================

def generate_report():

    connection = None

    try:

        connection = get_connection()

        # --------------------------------------------------------
        # Orders summary
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_orders,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'CONFIRMED'
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS confirmed_orders,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'FAILED'
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS failed_orders,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN status = 'CANCELLED'
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS cancelled_orders,
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
                """
            )

            order_summary = cursor.fetchone()

        # --------------------------------------------------------
        # Products summary
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_products,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN active = 1
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS active_products,
                    COALESCE(
                        SUM(stock_count),
                        0
                    ) AS total_stock
                FROM products
                """
            )

            product_summary = cursor.fetchone()

        # --------------------------------------------------------
        # Low stock products
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    id,
                    name,
                    stock_count
                FROM products
                WHERE active = 1
                  AND stock_count <= 5
                ORDER BY stock_count ASC
                """
            )

            low_stock_products = cursor.fetchall()

        # --------------------------------------------------------
        # Generate CSV
        # --------------------------------------------------------

        output = io.StringIO()

        writer = csv.writer(output)

        writer.writerow(
            [
                "Metric",
                "Value"
            ]
        )

        writer.writerow(
            [
                "Report Date",
                datetime.now(
                    timezone.utc
                ).strftime("%Y-%m-%d")
            ]
        )

        writer.writerow(
            [
                "Total Orders",
                order_summary["total_orders"]
            ]
        )

        writer.writerow(
            [
                "Confirmed Orders",
                order_summary["confirmed_orders"]
            ]
        )

        writer.writerow(
            [
                "Failed Orders",
                order_summary["failed_orders"]
            ]
        )

        writer.writerow(
            [
                "Cancelled Orders",
                order_summary["cancelled_orders"]
            ]
        )

        writer.writerow(
            [
                "Total Revenue",
                order_summary["total_revenue"]
            ]
        )

        writer.writerow(
            [
                "Total Products",
                product_summary["total_products"]
            ]
        )

        writer.writerow(
            [
                "Active Products",
                product_summary["active_products"]
            ]
        )

        writer.writerow(
            [
                "Total Stock",
                product_summary["total_stock"]
            ]
        )

        writer.writerow([])

        writer.writerow(
            [
                "Low Stock Products"
            ]
        )

        writer.writerow(
            [
                "Product ID",
                "Product Name",
                "Stock Count"
            ]
        )

        for product in low_stock_products:

            writer.writerow(
                [
                    product["id"],
                    product["name"],
                    product["stock_count"]
                ]
            )

        report_date = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d")

        key = (
            f"daily-reports/"
            f"cloudmart-report-{report_date}.csv"
        )

        # --------------------------------------------------------
        # Upload report
        # --------------------------------------------------------

        s3.put_object(
            Bucket=REPORTS_BUCKET,
            Key=key,
            Body=output.getvalue().encode("utf-8"),
            ContentType="text/csv"
        )

        put_metric()

        print(
            f"Report generated successfully: "
            f"s3://{REPORTS_BUCKET}/{key}"
        )

        return {
            "status": "SUCCESS",
            "bucket": REPORTS_BUCKET,
            "key": key
        }

    finally:

        if connection:

            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    print("Report Lambda started")

    try:

        result = generate_report()

        return result

    except Exception as exc:

        print(
            f"Report generation failed: {exc}"
        )

        raise