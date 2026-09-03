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

CREATE TABLE IF NOT EXISTS products (

    product_id INT NOT NULL AUTO_INCREMENT,

    name VARCHAR(255) NOT NULL,

    description TEXT,

    price DECIMAL(10,2) NOT NULL,

    stock_count INT NOT NULL DEFAULT 0,

    created_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (product_id)

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
-- INDEXES
-- =====================================================

-- These indexes are useful for searching/filtering.
-- The deployment code should tolerate these already
-- existing if schema.sql is executed more than once.

CREATE INDEX IF NOT EXISTS idx_product_name
ON products(name);

CREATE INDEX IF NOT EXISTS idx_orders_customer
ON orders(customer_id);

CREATE INDEX IF NOT EXISTS idx_orders_status
ON orders(status);

CREATE INDEX IF NOT EXISTS idx_orders_created_at
ON orders(created_at);

CREATE INDEX IF NOT EXISTS idx_order_items_product
ON order_items(product_id);


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
-- This prevents:
--
-- First run:
-- Laptop   = 1
-- Mouse    = 2
-- Keyboard = 3
--
-- Second run:
-- Laptop   = 4  <-- OLD PROBLEM
--
-- =====================================================

INSERT INTO products
(
    product_id,
    name,
    description,
    price,
    stock_count
)
VALUES
(
    1,
    'Laptop',
    'Gaming Laptop',
    75000.00,
    10
),
(
    2,
    'Mouse',
    'Wireless Mouse',
    1500.00,
    25
),
(
    3,
    'Keyboard',
    'Mechanical Keyboard',
    3500.00,
    15
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
-- ON DUPLICATE KEY UPDATE does NOT reset quantity.
--
-- This is important because the Order Processor will
-- change inventory quantity.
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