-- =====================================================
-- CLOUDMART DATABASE SCHEMA
-- =====================================================
-- Database:
-- cloudmart
--
-- Tables:
-- customers
-- products
-- inventory
-- orders
-- order_items
-- audit_logs
--
-- Relationships:
--
-- customers
--     |
--     | 1 : many
--     v
-- orders
--
-- products
--     |
--     | 1 : 1
--     v
-- inventory
--
-- orders
--     |
--     | 1 : many
--     v
-- order_items
--     ^
--     |
-- products
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

    created_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (customer_id),

    UNIQUE KEY uk_customers_email (email)

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
-- A product is NOT physically deleted.
-- DELETE operations will be handled as soft deletes
-- by the Product Lambda.
--
-- When stock reaches 0:
--     status = INACTIVE
--
-- When stock becomes greater than 0:
--     status = ACTIVE
--
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
        CHECK (status IN ('ACTIVE', 'INACTIVE'))

) ENGINE=InnoDB;


-- =====================================================
-- INVENTORY TABLE
-- =====================================================
--
-- IMPORTANT:
-- One product can have only ONE inventory row.
--
-- product_id is therefore UNIQUE.
--
-- =====================================================

CREATE TABLE IF NOT EXISTS inventory (

    inventory_id INT NOT NULL AUTO_INCREMENT,

    product_id INT NOT NULL,

    quantity INT NOT NULL DEFAULT 0,

    last_updated TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (inventory_id),

    UNIQUE KEY uk_inventory_product (product_id),

    CONSTRAINT fk_inventory_product
        FOREIGN KEY (product_id)
        REFERENCES products(product_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE

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
        ON UPDATE CASCADE

) ENGINE=InnoDB;


-- =====================================================
-- ORDER ITEMS TABLE
-- =====================================================
--
-- Composite primary key:
--
-- (order_id, product_id)
--
-- This means the same product cannot appear twice
-- inside the same order.
--
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
        ON UPDATE CASCADE

) ENGINE=InnoDB;


-- =====================================================
-- AUDIT LOGS TABLE
-- =====================================================
--
-- This table maintains the history of important
-- product and order operations.
--
-- Examples:
--
-- Product:
--     CREATE_PRODUCT
--     UPDATE_PRODUCT
--     SOFT_DELETE_PRODUCT
--     STOCK_INCREASED
--     STOCK_DECREASED
--
-- Order:
--     ORDER_CREATED
--     ORDER_CONFIRMED
--     ORDER_FAILED
--     ORDER_CANCELLED
--
-- old_value:
--     State before the operation.
--
-- new_value:
--     State after the operation.
--
-- performed_by:
--     User/system responsible for the operation.
--
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
-- IMPORTANT:
-- MySQL does not support:
--
-- CREATE INDEX IF NOT EXISTS
--
-- Therefore IF NOT EXISTS has been removed.
--
-- The Order Lambda initialization code handles
-- MySQL error 1061 when these indexes already exist.
--
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

INSERT INTO customers
(
    customer_id,
    name,
    email,
    phone
)
VALUES
(
    'CUST101',
    'Akshay',
    'akshay@example.com',
    '9876543210'
),
(
    'CUST102',
    'Rahul',
    'rahul@example.com',
    '9876543211'
),
(
    'CUST103',
    'Priya',
    'priya@example.com',
    '9876543212'
)
ON DUPLICATE KEY UPDATE

    name = VALUES(name),

    phone = VALUES(phone);


-- =====================================================
-- SAMPLE PRODUCTS
-- =====================================================
--
-- IMPORTANT:
-- Explicit product IDs are used.
--
-- First run:
-- Laptop   = 1
-- Mouse    = 2
-- Keyboard = 3
--
-- Re-running the schema will NOT create:
-- Laptop   = 4
-- Mouse    = 5
-- Keyboard = 6
--
-- Existing stock_count is NOT overwritten.
-- Existing status is NOT overwritten.
--
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
-- SAMPLE INVENTORY
-- =====================================================
--
-- IMPORTANT:
-- product_id is UNIQUE.
--
-- Existing inventory quantity is NOT reset.
--
-- This is important because the Order Processor
-- will modify inventory quantity.
--
-- =====================================================

INSERT INTO inventory
(
    product_id,
    quantity
)
VALUES
(
    1,
    10
),
(
    2,
    25
),
(
    3,
    15
)
ON DUPLICATE KEY UPDATE

    product_id = VALUES(product_id);


-- =====================================================
-- SAMPLE ORDER
-- =====================================================
--
-- This sample order is inserted only if it does not
-- already exist.
--
-- Existing order status is NOT reset.
--
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