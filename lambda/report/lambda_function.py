import os
import csv
import io
from datetime import datetime, timezone

import boto3
import pymysql


# ================================================================
# CONFIGURATION
# ================================================================

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

s3 = boto3.client("s3")
cloudwatch = boto3.client("cloudwatch")


# ================================================================
# DATABASE CONNECTION
# ================================================================

def get_connection():

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=5,
        read_timeout=15,
        write_timeout=15,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ================================================================
# CLOUDWATCH METRIC
# ================================================================

def put_metric(metric_name, value=1):

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": metric_name,
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
                    "Value": value,
                    "Unit": "Count",
                }
            ],
        )

    except Exception as exc:

        print(
            f"Metric publishing failed: {type(exc).__name__}"
        )


# ================================================================
# GENERATE REPORT
# ================================================================

def generate_report():

    connection = None

    try:

        connection = get_connection()

        report_date = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d")

        generated_at = datetime.now(
            timezone.utc
        ).isoformat()

        # --------------------------------------------------------
        # ORDERS
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    order_id,
                    customer_id,
                    status,
                    total_amount,
                    created_at,
                    updated_at
                FROM orders
                ORDER BY created_at DESC
                """
            )

            orders = cursor.fetchall()

        # --------------------------------------------------------
        # PRODUCTS
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    product_id,
                    product_name,
                    category,
                    price,
                    stock_count,
                    status,
                    created_at,
                    updated_at
                FROM products
                ORDER BY product_id
                """
            )

            products = cursor.fetchall()

        # --------------------------------------------------------
        # INVENTORY
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    product_id,
                    quantity,
                    updated_at
                FROM inventory
                ORDER BY product_id
                """
            )

            inventory = cursor.fetchall()

        # --------------------------------------------------------
        # SUMMARY
        # --------------------------------------------------------

        total_orders = len(orders)

        confirmed_orders = sum(
            1
            for order in orders
            if order["status"] == "CONFIRMED"
        )

        processing_orders = sum(
            1
            for order in orders
            if order["status"] == "PROCESSING"
        )

        failed_orders = sum(
            1
            for order in orders
            if order["status"] == "FAILED"
        )

        cancelled_orders = sum(
            1
            for order in orders
            if order["status"] == "CANCELLED"
        )

        total_sales = sum(
            float(order["total_amount"] or 0)
            for order in orders
            if order["status"] == "CONFIRMED"
        )

        low_stock_products = sum(
            1
            for item in inventory
            if int(item["quantity"]) <= 5
        )

        # --------------------------------------------------------
        # CSV
        # --------------------------------------------------------

        output = io.StringIO()

        writer = csv.writer(output)

        writer.writerow(
            [
                "CloudMart Daily Report"
            ]
        )

        writer.writerow(
            [
                "Environment",
                ENVIRONMENT
            ]
        )

        writer.writerow(
            [
                "Report Date",
                report_date
            ]
        )

        writer.writerow(
            [
                "Generated At",
                generated_at
            ]
        )

        writer.writerow([])

        # --------------------------------------------------------
        # SUMMARY SECTION
        # --------------------------------------------------------

        writer.writerow(
            [
                "SUMMARY"
            ]
        )

        writer.writerow(
            [
                "Metric",
                "Value"
            ]
        )

        writer.writerow(
            [
                "Total Orders",
                total_orders
            ]
        )

        writer.writerow(
            [
                "Confirmed Orders",
                confirmed_orders
            ]
        )

        writer.writerow(
            [
                "Processing Orders",
                processing_orders
            ]
        )

        writer.writerow(
            [
                "Failed Orders",
                failed_orders
            ]
        )

        writer.writerow(
            [
                "Cancelled Orders",
                cancelled_orders
            ]
        )

        writer.writerow(
            [
                "Total Sales",
                f"{total_sales:.2f}"
            ]
        )

        writer.writerow(
            [
                "Low Stock Products",
                low_stock_products
            ]
        )

        writer.writerow([])

        # --------------------------------------------------------
        # ORDERS SECTION
        # --------------------------------------------------------

        writer.writerow(
            [
                "ORDERS"
            ]
        )

        writer.writerow(
            [
                "Order ID",
                "Customer ID",
                "Status",
                "Total Amount",
                "Created At",
                "Updated At",
            ]
        )

        for order in orders:

            writer.writerow(
                [
                    order["order_id"],
                    order["customer_id"],
                    order["status"],
                    order["total_amount"],
                    order["created_at"],
                    order["updated_at"],
                ]
            )

        writer.writerow([])

        # --------------------------------------------------------
        # PRODUCTS SECTION
        # --------------------------------------------------------

        writer.writerow(
            [
                "PRODUCTS"
            ]
        )

        writer.writerow(
            [
                "Product ID",
                "Product Name",
                "Category",
                "Price",
                "Stock Count",
                "Status",
                "Created At",
                "Updated At",
            ]
        )

        for product in products:

            writer.writerow(
                [
                    product["product_id"],
                    product["product_name"],
                    product["category"],
                    product["price"],
                    product["stock_count"],
                    product["status"],
                    product["created_at"],
                    product["updated_at"],
                ]
            )

        writer.writerow([])

        # --------------------------------------------------------
        # INVENTORY SECTION
        # --------------------------------------------------------

        writer.writerow(
            [
                "INVENTORY"
            ]
        )

        writer.writerow(
            [
                "Product ID",
                "Quantity",
                "Updated At",
            ]
        )

        for item in inventory:

            writer.writerow(
                [
                    item["product_id"],
                    item["quantity"],
                    item["updated_at"],
                ]
            )

        # --------------------------------------------------------
        # UPLOAD TO S3
        # --------------------------------------------------------

        object_key = (
            f"daily-reports/"
            f"cloudmart-report-{report_date}.csv"
        )

        s3.put_object(
            Bucket=REPORTS_BUCKET,
            Key=object_key,
            Body=output.getvalue().encode("utf-8"),
            ContentType="text/csv",
        )

        put_metric("ReportsGenerated")

        print(
            f"Report generated successfully: {object_key}"
        )

        return {
            "bucket": REPORTS_BUCKET,
            "key": object_key,
            "report_date": report_date,
            "total_orders": total_orders,
            "confirmed_orders": confirmed_orders,
            "failed_orders": failed_orders,
            "total_sales": total_sales,
        }

    finally:

        if connection:
            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    print(
        "CloudMart Report Lambda started"
    )

    try:

        result = generate_report()

        return {
            "statusCode": 200,
            "body": result,
        }

    except Exception as exc:

        print(
            f"Report generation failed: {type(exc).__name__}"
        )

        raise