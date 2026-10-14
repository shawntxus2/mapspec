-- Oracle. Note launch_date and discontinued_date: Oracle DATE is a date PLUS a time to
-- the second, not an ANSI DATE. Mapping either to another platform's DATE discards the
-- time component silently, which is why types are normalized platform-qualified.
CREATE TABLE mart.dim_product (
  product_key           NUMBER(10)     NOT NULL,
  product_durable_key   NUMBER(10)     NOT NULL,
  product_id            VARCHAR2(20)   NOT NULL,
  product_name          VARCHAR2(200),
  category_code         VARCHAR2(10),
  status_code           VARCHAR2(8),
  list_price            NUMBER(18,4),
  original_list_price   NUMBER(18,4),
  launch_date           DATE,
  discontinued_date     DATE,
  supplier_id           VARCHAR2(20),
  valid_from            TIMESTAMP(3)   NOT NULL,
  valid_to              TIMESTAMP(3)   NOT NULL,
  is_current            CHAR(1)        NOT NULL,
  version_no            NUMBER(5)      NOT NULL,
  record_hash           VARCHAR2(64)   NOT NULL,
  attribute_hash        VARCHAR2(64),
  dw_load_ts            TIMESTAMP(3)   NOT NULL,
  dw_source_system      VARCHAR2(20)   NOT NULL,
  CONSTRAINT pk_dim_product  PRIMARY KEY (product_key),
  CONSTRAINT uk_dim_product  UNIQUE (product_durable_key, valid_from),
  CONSTRAINT ck_dim_product_status CHECK (status_code IN ('ACTIVE', 'DISCONTINUED', 'PENDING'))
);
