"""Real temporary SQLite retrieval for module labels that differ from filenames."""

import tempfile
import unittest
from pathlib import Path

from backend.app import rag_service
from backend.app.chunking_service import ensure_course_chunks
from backend.app.conversation_service import ConversationService
from backend.app.document_reference_service import resolve_document_reference
from backend.app.embedding_service import embed_course
from backend.app.ingestion_store import IngestionStore
from backend.app.query_understanding_service import empty_understanding
from backend.app.retrieval_router import route_query
from backend.app.retrieval_service import retrieve_evidence
from backend.evaluation.fixtures import ReplayProvider
from backend.tests.test_embeddings import FakeProvider


class ModuleFixture:
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=IngestionStore('student',Path(temp.name)/'data.sqlite3')
        self.cid=42
        self.assignments=[{'canvas_id':104,'course_id':42,'title':'Lab 4','source_type':'assignment','metadata':{}},
                          {'canvas_id':204,'course_id':42,'title':'Assignment 4','source_type':'assignment','metadata':{}},
                          {'canvas_id':140,'course_id':42,'title':'Lab 40','source_type':'assignment','metadata':{}}]
        self.snapshot={'course_id':42,'synced_at':'fixed','assignments':self.assignments,
                       'modules':[{'canvas_id':74,'course_id':42,'title':'Lab 4'},
                                  {'canvas_id':70,'course_id':42,'title':'Assignment 4'},
                                  {'canvas_id':740,'course_id':42,'title':'Lab 40'}]}
        self.store.save_course(self.snapshot)
        for a in self.assignments:
            self.save(f"assignment:{a['canvas_id']}",a['title'],'' if a['canvas_id']==104 else 'A separate assignment with distinct requirements.',source_type='assignment',status='no_text' if a['canvas_id']==104 else 'ok')
        self.save('file:901','ProgrammingTask4.doc','Build a client and server that encrypt and share files.',module_id=74,module_name='Lab 4')
        self.save('file:902','Worksheet.docx','Different numbered assignment instructions.',module_id=70,module_name='Assignment 4')
        self.save('file:903','AnotherTask.pdf','Lab forty is different work.',module_id=740,module_name='Lab 40')
        self.save('file:904','Solutions.pdf','Completed solution code.',module_id=74,module_name='Lab 4')
        self.index()

    def save(self,key,title,text,*,source_type='file',status='ok',module_id=None,module_name=None,**extra):
        refs=[{'source_type':'module_item','canvas_id':int(key.split(':')[1])+10000,
               'module_id':module_id,'module_name':module_name,'title':title}] if module_id else []
        self.store.save_document(self.cid,{'document_key':key,'canvas_id':int(key.split(':')[1]),'course_id':self.cid,
            'source_type':source_type,'title':title,'status':status,'text':text,'references':refs,
            'url':f'https://canvas.example/courses/{self.cid}/{key.replace(":","/")}',**extra},'fixed')

    def index(self):
        ensure_course_chunks(self.cid,self.store)
        self.embedding=FakeProvider();embed_course(self.cid,store=self.store,provider=self.embedding)
        self.embedding.calls.clear()

    def route(self,q='explain lab 4',**changes):
        plan=empty_understanding(q,intent='explain_assignment',assignment_reference='Lab 4',**changes)
        return route_query(self.cid,q,plan,store=self.store,provider=self.embedding)

    def sources(self,result):
        return {s['document_key'] for item in result['results'] for s in item['sources']}


class ModuleMatchingTests(ModuleFixture,unittest.TestCase):
    def test_empty_native_assignment_uses_matching_module_file(self):
        result=self.route()
        self.assertEqual(result['status'],'ready')
        self.assertEqual(self.sources(result),{'file:901'})
        self.assertEqual(result['resolved_assignments'],[104])
        self.assertNotIn('assignment:204',result['resolved_documents'])
        self.assertFalse(self.embedding.calls)  # Overview reads existing vectors without a question call.

    def test_alias_case_and_punctuation_match_the_module(self):
        for q in ('explain LAB #4','what does laboratory 04 ask','walk me through lab 4'):
            with self.subTest(q=q):
                result=retrieve_evidence(self.cid,q,store=self.store,provider=self.embedding,retrieval_mode='overview')
                self.assertEqual(self.sources(result),{'file:901'})

    def test_module_four_does_not_match_forty_or_other_assignment_kind(self):
        result=retrieve_evidence(self.cid,'explain Lab 40',store=self.store,provider=self.embedding,retrieval_mode='overview')
        self.assertEqual(self.sources(result),{'file:903','assignment:140'})
        self.assertNotIn('file:901',result['resolved_documents'])

    def test_explicit_filename_support_uses_module_context_not_its_assignment_number(self):
        result=resolve_document_reference('explain ProgrammingTask4.doc',self.store.load_documents(self.cid),modules=self.snapshot['modules'])
        self.assertEqual(result['document_keys'],['file:901'])
        self.assertEqual(result['supporting_document_keys'],['assignment:104'])

    def test_question_and_solution_roles_remain_separate(self):
        plan=empty_understanding('solution for lab 4',intent='solve_question',assignment_reference='Lab 4')
        result=route_query(self.cid,'show me the solution for lab 4',plan,store=self.store,provider=self.embedding)
        self.assertEqual(self.sources(result),{'file:904'})
        self.assertNotIn('file:904',self.sources(self.route()))

    def test_explicit_filename_can_still_select_a_solution(self):
        result=resolve_document_reference('explain Solutions.pdf',self.store.load_documents(self.cid),modules=self.snapshot['modules'])
        self.assertEqual(result['document_keys'],['file:904'])

    def test_direct_assignment_links_keep_priority_over_ambiguous_modules(self):
        self.save('file:905','Attached instructions.txt','The directly attached task.',references=[{'source_type':'assignment','canvas_id':104}])
        self.snapshot['modules'].append({'canvas_id':75,'course_id':42,'title':'Lab 4'})
        self.store.save_course(self.snapshot)
        self.save('file:906','Other file.txt','Ambiguous module task.',module_id=75,module_name='Lab 4')
        self.index()
        result=self.route()
        self.assertEqual(self.sources(result),{'file:905'})

    def test_duplicate_module_labels_require_clarification(self):
        self.snapshot['modules'].append({'canvas_id':75,'course_id':42,'title':'Lab 4'})
        self.store.save_course(self.snapshot)
        self.save('file:906','Other file.txt','Another task.',module_id=75,module_name='Lab 4')
        self.index()
        result=self.route()
        self.assertEqual(result.get('notice_status'),'clarification_needed')
        self.assertFalse(result['results'])

    def test_direct_solution_link_does_not_hide_module_question_instructions(self):
        self.save('file:905','Attached solutions.txt','A directly linked solution.',
                  references=[{'source_type':'assignment','canvas_id':104}])
        self.index()
        self.assertEqual(self.sources(self.route()),{'file:901'})

    def test_broad_week_module_does_not_attach_all_its_files_to_a_lab(self):
        self.snapshot['modules'][0]['title']='Week 4'
        self.store.save_course(self.snapshot)
        result=self.route()
        self.assertEqual(result['status'],'no_content')
        self.assertFalse(result['results'])

    def test_renamed_module_uses_current_title_instead_of_old_reference_label(self):
        self.snapshot['modules'][0]['title']='Lab 5'
        self.store.save_course(self.snapshot)
        result=self.route()
        self.assertEqual(result['status'],'no_content')
        result=retrieve_evidence(self.cid,'explain ProgrammingTask4.doc',store=self.store,provider=self.embedding,retrieval_mode='overview')
        self.assertEqual(result['results'][0]['sources'][0]['module_names'],['Lab 5'])

    def test_raw_canvas_module_shape_and_active_solution_followup(self):
        modules=[{'id':74,'name':'Lab 4'}]
        result=resolve_document_reference('show its solution',self.store.load_documents(self.cid),['file:901'],modules=modules)
        self.assertEqual(result['document_keys'],['file:904'])
        self.assertEqual(result['supporting_document_keys'],['assignment:104'])

    def test_generic_pdf_followup_does_not_select_a_module_named_pdf(self):
        self.snapshot['modules'].append({'canvas_id':81,'course_id':42,'title':'PDF'})
        self.save('file:909','Other.pdf','Unrelated generic PDF module.',module_id=81,module_name='PDF')
        result=resolve_document_reference('explain this pdf',self.store.load_documents(self.cid),['file:901'],modules=self.snapshot['modules'])
        self.assertEqual(result['document_keys'],['file:901'])

    def test_shared_file_does_not_merge_different_assignment_requirements(self):
        self.save('file:901','ProgrammingTask4.doc','Shared task.',references=[
            {'source_type':'module_item','module_id':74,'module_name':'Lab 4'},
            {'source_type':'module_item','module_id':740,'module_name':'Lab 40'}])
        result=resolve_document_reference('explain ProgrammingTask4.doc',self.store.load_documents(self.cid),modules=self.snapshot['modules'])
        self.assertFalse(result['supporting_document_keys'])

    def test_module_only_work_does_not_require_a_native_gradebook_assignment(self):
        self.snapshot['modules'].append({'canvas_id':80,'course_id':42,'title':'Project Aurora'})
        self.store.save_course(self.snapshot)
        self.save('file:908','Instructions.docx','Design a sensor network.',module_id=80,module_name='Project Aurora')
        self.index()
        plan=empty_understanding('explain Project Aurora',intent='explain_assignment',assignment_reference='Project Aurora')
        result=route_query(self.cid,'explain Project Aurora',plan,store=self.store,provider=self.embedding)
        self.assertEqual(self.sources(result),{'file:908'})

    def test_unindexed_module_file_reports_indexing_needed_without_global_fallback(self):
        with self.store._connection() as c:
            c.execute('DELETE FROM chunk_embeddings')
        result=self.route()
        self.assertEqual(result['status'],'not_indexed')
        self.assertFalse(result['results'])

    def test_unavailable_module_file_does_not_retrieve_stale_text(self):
        self.save('file:901','ProgrammingTask4.doc','',status='unavailable',module_id=74,module_name='Lab 4')
        result=self.route()
        self.assertFalse(result['results'])
        self.assertNotIn('file:902',result.get('resolved_documents',[]))

    def test_same_labels_in_other_accounts_and_future_courses_remain_isolated(self):
        other=IngestionStore('other',self.store.path)
        other.save_course(self.snapshot)
        q='explain lab 4';plan=empty_understanding(q,intent='explain_assignment',assignment_reference='Lab 4')
        result=route_query(self.cid,q,plan,store=other,provider=self.embedding)
        self.assertFalse(result['results'])
        self.cid=8127
        self.store.save_course({'course_id':self.cid,'synced_at':'fixed','assignments':[],
                               'modules':[{'canvas_id':170,'course_id':self.cid,'title':'Workshop 12'}]})
        self.save('file:3101','DifferentName.txt','Build the future semester prototype.',module_id=170,module_name='Workshop 12')
        self.index()
        plan=empty_understanding('Explain Workshop 12',intent='explain_assignment',assignment_reference='Workshop 12')
        result=route_query(self.cid,'Explain Workshop 12',plan,store=self.store,provider=self.embedding)
        self.assertEqual(self.sources(result),{'file:3101'})

    def test_full_answer_receives_module_file_and_preserves_provenance(self):
        plan=empty_understanding('Explain Lab 4',intent='explain_assignment',assignment_reference='Lab 4')
        provider=ReplayProvider(plan)
        result=rag_service.answer_question(self.cid,'Explain Lab 4',store=self.store,embedding_provider=self.embedding,llm_provider=provider)
        self.assertEqual(result['status'],'answered')
        self.assertIn('encrypt and share files',provider.contexts[0][0]['text'])
        self.assertEqual(provider.contexts[0][0]['module_names'],['Lab 4'])
        origin=result['sources'][0]['origins'][0]
        self.assertEqual(origin['references'][0]['module_id'],74)
        self.assertEqual(origin['references'][0]['module_name'],'Lab 4')


class ModuleConversationTests(ModuleFixture,unittest.IsolatedAsyncioTestCase):
    async def test_followup_keeps_the_module_work_instead_of_filename_family(self):
        sessions=ConversationService()
        provider=ReplayProvider(empty_understanding('Explain Lab 4',intent='explain_assignment',assignment_reference='Lab 4'))
        session=sessions.acquire(self.store.namespace,self.cid)
        events=[e async for e in sessions.events(session,'Explain Lab 4','first',store=self.store,provider=provider,embedding_provider=self.embedding)]
        self.assertEqual(events[-1]['data']['status'],'answered')
        provider.plan=empty_understanding('Explain the client portion',intent='explain_question',use_active_documents=True)
        session=sessions.acquire(self.store.namespace,self.cid,session.id)
        events=[e async for e in sessions.events(session,'What about the client?','second',store=self.store,provider=provider,embedding_provider=self.embedding)]
        self.assertEqual(events[-1]['data']['status'],'answered')
        self.assertNotIn('assignment:204',events[-1]['data']['resolved_documents'])
