-- =====================================================
-- PRODUCTS TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS products (

    product_id INT AUTO_INCREMENT PRIMARY KEY,

    name VARCHAR(255) NOT NULL,

    description TEXT,

    price DECIMAL(10,2) NOT NULL,

    stock_count INT NOT NULL DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP

);

-- =====================================================
-- INVENTORY TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS inventory (

    inventory_id INT AUTO_INCREMENT PRIMARY KEY,

    product_id INT NOT NULL,

    quantity INT NOT NULL DEFAULT 0,

    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_inventory_product
    FOREIGN KEY (product_id)
    REFERENCES products(product_id)
    ON DELETE CASCADE

);

-- =====================================================
-- INDEXES
-- =====================================================

CREATE INDEX idx_product_name
ON products(name);

CREATE INDEX idx_inventory_product
ON inventory(product_id);

-- =====================================================
-- SAMPLE PRODUCTS
-- =====================================================

INSERT INTO products
(name, description, price, stock_count)
VALUES
('Laptop', 'Gaming Laptop', 75000.00, 10),
('Mouse', 'Wireless Mouse', 1500.00, 25),
('Keyboard', 'Mechanical Keyboard', 3500.00, 15);

-- =====================================================
-- SAMPLE INVENTORY
-- =====================================================

INSERT INTO inventory
(product_id, quantity)
VALUES
(1, 10),
(2, 25),
(3, 15);
`