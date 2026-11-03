import dataclasses
import json
import time
import unittest

from tests.support import isolated_app
from tests.test_app import photo


class AplusTests(unittest.TestCase):
    def setUp(self):
        context=isolated_app()
        self.studio,self.client=context.__enter__()
        self.addCleanup(context.__exit__,None,None,None)

    def profile(self, name='A+ test profile', **changes):
        result=self.client.post('/api/aplus/profiles',json={'name':name,**changes})
        self.assertEqual(result.status_code,200,result.text)
        return result.json()['id']

    def generate(self,pid,**changes):
        data={'kind':'aplus','aplus_profile_id':pid,'canvas':'banner','skus':'["ASIN-1"]','scenes':'A hero scene'}
        data.update(changes)
        return self.client.post('/api/generate',data=data,files={'files':('ASIN-1.png',photo(),'image/png')},headers={'Idempotency-Key':'aplus-job-0001'})

    def test_profile_snapshot_includes_catalog_and_survives_edits_and_revision(self):
        pid=self.profile(master_instructions='Original immutable A+ master instructions')
        self.client.post('/api/catalog/import',files={'file':('catalog.csv',b'SKU,ASIN,Title\nSKU-1,ASIN-1,Exact product\n','text/csv')})
        self.client.put('/api/memory/products/SKU-1',json={'facts':'Verified capacity 750ml'})
        response=self.generate(pid)
        self.assertEqual(response.status_code,200,response.text)
        parent=self.client.get('/api/renders').json()[0]
        self.client.put('/api/aplus/profiles/'+pid,json={'name':'Changed profile','master_instructions':'Changed A+ instructions that must not affect revisions'})
        self.assertEqual(self.generate(pid).json(),response.json())
        with self.studio.db() as c:
            record=dict(c.execute('SELECT * FROM renders WHERE id=?',(parent['id'],)).fetchone())
            c.execute("UPDATE renders SET status='completed',file=? WHERE id=?",(parent['id']+'.jpg',parent['id']))
        memory=json.loads(record['memory_snapshot'])
        self.assertEqual(record['sku'],'SKU-1')
        self.assertEqual(record['aplus_profile_id'],pid)
        self.assertIn('Verified capacity',memory['facts'])
        self.assertEqual(memory['catalog']['title'],'Exact product')
        self.assertEqual(memory['aplus']['name'],'A+ test profile')
        prompt=self.studio.make_prompt('aplus',record['prompt'],record['memory_snapshot'])
        self.assertIn('Original immutable A+ master instructions',prompt)
        self.assertIn('Exact product',prompt)
        revision=self.client.post('/api/revise',json={'render_id':parent['id'],'instructions':'Brighten the background only'})
        self.assertEqual(revision.status_code,200,revision.text)
        with self.studio.db() as c:
            child=c.execute('SELECT * FROM renders WHERE id=?',(revision.json()['render_id'],)).fetchone()
        self.assertEqual(child['aplus_profile_id'],pid)
        self.assertEqual(json.loads(child['memory_snapshot'])['aplus'],memory['aplus'])

    def test_profile_identity_is_part_of_request_deduplication(self):
        first,second=self.profile('First profile'),self.profile('Second profile')
        self.assertEqual(self.generate(first).status_code,200)
        self.assertEqual(self.generate(second).status_code,409)
        self.assertEqual(len(self.client.get('/api/jobs').json()),1)

    def test_profile_and_canvas_validation_cleanup(self):
        pid=self.profile()
        for profile,changes in [('',{}),('missing',{}),(pid,{'canvas':'square'}),(pid,{'kind':'main'})]:
            response=self.generate(profile,**changes)
            self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(list(self.studio.UPLOADS.iterdir()),[])
        self.assertEqual(self.client.get('/api/jobs').json(),[])

    def test_owner_permissions_including_account_named_demo_user(self):
        pid=self.profile()
        for name in ['employee','Demo user']:
            self.studio.app.dependency_overrides[self.studio.current_user]=lambda name=name:name
            self.assertFalse(self.client.get('/api/aplus/profiles').json()['can_edit'])
            self.assertEqual(self.client.post('/api/aplus/profiles',json={'name':'Denied'}).status_code,403)
            self.assertEqual(self.client.put('/api/aplus/profiles/'+pid,json={'name':'Denied'}).status_code,403)
            self.assertEqual(self.client.put('/api/memory/company',json={'instructions':'Cannot change company instructions'}).status_code,403)
            self.assertEqual(self.client.put('/api/usage/checkpoint',json={'balance_checkpoint_usd':10,'alert_percent':20}).status_code,403)
            self.assertEqual(self.client.post('/api/aplus/profiles/'+pid+'/logo',files={'file':('a.png',photo(),'image/png')}).status_code,403)

    def test_profile_fields_and_duplicate_name_validation(self):
        self.profile()
        self.assertEqual(self.client.post('/api/aplus/profiles',json={'name':'a+ TEST profile'}).status_code,409)
        for payload in [{'name':'  '},{'name':'Valid','primary_color':'red'},{'name':'Valid','master_instructions':'short'}]:
            self.assertEqual(self.client.post('/api/aplus/profiles',json=payload).status_code,422)

    def test_logo_is_sanitized_and_only_served_to_authenticated_users(self):
        pid=self.profile()
        response=self.client.post('/api/aplus/profiles/'+pid+'/logo',files={'file':('a.png',photo(),'image/png')})
        self.assertEqual(response.status_code,200,response.text)
        url=response.json()['logo_url']
        self.assertEqual(self.client.get(url).headers['content-type'],'image/png')
        self.client.post('/api/logout')
        self.assertEqual(self.client.get(url).status_code,401)

    def test_feature_switch_disables_new_jobs_without_changing_existing_kinds(self):
        pid=self.profile()
        self.studio.settings=dataclasses.replace(self.studio.settings,enable_aplus=False,enable_usage=False)
        self.assertEqual(self.client.get('/api/aplus/profiles').status_code,404)
        self.assertEqual(self.generate(pid).status_code,404)
        self.assertEqual(self.client.get('/api/usage').status_code,404)
        self.assertEqual(self.client.get('/api/config').json()['features'],{'aplus':False,'usage':False})
        self.assertEqual(self.generate('',kind='main',canvas='square').status_code,200)

    def test_main_prompt_does_not_apply_aplus_styling(self):
        memory=json.dumps({'aplus':{'name':'Injected profile'}})
        self.assertNotIn('Injected profile',self.studio.make_prompt('main','White background',memory))


class AplusDemoTests(unittest.TestCase):
    def test_both_aplus_export_sizes_complete_through_existing_queue(self):
        from PIL import Image
        import io
        with isolated_app(start_queue=True) as (studio,client):
            pid=client.post('/api/aplus/profiles',json={'name':'Demo profile'}).json()['id']
            for canvas in ('banner','mobile'):
                response=client.post('/api/generate',data={'kind':'aplus','aplus_profile_id':pid,'canvas':canvas,'skus':'["A"]','scenes':'Hero background'},files={'files':('a.png',photo(),'image/png')})
                self.assertEqual(response.status_code,200,response.text)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                renders=client.get('/api/renders').json()
                if len(renders)==2 and all(row['status']=='completed' for row in renders):break
                time.sleep(.05)
            self.assertEqual([row['status'] for row in renders],['completed','completed'])
            for row in renders:
                result=Image.open(io.BytesIO(client.get(row['url']).content))
                self.assertEqual(result.size,studio.PRESETS[row['canvas']][1])
            self.assertEqual(client.get('/api/usage').json()['requests'],0)
