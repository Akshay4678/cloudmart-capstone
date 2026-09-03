import base64
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import pymysql


# ================================================================
# AWS CLIENTS
# ================================================================

sqs = boto3.client("sqs")
cloudwatch = boto3.client("cloudwatch")


# ================================================================
# ENVIRONMENT VARIABLES
# ================================================================

QUEUE_URL = os.environ["ORDER_QUEUE_URL"]

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")


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
        read_timeout=10,
        write_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# ================================================================
# METRICS
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
                            "Value": "Order",
                        },
                    ],
                }
            ],
        )

    except Exception as exc:
        print(f"Metric error: {exc}")


# ================================================================
# HTTP RESPONSE
# ================================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }


# ================================================================
# REQUEST BODY PARSER
# ================================================================

def parse_request_body(event):

    body = event.get("body")

    if body is None:
        return {}

    if isinstance(body, dict):
        return body

    if not isinstance(body, str):
        raise ValueError("Request body must be JSON")

    if event.get("isBase64Encoded"):

        try:
            body = base64.b64decode(body).decode("utf-8")

        except Exception as exc:
            raise ValueError(
                "Invalid base64 request body"
            ) from exc

    try:
        return json.loads(body)

    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc


# ================================================================
# SCHEMA FILE
# ================================================================

def get_schema_path():

    schema_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "schema.sql",
    )

    if not os.path.isfile(schema_path):

        raise FileNotFoundError(
            "schema.sql was not found in the Lambda package: "
            + schema_path
        )

    return schema_path


# ================================================================
# EXECUTE SCHEMA.SQL
# ================================================================

def execute_schema_file(connection):
    """
    Executes schema.sql packaged inside the Order Lambda.

    The deployment workflow copies:

        schema.sql

    into:

        build/order/schema.sql

    before creating the Lambda ZIP.

    This function is intentionally designed to tolerate
    objects that already exist.
    """

    schema_path = get_schema_path()

    print(f"Reading schema file: {schema_path}")

    with open(
        schema_path,
        "r",
        encoding="utf-8",
    ) as schema_file:

        sql = schema_file.read()

    if not sql.strip():

        raise ValueError("schema.sql is empty")

    # ------------------------------------------------------------
    # Remove normal -- comments.
    # ------------------------------------------------------------

    cleaned_lines = []

    for line in sql.splitlines():

        stripped = line.strip()

        if stripped.startswith("--"):
            continue

        cleaned_lines.append(line)

    sql = "\n".join(cleaned_lines)

    # ------------------------------------------------------------
    # The current CloudMart schema contains ordinary SQL
    # statements separated by semicolons.
    #
    # It does not contain stored procedures or triggers.
    # ------------------------------------------------------------

    statements = []

    for statement in sql.split(";"):

        statement = statement.strip()

        if statement:
            statements.append(statement)

    print(
        f"Found {len(statements)} SQL statements in schema.sql"
    )

    executed = 0
    skipped = 0

    with connection.cursor() as cursor:

        for statement in statements:

            preview = (
                statement[:150]
                .replace("\n", " ")
                .replace("\r", " ")
            )

            print(
                "Schema statement: "
                + preview
            )

            try:

                cursor.execute(statement)

                executed += 1

            except pymysql.err.OperationalError as exc:

                error_code = exc.args[0] if exc.args else None
                error_message = str(exc).lower()

                # ------------------------------------------------
                # MySQL error 1061:
                # Duplicate key name
                #
                # This happens when CREATE INDEX is executed
                # again during another deployment.
                # ------------------------------------------------

                if (
                    error_code == 1061
                    or "duplicate key name" in error_message
                ):

                    print(
                        "Index already exists. "
                        "Skipping statement."
                    )

                    skipped += 1

                    continue

                # ------------------------------------------------
                # MySQL error 1826:
                # Duplicate foreign key constraint name
                # ------------------------------------------------

                if (
                    error_code == 1826
                    or "duplicate foreign key constraint name"
                    in error_message
                ):

                    print(
                        "Foreign key already exists. "
                        "Skipping statement."
                    )

                    skipped += 1

                    continue

                # ------------------------------------------------
                # MySQL error 1050:
                # Table already exists
                #
                # CREATE TABLE IF NOT EXISTS normally prevents
                # this, but tolerate it for safety.
                # ------------------------------------------------

                if (
                    error_code == 1050
                    or "table already exists" in error_message
                ):

                    print(
                        "Table already exists. "
                        "Skipping statement."
                    )

                    skipped += 1

                    continue

                # ------------------------------------------------
                # Any other SQL error is a real failure.
                # ------------------------------------------------

                print(
                    f"Schema statement failed: {exc}"
                )

                raise

    connection.commit()

    print(
        "schema.sql execution completed. "
        f"Executed: {executed}, "
        f"Skipped existing objects: {skipped}"
    )


# ================================================================
# ENSURE CUSTOMERS TABLE
# ================================================================

def ensure_customer_table(connection):
    """
    Creates the customers table if it does not already exist.

    This is required because the existing RDS database may have
    been created before the customers table was added.
    """

    sql = """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id VARCHAR(100) NOT NULL,
        name VARCHAR(100) NOT NULL,
        email VARCHAR(255) NOT NULL,
        phone VARCHAR(20),
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (customer_id),
        UNIQUE KEY uk_customers_email (email)
    ) ENGINE=InnoDB
    """

    with connection.cursor() as cursor:

        cursor.execute(sql)

    connection.commit()

    print("customers table verified.")


# ================================================================
# ENSURE ORDERS TABLE
# ================================================================

def ensure_orders_table(connection):
    """
    Ensures the orders table exists with the expected structure.

    IMPORTANT:
    Existing CloudMart databases already contain orders.
    Therefore CREATE TABLE IF NOT EXISTS alone cannot modify
    an existing table.
    """

    sql = """
    CREATE TABLE IF NOT EXISTS orders (
        order_id VARCHAR(50) NOT NULL,
        customer_id VARCHAR(100) NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'PROCESSING',
        total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (order_id)
    ) ENGINE=InnoDB
    """

    with connection.cursor() as cursor:

        cursor.execute(sql)

    connection.commit()

    print("orders table verified.")


# ================================================================
# ENSURE ORDER_ITEMS TABLE
# ================================================================

def ensure_order_items_table(connection):

    sql = """
    CREATE TABLE IF NOT EXISTS order_items (
        order_item_id INT AUTO_INCREMENT PRIMARY KEY,
        order_id VARCHAR(50) NOT NULL,
        product_id INT NOT NULL,
        quantity INT NOT NULL,
        price DECIMAL(10,2) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB
    """

    with connection.cursor() as cursor:

        cursor.execute(sql)

    connection.commit()

    print("order_items table verified.")


# ================================================================
# ENSURE CUSTOMER FOREIGN KEY
# ================================================================

def ensure_order_customer_foreign_key(connection):
    """
    Adds:

        orders.customer_id
            ->
        customers.customer_id

    if the constraint does not already exist.
    """

    with connection.cursor() as cursor:

        cursor.execute(
            """
            SELECT COUNT(*) AS constraint_count
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
              AND TABLE_NAME = 'orders'
              AND CONSTRAINT_NAME = 'fk_orders_customer'
              AND CONSTRAINT_TYPE = 'FOREIGN KEY'
            """
        )

        result = cursor.fetchone()

        constraint_exists = (
            int(result["constraint_count"]) > 0
        )

        if constraint_exists:

            print(
                "fk_orders_customer already exists."
            )

            connection.commit()

            return

        # --------------------------------------------------------
        # Find existing orders whose customer does not exist.
        # --------------------------------------------------------

        cursor.execute(
            """
            SELECT DISTINCT
                o.customer_id
            FROM orders o
            LEFT JOIN customers c
                ON c.customer_id = o.customer_id
            WHERE o.customer_id IS NOT NULL
              AND o.customer_id <> ''
              AND c.customer_id IS NULL
            """
        )

        missing_customers = cursor.fetchall()

        # --------------------------------------------------------
        # Create placeholder customer records so that adding
        # the foreign key does not fail on existing orders.
        # --------------------------------------------------------

        for row in missing_customers:

            customer_id = str(
                row["customer_id"]
            )

            # Deterministic unique value.
            safe_suffix = uuid.uuid5(
                uuid.NAMESPACE_DNS,
                customer_id,
            ).hex[:20]

            placeholder_email = (
                f"customer-{safe_suffix}"
                "@cloudmart.local"
            )

            print(
                "Creating placeholder customer: "
                f"{customer_id}"
            )

            cursor.execute(
                """
                INSERT IGNORE INTO customers
                (
                    customer_id,
                    name,
                    email
                )
                VALUES
                (
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    customer_id,
                    f"Customer {customer_id}",
                    placeholder_email,
                ),
            )

        # --------------------------------------------------------
        # Add the foreign key.
        # --------------------------------------------------------

        cursor.execute(
            """
            ALTER TABLE orders
            ADD CONSTRAINT fk_orders_customer
            FOREIGN KEY (customer_id)
            REFERENCES customers(customer_id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
            """
        )

    connection.commit()

    print(
        "fk_orders_customer created successfully."
    )


# ================================================================
# ENSURE ORDER ITEM FOREIGN KEYS
# ================================================================

def ensure_order_item_foreign_keys(connection):
    """
    Adds the order_items foreign keys if they are missing.
    """

    with connection.cursor() as cursor:

        # --------------------------------------------------------
        # Order -> order_items
        # --------------------------------------------------------

        cursor.execute(
            """
            SELECT COUNT(*) AS constraint_count
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
              AND TABLE_NAME = 'order_items'
              AND CONSTRAINT_NAME = 'fk_order_items_order'
              AND CONSTRAINT_TYPE = 'FOREIGN KEY'
            """
        )

        result = cursor.fetchone()

        order_fk_exists = (
            int(result["constraint_count"]) > 0
        )

        if not order_fk_exists:

            try:

                cursor.execute(
                    """
                    ALTER TABLE order_items
                    ADD CONSTRAINT fk_order_items_order
                    FOREIGN KEY (order_id)
                    REFERENCES orders(order_id)
                    ON DELETE CASCADE
                    """
                )

                print(
                    "fk_order_items_order created."
                )

            except pymysql.err.OperationalError as exc:

                # Duplicate FK name means it already exists.
                if exc.args and exc.args[0] == 1826:

                    print(
                        "fk_order_items_order already exists."
                    )

                else:

                    raise

        else:

            print(
                "fk_order_items_order already exists."
            )

        # --------------------------------------------------------
        # Product -> order_items
        # --------------------------------------------------------

        cursor.execute(
            """
            SELECT COUNT(*) AS constraint_count
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
              AND TABLE_NAME = 'order_items'
              AND CONSTRAINT_NAME = 'fk_order_items_product'
              AND CONSTRAINT_TYPE = 'FOREIGN KEY'
            """
        )

        result = cursor.fetchone()

        product_fk_exists = (
            int(result["constraint_count"]) > 0
        )

        if not product_fk_exists:

            try:

                cursor.execute(
                    """
                    ALTER TABLE order_items
                    ADD CONSTRAINT fk_order_items_product
                    FOREIGN KEY (product_id)
                    REFERENCES products(product_id)
                    ON DELETE RESTRICT
                    """
                )

                print(
                    "fk_order_items_product created."
                )

            except pymysql.err.OperationalError as exc:

                if exc.args and exc.args[0] == 1826:

                    print(
                        "fk_order_items_product already exists."
                    )

                else:

                    raise

        else:

            print(
                "fk_order_items_product already exists."
            )

    connection.commit()


# ================================================================
# INITIALIZE DATABASE SCHEMA
# ================================================================

def initialize_schema():

    connection = None

    try:

        print(
            "=================================================="
        )
        print(
            "STARTING DATABASE SCHEMA INITIALIZATION"
        )
        print(
            "=================================================="
        )

        connection = get_connection()

        print(
            f"Connected to database: {DB_NAME}"
        )

        # --------------------------------------------------------
        # 1. Execute repository schema.sql
        # --------------------------------------------------------

        execute_schema_file(connection)

        # --------------------------------------------------------
        # 2. Ensure customers table
        # --------------------------------------------------------

        ensure_customer_table(connection)

        # --------------------------------------------------------
        # 3. Ensure orders table
        # --------------------------------------------------------

        ensure_orders_table(connection)

        # --------------------------------------------------------
        # 4. Ensure order_items table
        # --------------------------------------------------------

        ensure_order_items_table(connection)

        # --------------------------------------------------------
        # 5. Add customer foreign key
        # --------------------------------------------------------

        ensure_order_customer_foreign_key(
            connection
        )

        # --------------------------------------------------------
        # 6. Add order_items foreign keys
        # --------------------------------------------------------

        ensure_order_item_foreign_keys(
            connection
        )

        # --------------------------------------------------------
        # 7. Ensure a test customer exists.
        #
        # The GitHub Actions smoke test uses CUST101.
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                INSERT IGNORE INTO customers
                (
                    customer_id,
                    name,
                    email,
                    phone
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    "CUST101",
                    "CloudMart Test Customer",
                    "cust101@cloudmart.local",
                    "9876543210",
                ),
            )

        connection.commit()

        print(
            "CUST101 customer verified."
        )

        print(
            "=================================================="
        )
        print(
            "DATABASE SCHEMA INITIALIZATION SUCCESSFUL"
        )
        print(
            "=================================================="
        )

        return {
            "statusCode": 200,
            "message": (
                "Database schema initialized successfully"
            ),
            "database": DB_NAME,
        }

    except Exception as exc:

        if connection:

            try:
                connection.rollback()

            except Exception:
                pass

        print(
            "Schema initialization error: "
            f"{exc}"
        )

        raise

    finally:

        if connection:

            connection.close()

            print(
                "Database connection closed."
            )


# ================================================================
# CREATE ORDER
# ================================================================

def create_order(body):

    if not isinstance(body, dict):

        raise ValueError(
            "Request body must be a JSON object"
        )

    customer_id = body.get("customer_id")

    items = body.get("items")

    # ------------------------------------------------------------
    # Validate customer
    # ------------------------------------------------------------

    if not customer_id:

        raise ValueError(
            "customer_id is required"
        )

    customer_id = str(
        customer_id
    ).strip()

    if not customer_id:

        raise ValueError(
            "customer_id cannot be empty"
        )

    if len(customer_id) > 100:

        raise ValueError(
            "customer_id must not exceed 100 characters"
        )

    # ------------------------------------------------------------
    # Validate items
    # ------------------------------------------------------------

    if not isinstance(items, list):

        raise ValueError(
            "items must be an array"
        )

    if len(items) == 0:

        raise ValueError(
            "items must contain at least one item"
        )

    if len(items) > 100:

        raise ValueError(
            "Too many order items"
        )

    normalized_items = []

    for item in items:

        if not isinstance(item, dict):

            raise ValueError(
                "Each item must be an object"
            )

        if "product_id" not in item:

            raise ValueError(
                "product_id is required"
            )

        if "quantity" not in item:

            raise ValueError(
                "quantity is required"
            )

        try:

            product_id = int(
                item["product_id"]
            )

            quantity = int(
                item["quantity"]
            )

        except (TypeError, ValueError):

            raise ValueError(
                "product_id and quantity must be numbers"
            )

        if product_id <= 0:

            raise ValueError(
                "product_id must be greater than zero"
            )

        if quantity <= 0:

            raise ValueError(
                "quantity must be greater than zero"
            )

        normalized_items.append(
            {
                "product_id": product_id,
                "quantity": quantity,
            }
        )

    # ------------------------------------------------------------
    # Generate order ID
    #
    # IMPORTANT:
    # orders table uses order_id, NOT id.
    # ------------------------------------------------------------

    order_id = (
        "ORD-"
        + uuid.uuid4().hex[:12].upper()
    )

    now = datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )

    connection = None

    try:

        connection = get_connection()

        # --------------------------------------------------------
        # Verify customer
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    customer_id
                FROM customers
                WHERE customer_id = %s
                """,
                (
                    customer_id,
                ),
            )

            customer = cursor.fetchone()

        if not customer:

            connection.rollback()

            return response(
                400,
                {
                    "message": (
                        "Customer does not exist"
                    ),
                    "customer_id": customer_id,
                },
            )

        # --------------------------------------------------------
        # Read product information
        #
        # Actual CloudMart products columns:
        #
        # product_id
        # name
        # description
        # price
        # stock_count
        #
        # There is NO products.id.
        # --------------------------------------------------------

        total_amount = Decimal(
            "0.00"
        )

        priced_items = []

        with connection.cursor() as cursor:

            for item in normalized_items:

                product_id = item[
                    "product_id"
                ]

                quantity = item[
                    "quantity"
                ]

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        price,
                        stock_count
                    FROM products
                    WHERE product_id = %s
                    """,
                    (
                        product_id,
                    ),
                )

                product = cursor.fetchone()

                if not product:

                    raise ValueError(
                        f"Product {product_id} "
                        "does not exist"
                    )

                price = Decimal(
                    str(product["price"])
                )

                stock_count = int(
                    product["stock_count"]
                )

                if quantity > stock_count:

                    raise ValueError(
                        f"Insufficient stock for "
                        f"product {product_id}. "
                        f"Available stock: "
                        f"{stock_count}"
                    )

                priced_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "price": price,
                    }
                )

                total_amount += (
                    price * quantity
                )

        # --------------------------------------------------------
        # Insert order
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                INSERT INTO orders
                (
                    order_id,
                    customer_id,
                    status,
                    total_amount,
                    created_at,
                    updated_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    order_id,
                    customer_id,
                    "PROCESSING",
                    total_amount,
                    now,
                    now,
                ),
            )

        # --------------------------------------------------------
        # Insert order items
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            for item in priced_items:

                cursor.execute(
                    """
                    INSERT INTO order_items
                    (
                        order_id,
                        product_id,
                        quantity,
                        price,
                        created_at
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        order_id,
                        item["product_id"],
                        item["quantity"],
                        item["price"],
                        now,
                    ),
                )

        # --------------------------------------------------------
        # Commit DB transaction
        # --------------------------------------------------------

        connection.commit()

        # --------------------------------------------------------
        # Send order to SQS
        # --------------------------------------------------------

        message = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": [
                {
                    "product_id": item[
                        "product_id"
                    ],
                    "quantity": item[
                        "quantity"
                    ],
                    "price": str(
                        item["price"]
                    ),
                }
                for item in priced_items
            ],
            "total_amount": str(
                total_amount
            ),
        }

        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(
                message
            ),
        )

        # --------------------------------------------------------
        # Metrics
        # --------------------------------------------------------

        put_metric(
            "OrdersCreated"
        )

        put_metric(
            "OrderRequests"
        )

        # --------------------------------------------------------
        # Response
        # --------------------------------------------------------

        return response(
            202,
            {
                "message": (
                    "Order accepted for processing"
                ),
                "order_id": order_id,
                "status": "PROCESSING",
            },
        )

    except ValueError as exc:

        if connection:

            connection.rollback()

        print(
            f"Order validation error: {exc}"
        )

        put_metric(
            "OrderRequests"
        )

        return response(
            400,
            {
                "message": str(exc),
            },
        )

    except Exception as exc:

        if connection:

            connection.rollback()

        print(
            f"Order creation error: {exc}"
        )

        put_metric(
            "OrdersFailed"
        )

        put_metric(
            "OrderRequests"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:

            connection.close()


# ================================================================
# GET SINGLE ORDER
# ================================================================

def get_order(order_id):

    connection = None

    try:

        connection = get_connection()

        # --------------------------------------------------------
        # Get order
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
                WHERE order_id = %s
                """,
                (
                    order_id,
                ),
            )

            order = cursor.fetchone()

        if not order:

            return response(
                404,
                {
                    "message": (
                        "Order not found"
                    ),
                },
            )

        # --------------------------------------------------------
        # Get order items
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    order_item_id,
                    product_id,
                    quantity,
                    price,
                    created_at
                FROM order_items
                WHERE order_id = %s
                ORDER BY order_item_id
                """,
                (
                    order_id,
                ),
            )

            items = cursor.fetchall()

        order["items"] = items

        return response(
            200,
            order,
        )

    except Exception as exc:

        print(
            f"Get order error: {exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:

            connection.close()


# ================================================================
# GET CUSTOMER ORDERS
# ================================================================

def get_customer_orders(customer_id):

    connection = None

    try:

        connection = get_connection()

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
                WHERE customer_id = %s
                ORDER BY created_at DESC
                """,
                (
                    customer_id,
                ),
            )

            orders = cursor.fetchall()

        return response(
            200,
            {
                "orders": orders,
            },
        )

    except Exception as exc:

        print(
            f"Get customer orders error: {exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:

            connection.close()


# ================================================================
# UPDATE ORDER
# ================================================================

def update_order(order_id, body):

    if not isinstance(body, dict):

        return response(
            400,
            {
                "message": (
                    "Request body must be "
                    "a JSON object"
                ),
            },
        )

    status = body.get(
        "status"
    )

    allowed_statuses = [
        "PROCESSING",
        "CONFIRMED",
        "FAILED",
        "CANCELLED",
    ]

    if status not in allowed_statuses:

        return response(
            400,
            {
                "message": (
                    "Invalid order status"
                ),
            },
        )

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = %s,
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    status,
                    datetime.now(
                        timezone.utc
                    ).replace(
                        tzinfo=None
                    ),
                    order_id,
                ),
            )

            if cursor.rowcount == 0:

                connection.rollback()

                return response(
                    404,
                    {
                        "message": (
                            "Order not found"
                        ),
                    },
                )

        connection.commit()

        return response(
            200,
            {
                "message": (
                    "Order updated"
                ),
                "order_id": order_id,
                "status": status,
            },
        )

    except Exception as exc:

        if connection:

            connection.rollback()

        print(
            f"Update order error: {exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:

            connection.close()


# ================================================================
# CANCEL ORDER
# ================================================================

def cancel_order(order_id):

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    status
                FROM orders
                WHERE order_id = %s
                """,
                (
                    order_id,
                ),
            )

            order = cursor.fetchone()

            if not order:

                return response(
                    404,
                    {
                        "message": (
                            "Order not found"
                        ),
                    },
                )

            if order["status"] != "PROCESSING":

                return response(
                    400,
                    {
                        "message": (
                            "Order cannot be cancelled"
                        ),
                    },
                )

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = 'CANCELLED',
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    datetime.now(
                        timezone.utc
                    ).replace(
                        tzinfo=None
                    ),
                    order_id,
                ),
            )

        connection.commit()

        return response(
            200,
            {
                "message": (
                    "Order cancelled"
                ),
                "order_id": order_id,
                "status": "CANCELLED",
            },
        )

    except Exception as exc:

        if connection:

            connection.rollback()

        print(
            f"Cancel order error: {exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:

            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    if not isinstance(event, dict):

        event = {}

    print(
        "Order Lambda request: "
        f"{event.get('httpMethod', '')} "
        f"{event.get('path', '')}"
    )

    # ============================================================
    # DATABASE SCHEMA INITIALIZATION
    #
    # Called from GitHub Actions using:
    #
    # {
    #     "action": "initialize_schema"
    # }
    # ============================================================

    if event.get(
        "action"
    ) == "initialize_schema":

        try:

            result = initialize_schema()

            return response(
                200,
                result,
            )

        except Exception as exc:

            print(
                "Schema initialization failed: "
                f"{exc}"
            )

            return response(
                500,
                {
                    "message": (
                        "Schema initialization failed"
                    ),
                    "error": str(exc),
                },
            )

    # ============================================================
    # API GATEWAY INFORMATION
    # ============================================================

    method = (
        event.get(
            "httpMethod",
            "",
        )
        .upper()
    )

    path = event.get(
        "path",
        "",
    )

    path_parameters = (
        event.get(
            "pathParameters"
        )
        or {}
    )

    query_parameters = (
        event.get(
            "queryStringParameters"
        )
        or {}
    )

    order_id = path_parameters.get(
        "orderId"
    )

    # ============================================================
    # POST /orders
    # ============================================================

    if (
        method == "POST"
        and not order_id
    ):

        try:

            body = parse_request_body(
                event
            )

            return create_order(
                body
            )

        except ValueError as exc:

            return response(
                400,
                {
                    "message": str(exc),
                },
            )

    # ============================================================
    # POST /orders/{orderId}/cancel
    # ============================================================

    if (
        method == "POST"
        and order_id
        and path.endswith(
            "/cancel"
        )
    ):

        return cancel_order(
            order_id
        )

    # ============================================================
    # GET /orders/{orderId}
    # ============================================================

    if (
        method == "GET"
        and order_id
    ):

        return get_order(
            order_id
        )

    # ============================================================
    # GET /orders?customer_id=...
    # ============================================================

    if method == "GET":

        customer_id = (
            query_parameters.get(
                "customer_id"
            )
        )

        if not customer_id:

            return response(
                400,
                {
                    "message": (
                        "customer_id query "
                        "parameter is required"
                    ),
                },
            )

        return get_customer_orders(
            customer_id
        )

    # ============================================================
    # PUT /orders/{orderId}
    # ============================================================

    if (
        method == "PUT"
        and order_id
    ):

        try:

            body = parse_request_body(
                event
            )

            return update_order(
                order_id,
                body,
            )

        except ValueError as exc:

            return response(
                400,
                {
                    "message": str(exc),
                },
            )

    # ============================================================
    # UNKNOWN ROUTE
    # ============================================================

    return response(
        404,
        {
            "message": "Route not found",
        },
    )