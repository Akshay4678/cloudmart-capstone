import csv
import io
import os
from datetime import datetime, timezone

import boto3


# =========================================================
# AWS CLIENTS
# =========================================================

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
cloudwatch = boto3.client("cloudwatch")


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

ORDERS_TABLE_NAME = os.environ["ORDERS_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]

ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev"
)

orders_table = dynamodb.Table(
    ORDERS_TABLE_NAME
)


# =========================================================
# CLOUDWATCH METRIC
# =========================================================

def put_metric():

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": "ReportsGenerated",
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

    except Exception as e:

        print(
            "CloudWatch metric failed:",
            type(e).__name__
        )


# =========================================================
# SCAN ALL ORDERS
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

    while "LastEvaluatedKey" in response:

        response = orders_table.scan(
            ExclusiveStartKey=(
                response["LastEvaluatedKey"]
            )
        )

        orders.extend(
            response.get(
                "Items",
                []
            )
        )

    return orders


# =========================================================
# CREATE CSV
# =========================================================

def generate_csv(orders):

    output = io.StringIO()

    writer = csv.writer(output)

    writer.writerow(
        [
            "order_id",
            "customer_id",
            "created_at",
            "status",
            "total_amount",
            "items"
        ]
    )

    for order in orders:

        items = order.get(
            "items",
            []
        )

        writer.writerow(
            [
                order.get(
                    "order_id",
                    ""
                ),
                order.get(
                    "customer_id",
                    ""
                ),
                order.get(
                    "created_at",
                    ""
                ),
                order.get(
                    "status",
                    ""
                ),
                order.get(
                    "total_amount",
                    ""
                ),
                str(items)
            ]
        )

    return output.getvalue()


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):

    print(
        "========== REPORT LAMBDA START =========="
    )

    try:

        # -------------------------------------------------
        # GET ORDERS
        # -------------------------------------------------

        orders = get_all_orders()

        print(
            "Orders found:",
            len(orders)
        )

        # -------------------------------------------------
        # GENERATE CSV
        # -------------------------------------------------

        csv_content = generate_csv(
            orders
        )

        # -------------------------------------------------
        # REPORT DATE
        # -------------------------------------------------

        report_date = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d")

        report_key = (
            f"reports/"
            f"orders-{report_date}.csv"
        )

        # -------------------------------------------------
        # UPLOAD TO S3
        # -------------------------------------------------

        s3.put_object(
            Bucket=REPORTS_BUCKET,
            Key=report_key,
            Body=csv_content.encode("utf-8"),
            ContentType="text/csv"
        )

        print(
            "Report uploaded:",
            report_key
        )

        # -------------------------------------------------
        # METRIC
        # -------------------------------------------------

        put_metric()

        print(
            "========== REPORT LAMBDA END =========="
        )

        return {
            "statusCode": 200,
            "message": "Report generated successfully",
            "report_key": report_key,
            "order_count": len(orders)
        }

    except Exception as e:

        print(
            "Report generation failed:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)
        )

        raise