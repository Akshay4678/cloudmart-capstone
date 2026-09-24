-- ============================================================
-- CLOUDMART DATABASE SCHEMA
-- ============================================================
--
-- Database: cloudmart
-- Environment: dev
--
-- Important:
-- 1. No DROP TABLE commands are used.
-- 2. Existing tables and data are preserved.
-- 3. Column names match the existing RDS tables.
-- ============================================================


-- ============================================================
-- DATABASE
-- ============================================================

CREATE DATABASE IF NOT EXISTS cloudmart;

USE cloudmart;


-- ============================================================
-- CUSTOMERS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS customers (

    customer_id VARCHAR(100) PRIMARY KEY,

    name VARCHAR(100) NOT NULL,

    email VARCHAR(255) NOT NULL UNIQUE,

    phone VARCHAR(20),

    auth_token_hash VARCHAR(64) NOT NULL,

    role VARCHAR(20) NOT NULL DEFAULT 'USER',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT chk_customer_role
        CHECK (
            role IN ('USER', 'ADMIN')
        )
);


-- ============================================================
-- INSERT ADMIN CUSTOMER
-- ============================================================

INSERT IGNORE INTO customers
(
    customer_id,
    name,
    email,
    phone,
    auth_token_hash,
    role
)
VALUES
(
    'CUST_ADMIN',
    'sultanAdmin',
    'sultanadmin123@admin.com',
    '9666666904',
    SHA2(
        CONCAT(
            'sultanadmin123@admin.com',
            '9666666904'
        ),
        256
    ),
    'ADMIN'
);


-- ============================================================
-- INSERT USER CUSTOMERS
-- ============================================================

INSERT IGNORE INTO customers
(
    customer_id,
    name,
    email,
    phone,
    auth_token_hash,
    role
)
VALUES
(
    'CUST101',
    'akshay',
    'uppu4678@gmail.com',
    '8891222333',
    SHA2(
        CONCAT(
            'uppu4678@gmail.com',
            '8891222333'
        ),
        256
    ),
    'USER'
),
(
    'CUST102',
    'rahul',
    'rahuldhoni07@gmail.com',
    '7791222344',
    SHA2(
        CONCAT(
            'rahuldhoni07@gmail.com',
            '7791222344'
        ),
        256
    ),
    'USER'
),
(
    'CUST103',
    'Karthik',
    'karthikpadi09@gmail.com',
    '9848909333',
    SHA2(
        CONCAT(
            'karthikpadi09@gmail.com',
            '9848909333'
        ),
        256
    ),
    'USER'
);


-- ============================================================
-- PRODUCTS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS products (

    product_id INT AUTO_INCREMENT PRIMARY KEY,

    name VARCHAR(255) NOT NULL,

    description TEXT,

    price DECIMAL(10,2) NOT NULL,

    stock_count INT NOT NULL DEFAULT 0,

    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT chk_product_price
        CHECK (
            price >= 0
        ),

    CONSTRAINT chk_product_stock
        CHECK (
            stock_count >= 0
        )
);


-- ============================================================
-- INSERT SAMPLE PRODUCT: LAPTOP
-- ============================================================

INSERT INTO products
(
    name,
    description,
    price,
    stock_count,
    status
)
SELECT
    'Laptop',
    'Business laptop',
    65000.00,
    20,
    'ACTIVE'
WHERE NOT EXISTS
(
    SELECT 1
    FROM products
    WHERE name = 'Laptop'
);


-- ============================================================
-- INSERT SAMPLE PRODUCT: WIRELESS MOUSE
-- ============================================================

INSERT INTO products
(
    name,
    description,
    price,
    stock_count,
    status
)
SELECT
    'Wireless Mouse',
    'Wireless optical mouse',
    1200.00,
    50,
    'ACTIVE'
WHERE NOT EXISTS
(
    SELECT 1
    FROM products
    WHERE name = 'Wireless Mouse'
);


-- ============================================================
-- INSERT SAMPLE PRODUCT: MECHANICAL KEYBOARD
-- ============================================================

INSERT INTO products
(
    name,
    description,
    price,
    stock_count,
    status
)
SELECT
    'Mechanical Keyboard',
    'Mechanical RGB keyboard',
    4500.00,
    30,
    'ACTIVE'
WHERE NOT EXISTS
(
    SELECT 1
    FROM products
    WHERE name = 'Mechanical Keyboard'
);


-- ============================================================
-- INSERT SAMPLE PRODUCT: MONITOR
-- ============================================================

INSERT INTO products
(
    name,
    description,
    price,
    stock_count,
    status
)
SELECT
    'Monitor',
    '24 inch Full HD monitor',
    12000.00,
    15,
    'ACTIVE'
WHERE NOT EXISTS
(
    SELECT 1
    FROM products
    WHERE name = 'Monitor'
);


-- ============================================================
-- INSERT SAMPLE PRODUCT: USB-C CABLE
-- ============================================================

INSERT INTO products
(
    name,
    description,
    price,
    stock_count,
    status
)
SELECT
    'USB-C Cable',
    'High-speed USB-C cable',
    800.00,
    100,
    'ACTIVE'
WHERE NOT EXISTS
(
    SELECT 1
    FROM products
    WHERE name = 'USB-C Cable'
);


-- ============================================================
-- ORDERS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS orders (

    order_id VARCHAR(50) PRIMARY KEY,

    customer_id VARCHAR(100) NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'PROCESSING',

    total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT chk_order_status
        CHECK (
            status IN (
                'PROCESSING',
                'CONFIRMED',
                'SHIPPED',
                'DELIVERED',
                'CANCELLED',
                'FAILED'
            )
        ),

    CONSTRAINT chk_order_total
        CHECK (
            total_amount >= 0
        ),

    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE
);


-- ============================================================
-- ORDER ITEMS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS order_items (

    order_item_id INT AUTO_INCREMENT PRIMARY KEY,

    order_id VARCHAR(50) NOT NULL,

    product_id INT NOT NULL,

    quantity INT NOT NULL,

    unit_price DECIMAL(12,2) NOT NULL,

    subtotal DECIMAL(12,2) NOT NULL,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id)
        REFERENCES orders(order_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id)
        REFERENCES products(product_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT chk_order_item_quantity
        CHECK (
            quantity > 0
        ),

    CONSTRAINT chk_order_item_unit_price
        CHECK (
            unit_price >= 0
        ),

    CONSTRAINT chk_order_item_subtotal
        CHECK (
            subtotal >= 0
        )
);


-- ============================================================
-- AUDIT LOGS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_logs (

    audit_id BIGINT AUTO_INCREMENT PRIMARY KEY,

    customer_id VARCHAR(100),

    action VARCHAR(100) NOT NULL,

    entity_type VARCHAR(100),

    entity_id VARCHAR(100),

    details TEXT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_audit_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
        ON DELETE SET NULL
        ON UPDATE CASCADE
);


-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================

SELECT
    customer_id,
    name,
    email,
    phone,
    role
FROM customers;


SELECT
    product_id,
    name,
    description,
    price,
    stock_count,
    status
FROM products;


SELECT
    order_id,
    customer_id,
    status,
    total_amount,
    created_at,
    updated_at
FROM orders;


SELECT
    TABLE_NAME,
    TABLE_ROWS
FROM information_schema.tables
WHERE table_schema = 'cloudmart';


-- ============================================================
-- TEST TOKENS
-- ============================================================

-- Admin token:
-- sultanadmin123@admin.com9666666904

-- Akshay token:
-- uppu4678@gmail.com8891222333

-- Rahul token:
-- rahuldhoni07@gmail.com7791222344

-- Karthik token:
-- karthikpadi09@gmail.com9848909333