-- E-commerce sample schema for the Text-to-SQL demo.
-- Loaded automatically by the Postgres container on first boot.

CREATE TABLE IF NOT EXISTS customers (
    customer_id     SERIAL PRIMARY KEY,
    email           VARCHAR(255) UNIQUE NOT NULL,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    country         VARCHAR(2)   NOT NULL,           -- ISO-3166 alpha-2
    signup_date     DATE         NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE  customers IS 'Registered shoppers.';
COMMENT ON COLUMN customers.country IS 'ISO 3166-1 alpha-2 country code.';
COMMENT ON COLUMN customers.is_active IS 'Whether the account is currently active.';

CREATE TABLE IF NOT EXISTS categories (
    category_id     SERIAL PRIMARY KEY,
    name            VARCHAR(100) UNIQUE NOT NULL,
    parent_id       INTEGER REFERENCES categories(category_id)
);

COMMENT ON TABLE categories IS 'Product taxonomy. Self-referential parent_id allows hierarchies.';

CREATE TABLE IF NOT EXISTS products (
    product_id      SERIAL PRIMARY KEY,
    sku             VARCHAR(50) UNIQUE NOT NULL,
    name            VARCHAR(255) NOT NULL,
    category_id     INTEGER NOT NULL REFERENCES categories(category_id),
    unit_price      NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
    cost            NUMERIC(10,2) NOT NULL CHECK (cost >= 0),
    is_discontinued BOOLEAN NOT NULL DEFAULT FALSE
);

COMMENT ON TABLE  products IS 'Catalog of items available for sale.';
COMMENT ON COLUMN products.unit_price IS 'List price in USD; what customers pay per unit.';
COMMENT ON COLUMN products.cost IS 'Wholesale cost in USD per unit; used for margin calculations.';

CREATE TABLE IF NOT EXISTS orders (
    order_id        SERIAL PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date      TIMESTAMP NOT NULL,
    status          VARCHAR(20) NOT NULL CHECK (status IN ('pending','paid','shipped','delivered','cancelled','refunded')),
    shipping_country VARCHAR(2) NOT NULL
);

COMMENT ON TABLE  orders IS 'Header row for each customer order.';
COMMENT ON COLUMN orders.status IS 'Lifecycle state: pending, paid, shipped, delivered, cancelled, refunded.';

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id   SERIAL PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
    discount        NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (discount >= 0)
);

COMMENT ON TABLE  order_items IS 'Line items belonging to an order.';
COMMENT ON COLUMN order_items.unit_price IS 'Price actually charged per unit at time of sale (may differ from products.unit_price).';
COMMENT ON COLUMN order_items.discount IS 'Per-line discount in USD. Net line revenue = quantity * unit_price - discount.';

CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date  ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product_id ON order_items(product_id);
CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
