"""Existing schema migration 2; version and SQL preserved."""


def apply(c):
    duplicates = c.execute("""SELECT 1 FROM catalog_products
        GROUP BY sku COLLATE NOCASE HAVING COUNT(*)>1 LIMIT 1""").fetchone()
    if duplicates:
        import logging

        logging.getLogger(__name__).warning(
            "Catalog contains duplicate SKUs; unique index deferred. "
            "Resolve duplicate catalog records to enable uniqueness on next startup."
        )
        return False
    else:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_catalog_sku ON catalog_products(sku COLLATE NOCASE)"
        )
    return True
