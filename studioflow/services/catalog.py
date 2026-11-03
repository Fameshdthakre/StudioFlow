"""Catalog identity, transactional CSV import and filename matching."""

import csv
import io
import re
import uuid

from fastapi import HTTPException

from .common import audit, now

CSV_COLUMNS = [
    "ASIN",
    "Item Code",
    "SKU",
    "Colour",
    "Size",
    "Variation",
    "Title",
    "Item Highlight",
    "Bullet Point 1",
    "Bullet Point 2",
    "Bullet Point 3",
    "Bullet Point 4",
    "Bullet Point 5",
]


def canonical_sku(sku):
    if not isinstance(sku, str):
        raise HTTPException(400, "Product identifier must be text")
    key = sku.strip().upper()
    if (
        not 1 <= len(key) <= 60
        or any(c in key for c in "/\\\\?&#%")
        or any(ord(c) < 32 for c in key)
    ):
        raise HTTPException(
            400, "SKU must be 1-60 characters and not contain URL special characters"
        )
    return key


def format_catalog_facts(p):
    lines = []
    if p.get("title"):
        lines.append(f"Title: {p['title']}")
    ids = []
    if p.get("asin"):
        ids.append(f"ASIN: {p['asin']}")
    if p.get("item_code"):
        ids.append(f"Item Code: {p['item_code']}")
    if p.get("sku"):
        ids.append(f"SKU: {p['sku']}")
    if ids:
        lines.append(" | ".join(ids))
    attrs = []
    if p.get("colour"):
        attrs.append(f"Colour: {p['colour']}")
    if p.get("size"):
        attrs.append(f"Size: {p['size']}")
    if p.get("variation"):
        attrs.append(f"Variation: {p['variation']}")
    if attrs:
        lines.append("Attributes: " + ", ".join(attrs))
    if p.get("item_highlight"):
        lines.append(f"Key Highlight: {p['item_highlight']}")
    bullets = [
        p.get(f"bullet_{i}", "").strip()
        for i in range(1, 6)
        if p.get(f"bullet_{i}", "").strip()
    ]
    if bullets:
        lines.append("Key Features:")
        for b in bullets:
            lines.append(f"- {b}")
    return "\n".join(lines)


def find_catalog_product(c, identifier):
    if not identifier:
        return None
    key = canonical_sku(identifier)
    # A SKU is authoritative. Aliases can be shared by catalog variations, so
    # never select an arbitrary row when an ASIN or item code is ambiguous.
    rows = c.execute(
        "SELECT * FROM catalog_products WHERE sku=? COLLATE NOCASE LIMIT 2", (key,)
    ).fetchall()
    if not rows:
        rows = c.execute(
            """SELECT * FROM catalog_products
            WHERE asin=? COLLATE NOCASE OR item_code=? COLLATE NOCASE LIMIT 2""",
            (key, key),
        ).fetchall()
    if len(rows) > 1:
        raise HTTPException(
            409, "Product identifier matches multiple catalog items. Use the exact SKU."
        )
    return dict(rows[0]) if rows else None


def resolve_product_sku(c, identifier):
    key = canonical_sku(identifier)
    product = find_catalog_product(c, key)
    return canonical_sku(product["sku"]) if product else key


def import_catalog(workspace, file, user):
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "Catalog file must be under 10 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            raise HTTPException(
                400, "Unable to decode CSV file. Please use UTF-8 or CSV encoding."
            )

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV file has no header row")

    field_map = {}
    for h in reader.fieldnames:
        clean_h = re.sub(r"[^a-zA-Z0-9]", "", h.lower())
        if clean_h == "asin":
            field_map[h] = "asin"
        elif clean_h in ("itemcode", "itemcod", "code", "item_code"):
            field_map[h] = "item_code"
        elif clean_h in ("sku", "itemsku"):
            field_map[h] = "sku"
        elif clean_h in ("colour", "color"):
            field_map[h] = "colour"
        elif clean_h == "size":
            field_map[h] = "size"
        elif clean_h in ("variation", "variant"):
            field_map[h] = "variation"
        elif clean_h in ("title", "producttitle", "name", "productname"):
            field_map[h] = "title"
        elif clean_h in ("itemhighlight", "highlight", "highlights", "keyhighlight"):
            field_map[h] = "item_highlight"
        elif clean_h in ("bulletpoint1", "bullet1", "bp1", "bulletpoint_1"):
            field_map[h] = "bullet_1"
        elif clean_h in ("bulletpoint2", "bullet2", "bp2", "bulletpoint_2"):
            field_map[h] = "bullet_2"
        elif clean_h in ("bulletpoint3", "bullet3", "bp3", "bulletpoint_3"):
            field_map[h] = "bullet_3"
        elif clean_h in ("bulletpoint4", "bullet4", "bp4", "bulletpoint_4"):
            field_map[h] = "bullet_4"
        elif clean_h in ("bulletpoint5", "bullet5", "bp5", "bulletpoint_5"):
            field_map[h] = "bullet_5"

    if not {"sku", "asin", "item_code"}.intersection(field_map.values()):
        raise HTTPException(400, "CSV needs an SKU, ASIN or Item Code column")

    imported = 0
    updated = 0
    with workspace.db() as c:
        # Serialize the lookup/upsert sequence, including databases created
        # before SKU uniqueness was enforced.
        c.execute("BEGIN IMMEDIATE")
        for row_number, row in enumerate(reader, start=2):
            item = {}
            for original_h, val in row.items():
                if original_h in field_map:
                    item[field_map[original_h]] = str(val or "").strip()

            sku = (
                (item.get("sku") or item.get("asin") or item.get("item_code") or "")
                .strip()
                .upper()
            )
            if not sku:
                continue
            try:
                sku = canonical_sku(sku)
                asin = canonical_sku(item["asin"]) if item.get("asin") else ""
                item_code = (
                    canonical_sku(item["item_code"]) if item.get("item_code") else ""
                )
            except HTTPException as ex:
                raise HTTPException(400, f"CSV row {row_number}: {ex.detail}") from ex
            colour = item.get("colour", "").strip()
            size = item.get("size", "").strip()
            variation = item.get("variation", "").strip()
            title = item.get("title", "").strip()
            highlight = item.get("item_highlight", "").strip()
            b1 = item.get("bullet_1", "").strip()
            b2 = item.get("bullet_2", "").strip()
            b3 = item.get("bullet_3", "").strip()
            b4 = item.get("bullet_4", "").strip()
            b5 = item.get("bullet_5", "").strip()

            p_dict = {
                "sku": sku,
                "asin": asin,
                "item_code": item_code,
                "colour": colour,
                "size": size,
                "variation": variation,
                "title": title,
                "item_highlight": highlight,
                "bullet_1": b1,
                "bullet_2": b2,
                "bullet_3": b3,
                "bullet_4": b4,
                "bullet_5": b5,
            }
            facts = format_catalog_facts(p_dict)

            existing_rows = c.execute(
                "SELECT id,facts_summary FROM catalog_products WHERE sku=? COLLATE NOCASE LIMIT 2",
                (sku,),
            ).fetchall()
            if len(existing_rows) > 1:
                raise HTTPException(
                    409,
                    f"CSV row {row_number}: duplicate catalog SKU {sku}. Resolve duplicates before importing.",
                )
            existing = existing_rows[0] if existing_rows else None
            if existing:
                cid = existing["id"]
                # Older imports copied their generated summary into the manual
                # facts field. Remove only an unchanged copy with no later
                # manual-edit audit, preserving actual human edits.
                legacy = c.execute(
                    "SELECT facts,updated_at FROM product_memory WHERE sku=?", (sku,)
                ).fetchone()
                if (
                    legacy
                    and legacy["facts"]
                    and legacy["facts"] == existing["facts_summary"]
                ):
                    edited = c.execute(
                        """SELECT 1 FROM memory_audit WHERE event='sku_facts_updated'
                        AND sku=? AND created_at>=? LIMIT 1""",
                        (sku, legacy["updated_at"] or ""),
                    ).fetchone()
                    if not edited:
                        c.execute(
                            "UPDATE product_memory SET facts='' WHERE sku=?", (sku,)
                        )
                c.execute(
                    """
                    UPDATE catalog_products SET 
                    asin=?, item_code=?, colour=?, size=?, variation=?,
                    title=?, item_highlight=?, bullet_1=?, bullet_2=?, bullet_3=?, bullet_4=?, bullet_5=?,
                    facts_summary=?, updated_by=?, updated_at=?
                    WHERE id=?
                """,
                    (
                        asin,
                        item_code,
                        colour,
                        size,
                        variation,
                        title,
                        highlight,
                        b1,
                        b2,
                        b3,
                        b4,
                        b5,
                        facts,
                        user,
                        now(),
                        cid,
                    ),
                )
                updated += 1
            else:
                cid = uuid.uuid4().hex
                c.execute(
                    """
                    INSERT INTO catalog_products (
                        id, sku, asin, item_code, colour, size, variation,
                        title, item_highlight, bullet_1, bullet_2, bullet_3, bullet_4, bullet_5,
                        facts_summary, updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        cid,
                        sku,
                        asin,
                        item_code,
                        colour,
                        size,
                        variation,
                        title,
                        highlight,
                        b1,
                        b2,
                        b3,
                        b4,
                        b5,
                        facts,
                        user,
                        now(),
                    ),
                )
                imported += 1

                # Catalog-derived facts stay in catalog_products. This row stores
                # only manually maintained facts, corrections, and branding.
            c.execute(
                """INSERT OR IGNORE INTO product_memory(sku,facts,updated_by,updated_at)
                VALUES(?,?,?,?)""",
                (sku, "", user, now()),
            )

        total = c.execute("SELECT COUNT(*) n FROM catalog_products").fetchone()["n"]
        audit(
            c,
            user,
            "catalog_csv_imported",
            "",
            f"Imported: {imported}, Updated: {updated}",
        )

    return {"ok": True, "imported": imported, "updated": updated, "total": total}


def get_catalog(workspace, q, user):
    term = f"%{q.strip().upper()}%"
    with workspace.db() as c:
        if q.strip():
            rows = c.execute(
                """
                SELECT * FROM catalog_products 
                WHERE UPPER(sku) LIKE ? OR UPPER(asin) LIKE ? OR UPPER(item_code) LIKE ? OR UPPER(title) LIKE ?
                ORDER BY updated_at DESC LIMIT 200
            """,
                (term, term, term, term),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM catalog_products ORDER BY updated_at DESC LIMIT 200"
            ).fetchall()
        total = c.execute("SELECT COUNT(*) n FROM catalog_products").fetchone()["n"]
    return {"products": [dict(r) for r in rows], "total": total}


def delete_catalog_product(workspace, id, user):
    if not workspace.is_admin(user):
        raise HTTPException(403, "Only workspace owner can delete catalog items")
    with workspace.db() as c:
        c.execute("DELETE FROM catalog_products WHERE id=?", (id,))
    return {"ok": True}


def match_files(workspace, body, user):
    with workspace.db() as c:
        products = [
            dict(r)
            for r in c.execute(
                "SELECT sku, asin, item_code, colour, size, variation, title FROM catalog_products"
            ).fetchall()
        ]

    def norm(s):
        return re.sub(r"[^a-zA-Z0-9]", "", str(s or "").lower())

    matches = {}
    for filename in body.filenames:
        base = re.sub(r"\.[^.]+$", "", str(filename)).strip()
        norm_base = norm(base)
        best_p = None
        best_score = 0
        method = ""
        ambiguous = False

        for p in products:
            n_sku = norm(p["sku"])
            n_asin = norm(p["asin"])
            n_code = norm(p["item_code"])

            score = 0
            m_type = ""

            if n_sku and n_sku == norm_base:
                score = 100
                m_type = "Exact SKU"
            elif n_asin and n_asin == norm_base:
                score = 95
                m_type = "Exact ASIN"
            elif n_code and n_code == norm_base:
                score = 90
                m_type = "Exact Item Code"
            elif n_sku and len(n_sku) >= 3 and n_sku in norm_base:
                score = 80
                m_type = "SKU in filename"
            elif n_asin and len(n_asin) >= 4 and n_asin in norm_base:
                score = 75
                m_type = "ASIN in filename"
            elif n_code and len(n_code) >= 3 and n_code in norm_base:
                score = 70
                m_type = "Item Code in filename"

            if score > best_score:
                best_score = score
                best_p = p
                method = m_type
                ambiguous = False
            elif score and score == best_score and best_p and p["sku"] != best_p["sku"]:
                ambiguous = True

        if best_p and best_score >= 70 and not ambiguous:
            matches[filename] = {
                "matched": True,
                "score": best_score,
                "method": method,
                "sku": best_p["sku"],
                "asin": best_p["asin"],
                "item_code": best_p["item_code"],
                "title": best_p["title"],
                "colour": best_p["colour"],
                "variation": best_p["variation"],
            }
        else:
            matches[filename] = {"matched": False, "ambiguous": ambiguous}

    return {"matches": matches}
