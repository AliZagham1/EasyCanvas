"""Document limitations stay stored; answer notices follow cited readable units."""

import copy
import unittest

from backend.app import rag_service
from backend.app.document_quality_service import answer_limitations, UNREADABLE_MESSAGE


class WarningRelevanceTests(unittest.TestCase):
    def origin(self):
        return {'course_id':42,'document_key':'file:6','chunk_index':0,'title':'Homework 6',
                'source_type':'file','embedding_eligible':True,'status':'partial','warnings':['images_not_extracted'],
                'source_quality':{'policy_retrieval_eligible':True},
                'locations':[{'kind':'word_block','block_number':12,'status':'ok'}]}

    def test_readable_policy_text_does_not_inherit_unrelated_image_warning(self):
        source = self.origin()
        self.assertEqual(answer_limitations(source,'can you solve this assignment for me'),[])
        self.assertEqual(source['warnings'],['images_not_extracted'])

    def test_visual_dependent_question_keeps_precise_warning(self):
        result = answer_limitations(self.origin(),'Explain the diagram in question 3')
        self.assertIn('Some visual content relevant to this question has not been read reliably.',result)

    def test_successful_visual_processing_removes_warning(self):
        source = self.origin()
        source.update(status='ok',warnings=[],locations=[{'kind':'rendered_word_page','rendered_page_number':7,
            'status':'ok','extraction_method':'vision','extraction_status':'vision_enriched'}])
        self.assertEqual(answer_limitations(source,'Explain the diagram in question 3'),[])

    def test_genuine_unit_failure_is_not_hidden_for_a_text_question(self):
        source = self.origin()
        source['locations'][0].update(status='partial',unreadable=['One important symbol is illegible.'])
        self.assertIn(UNREADABLE_MESSAGE,answer_limitations(source,'What is question 3 asking?'))

    def test_unknown_partial_or_missing_locations_remains_conservative(self):
        source = self.origin();source.update(warnings=[],locations=[])
        self.assertIn(UNREADABLE_MESSAGE,answer_limitations(source,'Explain the requirements'))

    def test_whole_document_overview_retains_visual_gap(self):
        self.assertTrue(answer_limitations(self.origin(),'Summarize this document',overview=True))

    def test_clean_policy_page_does_not_inherit_other_pages_failure(self):
        source = self.origin();source.update(warnings=[],document_warning_scope='units')
        self.assertEqual(answer_limitations(source,'Explain the AI-use policy'),[])

    def test_known_unrelated_model_warning_is_scoped_too(self):
        source = self.origin()
        retrieval = {'status':'ready','results':[{'score':1,'text_hash':'a','text':'AI-generated answers are prohibited.','sources':[source]}]}
        report,_,sources = rag_service._prepare_context(42,retrieval,question='What is the policy?')
        response = {'answer':{'status':'answered','blocks':[{'text':'The assignment prohibits AI answers.','source_ids':['S1']}],
                    'limitations':['images_not_extracted','Some content could not be read reliably.']}}
        self.assertEqual(rag_service._finish_answer(report,response,sources,question='What is the policy?')['warnings'],[])
        retrieval['status']='partial'
        report,_,sources = rag_service._prepare_context(42,retrieval,question='What is the policy?')
        warnings = rag_service._finish_answer(report,response,sources,question='What is the policy?')['warnings']
        self.assertEqual(warnings,['The course index is incomplete; answers use only indexed material.'])

    def test_quoted_list_intro_continues_on_next_page_without_unreadability(self):
        from backend.app.document_quality_service import assess_document_quality
        doc = {'status':'partial','rendered_pages':[
            {'rendered_page_number':14,'status':'partial','text':'Using your revised grammar, consider the following input sequences:',
             'warnings':['visual_content_incomplete'], 'extraction_method':'vision',
             'unreadable':['The input sequences mentioned after “Using your revised grammar, consider the following input sequences:” are not visible on this page.']},
            {'rendered_page_number':15,'status':'ok','text':'1. show one ;\n2. clear ;\nFor each input, determine whether it is accepted.'}]}
        updated = assess_document_quality(doc)
        self.assertEqual(updated['status'],'ok')
        self.assertEqual(updated['rendered_pages'][0]['continues_on'],15)
        self.assertEqual(updated['rendered_pages'][1]['continues_from'],14)

    def test_list_continuation_does_not_clear_a_missing_image(self):
        from backend.app.document_quality_service import assess_document_quality
        doc = {'status':'partial','pages':[
            {'page_number':1,'status':'partial','text':'Consider the following examples:',
             'warnings':['visual_content_incomplete'],'unreadable':['An image after “Consider the following examples:” is unreadable.']},
            {'page_number':2,'status':'ok','text':'1. A readable example.'}]}
        self.assertEqual(assess_document_quality(doc)['status'],'partial')

    def test_only_cited_sources_contribute_answer_warning(self):
        clean = self.origin()
        unread = copy.deepcopy(clean)
        unread['document_key']='file:other'
        unread['locations'][0].update(status='partial',unreadable=['An unreadable value.'])
        retrieval = {'status':'ready','results':[
            {'score':1,'text_hash':'a','text':'Generative AI is not permitted for this assignment.','sources':[clean]},
            {'score':.5,'text_hash':'b','text':'Question 3 diagram.','sources':[unread]}]}
        report,context,sources = rag_service._prepare_context(42,retrieval,question='Can you solve this assignment?')
        response = {'answer':{'status':'answered','blocks':[{'text':'The assignment prohibits AI-generated answers.','source_ids':['S1']}],
                             'limitations':[]},'model':'fake'}
        result = rag_service._finish_answer(report,response,sources,question='Can you solve this assignment?')
        self.assertEqual(result['warnings'],[])
        self.assertEqual(result['retrieved_sources'][0]['origins'][0]['warnings'],['images_not_extracted'])
