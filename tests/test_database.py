"""Migration safety for an existing catalog, sessions, memory and render history."""
import tempfile
import unittest
from pathlib import Path

from studioflow.database import SCHEMA, connect, initialize


class MigrationTests(unittest.TestCase):
    def test_additive_migrations_preserve_legacy_data_and_are_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.sqlite3'
            with connect(path) as c:
                c.executescript(SCHEMA)
                c.execute("INSERT INTO sessions VALUES('session-hash','owner','created','expires')")
                c.execute("INSERT INTO jobs(id,user,kind,total,status) VALUES('job','owner','main',1,'finished')")
                c.execute("INSERT INTO renders(id,job_id,user,sku,kind,status,file) VALUES('render','job','owner','SKU-1','main','completed','original.jpg')")
                c.execute("INSERT INTO product_memory(sku,facts) VALUES('SKU-1','Verified facts')")
                c.execute("INSERT INTO catalog_products(id,sku,asin,title) VALUES('product','SKU-1','ASIN-1','Original title')")
                c.execute("INSERT INTO product_rules(id,sku,instruction) VALUES('rule','SKU-1','Approved rule')")
                c.execute("INSERT INTO brands(id,name) VALUES('brand','Saved brand')")
                tables = [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                columns = {name: [row['name'] for row in c.execute(f'PRAGMA table_info({name})')] for name in tables}
                before = {name: [tuple(row) for row in c.execute(f'SELECT * FROM {name}')] for name in tables}
            initialize(path)
            initialize(path)
            with connect(path) as c:
                for name in tables:
                    selected = ','.join(columns[name])
                    self.assertEqual([tuple(row) for row in c.execute(f'SELECT {selected} FROM {name}')], before[name], name)
                self.assertEqual([row[0] for row in c.execute('SELECT version FROM schema_migrations ORDER BY version')], [1, 2, 3, 4])
                self.assertEqual(c.execute('SELECT aplus_profile_id FROM renders').fetchone()[0], '')
                self.assertEqual(c.execute('SELECT COUNT(*) FROM usage_ledger').fetchone()[0], 0)
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
