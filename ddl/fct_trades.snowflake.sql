CREATE TABLE mart.fct_trades (
  trade_date        DATE             NOT NULL,
  trade_id          VARCHAR(36)      NOT NULL,
  product_key       NUMBER(10)       NOT NULL,
  customer_key      NUMBER(10)       NOT NULL,
  executed_at_utc   TIMESTAMP_NTZ(3) NOT NULL,
  quantity          NUMBER(18,4)     NOT NULL,
  unit_price        NUMBER(18,6)     NOT NULL,
  gross_amount      NUMBER(18,2)     NOT NULL,
  commission        NUMBER(18,2),
  settlement_date   DATE,
  venue_code        VARCHAR(10),
  dw_load_ts        TIMESTAMP_NTZ(3) NOT NULL,
  CONSTRAINT pk_fct_trades PRIMARY KEY (trade_date, trade_id),
  CONSTRAINT fk_fct_trades_product  FOREIGN KEY (product_key)  REFERENCES mart.dim_product(product_key),
  CONSTRAINT fk_fct_trades_customer FOREIGN KEY (customer_key) REFERENCES mart.dim_customer(customer_key),
  CONSTRAINT ck_fct_trades_venue CHECK (venue_code IN ('NYSE', 'NASDAQ', 'ARCA', 'BATS'))
);
