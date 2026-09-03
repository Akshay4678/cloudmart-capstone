import csv
import io
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import pymysql


# =========================================================
# AWS CLIENTS
# =========================================================

s3 = boto3.client(
    "s3"
)

dynamodb = boto3.resource(
    "dynamodb"
)

cloudwatch = boto3.client(
    "cloudwatch"
)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

REPORTS_BUCKET = os.environ[
    "REPORTS_BUCKET"
]

ORDERS_TABLE = os.environ[
    "ORDERS_TABLE"
]

DB_HOST = os.environ[
    "DB_HOST"
]

DB_NAME = os.environ[
    "DB_NAME"
]

DB_USER = os.environ[
    "DB_USER"
]

DB_PASSWORD = os.environ[
    "DB_PASSWORD"
]

DB_PORT = int(
    os.environ.get(
        "DB_PORT",
        "3306"
    )
)

ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev"
)


# =========================================================
# DYNAMODB TABLE
# =========================================================

orders_table = dynamodb.Table(
    ORDERS_TABLE
)


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_connection():

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor
    )


# =========================================================
# CLOUDWATCH METRIC
# =========================================================

def publish_metric(
    metric_name
):

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Dimensions": [
                        {
                            "Name": "Environment",
                            "Value": ENVIRONMENT
                        },
                        {
                            "Name": "Service",
                            "Value": "Report"
                        }
                    ],
                    "Value": 1,
                    "Unit": "Count"
                }
            ]
        )

    except Exception as error:

        print(
            "METRIC ERROR:",
            type(error).__name__,
            str(error)
        )


# =========================================================
# SCAN ORDERS
# =========================================================

def get_all_orders():

    orders = []

    response = orders_table.scan()

    orders.extend(
        response.get(
            "Items",
            []
        )
    )

    # -----------------------------------------------------
    # DynamoDB scan can be paginated.
    # -----------------------------------------------------

    while (
        "LastEvaluatedKey"
        in response
    ):

        response = orders_table.scan(
            ExclusiveStartKey=response[
                "LastEvaluatedKey"
            ]
        )

        orders.extend(
            response.get(
                "Items",
                []
            )
        )

    return orders


# =========================================================
# GET RDS SUMMARY
# =========================================================

def get_database_summary():

    connection = None

    try:

        connection = get_connection()

        summary = {
            "product_count": 0,
            "inventory_units": 0,
            "low_stock_products": 0
        }

        # -------------------------------------------------
        # PRODUCT COUNT
        # -------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM products
                """
            )

            result = cursor.fetchone()

            summary[
                "product_count"
            ] = int(
                result["count"]
            )

        # -------------------------------------------------
        # TOTAL INVENTORY
        # -------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT COALESCE(
                    SUM(stock_count),
                    0
                ) AS total_stock
                FROM products
                """
            )

            result = cursor.fetchone()

            summary[
                "inventory_units"
            ] = int(
                result["total_stock"]
            )

        # -------------------------------------------------
        # LOW STOCK
        # -------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM products
                WHERE stock_count <= 5
                """
            )

            result = cursor.fetchone()

            summary[
                "low_stock_products"
            ] = int(
                result["count"]
            )

        return summary

    finally:

        if connection:

            connection.close()


# =========================================================
# BUILD REPORT
# =========================================================

def build_report(
    orders,
    database_summary
):

    generated_at = datetime.now(
        timezone.utc
    )

    total_orders = len(
        orders
    )

    confirmed_orders = 0
    processing_orders = 0
    failed_orders = 0

    total_revenue = Decimal(
        "0"
    )

    # -----------------------------------------------------
    # ORDER SUMMARY
    # -----------------------------------------------------

    for order in orders:

        status = order.get(
            "status",
            ""
        )

        if status == "CONFIRMED":

            confirmed_orders += 1

        elif status == "PROCESSING":

            processing_orders += 1

        elif status == "FAILED":

            failed_orders += 1

        amount = order.get(
            "total_amount",
            Decimal("0")
        )

        try:

            total_revenue += Decimal(
                str(amount)
            )

        except Exception:

            pass

    # -----------------------------------------------------
    # REPORT ROWS
    # -----------------------------------------------------

    rows = []

    rows.append(
        {
            "report_generated_at": generated_at.isoformat(),
            "environment": ENVIRONMENT,
            "total_orders": total_orders,
            "confirmed_orders": confirmed_orders,
            "processing_orders": processing_orders,
            "failed_orders": failed_orders,
            "total_revenue": str(
                total_revenue
            ),
            "product_count": database_summary[
                "product_count"
            ],
            "inventory_units": database_summary[
                "inventory_units"
            ],
            "low_stock_products": database_summary[
                "low_stock_products"
            ]
        }
    )

    return rows


# =========================================================
# CONVERT REPORT TO CSV
# =========================================================

def generate_csv(
    rows
):

    output = io.StringIO()

    fieldnames = [
        "report_generated_at",
        "environment",
        "total_orders",
        "confirmed_orders",
        "processing_orders",
        "failed_orders",
        "total_revenue",
        "product_count",
        "inventory_units",
        "low_stock_products"
    ]

    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames
    )

    writer.writeheader()

    writer.writerows(
        rows
    )

    return output.getvalue()


# =========================================================
# UPLOAD REPORT TO S3
# =========================================================

def upload_report(
    csv_content,
    generated_at
):

    date_string = generated_at.strftime(
        "%Y-%m-%d"
    )

    key = (
        f"reports/"
        f"cloudmart-report-{date_string}.csv"
    )

    s3.put_object(
        Bucket=REPORTS_BUCKET,
        Key=key,
        Body=csv_content.encode(
            "utf-8"
        ),
        ContentType="text/csv"
    )

    print(
        "REPORT UPLOADED:",
        key
    )

    return key


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(
    event,
    context
):

    print(
        "========== REPORT LAMBDA START =========="
    )

    try:

        # -------------------------------------------------
        # GET ORDERS
        # -------------------------------------------------

        orders = get_all_orders()

        print(
            "ORDERS FOUND:",
            len(orders)
        )

        # -------------------------------------------------
        # GET RDS DATA
        # -------------------------------------------------

        database_summary = (
            get_database_summary()
        )

        print(
            "DATABASE SUMMARY:",
            json.dumps(
                database_summary
            )
        )

        # -------------------------------------------------
        # BUILD REPORT
        # -------------------------------------------------

        generated_at = datetime.now(
            timezone.utc
        )

        rows = build_report(
            orders,
            database_summary
        )

        # -------------------------------------------------
        # CSV
        # -------------------------------------------------

        csv_content = generate_csv(
            rows
        )

        # -------------------------------------------------
        # S3
        # -------------------------------------------------

        report_key = upload_report(
            csv_content,
            generated_at
        )

        # -------------------------------------------------
        # METRIC
        # -------------------------------------------------

        publish_metric(
            "ReportsGenerated"
        )

        print(
            "========== REPORT LAMBDA SUCCESS =========="
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": (
                        "Report generated successfully"
                    ),
                    "report_key": report_key
                }
            )
        }

    except Exception as error:

        print(
            "========== REPORT LAMBDA ERROR =========="
        )

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        raise