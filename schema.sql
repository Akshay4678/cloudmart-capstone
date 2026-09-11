-- =====================================================
-- CLOUDMART DATABASE SCHEMA
-- =====================================================
-- Database:
-- cloudmart
--
-- Tables:
-- customers
-- products
-- orders
-- order_items
-- audit_logs
--
-- =====================================================


-- =====================================================
-- CUSTOMERS TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS customers (

    customer_id VARCHAR(100) NOT NULL,

    name VARCHAR(100) NOT NULL,

    email VARCHAR(255) NOT NULL,

    phone VARCHAR(20),

    /*
    SHA-256 hexadecimal hash contains 64 characters.
    The original token is never stored.
    */

    auth_token_hash VARCHAR(64) NOT NULL,

    /*
    USER  = normal customer
    ADMIN = administrator
    */

    role VARCHAR(20) NOT NULL DEFAULT 'USER',

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (customer_id),

    UNIQUE KEY uk_customers_email (email),

    UNIQUE KEY uk_customers_auth_token (auth_token_hash),

    CONSTRAINT chk_customers_role
        CHECK (role IN ('USER', 'ADMIN'))

) ENGINE=InnoDB;


-- =====================================================
-- PRODUCTS TABLE
-- =====================================================
--
-- status:
--
-- ACTIVE
--     Product is available and can be displayed.
--
-- INACTIVE
--     Product is hidden from normal product listings.
--
-- Products are not physically deleted.
-- DELETE operations are handled as soft deletes
-- by the Product Lambda.
--
-- When stock reaches 0:
--     status = INACTIVE
--
-- When stock becomes greater than 0:
--     status = ACTIVE
--
-- stock_count is the only source of product stock.
-- =====================================================

CREATE TABLE IF NOT EXISTS products (

    product_id INT NOT NULL AUTO_INCREMENT,

    name VARCHAR(255) NOT NULL,

    description TEXT,

    price DECIMAL(10,2) NOT NULL,

    stock_count INT NOT NULL DEFAULT 0,

    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',

    created_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (product_id),

    CONSTRAINT chk_products_status
        CHECK (status IN ('ACTIVE', 'INACTIVE')),

    CONSTRAINT chk_products_stock
        CHECK (stock_count >= 0),

    CONSTRAINT chk_products_price
        CHECK (price >= 0)

) ENGINE=InnoDB;


-- =====================================================
-- ORDERS TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS orders (

    order_id VARCHAR(50) NOT NULL,

    customer_id VARCHAR(100) NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'PROCESSING',

    total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (order_id),

    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT chk_orders_total_amount
        CHECK (total_amount >= 0)

) ENGINE=InnoDB;


-- =====================================================
-- ORDER ITEMS TABLE
-- =====================================================
--
-- Composite primary key:
--
-- (order_id, product_id)
--
-- The same product cannot appear twice
-- inside the same order.
-- =====================================================

CREATE TABLE IF NOT EXISTS order_items (

    order_id VARCHAR(50) NOT NULL,

    product_id INT NOT NULL,

    quantity INT NOT NULL,

    price DECIMAL(10,2) NOT NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (order_id, product_id),

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

    CONSTRAINT chk_order_items_quantity
        CHECK (quantity > 0),

    CONSTRAINT chk_order_items_price
        CHECK (price >= 0)

) ENGINE=InnoDB;


-- =====================================================
-- AUDIT LOGS TABLE
-- =====================================================
--
-- This table maintains the history of important
-- product and order operations.
-- =====================================================

CREATE TABLE IF NOT EXISTS audit_logs (

    log_id BIGINT NOT NULL AUTO_INCREMENT,

    entity_type VARCHAR(30) NOT NULL,

    entity_id VARCHAR(100) NOT NULL,

    action VARCHAR(50) NOT NULL,

    old_value JSON NULL,

    new_value JSON NULL,

    performed_by VARCHAR(100) NULL,

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (log_id)

) ENGINE=InnoDB;


-- =====================================================
-- INDEXES
-- =====================================================
--
-- MySQL does not support:
--
-- CREATE INDEX IF NOT EXISTS
--
-- These indexes should be created during the first
-- database initialization.
--
-- Your Lambda initialization code should handle
-- MySQL error 1061 if an index already exists.
-- =====================================================

CREATE INDEX idx_product_name
ON products(name);

CREATE INDEX idx_products_status
ON products(status);

CREATE INDEX idx_orders_customer
ON orders(customer_id);

CREATE INDEX idx_orders_status
ON orders(status);

CREATE INDEX idx_orders_created_at
ON orders(created_at);

CREATE INDEX idx_order_items_product
ON order_items(product_id);

CREATE INDEX idx_audit_entity
ON audit_logs(entity_type, entity_id);

CREATE INDEX idx_audit_action
ON audit_logs(action);

CREATE INDEX idx_audit_created_at
ON audit_logs(created_at);


-- =====================================================
-- SAMPLE CUSTOMERS
-- =====================================================
--
-- The actual tokens are hashed before being stored.
--
-- Admin token:
--     cloudmartadmin123
--
-- Akshay token:
--     akshaytoken123
--
-- Rahul token:
--     rahultoken123
--
-- Priya token:
--     priyatoken123
-- =====================================================

INSERT INTO customers
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
    'ADMIN001',
    'Admin',
    'admin@cloudmart.com',
    '9999999999',
    SHA2('cloudmartadmin123', 256),
    'ADMIN'
),
(
    'CUST101',
    'Akshay',
    'akshay@example.com',
    '9876543210',
    SHA2('akshaytoken123', 256),
    'USER'
),
(
    'CUST102',
    'Rahul',
    'rahul@example.com',
    '9876543211',
    SHA2('rahultoken123', 256),
    'USER'
),
(
    'CUST103',
    'Priya',
    'priya@example.com',
    '9876543212',
    SHA2('priyatoken123', 256),
    'USER'
)
ON DUPLICATE KEY UPDATE

    name = VALUES(name),

    email = VALUES(email),

    phone = VALUES(phone),

    auth_token_hash = VALUES(auth_token_hash),

    role = VALUES(role);


-- =====================================================
-- SAMPLE PRODUCTS
-- =====================================================
--
-- Explicit product IDs are used.
--
-- First run:
--     Laptop   = 1
--     Mouse    = 2
--     Keyboard = 3
--
-- Existing stock_count is not overwritten.
-- Existing status is not overwritten.
-- =====================================================

INSERT INTO products
(
    product_id,
    name,
    description,
    price,
    stock_count,
    status
)
VALUES
(
    1,
    'Laptop',
    'Gaming Laptop',
    75000.00,
    10,
    'ACTIVE'
),
(
    2,
    'Mouse',
    'Wireless Mouse',
    1500.00,
    25,
    'ACTIVE'
),
(
    3,
    'Keyboard',
    'Mechanical Keyboard',
    3500.00,
    15,
    'ACTIVE'
)
ON DUPLICATE KEY UPDATE

    name = VALUES(name),

    description = VALUES(description),

    price = VALUES(price);


-- =====================================================
-- SAMPLE ORDER
-- =====================================================
--
-- This sample order is inserted only if it does not
-- already exist.
--
-- Existing order status is not reset.
-- =====================================================

INSERT INTO orders
(
    order_id,
    customer_id,
    status,
    total_amount
)
VALUES
(
    'ORD1001',
    'CUST101',
    'PROCESSING',
    75000.00
)
ON DUPLICATE KEY UPDATE

    customer_id = VALUES(customer_id),

    total_amount = VALUES(total_amount);


-- =====================================================
-- SAMPLE ORDER ITEM
-- =====================================================

INSERT INTO order_items
(
    order_id,
    product_id,
    quantity,
    price
)
VALUES
(
    'ORD1001',
    1,
    1,
    75000.00
)
ON DUPLICATE KEY UPDATE

    quantity = VALUES(quantity),

    price = VALUES(price);


-- =====================================================
-- END OF CLOUDMART DATABASE SCHEMA
-- =====================================================