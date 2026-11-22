import copy
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.src.qa_integral import (MODEL,EFFORT,canonical,connect,digest,file_hash,inventory,
    incremental,load_batch,record,report,segments,select_batch,technical_checks,valid_result)
from pipeline.src.qa_integral_runner import command,supervisor_lock


class IntegralTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.snapshot=self.base/'snapshot';self.snapshot.mkdir()
        self.nodes=[
            {'ref':'n1','labels':['Person'],'props':{'entity_id':'Person:a','name':'Alejo','aliases':['Alejo','Sr. Alejo'],'summary':'Alegación documentada. Otra afirmación.','is_pseudonym':True}},
            {'ref':'n2','labels':['Person'],'props':{'entity_id':'Person:a','name':'Alejo'}},
            {'ref':'n3','labels':['Company'],'props':{'name':'Sin identificador'}},
            {'ref':'c','labels':['Chunk'],'props':{'chunk_id':'ch1','doc_id':'doc.pdf','text':'Texto original de la fuente.'}},
            {'ref':'d','labels':['Document'],'props':{'doc_id':'doc.pdf'}},
            {'ref':'q','labels':['Claim'],'props':{'claim_id':'claim','text':'Una alegación documentada'}},
        ]
        self.rels=[
            {'ref':'r1','start':'n1','end':'n3','type':'MET_WITH','props':{'source_kind':'prensa','source_quote':'Le llamó por teléfono','amount':13.2}},
            {'ref':'r2','start':'n1','end':'n3','type':'MET_WITH','props':{'source_kind':'prensa','source_quote':'Le llamó por teléfono','amount':13.2}},
            {'ref':'r3','start':'n1','end':'n3','type':'UNKNOWN_NEW_TYPE','props':{'source_kind':'administrativo'}},
            {'ref':'r4','start':'c','end':'d','type':'PART_OF','props':{}},
            {'ref':'r5','start':'q','end':'n1','type':'ABOUT','props':{}},
        ]
        self.write_snapshot();self.root=self.base/'audit'/'run'
        artifact=self.base/'seed.tgz';artifact.write_bytes(b'synthetic-test-seed')
        self.seed={'version':'test','sha256':file_hash(artifact),'artifact_path':str(artifact)}
        inventory(self.root,self.snapshot,self.seed,{'MET_WITH':'Se reunió con'})

    def tearDown(self):self.tmp.cleanup()

    def write_snapshot(self):
        metadata={}
        for name,rows in [('nodes',self.nodes),('relationships',self.rels)]:
            (self.snapshot/f'{name}.jsonl').write_text(''.join(canonical(r)+'\n' for r in rows))
            metadata[name]={'count':len(rows),'sha256':file_hash(self.snapshot/f'{name}.jsonl')}
        (self.snapshot/'snapshot.json').write_text(canonical(metadata))

    def test_census_no_filters_no_collisions(self):
        db=connect(self.root)
        self.assertEqual(db.execute('select count(*) from objects').fetchone()[0],11)
        self.assertEqual(db.execute("select count(*) from objects where type='MET_WITH'").fetchone()[0],2)
        self.assertEqual(db.execute("select count(*) from objects where type='UNKNOWN_NEW_TYPE'").fetchone()[0],1)
        self.assertEqual(db.execute("select count(*) from units where kind='identity'").fetchone()[0],3)
        self.assertEqual(db.execute("select count(*) from units where kind='summary'").fetchone()[0],2)
        db.close()

    def test_lossless_summary_decimal_aliases(self):
        for text in ['13.250,23 euros. Dos afirmaciones.','Sr. Gómez llamó.\nNunca se reunió.','', 'Texto sin punto']:
            self.assertEqual(''.join(x[2] for x in segments(text)),text)

    def test_frozen_census(self):
        with self.assertRaises(ValueError):inventory(self.root,self.snapshot,self.seed)

    def test_new_census_requires_seed_artifact(self):
        seed={k:v for k,v in self.seed.items() if k!='artifact_path'}
        with self.assertRaises(ValueError):inventory(self.base/'missing-artifact',self.snapshot,seed)

    def test_duplicate_physical_locator_rejected(self):
        self.nodes.append(self.nodes[0]);self.write_snapshot()
        with self.assertRaises(ValueError):inventory(self.base/'bad',self.snapshot,self.seed)

    def test_dangling_endpoint_rejected(self):
        self.rels[0]['end']='absent';self.write_snapshot()
        with self.assertRaises(ValueError):inventory(self.base/'bad',self.snapshot,self.seed)

    def test_batch_immutable_and_no_double_assignment(self):
        a=select_batch(self.root,'a',2)
        b=select_batch(self.root,'b',2)
        self.assertFalse(set(a['units'])&set(b['units']))
        with self.assertRaises(ValueError):select_batch(self.root,'a',2)
        path=self.root/'batches/a.json';path.write_text('{}')
        with self.assertRaises(ValueError):load_batch(self.root,'a')

    def test_lock_across_runs(self):
        with supervisor_lock(self.root):
            with self.assertRaises(RuntimeError):
                with supervisor_lock(self.root.parent/'second'):pass

    def fixture(self, side='verifier', verdict='ok'):
        db=connect(self.root);unit=dict(db.execute("select * from units where kind='summary' limit 1").fetchone());db.close()
        target=json.loads(unit['value'])
        packet={'sources':[{'source_id':'source','status':'accessible','pages':{'1':'Texto original de la fuente.'}}]}
        finding={'unit_id':unit['unit_id'],'verdict':verdict,'reason':'Original leído','coverage_complete':True,
                 'checks':{k:'Comprobado en fuente' for k in ['identity','predicate','direction','modality','event_vs_report_date','amount_currency','provenance','procedural_status','public_presentation','currentness']},
                 'current_claim':False,'requested_sources':['Original completo doc.pdf'], 'assertions':[{'text':target,'verdict':'ok','evidence':[{'source_id':'source','page':'1','quote':'Texto original de la fuente.','original_page_required':False}]}]}
        receipt={'model':MODEL,'effort':EFFORT,'thread_id':side,'workspace':side,'exit_code':0,'packet_sha256':digest(packet),'finding_sha256':digest(finding),'attempt_id':side}
        return unit,packet,finding,receipt

    def state(self,uid):
        db=connect(self.root);s=db.execute('select state from units where unit_id=?',(uid,)).fetchone()[0];db.close();return s

    def test_mandatory_double_and_resume(self):
        u,p,f,r=self.fixture();self.assertTrue(record(self.root,u['unit_id'],'verifier',f,r,p)[0]);self.assertEqual(self.state(u['unit_id']),'pending')
        self.assertTrue(record(self.root,u['unit_id'],'verifier',f,r,p)[0])
        u,p,f,r=self.fixture('arbiter');self.assertTrue(record(self.root,u['unit_id'],'arbiter',f,r,p)[0]);self.assertEqual(self.state(u['unit_id']),'verified')

    def test_agreement_without_evidence_rejected(self):
        u,p,f,r=self.fixture();f['assertions'][0]['evidence'][0]['quote']='Una cita que no está en original';r['finding_sha256']=digest(f)
        self.assertFalse(record(self.root,u['unit_id'],'verifier',f,r,p)[0]);self.assertEqual(self.state(u['unit_id']),'pending')

    def test_defect_requires_an_evidenced_defective_assertion(self):
        u,p,f,r=self.fixture(verdict='defect')
        f.update(defect_code='MISATTRIBUTION',correction='Corregir el titular')
        r['finding_sha256']=digest(f)
        self.assertEqual(valid_result(u,f,r,p),(False,'defect-without-defective-assertion'))

    def test_presentation_observation_preserved_without_changing_gate(self):
        u,p,f,r=self.fixture()
        f['checks']['public_presentation']='TRIAJE_FUERA_DE_ALCANCE: revisar etiqueta de relación'
        r['finding_sha256']=digest(f)
        self.assertTrue(record(self.root,u['unit_id'],'verifier',f,r,p)[0])
        archived=self.root/'triage'/'presentation'/'verifier.json'
        candidate=json.loads(archived.read_text())
        self.assertEqual(candidate['status'],'open')
        validation=json.loads((self.root/'triage'/'presentation-validation'/'verifier.json').read_text())
        self.assertTrue(validation['reading_accepted'])
        self.assertEqual(candidate['finding'],f)
        self.assertEqual(report(self.root)['open_presentation_candidates'],1)
        before=archived.read_bytes()
        record(self.root,u['unit_id'],'verifier',f,r,p)
        self.assertEqual(archived.read_bytes(),before)
        self.assertEqual(self.state(u['unit_id']),'pending')

    def test_dead_agent_pending(self):
        u,p,f,r=self.fixture();r['exit_code']=1
        self.assertFalse(record(self.root,u['unit_id'],'verifier',f,r,p)[0]);self.assertEqual(self.state(u['unit_id']),'pending')

    def test_no_shared_reader(self):
        u,p,f,r=self.fixture();record(self.root,u['unit_id'],'verifier',f,r,p)
        r['attempt_id']='other';record(self.root,u['unit_id'],'arbiter',f,r,p)
        self.assertEqual(self.state(u['unit_id']),'pending')
        db=connect(self.root)
        self.assertEqual(db.execute('select count(*) from readings').fetchone()[0],0)
        self.assertEqual(db.execute("select count(*) from events where event='independence-invalidated'").fetchone()[0],2)
        db.close()
        for side in ('verifier','arbiter'):
            u,p,f,r=self.fixture(side);record(self.root,u['unit_id'],side,f,r,p)
        self.assertEqual(self.state(u['unit_id']),'verified')

    def test_reconcile_after_commit_before_crash(self):
        from pipeline.src.qa_integral import reconcile
        for side in ('verifier','arbiter'):
            u,p,f,r=self.fixture(side);record(self.root,u['unit_id'],side,f,r,p)
        db=connect(self.root)
        with db:db.execute("update units set state='pending' where unit_id=?",(u['unit_id'],))
        db.close()
        self.assertEqual(reconcile(self.root,u['unit_id']),'verified')

    def test_inaccessible_source_is_uncertainty_not_execution_failure(self):
        u,p,f,r=self.fixture(verdict='uncertain');f['assertions']=[];r['finding_sha256']=digest(f)
        record(self.root,u['unit_id'],'verifier',f,r,p)
        r.update(thread_id='arb',workspace='arb',attempt_id='arb');record(self.root,u['unit_id'],'arbiter',f,r,p)
        self.assertEqual(self.state(u['unit_id']),'uncertainty')

    def test_model_high_required(self):
        u,p,f,r=self.fixture();r['effort']='low';self.assertFalse(valid_result(u,f,r,p)[0])
        r['effort']='high';r['model']='other';self.assertFalse(valid_result(u,f,r,p)[0])

    def test_currentness_requires_consulted_source(self):
        u,p,f,r=self.fixture();f['current_claim']=True;r['finding_sha256']=digest(f)
        self.assertFalse(valid_result(u,f,r,p)[0])

    def test_original_page_required(self):
        u,p,f,r=self.fixture();f['assertions'][0]['evidence'][0]['original_page_required']=True;r['finding_sha256']=digest(f)
        self.assertFalse(valid_result(u,f,r,p)[0])
        r['images']={'source':['1']};self.assertTrue(valid_result(u,f,r,p)[0])

    def test_technical_provenance_not_blanket_green(self):
        technical_checks(self.root);db=connect(self.root)
        self.assertEqual(db.execute("select state from units where kind='technical-link' and value like '%ABOUT%'").fetchone()[0],'technical-open');db.close()

    def test_incremental_preserves_removed_denominator(self):
        self.rels.pop(1);self.write_snapshot();other=self.base/'new';inventory(other,self.snapshot,{**self.seed,'version':'two'})
        delta=incremental(self.root,other,self.base/'delta.json')
        self.assertEqual(sum(x['count'] for x in delta['removed']),1)
        self.assertEqual(report(self.root)['domain_total'],report(self.root)['domain_states']['pending'])

    def test_reader_flags_no_shell_or_history(self):
        cmd=command(Path('/tmp/reader'),Path('/tmp/schema'),Path('/tmp/result'))
        self.assertIn('--ephemeral',cmd);self.assertNotIn('resume',cmd);self.assertNotIn('fork',cmd)
        self.assertIn('shell_tool',cmd);self.assertIn('plugins',cmd);self.assertIn('gpt-6-astra',cmd)
        self.assertIn('model_reasoning_effort="high"',cmd)

    def test_summary_omission_rejected(self):
        u,p,f,r=self.fixture();f['assertions'][0]['text']='Una palabra';r['finding_sha256']=digest(f)
        self.assertEqual(valid_result(u,f,r,p),(False,'uncovered-target-text'))

    def test_unrelated_current_url_cannot_support_claim(self):
        u,p,f,r=self.fixture();f['current_claim']=True
        p['sources'].append({'source_id':'unrelated','status':'accessible','pages':{'1':'Sin relación con el hecho'},'url':'https://example.com','consulted_at':'2026-09-07'})
        r['packet_sha256']=digest(p);r['finding_sha256']=digest(f)
        self.assertEqual(valid_result(u,f,r,p),(False,'unverified-currentness'))

    def test_incremental_ignores_physical_id_churn(self):
        for n in self.nodes:n['ref']='new-'+n['ref']
        for r in self.rels:
            for key in ('ref','start','end'):r[key]='new-'+r[key]
        self.write_snapshot();other=self.base/'new';inventory(other,self.snapshot,{**self.seed,'version':'two'})
        delta=incremental(self.root,other,self.base/'delta.json')
        self.assertEqual(delta['added'],[]);self.assertEqual(delta['removed'],[])

    def test_image_only_original_can_be_read(self):
        u,p,f,r=self.fixture();p['sources'][0]['status']='image-only';p['sources'][0]['pages']={'1':''}
        r['images']={'source':['1']};f['assertions'][0]['evidence'][0]['original_page_required']=True
        r['packet_sha256']=digest(p);r['finding_sha256']=digest(f)
        self.assertTrue(valid_result(u,f,r,p)[0])
        record(self.root,u['unit_id'],'verifier',f,r,p)
        r.update(thread_id='arb',workspace='arb',attempt_id='arb')
        record(self.root,u['unit_id'],'arbiter',f,r,p)
        self.assertEqual(self.state(u['unit_id']),'needs-triage')

    def test_visual_quote_absent_from_text_cannot_auto_verify(self):
        u,p,f,r=self.fixture();r['images']={'source':['1']}
        f['assertions'][0]['evidence'][0].update(original_page_required=True,quote='Transcripción visual que no está en el texto')
        r['finding_sha256']=digest(f)
        for side in ('verifier','arbiter'):
            receipt={**r,'thread_id':side,'workspace':side,'attempt_id':side}
            self.assertTrue(record(self.root,u['unit_id'],side,f,receipt,p)[0])
        self.assertEqual(self.state(u['unit_id']),'needs-triage')

    def test_unsupported_uncertainty_rejected(self):
        u,p,f,r=self.fixture(verdict='uncertain');f['requested_sources']=[];r['finding_sha256']=digest(f)
        self.assertFalse(valid_result(u,f,r,p)[0])


class AdifCorrectionTest(unittest.TestCase):
    def test_drift_or_ambiguous_target_aborts_before_mutation(self):
        from unittest.mock import MagicMock
        from pipeline.src.qa_integral_adif_date import apply,BEFORE
        for rows in ([],[{'props':BEFORE},{'props':BEFORE}],[{'props':{**BEFORE,'source_quote':'changed'}}]):
            with self.subTest(rows=rows):
                driver=MagicMock();tx=MagicMock();tx.run.return_value=rows
                driver.session.return_value.__enter__.return_value.execute_write.side_effect=lambda fn:fn(tx)
                with self.assertRaises(ValueError):apply(driver)
                self.assertEqual(tx.run.call_count,1)

    def test_idempotent_apply_and_rollback_do_not_write(self):
        from unittest.mock import MagicMock
        from pipeline.src.qa_integral_adif_date import apply,BEFORE,AFTER
        for rollback,props in ((False,AFTER),(True,BEFORE)):
            driver=MagicMock();tx=MagicMock();tx.run.return_value=[{'props':props}]
            driver.session.return_value.__enter__.return_value.execute_write.side_effect=lambda fn:fn(tx)
            self.assertEqual(apply(driver,rollback=rollback),0)
            self.assertEqual(tx.run.call_count,1)


class SourcePipelineTest(unittest.TestCase):
    setUp=IntegralTest.setUp
    tearDown=IntegralTest.tearDown
    write_snapshot=IntegralTest.write_snapshot
    fixture=IntegralTest.fixture
    state=IntegralTest.state
    def builder(self):
        from pipeline.src.qa_integral_sources import PacketBuilder
        return PacketBuilder(self.root,self.base)

    def test_missing_chunk_id_does_not_recurse(self):
        from unittest.mock import patch
        db=connect(self.root)
        # Runtime fixture independent of census integrity check: source resolver must
        # survive an identifier-less chunk, which inventory expressly permits.
        row=db.execute("select object_id,payload from objects where type='Chunk'").fetchone()
        p=json.loads(row['payload']);p['props'].pop('chunk_id')
        db.execute('update objects set payload=? where object_id=?',(canonical(p),row['object_id']));db.commit();db.close()
        builder=self.builder()
        uid=builder.db.execute("select unit_id from units where kind='identity' limit 1").fetchone()[0]
        with patch('pipeline.src.qa_integral_sources.fetch_public',side_effect=OSError('offline')):
            packet=builder.build(uid)
        self.assertEqual(packet['unit_id'],uid);builder.db.close()

    def test_pdf_basename_collision_rejected(self):
        a=self.base/'data'/'a';b=self.base/'data'/'b';a.mkdir(parents=True);b.mkdir(parents=True)
        (a/'same.pdf').write_bytes(b'first');(b/'same.pdf').write_bytes(b'second')
        with self.assertRaises(ValueError):self.builder()

    def test_packet_recovers_partial_write(self):
        from unittest.mock import patch
        builder=self.builder();uid=builder.db.execute("select unit_id from units where kind='identity' limit 1").fetchone()[0]
        path=self.root/'packets'/f'{uid}.json';path.parent.mkdir();path.write_text('{}')
        with patch('pipeline.src.qa_integral_sources.fetch_public',side_effect=OSError('offline')):
            packet=builder.build(uid)
        self.assertEqual(packet['unit_id'],uid)
        self.assertTrue(path.with_suffix('.images.json').exists());self.assertTrue(path.with_suffix('.sha256').exists())
        self.assertEqual(len(list((self.root/'interrupted-packets').glob('*/*.json'))),1);builder.db.close()

    def test_reopen_preserves_judgments_and_requires_both_new_readers(self):
        from pipeline.src.qa_integral_sources import reopen_with_sources,packet_path
        u,p,f,r=self.fixture();record(self.root,u['unit_id'],'verifier',f,r,p)
        _,p,f,r=self.fixture('arbiter');record(self.root,u['unit_id'],'arbiter',f,r,p)
        old_batch=select_batch(self.root,'batch',2)
        req=reopen_with_sources(self.root,u['unit_id'],{'documents':[{'doc':'doc.pdf','pages':[1,2]}]})
        self.assertEqual(req['revision'],1);self.assertEqual(self.state(u['unit_id']),'pending')
        db=connect(self.root)
        self.assertEqual(db.execute('select count(*) from readings').fetchone()[0],0)
        self.assertEqual(db.execute("select count(*) from events where event='reading-superseded-by-source-revision'").fetchone()[0],2);db.close()
        self.assertIn('.v1.',str(packet_path(self.root,u['unit_id'])))
        self.assertEqual(load_batch(self.root,'batch'),old_batch)

    def test_truncated_snapshot_with_valid_endpoints_rejected(self):
        (self.snapshot/'relationships.jsonl').write_text(canonical(self.rels[0])+'\n')
        with self.assertRaises(ValueError):inventory(self.base/'truncated',self.snapshot,self.seed)

    def test_repeated_old_source_request_never_rolls_back_revision(self):
        from pipeline.src.qa_integral_sources import reopen_with_sources,source_revision
        u,_,_,_=self.fixture()
        first={'urls':['https://example.com/a']};second={'urls':['https://example.com/b']}
        for expected,req in enumerate((first,second,first),1):
            result=reopen_with_sources(self.root,u['unit_id'],req)
            self.assertEqual(result['revision'],expected)
        self.assertEqual(source_revision(self.root,u['unit_id'])['revision'],3)

    def test_interrupted_census_is_archived_and_rebuilt(self):
        root=self.base/'interrupted';root.mkdir();(root/'census.sqlite').write_bytes(b'partial')
        inventory(root,self.snapshot,self.seed)
        self.assertEqual(len(list(root.glob('census.interrupted-*.sqlite'))),1)

    def test_http_loopback_rejected(self):
        from unittest.mock import patch
        from pipeline.src.qa_integral_sources import fetch_public
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):fetch_public('https://example.com/private')

    def test_transient_fetch_retries_and_failed_archive_is_preserved(self):
        from unittest.mock import patch
        from pipeline.src.qa_integral_sources import archive_url
        url='https://example.com/original'
        with patch('pipeline.src.qa_integral_sources.fetch_public',side_effect=OSError('transient')) as fetch:
            failed=archive_url(self.root,url)
        self.assertEqual(fetch.call_count,3)
        old=(self.root/'sources'/f"{failed['source_id']}.json").read_bytes()
        with patch('pipeline.src.qa_integral_sources.fetch_public',return_value=(b'<p>'+b'Original document text '*8+b'</p>','text/html')):
            good=archive_url(self.root,url)
        self.assertEqual(good['status'],'accessible')
        self.assertNotEqual(good['source_id'],failed['source_id'])
        self.assertEqual((self.root/'sources'/f"{failed['source_id']}.json").read_bytes(),old)

    def test_packet_reads_pdf_not_graph_quote(self):
        import fitz
        from unittest.mock import patch
        data=self.base/'data';data.mkdir()
        pdf=fitz.open();page=pdf.new_page()
        original='El capitan Bonilla llamo por telefono. Importe: 5.700 euros. Condena no firme.'
        page.insert_text((40,80),original);pdf.save(data/'original.pdf');pdf.close()
        self.rels[0]['props'].update(source_doc='original.pdf',source_page=1,source_quote='CITA INVENTADA: se reunio presencialmente; sentencia firme; 9000 euros')
        self.write_snapshot();self.root=self.base/'with-pdf';inventory(self.root,self.snapshot,self.seed)
        builder=self.builder()
        uid=builder.db.execute("select u.unit_id from units u join objects o using(object_id) where o.ref='r1' and u.kind='edge'").fetchone()[0]
        with patch('pipeline.src.qa_integral_sources.fetch_public',side_effect=OSError('offline')):
            packet=builder.build(uid)
        text=' '.join(t for s in packet['sources'] for t in s['pages'].values())
        self.assertIn('llamo por telefono',text);self.assertIn('5.700 euros',text);self.assertIn('no firme',text)
        self.assertNotIn('CITA INVENTADA',text);self.assertTrue(packet['image_locators'])
        builder.db.close()


if __name__=='__main__':unittest.main()
